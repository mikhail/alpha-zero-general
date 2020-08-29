import json
import logging

import numpy as np

from curling import board as board_utils
from curling import constants as c
from curling import simulation
from curling import utils

log = logging.getLogger(__name__)


class GameException(Exception):
    """Logic within game is broken."""


class CurlingGame:

    def __init__(self):
        self.sim = simulation.Simulation()

    @classmethod
    def getBoardSize(cls):
        return board_utils.getBoardSize()

    @classmethod
    def getInitBoard(cls):
        return board_utils.getInitBoard()

    def getActionSize(self):
        return len(c.ACTION_LIST)

    def getNextState(self, board, player, action):
        log.debug(f'getNextState({self.stringRepresentation(board)}, {player}, {action}={utils.decodeAction(action)})')

        self.sim.setupBoard(board)

        totalThrownStones_before = self.sim.space.thrownStonesCount()
        assert totalThrownStones_before < 16
        self.sim.setupAction(player, action)
        self.sim.run()
        assert totalThrownStones_before + 1 == self.sim.space.thrownStonesCount()

        next_board = self.sim.getBoard()
        next_player = -player

        if self.sim.space.thrownStonesCount() < 16:
            np_check = utils.getNextPlayer(next_board, next_player)
            if next_player != np_check:
                raise GameException('Next player check failed.')

        return next_board, next_player

    def getValidMoves(self, board, player):
        log.debug(f'Board for player({player}):')
        log.debug(board_utils.getBoardRepr(board))
        board, player = self.getCanonicalForm(board, player), 1
        log.debug(f'Canonicalized for player({player}):')
        log.debug(board_utils.getBoardRepr(board))

        self.sim.setupBoard(board)

        if self.sim.space.thrownStonesCount() >= 16:
            log.error('Board: strRepr' + self.stringRepresentation(board))
            raise utils.GameException('getValidMoves requested after game end.')

        player_turn = utils.getNextPlayer(board, player)

        # Since we got canonical board player_turn should always be 1
        assert player_turn == player, f'Moves requested for player ({player}) do not match next player ({player_turn})'

        all_actions = [1] * self.getActionSize()

        for action in range(self.getActionSize()):
            h, w, b = utils.decodeAction(action)
            if h * b < 0:  # clever hack to check that handle and broom not both positive or both negative.
                all_actions[action] = 0

        if sum(all_actions) == 0:
            log.error('Entered a bad state: no valid moves.')
            raise GameException('No valid moves. This shouldnt happen')
        return all_actions

    def getGameEnded(self, board: np.array, player: int):

        # Convert everything to first-player perspective
        board = self.getCanonicalForm(board, player)
        player_color = c.P1_COLOR  # Because of the canonical form

        self.sim.setupBoard(board)
        log.debug(f'getGameEnded({board})')
        thrown_stones = self.sim.space.thrownStonesCount()
        # thrown_stones = board_utils.thrownStones(board)
        if thrown_stones < 16:
            log.debug('getGameEnded (thrown = %s) -> 0', thrown_stones)
            return 0

        house_radius = utils.dist(feet=6, inches=c.STONE_RADIUS_IN)
        stones = self.sim.getStones()
        log.debug('Stones in play:')
        for s in stones:
            log.debug(s)

        # TODO: Optimization - don't compute euclid twice
        near_button = sorted(stones, key=lambda s: utils.euclid(s.body.position, c.BUTTON_POSITION))
        in_house = list(filter(lambda s: utils.euclid(s.body.position, c.BUTTON_POSITION) < house_radius, near_button))

        if len(in_house) == 0:
            # Draw is better than being forced to 1
            log.debug('getGameEnded -> draw')
            return 0.00001

        win_count = 0
        win_color = in_house[0].color
        while len(in_house) > win_count and in_house[win_count].color == win_color:
            win_count += 1

        assert win_count > 0

        we_won = win_color == player_color
        log.debug(f"Who won? we_won = win_color == player_color ({we_won} = {win_color} == {player_color})")

        # Ignoring all the fancy logic about hammer or winning by 1 is bad.
        # Too complicated and possibly invalidates the training model
        # because same scenario (one rock in house) has different value for hammer vs not.
        win_value = win_count
        if not we_won:
            win_value *= -1

        log.debug('getGameEnded -> %s', win_value)
        return win_value

    @staticmethod
    def getCanonicalForm(board, player):
        return utils.getCanonicalForm(board, player)

    @staticmethod
    def getSymmetries(board: np.array, pi):
        all_symmetries = [(board, pi)]
        CurlingGame._permuate_symmetries(all_symmetries, board, pi, 0, 8)
        CurlingGame._permuate_symmetries(all_symmetries, board, pi, 8, 16)

        for i in range(len(all_symmetries)):
            s_board, _ = all_symmetries[i]
            flip = s_board.copy()
            flip[c.BOARD_X] *= -1  # vertical symmetry over center line
            all_symmetries.append((flip, pi))
        return all_symmetries

    @staticmethod
    def _permuate_symmetries(all_symmetries, board, pi, start, stop):
        log.debug('Permuating symmetries!')
        for i in range(start, stop):
            stone1 = board[:, i]
            if stone1[c.BOARD_THROWN] == c.NOT_THROWN:
                break
            for i2 in range(i + 1, stop):
                stone2 = board[:, i2]
                if stone2[c.BOARD_THROWN] == c.NOT_THROWN:
                    break
                if stone1[c.BOARD_IN_PLAY] == c.OUT_OF_PLAY and stone2[c.BOARD_IN_PLAY] == c.OUT_OF_PLAY:
                    continue
                swap = board.copy()
                swap[:, [i, i2]] = swap[:, [i2, i]]
                all_symmetries.append((swap, pi))

    @staticmethod
    def stringRepresentation(board: np.array):
        return json.dumps(np.around(board, decimals=2).tolist())

    @classmethod
    def boardFromString(cls, string: str):
        return np.array(json.loads(string))

    @classmethod
    def boardFromSchema(cls, data: dict):
        board = board_utils.getInitBoard()
        # 1. Set all stones out of play
        board_utils.scenario_all_out_of_play(board)

        # 2. Update stones that are explicitly in play
        for stone in data['stones']:
            sid = _get_schema_sid(stone)
            board[c.BOARD_X][sid] = stone['x'] * 12  # web data is in feet, we're in inches
            board[c.BOARD_Y][sid] = stone['y'] * 12
            board[c.BOARD_THROWN][sid] = c.THROWN
            board[c.BOARD_IN_PLAY][sid] = c.IN_PLAY

        # 3. Update stones that haven't been thrown yet
        for sid in range(data['game']['red'], 8):
            board[c.BOARD_THROWN][sid] = c.NOT_THROWN
            board[c.BOARD_IN_PLAY][sid] = c.IN_PLAY

        for sid in range(data['game']['blue'] + 8, 16):
            board[c.BOARD_THROWN][sid] = c.NOT_THROWN
            board[c.BOARD_IN_PLAY][sid] = c.IN_PLAY

        str_repr = cls.stringRepresentation(board)
        log.debug('Back to string: %s' % str_repr)
        return board

    @staticmethod
    def display(board):
        print(" -----------------------")
        print(np.array_str(board))
        print(" -----------------------")


def _get_schema_sid(stone):
    """Converts schema-provided stone to a board stone id (sid)."""
    log.error(stone)
    if stone['color'] == c.P1_COLOR:
        return stone['number'] - 1
    else:
        return stone['number'] + 8 - 1
