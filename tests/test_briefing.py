"""What the stock rally loop makes of the files Dakar writes: the briefing's distances and the progress bar's scale.

The stock code (tests/stock_rally.py is a port of it) builds a driveline's points from drivelineSpline.json, sets their heights
from the terrain block, and measures the stage along them: the race distance by projecting the SS start line and the last race
node on the line, the progress bar by putting every race node and pace note waypoint into the line's points. Earth's terrain
block covers a couple of kilometres round the car, and beyond it the terrain answers a constant: the far end of a stage came
out hundreds of metres under the race nodes, which keep the heights of the data. The Popoli briefing read a special stage
of -3.26 km and its progress bar ran to 48.6 km for the 7.3 km stage.
"""
import math

import pytest

lupa = pytest.importorskip("lupa")

from test_core import lua, at_popoli, logs  # noqa: E402,F401  (the stubbed game and Earth)
import stock_rally as sr  # noqa: E402

BASE = "/gameplay/missions/beamng_earth/%s/%s-%s/"
EDITIONS = {"popoli": ("dakar-popoli", "MISSION_POPOLI"), "testbed": ("dakar-testbed", "MISSION")}
ZSNAP, GEOM = "/lua/ge/extensions/editor/rallyEditor/zSnap", "editor/toolUtilities/geom"
LAST_NODE_BEFORE_STOP_M = 30
TERRAIN_BLOCK_RADIUS_M = 1000            # (Earth's block is ~2 km across)
BLOCK_BASE_HEIGHT = 250.0                # what the terrain answers beyond it (any constant: the briefing's -3.26 km came with ~250 m)


def plain(t):
    if hasattr(t, "keys"):
        keys = list(t.keys())
        if keys and all(isinstance(k, int) for k in keys):
            return [plain(t[i]) for i in range(1, len(keys) + 1)]
        return {str(k): plain(t[k]) for k in keys}
    return t


def start_loop(lua, edition):
    """The mission list's start of an edition's loop, where the player stands at its service park."""
    lua.execute("core.onExtensionLoaded()")
    prefix, mission = EDITIONS[edition]
    if edition == "popoli":
        at_popoli(lua)
    lua.execute("gameplay_missions_missionManager.startWithFade(%s, { setupModuleVehicles = 3 }, { startedFrom = 'missionScreen' })" % mission)
    assert lua.eval("STARTED") is True
    files = lua.eval("FILES")
    return prefix, {str(k): plain(files[k]) for k in files.keys()}


def leg_files(files, prefix, leg):
    kind = "rallyStage" if leg.startswith("ss") else "rallyRoadSection"
    base = BASE % (kind, prefix, leg)
    race, spline = files[base + "race.race.json"], files[base + "rally/drivelineSpline.json"]
    notebook = files.get(base + "rally/notebooks/structured.notebook.json")
    marks = {s["name"]: s["pos"] for s in race["startPositions"]}
    return {
        "nodes": spline["nodes"], "name": spline["name"],
        "length": spline["stats"]["raceDistanceKms"] * 1000,         # (the leg's own length)
        "start": marks.get("SS_start_line") or marks["TC_in"],
        "stop": marks.get("SS_stop_control") or marks["TC_out"],
        "pathnodes": [n["pos"] for n in race["pathnodes"]],
        "waypoints": [w["pos"] for pn in (notebook["pacenotes"] if notebook else []) for w in pn["pacenoteWaypoints"]],
        "special": leg.startswith("ss"),
    }


def terrain(nodes, centre):
    """Earth's terrain as the game answers it: the ground (here: the data) in the block round the car, one constant beyond."""
    def ground(x, y):
        if math.hypot(x - centre[0], y - centre[1]) <= TERRAIN_BLOCK_RADIUS_M:
            return min(nodes, key=lambda n: (n[0] - x) ** 2 + (n[1] - y) ** 2)[2]
        return BLOCK_BASE_HEIGHT
    return ground


@pytest.fixture(params=sorted(EDITIONS))
def loop(request, lua):
    prefix, files = start_loop(lua, request.param)
    return request.param, {leg: leg_files(files, prefix, leg) for leg in ("road1", "ss1")}


def test_the_briefings_distances_are_the_real_ones(loop):
    # the race distance is measured from the start to the last race node, 30 m short of the stop: the stage's own length less that
    edition, legs = loop
    for leg, f in legs.items():
        line = sr.points_free(f["nodes"])
        d = sr.distance_between(line, f["start"], f["pathnodes"][-1])
        assert d > 0
        # (a liaison of 169 m: 2% is 3 m, a smoothed right-angle corner takes a few metres more)
        assert d == pytest.approx(f["length"] - LAST_NODE_BEFORE_STOP_M, abs=max(0.02 * f["length"], 6)), (edition, leg)


def test_the_driveline_is_as_long_as_the_stage(loop):
    edition, legs = loop
    f = legs["ss1"]
    line = sr.points_free(f["nodes"])
    assert sr.polyline_length(line) == pytest.approx(f["length"], rel=0.02), edition
    # (a liaison's line also runs 22 m before its start and 12 m past its stop)
    f = legs["road1"]
    assert sr.polyline_length(sr.points_free(f["nodes"])) == pytest.approx(f["length"] + 34, rel=0.05)


def test_the_progress_bars_scale_is_the_stage_s(loop):
    # the route the progress bar is measured on has every race node and pace note waypoint put in it: to the finish and
    # to each split it is the stage's own distance (the route's point merging is not ported, so a few percent are allowed)
    edition, legs = loop
    f = legs["ss1"]
    line = sr.points_free(f["nodes"])
    total, at = sr.route_lengths(line, f["start"], f["stop"], f["pathnodes"], f["waypoints"])
    assert total == pytest.approx(f["length"], rel=0.06), edition
    assert at[-1] == pytest.approx(f["length"] - LAST_NODE_BEFORE_STOP_M, rel=0.06)
    for k in range(1, len(at) - 1):                          # (the splits: every 3 km along the stage)
        assert at[k] == pytest.approx(3000 * k, rel=0.06), (edition, k, at)


def test_the_race_nodes_and_the_stops_are_points_of_the_driveline(loop):
    # a node beside the line is projected on a straight extension of it (the Popoli liaison read 0.10 km for 0.14)
    edition, legs = loop
    for leg, f in legs.items():
        for p in f["pathnodes"] + [f["start"]]:
            assert min(sr.dist(p, n) for n in f["nodes"]) < 0.01, (edition, leg, p)
        assert min(sr.dist(f["stop"], n) for n in f["nodes"]) < 0.01


def test_the_game_s_terrain_beyond_earths_block_made_the_numbers_of_issue_66(lua):
    # the issue's numbers, from the old way of making the line (the terrain's heights): Special Stage Distance -3.26 km
    # and a progress bar of 48.6 km for the 7.3 km stage. If this stops failing the port above no longer is the game.
    prefix, files = start_loop(lua, "popoli")
    f = leg_files(files, prefix, "ss1")
    line = sr.points_on_terrain(f["nodes"], terrain(f["nodes"], f["start"]))
    assert sr.distance_between(line, f["start"], f["pathnodes"][-1]) / 1000 == pytest.approx(-3.26, abs=0.05)
    total, at = sr.route_lengths(line, f["start"], f["stop"], f["pathnodes"], f["waypoints"])
    assert total / 1000 == pytest.approx(48.6, abs=1.5)


def test_the_testbed_s_progress_bar_ran_long_too(lua):
    prefix, files = start_loop(lua, "testbed")
    f = leg_files(files, prefix, "ss1")
    line = sr.points_on_terrain(f["nodes"], terrain(f["nodes"], f["start"]))
    total, _ = sr.route_lengths(line, f["start"], f["stop"], f["pathnodes"], f["waypoints"])
    assert total > 1.3 * f["length"]


# ---------------------------------------------------------------------------------------------------------------------
# The tap on the game's conform call: what makes the game use the heights of the files

ZSNAP_STUBS = r"""
CONFORMED = {}
ZSNAP = { conformSplineGeometry = function(spline, ...) CONFORMED[#CONFORMED + 1] = 'terrain:' .. tostring(spline.name) end }
GEOM = { catmullRomFree = function(spline, minDiv) CONFORMED[#CONFORMED + 1] = 'free:' .. tostring(spline.name) .. ':' .. tostring(minDiv) end }
package.loaded['/lua/ge/extensions/editor/rallyEditor/zSnap'] = ZSNAP
package.loaded['editor/toolUtilities/geom'] = GEOM
"""


def conformed(lua):
    got = lua.eval("CONFORMED")
    return [got[i] for i in range(1, len(got) + 1)]


def test_the_driveline_is_made_with_the_heights_of_its_own_nodes(lua):
    lua.execute(ZSNAP_STUBS)
    prefix, files = start_loop(lua, "testbed")
    name = lua.eval("require('beamngDakar/legs').DRIVELINE_NAME")
    for leg in ("road1", "ss1"):
        assert leg_files(files, prefix, leg)["name"] == name
    lua.execute("ZSNAP.conformSplineGeometry({ name = %r }, false, 10, 2, 2)" % name)
    lua.execute("ZSNAP.conformSplineGeometry({ name = 'Rally Driveline' }, false, 10, 2, 2)")     # (the game's own: recorded)
    assert conformed(lua) == ["free:%s:10" % name, "terrain:Rally Driveline"]
    assert logs(lua, "tap installed on zSnap.conformSplineGeometry")


def test_the_tap_is_in_before_the_loop_reads_its_files(lua):
    # the schedule is made when the loop starts, from the files the start has just written: the tap goes in first
    lua.execute(ZSNAP_STUBS)
    lua.execute("core.onExtensionLoaded()")
    assert lua.eval("ZSNAP.__dakarOrig_conformSplineGeometry") is None       # (not before a loop starts: nothing is touched)
    lua.execute("ORDER = {}; local orig = jsonWriteFile; function jsonWriteFile(p, d) "
                "ORDER[#ORDER + 1] = ZSNAP.__dakarOrig_conformSplineGeometry ~= nil; return orig(p, d) end")
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, { setupModuleVehicles = 3 }, {})")
    order = lua.eval("ORDER")
    assert lua.eval("#ORDER") > 0 and all(order[i] for i in range(1, lua.eval("#ORDER") + 1))
    lua.execute("core.onExtensionUnloaded()")
    assert lua.eval("ZSNAP.__dakarOrig_conformSplineGeometry") is None        # (and given back at the end)
    lua.execute("ZSNAP.conformSplineGeometry({ name = require('beamngDakar/legs').DRIVELINE_NAME })")
    assert conformed(lua)[-1].startswith("terrain:")


def test_a_failing_free_conform_goes_to_the_games_own(lua):
    lua.execute(ZSNAP_STUBS)
    lua.execute("GEOM.catmullRomFree = function() error('boom') end")
    start_loop(lua, "testbed")
    lua.execute("ZSNAP.conformSplineGeometry({ name = require('beamngDakar/legs').DRIVELINE_NAME })")
    assert conformed(lua) == ["terrain:BeamNG.Dakar driveline"]
    assert logs(lua, "could not be conformed")


def test_without_the_games_modules_the_loop_still_starts(lua):
    prefix, files = start_loop(lua, "testbed")                  # (no zSnap in the stubbed game: only a warning)
    assert files and logs(lua, "the driveline heights are not ours")
