"""Stage curation end to end: the extension with stubbed game and Earth.

    python -m pytest tests

What the three parts do together: the loop is built from the curated stage; a menu start's loading screen waits for
Earth's corridor prefetch (the route message and its route_download pushes, through Earth's net module and the tapped
gps.onRouteDownload); and while the loop runs every terrain block Earth brings in (terrain.load / terrain.swap, tapped)
has the stage's ground shaped to the line the race path is on, again after a commit and after an anchor change.
"""
import json
import os

import pytest

lupa = pytest.importorskip("lupa")

from test_core import EDITION, POPOLI, lua, logs, launched, conv  # noqa: E402,F401  (the stubbed game and Earth)

WORLD_START = None

EARTH_SIDE = r"""
-- Earth's net module and its route answer, the gps' progress hook, the terrain module with its one block
NET_CONNECTED, NET_SENT, ROUTE_RESULTS = true, {}, {}
package.loaded['beamngEarth/net'] = {
  isConnected = function() return NET_CONNECTED end,
  send = function(msg) NET_SENT[#NET_SENT + 1] = msg; return true end,
}
beamngEarth_core.takeRoute = function(id) local r = ROUTE_RESULTS[id]; ROUTE_RESULTS[id] = nil; return r end
GPS_GOT = nil
package.loaded['beamngEarth/gps'] = { onRouteDownload = function(msg) GPS_GOT = msg end }

-- the terrain: a plane under the stage start (the game's ground), one block of 256 samples at 1 m
-- (the shaping fixes defects of the data only, so the stage has 4 m spikes, sigma 8 m, where a test puts them: SPIKES_AT)
GC = { x = 0, y = 0, z = 0 }
SPIKES_AT = {}
function GROUNDF(x, y)
  local z = GC.z + 0.05 * (x - GC.x) + 0.03 * (y - GC.y)
  for _, s in ipairs(SPIKES_AT) do z = z + 4 * math.exp(-((x - s[1]) ^ 2 + (y - s[2]) ^ 2) / 128) end
  return z
end
core_terrain = { getTerrainHeight = function(p) return GROUNDF(p.x, p.y) end }
function newBlock(px, py, pz, rel)
  local tb = { px = px, py = py, pz = pz, n = 256, h = {}, sets = 0, boxes = 0, rel = rel }
  function tb:getPosition() return { x = self.px, y = self.py, z = self.pz } end
  function tb:getSquareSize() return 1 end
  function tb:getWorldBox() return { getExtents = function() return { x = 256, y = 256, z = 100 } end } end
  function tb:getHeightGrid(gx, gy) return self.h[gx + gy * 256] end
  function tb:setHeightGrid(gx, gy, v) self.h[gx + gy * 256] = v; self.sets = self.sets + 1 end
  function tb:updateGrid(a, b) self.boxes = self.boxes + 1 end
  for gy = 0, 255 do for gx = 0, 255 do tb.h[gx + gy * 256] = GROUNDF(px + gx, py + gy) - (rel and pz or 0) end end
  return tb
end
function snapshot(tb) local c = {}; for k, v in pairs(tb.h) do c[k] = v end; return c end
function sameAs(tb, snap) for k, v in pairs(snap) do if tb.h[k] ~= v then return false end end; return true end
function worldAt(tb, x, y)
  return tb:getHeightGrid(math.floor(x - tb.px + 0.5), math.floor(y - tb.py + 0.5)) + (tb.rel and tb.pz or 0)
end
TB, SWAP_OK, TERRAIN_CALLS = nil, true, {}
package.loaded['beamngEarth/terrain'] = {
  SIZE = 256,
  load = function(...) TERRAIN_CALLS[#TERRAIN_CALLS + 1] = 'load'; return true end,
  swap = function(...) TERRAIN_CALLS[#TERRAIN_CALLS + 1] = 'swap'; return SWAP_OK end,
  get = function() return TB end,
}
"""


@pytest.fixture()
def earth(lua):
    lua.execute(EARTH_SIDE)
    return lua


def stage_start_world(lua):
    """The race path's SS_start_line in the frame of the anchor the loop was written for."""
    f = lua.eval("FILES['/gameplay/missions/beamng_earth/rallyStage/dakar-testbed-ss1/race.race.json']")
    for i in range(1, len(f.startPositions) + 1):
        if str(f.startPositions[i].name) == "SS_start_line":
            p = f.startPositions[i].pos
            return p[1], p[2], p[3]
    raise AssertionError("no SS_start_line")


def spike_the_stage(lua, dx=0.0, dy=0.0):
    """Two 4 m spikes of the elevation data on the stage's line, at the driveline's nodes 3 and 5 (30 and 90 m from the start), moved by
    (dx, dy) for a stage in another frame (the driveline file is written once, in the frame of the first anchor)."""
    nodes = lua.eval("FILES['/gameplay/missions/beamng_earth/rallyStage/dakar-testbed-ss1/rally/drivelineSpline.json'].nodes")
    lua.execute("SPIKES_AT = { { %r, %r }, { %r, %r } }" % (nodes[3][1] + dx, nodes[3][2] + dy, nodes[5][1] + dx, nodes[5][2] + dy))


def put_block_on_stage_start(lua, rel=True, pz=200.0, spikes=True):
    x, y, z = stage_start_world(lua)
    lua.execute("SPIKES_AT = {}")
    if spikes:
        spike_the_stage(lua)
    lua.execute("GC = { x = %r, y = %r, z = %r }" % (x, y, z + 1.5))          # (the game's ground a little over the line there)
    lua.execute("TB = newBlock(%r, %r, %r, %s); SNAP = snapshot(TB)" % (x - 100.0, y - 100.0, pz, str(rel).lower()))
    return x, y, z


def started(lua):
    """The loop started and running (as the game's own event says)."""
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")


def run_frames(lua, n=60):
    for _ in range(n):
        lua.execute("core.onUpdate(0.016)")


# --- the curated stage is what the loop is built from ---------------------------------------------------------------------

def test_the_launch_logs_what_the_curation_found_and_builds_the_stage_from_the_curated_line(earth):
    launched(earth)
    summary = logs(earth, "stage curation: ss1 curated")
    assert summary and "spike" in summary[0] and "largest height change" in summary[0] and "grade" in summary[0]
    # the race path's last node and the driveline are on the curated line: points every 10 m along it, not the source's
    # 16 m: the driveline (thinned to 30 m) still agrees with the pace notes, which read the same points
    rp = earth.eval("FILES['/gameplay/missions/beamng_earth/rallyStage/dakar-testbed-ss1/race.race.json']")
    assert len(rp.pathnodes) >= 3
    note = earth.eval("FILES['/gameplay/missions/beamng_earth/rallyStage/dakar-testbed-ss1/rally/notebooks/structured.notebook.json']")
    assert note is not None


def test_the_second_edition_is_curated_too(earth):
    earth.execute("core.onExtensionLoaded()")
    g = json.load(open(os.path.join(POPOLI, "geometry", "road1.json")))["points"][0]
    earth.execute("ANCHOR_LL = { 42.1700, 13.8300 }; PLAYER_LL = { %r, %r }" % (g[0], g[1]))
    earth.execute("local w = require('beamngDakar/legs').toWorld({ { %r, %r, %r } }, geodesy.newAnchor(ANCHOR_LL[1], ANCHOR_LL[2]));"
                  " PLAYER_XY = { w[1][1], w[1][2] }; CAR = { w[1][1], w[1][2], w[1][3] }" % tuple(g))
    earth.execute("gameplay_missions_missionManager.startWithFade(MISSION_POPOLI, {}, {})")
    line = logs(earth, "stage curation: ss1 curated")
    assert line and "7.3 km" in line[0] and "grade 51% -> 20%" in line[0]


# --- the preload and the loading screen -----------------------------------------------------------------------------------

def menu_start_to_launch(lua):
    """A main menu start up to the launch: the Earth level is in, the teleport to the service park committed."""
    lua.execute("core.onExtensionLoaded()")
    lua.execute("LEVEL = nil; EARTH_IN_LEVEL = false; ANCHOR_LL_SAVED = ANCHOR_LL; ANCHOR_LL = nil")
    lua.execute("gameplay_missions_missionManager.startWithLoadingLevel(MISSION, nil, nil, 'gameplaySelector')")
    lua.execute("LEVEL = 'beamng_earth'; EARTH_IN_LEVEL = true; ANCHOR_LL = ANCHOR_LL_SAVED")
    lua.execute("core.onUpdate(1)")                                   # the Earth level is in: the teleport to the service park
    lua.execute("earthAnchorCommit(37.0900, -7.9800)")
    lua.execute("core.onUpdate(5)")                                   # the settle delay: launch


def test_a_menu_start_holds_the_loading_screen_until_the_corridor_is_in(earth):
    menu_start_to_launch(earth)
    sent = earth.eval("NET_SENT")
    assert len(sent) == 1 and sent[1].type == "route" and sent[1].download == "route"
    # the stage's start to its finish (ss1: 37.153, -7.887 -> 37.2338, -8.1733)
    assert sent[1].lat0 == pytest.approx(37.153, abs=1e-3) and sent[1].lon0 == pytest.approx(-7.887, abs=1e-3)
    assert sent[1].lat1 == pytest.approx(37.2338, abs=1e-3) and sent[1].lon1 == pytest.approx(-8.1733, abs=1e-3)
    assert earth.eval("STARTS") == 0 and earth.eval("LOADING.beamngDakar") is True       # waiting, behind the loading screen
    assert earth.eval("next(FILES)") is None                          # (nothing written yet)
    assert logs(earth, "loading screen stays up until the stage corridor")
    earth.execute("core.onUpdate(1)")
    assert earth.eval("STARTS") == 0                                  # no answer yet
    # Earth's router answers, the server pushes progress through Earth's own message handler (gps.onRouteDownload)
    earth.execute("ROUTE_RESULTS[NET_SENT[1].id] = { points = { { 37.153, -7.887 }, { 37.2338, -8.1733 } }, dist = 40000 }")
    earth.execute("core.onUpdate(1); package.loaded['beamngEarth/gps'].onRouteDownload({ total = 100, done = 20, active = true })")
    assert earth.eval("GPS_GOT.done") == 20                           # (Earth's own handler still ran)
    earth.execute("core.onUpdate(6)")
    assert earth.eval("STARTS") == 0 and logs(earth, "20 of 100")
    assert earth.eval("LOADING.beamngDakar") is True
    earth.execute("package.loaded['beamngEarth/gps'].onRouteDownload({ total = 100, done = 100, active = false })")
    earth.execute("core.onUpdate(1)")                                 # the corridor is in: the loop starts, the screen is let go
    assert earth.eval("STARTS") == 1 and earth.eval("HELD_AT_START") is False
    assert logs(earth, "the corridor is in") and logs(earth, "the stage corridor is settled")
    assert earth.eval("next(FILES)") is not None                      # the route written then, for the anchor


def test_the_loading_screen_gate_ends_at_the_cap(earth):
    menu_start_to_launch(earth)
    earth.execute("ROUTE_RESULTS[NET_SENT[1].id] = { points = { { 37.153, -7.887 }, { 37.2338, -8.1733 } }, dist = 40000 }")
    earth.execute("core.onUpdate(1); package.loaded['beamngEarth/gps'].onRouteDownload({ total = 5000, done = 10, active = true })")
    earth.execute("core.onUpdate(100)")
    assert earth.eval("STARTS") == 0 and earth.eval("LOADING.beamngDakar") is True
    earth.execute("core.onUpdate(30)")                                 # past the 120 s cap since the request
    assert earth.eval("STARTS") == 1 and earth.eval("LOADING.beamngDakar") is False
    assert logs(earth, "going on, the server keeps fetching")


def test_a_menu_start_without_the_server_does_not_wait(earth):
    earth.execute("NET_CONNECTED = false")
    menu_start_to_launch(earth)
    assert earth.eval("STARTS") == 1 and earth.eval("#NET_SENT") == 0
    assert logs(earth, "not connected")


def test_an_in_game_start_does_not_hold_anything_and_the_download_runs_behind_it(earth):
    earth.execute("core.onExtensionLoaded()")
    earth.execute("gameplay_missions_missionManager.startWithFade(MISSION, {}, {})")      # the mission list's start, at the park
    assert earth.eval("STARTS") == 1                                  # at once
    assert earth.eval("LOADING.beamngDakar") is None                  # no loading screen was held
    assert earth.eval("#NET_SENT") == 1 and earth.eval("NET_SENT[1].download") == "route"
    assert logs(earth, "fetched in the background")
    earth.execute("core.onUpdate(1)")                                  # (and it goes on being read)
    earth.execute("ROUTE_RESULTS[NET_SENT[1].id] = { points = { { 37.153, -7.887 }, { 37.2338, -8.1733 } }, dist = 40000 }")
    earth.execute("core.onUpdate(1)")
    assert logs(earth, "Earth's route is")


def test_a_click_while_the_corridor_is_fetched_is_told_and_the_level_ending_lets_the_screen_go(earth):
    menu_start_to_launch(earth)
    earth.execute("gameplay_missions_missionManager.startWithFade(MISSION, {}, {})")
    assert earth.eval("STARTS") == 0 and "being prepared" in str(earth.eval("MESSAGES[#MESSAGES]"))
    earth.execute("core.onClientEndMission()")
    assert earth.eval("LOADING.beamngDakar") is False
    earth.execute("core.onUpdate(200)")
    assert earth.eval("STARTS") == 0                                   # (it does not start over a level that is gone)


# --- the ground shaping ---------------------------------------------------------------------------------------------------

def test_the_ground_under_the_stage_is_shaped_on_the_games_own_ground_not_moved_to_the_lines_heights(earth):
    # (the line's heights are the offline terrain data; where the game's ground is elsewhere the road stays on
    # it, smoothed, instead of being lifted or dug to the line: the race path's nodes are put on the ground by the loop)
    started(earth)
    x, y, z = put_block_on_stage_start(earth)
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b.ter')")              # Earth commits a block: tapped
    run_frames(earth, 80)
    assert earth.eval("TB.sets") > 500                                                 # (the spikes of the line's data)
    g = earth.eval("GROUNDF(%r, %r)" % (float(round(x)), float(round(y))))
    assert g - z == pytest.approx(1.5, abs=0.1)
    assert earth.eval("worldAt(TB, %r, %r)" % (x, y)) == pytest.approx(g, abs=0.1)    # on the game's ground, not the line
    assert logs(earth, "shaping:") and logs(earth, "cells written along")


def test_a_stage_on_ground_without_defects_is_left_exactly_as_earth_made_it(earth):
    # (the plane the stage's ground is, a cross slope and a grade included, is relief: not one cell is written, and the
    # log says that no stretch needed fixing)
    started(earth)
    x, y, z = put_block_on_stage_start(earth, spikes=False)
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b.ter')")
    run_frames(earth, 80)
    assert earth.eval("TB.sets") == 0 and earth.eval("sameAs(TB, SNAP)") is True
    assert logs(earth, "0 stretches of the line fixed")


def test_every_commit_shapes_again_on_the_new_blocks_data(earth):
    started(earth)
    x, y, z = put_block_on_stage_start(earth)
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b1.ter')")
    run_frames(earth, 80)
    first_sets = earth.eval("TB.sets")
    assert first_sets > 500
    # terrain follow: Earth swaps a .ter in (new heights, another base height); the tap sees it
    earth.execute("OLD = TB; GC.z = GC.z + 1.0; TB = newBlock(%r, %r, 190.0, false); SNAP2 = snapshot(TB)" % (x - 90.0, y - 100.0))
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b2.ter')")
    run_frames(earth, 80)
    assert earth.eval("TB.sets") > 500
    assert earth.eval("OLD.sets") == first_sets                         # (the old block's data is not touched again)
    g = lambda: earth.eval("GROUNDF(%r, %r)" % (float(round(x)), float(round(y))))
    assert earth.eval("worldAt(TB, %r, %r)" % (x, y)) == pytest.approx(g(), abs=0.1)     # (the new block's ground: 1 m up)
    # a swap that did not take (Earth's call returned false: no new heights) is not a new block
    earth.execute("SWAP_OK = false; SETS = TB.sets")
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b3.ter')")
    run_frames(earth, 20)
    assert earth.eval("TB.sets") == earth.eval("SETS")
    # and importMaps (load) is a commit too
    earth.execute("TB = newBlock(%r, %r, 190.0, true); SNAP3 = snapshot(TB)" % (x - 100.0, y - 100.0))
    earth.execute("package.loaded['beamngEarth/terrain'].load('b4.png')")
    run_frames(earth, 80)
    assert earth.eval("TB.sets") > 500
    assert earth.eval("worldAt(TB, %r, %r)" % (x, y)) == pytest.approx(g(), abs=0.1)


def test_an_anchor_change_shapes_the_stage_in_the_new_frame(earth):
    started(earth)
    x, y, z = put_block_on_stage_start(earth)
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b1.ter')")
    run_frames(earth, 80)
    assert earth.eval("TB.sets") > 500
    # a rebase: the origin moves, the stage's world points are other numbers; Earth's commit brings a block for the new frame
    earth.execute("earthRebase(37.0900, -7.9800, 37.1500, -7.8900)")
    nx, ny, nz = earth.eval("(function() local w = require('beamngDakar/legs').toWorld({ { 37.153, -7.887, 251.43 } }, "
                            "geodesy.newAnchor(37.1500, -7.8900)); return w[1][1], w[1][2], w[1][3] end)()")
    assert (abs(nx - x) > 1000) or (abs(ny - y) > 1000)
    spike_the_stage(earth, nx - x, ny - y)                              # (the spikes of the stage's line, moved with the frame)
    earth.execute("GC = { x = %r, y = %r, z = %r }" % (nx, ny, nz + 1.5))
    earth.execute("TB = newBlock(%r, %r, 200.0, true); SNAP4 = snapshot(TB)" % (nx - 100.0, ny - 100.0))
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b5.ter')")
    run_frames(earth, 80)
    assert earth.eval("TB.sets") > 500                                 # shaped in the new frame, where the stage is now
    assert earth.eval("worldAt(TB, %r, %r)" % (nx, ny)) == pytest.approx(
        earth.eval("(function() return worldAt(TB, %r, %r) end)()" % (nx, ny)), abs=1e-6)
    zs = earth.eval("worldAt(TB, %r, %r)" % (nx, ny))
    assert abs(zs - nz) < 12                                           # on the line's height (the new frame's bias aside)


def test_a_swap_is_shaped_at_the_car_before_the_tapped_call_returns(earth):
    # (the car on the stage when Earth swaps the block: the ground under it is the shaped road in the same Lua call, before
    # the next physics step, not some frames later)
    started(earth)
    x, y, z = put_block_on_stage_start(earth)
    earth.execute("CAR = { %r, %r, %r }" % (x, y, z))
    earth.execute("getPlayerVehicle = function() return { getPosition = function() return vec(CAR) end, "
                  "getVelocity = function() return vec({ 25, 0, 0 }) end } end")
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b.ter')")           # (no frame has run)
    assert earth.eval("TB.sets") > 200                                                 # (the spike 30 m ahead, the part of it within reach of the car)
    g = earth.eval("GROUNDF(%r, %r)" % (float(round(x)), float(round(y))))
    assert earth.eval("worldAt(TB, %r, %r)" % (float(round(x)), float(round(y)))) == pytest.approx(g, abs=0.1)
    said = logs(earth, "the block is new")
    assert said and "shaped at once" in said[0] and "25.0 m/s" in said[0]
    run_frames(earth, 80)
    assert logs(earth, "on the road core")                                          # the job's log says where the largest change was


def test_a_swap_during_a_rebase_waits_for_the_new_frames_stages(earth):
    started(earth)
    x, y, z = put_block_on_stage_start(earth)
    earth.execute("CAR = { %r, %r, %r }" % (x, y, z))
    earth.execute("package.loaded['beamngEarth/rebase'].moveVehicles({}, geodesy)")      # phase 1: the frame is about to change
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b.ter')")
    assert earth.eval("TB.sets") == 0                                                     # (the stages are in the old frame: not shaped now)
    assert not logs(earth, "the block is new")


def test_the_original_heights_come_back_when_the_loop_stops(earth):
    started(earth)
    put_block_on_stage_start(earth)
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b.ter')")
    run_frames(earth, 80)
    assert earth.eval("sameAs(TB, SNAP)") is False
    earth.execute("core.onAnyMissionChanged('stopped', MISSION)")
    run_frames(earth, 80)
    assert earth.eval("sameAs(TB, SNAP)") is True
    # a commit after the stop shapes nothing
    earth.execute("TB = newBlock(0, 0, 0, true); package.loaded['beamngEarth/terrain'].swap('c.ter')")
    run_frames(earth, 10)
    assert earth.eval("TB.sets") == 0


def test_the_windows_switch_turns_the_shaping_off_and_back_on(earth):
    started(earth)
    put_block_on_stage_start(earth)
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b.ter')")
    run_frames(earth, 80)
    earth.execute("core.setShaping(false)")
    run_frames(earth, 80)
    assert earth.eval("sameAs(TB, SNAP)") is True
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b2.ter')")             # (a commit while it is off: nothing)
    sets = earth.eval("TB.sets")
    run_frames(earth, 20)
    assert earth.eval("TB.sets") == sets
    earth.execute("core.setShaping(true)")
    run_frames(earth, 80)
    assert earth.eval("sameAs(TB, SNAP)") is False


def test_the_window_has_the_switch_and_the_status(earth):
    started(earth)
    put_block_on_stage_start(earth)
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b.ter')")
    earth.execute("""
      TEXTS, CHECKS, TICK = {}, {}, nil
      ui_imgui = setmetatable({ Begin = function() return true end, End = function() end,
        Text = function(t) TEXTS[#TEXTS + 1] = t end,
        SetNextWindowSize = function() end, SetNextWindowPos = function() end, ImVec2 = function() end, Cond_FirstUseEver = 0,
        BoolPtr = function(v) return { [0] = v } end,
        Checkbox = function(label, ptr) CHECKS[#CHECKS + 1] = label; if TICK == label then ptr[0] = not ptr[0]; return true end; return false end,
        Button = function() return false end }, {})
    """)
    run_frames(earth, 80)
    earth.execute("TEXTS = {}; core.onUpdate(0.016)")
    texts = [str(earth.eval("TEXTS")[i]) for i in range(1, len(earth.eval("TEXTS")) + 1)]
    ground = [t for t in texts if t.startswith("Ground:")]
    assert ground and "shaped" in ground[0] and "cells" in ground[0] and "largest change" in ground[0] and "s ago" in ground[0]
    checks = earth.eval("CHECKS")
    labels = [str(checks[i]) for i in range(1, len(checks) + 1)]
    assert "Shape the ground along the stages" in labels
    earth.execute("TICK = 'Shape the ground along the stages'")                          # the click
    earth.execute("TEXTS = {}; core.onUpdate(0.016); TICK = nil")
    run_frames(earth, 80)
    assert earth.eval("sameAs(TB, SNAP)") is True
    texts = [str(earth.eval("TEXTS")[i]) for i in range(1, len(earth.eval("TEXTS")) + 1)]
    assert any("switched off" in t for t in texts if t.startswith("Ground:"))


def test_a_bridge_tagged_in_the_geometry_is_left_alone_and_the_stretches_either_side_are_shaped(earth):
    # (a geometry file may carry "structures": metres along the line that OpenStreetMap says are a bridge or a tunnel)
    earth.execute("JSON['/dakar/editions/testbed-algarve/geometry/ss1.json'].structures = { { from = 60, to = 140, kind = 'bridge' } }")
    started(earth)
    x, y, z = put_block_on_stage_start(earth)
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b.ter')")
    run_frames(earth, 120)
    nodes = earth.eval("FILES['/gameplay/missions/beamng_earth/rallyStage/dakar-testbed-ss1/rally/drivelineSpline.json'].nodes")
    at = lambda i: (nodes[i][1], nodes[i][2], nodes[i][3])          # (node 2 is the start; they are 30 m apart)
    untouched = lambda cx, cy: earth.eval(
        "(function() for dx = -2, 2 do for dy = -2, 2 do local k = math.floor(%r - TB.px + 0.5) + dx + "
        "(math.floor(%r - TB.py + 0.5) + dy) * 256; if TB.h[k] ~= SNAP[k] then return false end end end; return true end)()"
        % (cx, cy))
    px, py, pz = at(3)                                              # 30 m along: before the bridge
    assert untouched(px, py) is False                               # (the spike there is taken out)
    bx, by, bz = at(5)                                              # 90 m along: on it (the road bends: 23 m from its 60 m point)
    assert untouched(bx, by) is True                                # as Earth made it
    assert logs(earth, "1 span") or logs(earth, "span")


def test_the_new_taps_hand_the_originals_results_back_to_earth(earth):
    earth.execute("core.onExtensionLoaded()")
    earth.execute("R1 = package.loaded['beamngEarth/terrain'].load('x.png'); SWAP_OK = false; R2 = package.loaded['beamngEarth/terrain'].swap('x.ter')")
    assert earth.eval("R1") is True and earth.eval("R2") is False
    earth.execute("package.loaded['beamngEarth/gps'].onRouteDownload({ total = 1, done = 1, active = false })")
    assert earth.eval("GPS_GOT.total") == 1                                       # (and the original ran)
    assert earth.eval("package.loaded['beamngEarth/terrain'].__dakarOrig_swap ~= nil")


def test_without_earths_terrain_module_the_loop_still_runs(lua):
    # (optional taps: an Earth without the module costs the shaping and the progress, nothing else)
    launched(lua)
    assert lua.eval("STARTED") is True
    assert logs(lua, "tap not installed")
