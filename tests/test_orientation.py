"""Which way the orientations Dakar writes point, read with the game's own quaternion maths (beamng_quat.py, ported from
lua/common/mathlib.lua), and checked against what the game's own files say.

Three things the game reads, three conventions:
  * a time control's or start position's orientation: its +y is the way on (rallySuperCountdown / vehicleStoppedNearPlane take
    the plane's normal to be rot * (0, 1, 0) and the car comes from the -y side);
  * a mission's start trigger: quatFromDir's, +y along the way the car faces (spawn.safeTeleport and the POI list);
  * the service stall: set on the vehicle object as it is by rallyMoveVehicleTo (setPositionRotation), and a vehicle's front
    is its local -y.
The game's `q * v` turns the other way round from the textbook rotation (a turn about +z of a takes +y to the compass bearing
a), which is what the builders had wrong: the loop gave the stall {0, 0, 0.064, 0.998} for a way north-north-west (bearing 352.7),
an orientation whose +y points north-north-east and whose front, the car's, points south-south-west.
"""
import math
import os

import pytest

import beamng_quat as bq

lupa = pytest.importorskip("lupa")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DAKAR_EXT = os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions").replace("\\", "/")


@pytest.fixture(scope="module")
def legs():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end")
    rt.execute("package.path = package.path .. ';%s/?.lua'" % DAKAR_EXT)
    return rt.eval("(require('beamngDakar/legs'))")


def q_of(t):
    return (t[1], t[2], t[3], t[4])


def unit_of(deg):
    return math.cos(math.radians(deg)), math.sin(math.radians(deg))


BEARINGS = list(range(0, 360, 15)) + [352.7, 97.3, 83.8, 262.7]


@pytest.mark.parametrize("bearing", BEARINGS)
def test_a_direction_becomes_the_game_orientation_for_it_whatever_way_it_points(legs, bearing):
    # (a compass bearing: 0 north, 90 east; the world's x is east, y north)
    dx, dy = math.sin(math.radians(bearing)), math.cos(math.radians(bearing))
    want = bq.heading(bq.from_dir((dx, dy, 0)))              # the game's own constructor for "+y along this"
    got = bq.heading(q_of(legs.yawQuat(dx, dy)))
    assert bq.off(got, want) < 1e-6 and bq.off(got, math.degrees(math.atan2(dy, dx))) < 1e-6
    # a vehicle that faces it: front (local -y) along it, as spawn.safeTeleport makes it from quatFromDir's (a half turn first)
    car = q_of(legs.carQuat(dx, dy))
    assert bq.off(bq.car_heading(car), math.degrees(math.atan2(dy, dx))) < 1e-6
    made = bq.mul(bq.HALF_TURN, bq.from_dir((dx, dy, 0)))
    assert bq.off(bq.car_heading(made), bq.car_heading(car)) < 1e-6


def test_east_and_west_are_not_swapped(legs):
    # the builders' old turn took +y to the mirror image of the direction: north-north-west to north-north-east, and east to west
    east = q_of(legs.yawQuat(1, 0))
    assert bq.heading(east) == pytest.approx(0, abs=1e-9)
    assert bq.off(bq.heading(q_of(legs.yawQuat(-1, 0))), 180) < 1e-9
    nnw = (math.sin(math.radians(352.7)), math.cos(math.radians(352.7)))
    z, w = legs.yawQuat(*nnw)[3], legs.yawQuat(*nnw)[4]
    assert bq.heading((0, 0, z, w)) == pytest.approx(math.degrees(math.atan2(nnw[1], nnw[0])), abs=1e-9)


def test_the_testbed_stall_faces_north_not_south(legs):
    # the testbed's service park: the lead goes at bearing 352.7 (north); the stall the game was given was
    # {0, 0, 0.06397, 0.99795}, a car facing bearing 187.3
    dx, dy = math.sin(math.radians(352.7)), math.cos(math.radians(352.7))
    old = (0, 0, 0.06397, 0.99795)
    assert bq.off(bq.car_heading(old), 90 - 187.3) < 0.1                                        # (south-south-west: the wrong way)
    assert bq.off(bq.car_heading(old), math.degrees(math.atan2(dy, dx))) > 150
    new = q_of(legs.carQuat(dx, dy))
    assert bq.off(bq.car_heading(new), math.degrees(math.atan2(dy, dx))) < 1e-6
    assert new[2] > 0.99 and abs(new[3]) < 0.1                                                  # {0, 0, 0.998, 0.064}


# ---------------------------------------------------------------------------------------------------------------------
# the game's own files, to show the reading above is the game's: numbers copied from BeamNG.drive's gameplay/missions

def test_a_stock_stage_start_line_points_along_its_own_pathnode_normal():
    # italy/rallyStage/ngrc25-cava-di-granito/race.race.json: SS_start_line and the start node's normal (the way the route goes)
    rot = (-0.015945293768808, 0.022768993033931, 0.81879710179736, 0.57340964994591)
    normal = (0.94191750998707, -0.33076027629546)
    assert bq.off(bq.heading(rot), math.degrees(math.atan2(normal[1], normal[0]))) < 5
    # (the textbook rotation would have it pointing 130 degrees away)
    textbook = math.degrees(2 * math.atan2(rot[2], rot[3])) + 90
    assert bq.off(textbook, math.degrees(math.atan2(normal[1], normal[0]))) > 90


def test_a_stock_loop_turns_its_cars_back_to_tc0_and_its_start_trigger_faces_it():
    # italy/rallyLoop/ngrc25/info.json (serviceStallPos / Rot, startTrigger) and rallyRoadSection/ngrc25-road1's TC_in
    stall_pos, stall_rot = (-1147.3521728516, 1664.3903808594), (0, 0, 0.99551386466168, 0.094615777047859)
    tc0 = (-1188.4068603516, 1613.6228027344)
    trigger_rot = (0, 0, -0.77380603696984, 0.63342289797105)
    to_tc0 = math.degrees(math.atan2(tc0[1] - stall_pos[1], tc0[0] - stall_pos[0]))
    assert bq.off(bq.car_heading(stall_rot), to_tc0) > 100            # the dashboard's "Reverse out and reach TC0"
    assert bq.off(bq.heading(trigger_rot), to_tc0) < 45               # the start trigger's +y is toward TC0
    # the developers' "quick jump" to the start (gameplay/rallyLoop.lua, a vehicle's own getRotation()) is the trigger's
    # orientation after spawn.safeTeleport's half turn: the car faces the same way as the trigger does
    jump = (0.0, 0.0, 0.633423, 0.773806)
    assert bq.off(bq.car_heading(jump), bq.heading(trigger_rot)) < 1
    assert bq.off(bq.car_heading(bq.mul(bq.HALF_TURN, trigger_rot)), bq.heading(trigger_rot)) < 1e-3
