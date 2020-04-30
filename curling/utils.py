import logging
import math
from typing import List

import numpy as np
import pymunk

from curling import constants as c

log = logging.getLogger(__name__)


def dist(inches=0., feet=0., meters=0.):
    """Returns value in inches"""
    return (feet * 12.0) + inches + (meters * 39.3701)


ICE_WIDTH = dist(feet=14)
# TODO: FIXME: XXX: NOTE: Why the hell is this 130? Either 132 (including hack) or 126 (back to back)
ICE_LENGTH = dist(feet=130)
BOX_LENGTH = dist(feet=27)
STONE_RADIUS = dist(inches=c.STONE_RADIUS_IN)

HOG_LINE = ICE_LENGTH - BOX_LENGTH
BACKLINE = ICE_LENGTH
BACKLINE_BUFFER = 2 * STONE_RADIUS
BACKLINE_ELIM = BACKLINE + BACKLINE_BUFFER  # Point at which the stones are removed from the game

# For conversion between Board and Real
X_SCALE = (ICE_WIDTH - 2.0 * STONE_RADIUS) / ICE_WIDTH
Y_SCALE = (BACKLINE_ELIM - (0.0 * STONE_RADIUS)) / BACKLINE_ELIM  # Why is this 0? Test works, but why??


class GameException(Exception):
    """Raise when rules of the game are broken."""


class ShooterNotInGame(GameException):
    """For when the shooter has already been removed from the game."""


class Space(pymunk.Space):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        log.debug('Creating a new space 🎉 ')

        self.five_rock_rule_violation = False
        self.p1_removed_stones = 0
        self.p2_removed_stones = 0
        self.shooter_color = 'Unknown'

    def get_stones(self) -> List['Stone']:
        return [s for s in self.shapes if type(s) == Stone]

    def thrownStonesCount(self):
        return len(self.get_stones()) + self.p1_removed_stones + self.p2_removed_stones

    def get_shooter(self):
        shooters = [s for s in self.get_stones() if s.is_shooter]

        if len(shooters) == 1:
            return shooters[0]
        if len(shooters) == 0:
            raise ShooterNotInGame()

        raise GameException("Found %s shooter stones. Expected 1 or 0." % len(shooters))

    def get_shooter_color(self):
        return self.shooter_color

    def remove_stone(self, stone, reason=''):
        log.debug(f'- {stone} {reason}')
        team = stone.getTeamId()
        if team == c.P1:
            self.p1_removed_stones += 1
        else:
            self.p2_removed_stones += 1

        self.remove(stone, stone.body)


class Stone(pymunk.Circle):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Set to true if stone created in the guard zone. Used for 5-rock rule
        self.is_guard = False

        # Indicates that this is the stone that will be thrown.
        self.is_shooter = False

        self.id = 0
        self.color = 'unknown'

    def __repr__(self):
        self.updateGuardValue()
        guard = ' guard' if self.is_guard and not self.moving() else ''
        moving = ' moving' if self.moving() else ''
        return (
            f'<Stone {self.id} {self.color}{guard}{moving} @ ('
            f'{self.body.position.x:n},{self.body.position.y:n}'
            ')>'
        )

    def moving(self):
        vx = abs(self.body.velocity.x) > 0.01
        vy = abs(self.body.velocity.y) > 0.01
        return vx or vy

    def updateGuardValue(self):
        radius = STONE_RADIUS
        hog_line = radius + dist(feet=6 + 6 + 21 + 72)
        tee_line = hog_line + dist(feet=21)
        from_pin = euclid(self.body.position, pymunk.Vec2d(0, tee_line))

        y = self.body.position.y

        before_tee = y < tee_line
        not_in_house = from_pin > dist(feet=6) + STONE_RADIUS

        self.is_guard = before_tee and not_in_house

    def getTeamId(self):
        return c.P1 if self.color == c.P1_COLOR else c.P2


ADJUSTER = 0.5  # Needed to help int() function properly. Python's round() does Engineering rounding.


def realToBoard(x, y) -> (int, int):
    bx = (x + ICE_WIDTH / 2 - STONE_RADIUS) / X_SCALE * c.BOARD_RESOLUTION - ADJUSTER
    by = (y - HOG_LINE - STONE_RADIUS) / Y_SCALE * c.BOARD_RESOLUTION - ADJUSTER
    ix, iy = int(bx), int(by)
    log.debug(f'realToBoard({x}, {y}) -> int({bx, by}) = {ix, iy}')
    return ix, iy


def boardToReal(x: float, y: float):
    real_x = ((float(x) + ADJUSTER) / c.BOARD_RESOLUTION) * X_SCALE + STONE_RADIUS - (ICE_WIDTH / 2.0)
    real_y = ((float(y) + ADJUSTER) / c.BOARD_RESOLUTION) * Y_SCALE + STONE_RADIUS + HOG_LINE
    log.debug(f'boardToReal({x, y}) -> {real_x, real_y}')
    return real_x, real_y


def decodeAction(action):
    assert action >= 0
    return c.ACTION_LIST[action]


def weight_to_dist(w):
    return dist(feet=c.WEIGHT_FT[w.lower()])


def toFt(x):
    return f'{x / 12:3.1f}'


def getPlayerColor(player):
    return c.P1_COLOR if player == 1 else c.P2_COLOR


def stone_velocity(body, gravity, damping, dt):
    F_normal = body.mass * dist(meters=c.G_FORCE)
    F_fr = c.SURFACE_FRICTION * F_normal
    body.force = body.velocity.normalized() * F_fr * -1

    V_curl = getCurlingVelocity(body)  # * dt
    body.velocity -= V_curl

    pymunk.Body.update_velocity(body, gravity, damping, dt)


def calculateVelocityVector(weight: str, broom: int):
    F_normal = c.STONE_MASS * dist(meters=c.G_FORCE)
    F_fr = c.SURFACE_FRICTION * F_normal

    work = weight_to_dist(weight) * F_fr  # W = d*F
    vel = math.sqrt(2.0 * work / c.STONE_MASS)  # W = v^2 * m * 1/2

    x = dist(feet=broom)
    y = weight_to_dist(weight)
    direction = pymunk.Vec2d(x, y).normalized()
    return direction * vel


def addBoundaries(space: Space):
    log.info('Adding boundaries to space')
    left = -ICE_WIDTH / 2
    right = ICE_WIDTH / 2
    # stones are removed when they exit the actual backline.
    backline = dist(feet=130) + 2 * STONE_RADIUS
    log.debug(f'Boundaries (left, right, backline) = {left, right, backline}')
    log.debug(
        f'Adjusted Boundaries (left, right, backline) = {left + STONE_RADIUS, right - STONE_RADIUS, backline + 2 * STONE_RADIUS}')
    w1, w2, w3 = (
        pymunk.Segment(space.static_body, (left, 0), (left, backline), 1),
        pymunk.Segment(space.static_body, (left, backline), (right, backline), 1),
        pymunk.Segment(space.static_body, (right, backline), (right, 0), 1)
    )

    w1.collision_type = 2
    w2.collision_type = 2
    w3.collision_type = 2

    def remove_stone(arbiter, local_space, data):
        stone = arbiter.shapes[0]

        if five_rock_rule(stone, local_space):
            local_space.five_rock_rule_violation = True
            return False
        local_space.remove_stone(stone, 'Collision with the wall')

        return True

    space.add_collision_handler(1, 2).begin = remove_stone

    space.add(w1, w2, w3)


def still_moving(shape):
    vx = abs(shape.body.velocity.x) > 0.001
    vy = abs(shape.body.velocity.y) > 0.001
    return vx or vy


def newStone(color: str):
    body = pymunk.Body()
    body.velocity_func = stone_velocity
    stone = Stone(body, STONE_RADIUS)
    stone.mass = c.STONE_MASS
    stone.color = color
    stone.friction = 1.004  # interaction with other objects, not with "ice"
    stone.density = 1
    stone.elasticity = 0.999999

    stone.collision_type = 1
    return stone


def sqGauss(x: float, m=1., o=0., em=1., eo=0.):
    return (x * m + o) * math.exp(-(math.pow(x, 2) * em + eo))


def getCurlingVelocity(body):
    # numbers taken from index.ts but adjusted to work. Unknown discrepancy
    # Adjustments made to curl 6ft on tee-line draw.

    speed = body.velocity.length / 12 / 6  # 12 because ft/inch. 6 - no idea

    # using 008 instead of 005 also don't know why
    curlFromSpeed = sqGauss(speed, 0.008, 0, 0.2, 1.5)

    curl_effect = 1
    if abs(body.angular_velocity) < 0.01:
        curl_effect = 0

    direction = 90 if body.angular_velocity < 0 else -90
    curlVector = body.velocity.normalized() * curlFromSpeed * curl_effect
    curlVector.rotate_degrees(direction)

    return curlVector


def getBoardRepr(board):
    team1 = np.argwhere(board == c.P1)
    team2 = np.argwhere(board == c.P2)
    return '1:' + str(team1.tolist()) + ':2:' + str(team2.tolist()) + ':d:' + str(board[-1][0:16].astype(int).tolist())


def euclid(v1, v2):
    return math.sqrt(((v1.x - v2.x) ** 2) + ((v1.y - v2.y) ** 2))


def five_rock_rule(stone, space: Space):
    total_stones = space.thrownStonesCount()
    if total_stones > 5:
        return False

    if stone.color == space.get_shooter_color():
        return False

    if not stone.is_guard:
        return False

    return True


def getInitBoard():
    board = np.zeros(getBoardSize(), int)
    board[-1][0:8] = [c.P1_NOT_THROWN] * 8
    board[-1][8:16] = [c.P2_NOT_THROWN] * 8

    return board


def getBoardSize() -> (int, int):
    width_px = int(ICE_WIDTH * c.BOARD_RESOLUTION)
    height_px = int(BOX_LENGTH * c.BOARD_RESOLUTION)

    data_layer = 1  # 1 entire row is dedicated to keep track of data (stones thrown/out of play)
    return width_px + data_layer, height_px


def getNextPlayer(board, player=c.P1):
    data_row = board[-1]

    # 0 = empty ice

    # 1 = player 1 stone in play
    # 2 = player 1 stone not thrown yet
    # 3 = player 1 stone out of play

    # -1 = player 2 stone in play
    # -2 = player 2 stone not thrown yet
    # -3 = player 2 stone out of play

    # data row begins with 22222222 -2-2-2-2-2-2-2-2

    for i in range(8):  # Check 8 stones
        if data_row[i] == c.P1_NOT_THROWN:
            return 1

        if data_row[i + 8] == c.P2_NOT_THROWN:
            return -1

    raise GameException("It is nobody's turn. Player: %s Data row: %s" % (player, data_row[0:16]))


def getData(board: np.ndarray):
    return board[-1][0:16]


def getStoneLocations(board: np.ndarray) -> (List[int], List[int]):
    return np.argwhere(board == c.P1), np.argwhere(board == c.P2)