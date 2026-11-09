"""The extension end to end with stubbed game and Earth: the taps, the start sequence, the probe at a rebase.

Earth's real geodesy.lua does the maths; the game's globals, the rally manager and Earth's rebase/anchor functions are
stubs that behave like the originals (rebase.moveVehicles(xf, geodesy), mpanchor.announceOwn(lat, lon)).
"""
import json
import math
import os

import pytest

lupa = pytest.importorskip("lupa")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EARTH = os.environ.get("BEAMNG_EARTH_DIR") or os.path.join(os.path.dirname(ROOT), "BeamNG.Earth")
EARTH_EXT = os.path.join(EARTH, "mod", "beamng_earth", "lua", "ge", "extensions").replace("\\", "/")
DAKAR_EXT = os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions").replace("\\", "/")
EDITION = os.path.join(ROOT, "editions", "testbed-algarve")
POPOLI = os.path.join(ROOT, "editions", "svolte-di-popoli")        # (the second edition)

if not os.path.isdir(EARTH_EXT):
    pytest.skip("BeamNG.Earth not found (set BEAMNG_EARTH_DIR)", allow_module_level=True)
if not os.path.isfile(os.path.join(EDITION, "geometry", "ss1.json")):
    pytest.skip("run tools/fetch_route.py editions/testbed-algarve first", allow_module_level=True)

STUBS = r"""
math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end
package.path = package.path .. ';%(earth)s/?.lua;%(dakar)s/?.lua'
LOG, FILES, CALLS, JSON = {}, {}, {}, {}
function log(level, tag, msg) LOG[#LOG + 1] = level .. ' ' .. tag .. ' ' .. msg end
function dumps(t) return tostring(t) end
function jsonReadFile(path) return JSON[path] end
function jsonWriteFile(path, data) FILES[path] = data; return true end

-- vec3 as the game has it: x, y, z and :set
local V = {}
V.__index = V
function V:set(x, y, z) self.x, self.y, self.z = x, y, z end
function vec(p) return setmetatable({ x = p[1], y = p[2], z = p[3] }, V) end

-- Earth's side
geodesy = (require('beamngEarth/geodesy'))
package.loaded['beamngEarth/rebase'] = { moveVehicles = function(xf, g) CALLS[#CALLS + 1] = 'moveVehicles'; return { moved = true }, 7 end }
package.loaded['beamngEarth/mpanchor'] = { announceOwn = function(lat, lon) CALLS[#CALLS + 1] = 'announceOwn' end }
-- the player stands at the service park (the edition's first point) unless a test moves him; Earth's anchor is near it
PLAYER_LL, ANCHOR_LL = { 37.0885, -7.9756 }, { 37.0900, -7.9800 }
beamngEarth_core = {
  teleport = function(lat, lon, name, opts) CALLS[#CALLS + 1] = 'teleport'; TELEPORT = { lat, lon } end,
  _state = function() return { player = { lat = PLAYER_LL[1], lon = PLAYER_LL[2], x = PLAYER_XY and PLAYER_XY[1] or 10,
    y = PLAYER_XY and PLAYER_XY[2] or 20 }, anchorId = 7, inOurLevel = EARTH_IN_LEVEL ~= false, pending = EARTH_PENDING,
    anchor = ANCHOR_LL and geodesy.newAnchor(ANCHOR_LL[1], ANCHOR_LL[2]) or nil } end,
}
-- the level select's spawn point (levelinfo.chosen) and the freeroam start of a level from the menu
package.loaded['beamngEarth/levelinfo'] = { chosen = function() return 'bne_random' end }
LEVEL = 'beamng_earth'
function getCurrentLevelIdentifier() return LEVEL end
FREEROAM = nil
freeroam_freeroam = { startFreeroamByName = function(name, sp) FREEROAM = { name, sp }; return true end }
LOADING = {}       -- the game's loading screen requests by tag (it stays up while any is true)
core_gamestate = {
  requestEnterLoadingScreen = function(tag) LOADING[tag] = true end,
  requestExitLoadingScreen = function(tag) LOADING[tag] = false end,
}
LEVEL_STARTS = 0
function realLevelStart(m, settings, level, from) LEVEL_STARTS = LEVEL_STARTS + 1 end
-- Earth's roads (readyAround: the road tiles round (x, y) are in) and its pause (simhold)
ROADS_READY, PAUSED, PAUSES = true, false, {}
ROADS_CALLS = {}
package.loaded['beamngEarth/roads'] = { readyAround = function(aid, x, y, r)
  ROADS_ASKED = { aid, x, y, r }; ROADS_CALLS[#ROADS_CALLS + 1] = ROADS_ASKED
  if ROADS_AT then return ROADS_AT(x, y) end
  return ROADS_READY
end }
package.loaded['beamngEarth/simhold'] = {
  pause = function(on) PAUSED = on; PAUSES[#PAUSES + 1] = on end,
  paused = function() return PAUSED end,
}
MESSAGES = {}
function ui_message(msg) MESSAGES[#MESSAGES + 1] = msg end

-- the game's side
CAR = { 0, 0, 0 }
function getPlayerVehicle() return { getPosition = function() return vec(CAR) end } end

-- scene objects as the game has them
CREATED, DELETED = {}, 0
function vec3(x, y, z) return { x = x, y = y, z = z } end
scenetree = {
  MissionGroup = { addObject = function() end },
  findObjectById = function(id)
    local o = CREATED[id]
    return { delete = function() DELETED = DELETED + 1 end, setPosRot = function(_, ...) if o then o:setPosRot(...) end end }
  end,
}
function createObject(cls)
  local o = { cls = cls, fields = {} }
  function o:setField(k, i, v) self.fields[k] = v end
  function o:setPosRot(x, y, z, qx, qy, qz, qw) self.pos = { x, y, z }; self.q = { qx, qy, qz, qw } end
  function o:registerObject(n) end
  function o:getID() return self.id end
  CREATED[#CREATED + 1] = o
  o.id = #CREATED
  return o
end
MISSION = { missionTypeData = { dakarEdition = 'testbed-algarve' }, startTrigger = {}, fgVariables = {}, defaultUserSettings = { setupModuleVehicles = 1 } }
MISSION_POPOLI = { missionTypeData = { dakarEdition = 'svolte-di-popoli' }, startTrigger = {}, fgVariables = {}, defaultUserSettings = { setupModuleVehicles = 1 } }
STARTED, STARTED_ARGS, FOREGROUND, STOPPED = false, nil, nil, false
-- the loops of the installed editions, by id (the prefix of each edition's missions)
-- (a leg's own mission, rallyStage / rallyRoadSection, is a table of its own: LEG_MISSIONS[id])
LEG_MISSIONS = {}
-- as the game's missions.lua: the list of missions is read once (LISTED: false, an add-on mounted after it was read, until
-- clearCache), and getLocations copies a mission's start the first time it is asked
LISTED, MOUNTED, LIST_CLEARS, POI_CLEARS, LOCATIONS = true, true, 0, 0, {}
gameplay_missions_missions = { getMissionById = function(id)
  if not LISTED then return nil end
  if id:find('/rallyStage/') or id:find('/rallyRoadSection/') then
    LEG_MISSIONS[id] = LEG_MISSIONS[id] or { id = id, startTrigger = { type = 'coordinates', pos = { 0, 0, 0 } } }
    return LEG_MISSIONS[id]
  end
  local m = id:find('dakar%%-popoli') and MISSION_POPOLI or id:find('dakar%%-testbed') and MISSION or nil
  if m then m.id = id end
  return m
end,
getLocations = function(m)
  if not LOCATIONS[m.id] then
    local p = m.startTrigger.pos or { 0, 0, 0 }
    LOCATIONS[m.id] = { { type = 'coordinates', pos = vec3(p[1], p[2], p[3]) } }
  end
  return LOCATIONS[m.id]
end,
-- the missions built again from their info.json: the legs' at 0, 0, 0, the loops' without what was set on them
clearCache = function()
  LIST_CLEARS = LIST_CLEARS + 1
  LISTED = MOUNTED
  LEG_MISSIONS, LOCATIONS = {}, {}
  for _, m in ipairs({ MISSION, MISSION_POPOLI }) do m.startTrigger, m.dakarFrame = {}, nil end
end }
gameplay_rawPois = { clear = function() POI_CLEARS = POI_CLEARS + 1 end }
-- the game's file system: the folders of the editions folder (and, as the real one may, the files)
FS = { directoryList = function(self, path) return { '/dakar/editions/svolte-di-popoli', '/dakar/editions/testbed-algarve' } end }
STARTS = 0
function realStart(m, settings, options) HELD_AT_START = LOADING.beamngDakar; STARTED = true; STARTS = STARTS + 1; STARTED_ARGS = { m, settings, options } end
gameplay_missions_missionManager = {
  startWithFade = realStart, start = realStart, startWithLoadingLevel = realLevelStart,
  getForegroundMissionId = function() return FOREGROUND end,
  stopForegroundMissionInstantly = function() STOPPED = true; FOREGROUND = nil end,
}
RACEPATH = nil
gameplay_rally = { getRallyManager = function() return RACEPATH and { getRacePath = function() return RACEPATH end } or nil end }

-- the extension, registered under its global name the way the game does
core = dofile('%(dakar)s/beamngDakar/core.lua')
beamngDakar_core = core

function earthAnchorCommit(lat, lon) package.loaded['beamngEarth/mpanchor'].announceOwn(lat, lon) end

-- Earth's rebase from anchor (la0, lo0) to (la1, lo1): phase 1 (moveVehicles with the frame change), then the commit
function earthRebase(la0, lo0, la1, lo1)
  local old, new = geodesy.newAnchor(la0, lo0), geodesy.newAnchor(la1, lo1)
  local xf = old:worldTransformTo(new, 0, 0, 0)
  package.loaded['beamngEarth/rebase'].moveVehicles(xf, geodesy)
  earthAnchorCommit(la1, lo1)
end

-- the active rally manager holds the stage's race path as its file had it
function loadRacePathFromFiles()
  local f = FILES['/gameplay/missions/beamng_earth/rallyStage/dakar-testbed-ss1/race.race.json']
  local rp = { startPositions = { sorted = {} }, pathnodes = { sorted = {} } }
  for _, s in ipairs(f.startPositions) do rp.startPositions.sorted[#rp.startPositions.sorted + 1] = { name = s.name, pos = vec(s.pos) } end
  for _, n in ipairs(f.pathnodes) do rp.pathnodes.sorted[#rp.pathnodes.sorted + 1] = { pos = vec(n.pos) } end
  RACEPATH = rp
end
"""


def conv(rt, o):
    if isinstance(o, dict):
        t = rt.table()
        for k, v in o.items():
            t[k] = conv(rt, v)
        return t
    if isinstance(o, list):
        t = rt.table()
        for i, v in enumerate(o, 1):
            t[i] = conv(rt, v)
        return t
    return o


@pytest.fixture()
def lua():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute(STUBS % {"earth": EARTH_EXT, "dakar": DAKAR_EXT})
    put = rt.eval("function(k, v) JSON[k] = v end")
    # (the second edition's files too: an installed edition is read by the mission list's start, the window and the triggers)
    pedition = json.load(open(os.path.join(POPOLI, "legs.json"), encoding="utf-8"))
    pbase = "/dakar/editions/svolte-di-popoli/"
    put(pbase + "legs.json", conv(rt, pedition))
    for leg in pedition["legs"]:
        put(pbase + "geometry/%s.json" % leg["id"], conv(rt, json.load(open(os.path.join(POPOLI, "geometry", leg["id"] + ".json")))))
        closures = os.path.join(POPOLI, "closures", leg["id"] + ".json")
        if os.path.isfile(closures):
            put(pbase + "closures/%s.json" % leg["id"], conv(rt, json.load(open(closures))))
    edition = json.load(open(os.path.join(EDITION, "legs.json"), encoding="utf-8"))
    base = "/dakar/editions/testbed-algarve/"
    put(base + "legs.json", conv(rt, edition))
    for leg in edition["legs"]:
        g = json.load(open(os.path.join(EDITION, "geometry", leg["id"] + ".json")))
        put(base + "geometry/%s.json" % leg["id"], conv(rt, g))
        closures = os.path.join(EDITION, "closures", leg["id"] + ".json")      # (a special stage's closed side roads)
        if os.path.isfile(closures):
            put(base + "closures/%s.json" % leg["id"], conv(rt, json.load(open(closures))))
        if leg is edition["legs"][0]:           # the player stands at the service park: the first leg's first point
            rt.execute("PLAYER_LL = { %r, %r }; SERVICE_LL = PLAYER_LL" % (g["points"][0][0], g["points"][0][1]))
            rt.execute("local w = require('beamngDakar/legs').toWorld({ { %r, %r, %r } }, geodesy.newAnchor(ANCHOR_LL[1], ANCHOR_LL[2]));"
                       " PLAYER_XY = { w[1][1], w[1][2] }" % tuple(g["points"][0]))
    return rt


def logs(lua, needle):
    log = lua.eval("LOG")
    return [log[i] for i in range(1, len(log) + 1) if needle in log[i]]


def residual(line):
    return float(line.rsplit("residual ", 1)[1].split(" m")[0])


def launched(lua):
    lua.execute("core.start('testbed-algarve')")
    lua.execute("earthAnchorCommit(37.0900, -7.9800)")       # Earth commits the teleport's anchor
    lua.execute("core.onUpdate(5)")                          # the settle delay passes: the loop starts
    lua.execute("loadRacePathFromFiles()")


def test_start_teleports_then_launches_the_loop_in_the_anchor_frame(lua):
    lua.execute("core.start('testbed-algarve')")
    assert lua.eval("TELEPORT[1]") == pytest.approx(37.0870, abs=1e-3)
    assert lua.eval("STARTED") is False                      # not before Earth has committed the new anchor
    lua.execute("earthAnchorCommit(37.0900, -7.9800)")
    lua.execute("core.onUpdate(5)")
    assert lua.eval("STARTED") is True
    assert lua.eval("MISSION.id") == "beamng_earth/rallyLoop/dakar-testbed"
    keys = sorted(str(k) for k in lua.eval("FILES").keys())
    assert "/gameplay/missions/beamng_earth/rallyRoadSection/dakar-testbed-road1/race.race.json" in keys
    assert "/gameplay/missions/beamng_earth/rallyStage/dakar-testbed-ss1/rally/drivelineSpline.json" in keys
    # the service stall sits at the first leg's start, in that frame: near the anchor
    assert abs(lua.eval("MISSION.missionTypeData.serviceStallPos[1]")) < 1500
    assert abs(lua.eval("MISSION.missionTypeData.serviceStallPos[2]")) < 1500
    assert lua.eval("MISSION.startTrigger.pos[1]") == lua.eval("MISSION.missionTypeData.serviceStallPos[1]")


def test_the_taps_call_the_originals_and_return_their_values(lua):
    lua.execute("core.start('testbed-algarve')")
    lua.execute("local m, id = package.loaded['beamngEarth/rebase'].moveVehicles({}, geodesy); assert(m.moved and id == 7)")
    calls = lua.eval("CALLS")
    assert "moveVehicles" in [calls[i] for i in range(1, len(calls) + 1)]


def test_an_error_in_our_probe_never_reaches_earth(lua):
    lua.execute("core.start('testbed-algarve')")
    lua.execute("core.onRebaseBegin = function() error('boom') end")
    lua.execute("local m, id = package.loaded['beamngEarth/rebase'].moveVehicles({}, geodesy); assert(id == 7)")
    assert logs(lua, "onRebaseBegin failed")


def test_a_rebase_leaves_the_stock_rally_data_in_the_old_frame(lua):
    launched(lua)
    lua.execute("earthRebase(37.0900, -7.9800, 37.1500, -7.8900)")
    lines = logs(lua, "DAKAR-PROBE stock")
    start = [residual(x) for x in lines if "start:SS_start_line" in x]
    assert start and start[0] > 1000, lines        # kilometres: the data did not follow the world


def test_with_adapt_the_race_path_follows_earths_transform(lua):
    launched(lua)
    lua.execute("core.setAdapt(true)")
    lua.execute("earthRebase(37.0900, -7.9800, 37.1500, -7.8900)")
    adapted = logs(lua, "DAKAR-PROBE adapted")
    assert adapted
    # every start position and the first and last path node land on a lat/lon truth point of the new frame, to within
    # what one transform made 10-20 km from the points leaves (tests/test_legs.py: bounded, not growing per rebase);
    # a path node is 6 m wide
    assert max(residual(x) for x in adapted) < 1.0, adapted


def test_a_mission_list_start_at_the_service_park_runs_at_once_with_the_players_settings_and_no_teleport(lua):
    lua.execute("core.onExtensionLoaded()")                      # the taps go in (also the mission manager's)
    # the list: missionScreen -> missionManager.startWithFade(mission, flat settings, starting options)
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, { setupModuleVehicles = 3, voice = 'x' }, { startedFrom = 'missionScreen' })")
    assert lua.eval("STARTED") is True
    assert lua.eval("TELEPORT") is None                          # the player was not moved
    assert lua.eval("STARTED_ARGS[2].setupModuleVehicles") == 3   # the car the player picked, not the default
    assert lua.eval("STARTED_ARGS[3].startedFrom") == "missionScreen"
    keys = sorted(str(k) for k in lua.eval("FILES").keys())
    assert "/gameplay/missions/beamng_earth/rallyStage/dakar-testbed-ss1/race.race.json" in keys
    assert lua.eval("core.status()") is not None
    assert logs(lua, "writing the route for the current anchor")


def test_a_mission_list_start_far_from_the_service_park_says_where_to_go_and_starts_nothing(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("PLAYER_LL = { 38.7, -9.15 }")                   # Lisbon: about 220 km north-west of Faro
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, { a = 1 }, {})")
    assert lua.eval("STARTED") is False and lua.eval("TELEPORT") is None
    assert lua.eval("next(FILES)") is None                        # no route written either
    msg = str(lua.eval("MESSAGES[1]"))
    assert "service park" in msg and "south-east" in msg and "km" in msg, msg


def test_a_start_waits_for_the_roads_around_the_player_with_the_game_paused(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("ROADS_READY = false")
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, { setupModuleVehicles = 3 }, {})")
    assert lua.eval("STARTED") is False and lua.eval("PAUSED") is True        # held, the game paused
    assert lua.eval("ROADS_ASKED[1]") == 7 and lua.eval("ROADS_ASKED[4]") == 60       # this anchor, 60 m round each sample
    assert lua.eval("next(FILES)") is None                                    # (nothing written yet)
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, {}, {})")     # an impatient second click
    lua.execute("core.onUpdate(5)")
    assert lua.eval("STARTED") is False and lua.eval("PAUSED") is True        # roads still missing: still waiting
    lua.execute("ROADS_READY = true")
    lua.execute("core.onUpdate(1)")
    assert lua.eval("STARTED") is True and lua.eval("PAUSED") is False        # in: released, then started
    assert lua.eval("STARTED_ARGS[2].setupModuleVehicles") == 3               # the click's own settings
    assert logs(lua, "roads wait over") and lua.eval("TELEPORT") is None


def test_the_roads_wait_ends_after_a_minute(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("ROADS_READY = false")
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, {}, {})")
    lua.execute("core.onUpdate(50)")
    assert lua.eval("STARTED") is False
    lua.execute("core.onUpdate(15)")
    assert lua.eval("STARTED") is True and lua.eval("PAUSED") is False


def test_the_player_resuming_the_game_cancels_the_roads_wait(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("ROADS_READY = false")
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, {}, {})")
    lua.execute("PAUSED = false")                                             # the pause key
    lua.execute("core.onUpdate(1)")
    assert lua.eval("STARTED") is False and lua.eval("#PAUSES") == 1          # their call: we do not unpause again, nor start
    lua.execute("ROADS_READY = true")
    lua.execute("core.onUpdate(1)")
    assert lua.eval("STARTED") is False


def test_the_window_can_skip_the_roads_wait(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("ROADS_READY = false")
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, {}, {})")
    lua.execute("core.endRoadsWait('skipped from the window')")
    assert lua.eval("STARTED") is True and lua.eval("PAUSED") is False


def test_a_teleport_to_the_service_park_waits_for_the_roads_too_and_does_not_start(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("ROADS_READY = false")
    lua.execute("core.start('testbed-algarve', true)")
    lua.execute("earthAnchorCommit(37.0900, -7.9800)")
    lua.execute("core.onUpdate(5)")                                           # the settle delay passes
    assert lua.eval("PAUSED") is True and lua.eval("STARTED") is False
    lua.execute("ROADS_READY = true")
    lua.execute("core.onUpdate(1)")
    assert lua.eval("PAUSED") is False and lua.eval("STARTED") is False       # arrived, nothing started
    assert logs(lua, "roads wait over")


def test_a_teleport_and_start_waits_for_the_roads_then_launches(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("ROADS_READY = false")
    lua.execute("core.start('testbed-algarve')")
    lua.execute("earthAnchorCommit(37.0900, -7.9800)")
    lua.execute("core.onUpdate(5)")
    assert lua.eval("STARTED") is False and lua.eval("PAUSED") is True
    lua.execute("ROADS_READY = true")
    lua.execute("core.onUpdate(1)")
    assert lua.eval("STARTED") is True and lua.eval("PAUSED") is False


def test_the_second_entry_point_is_held_too_and_other_missions_pass_through(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("PLAYER_LL = { 38.7, -9.15 }")
    lua.execute("gameplay_missions_missionManager.start(MISSION, {}, {})")
    assert lua.eval("STARTED") is False
    lua.execute("""OTHER = { id = 'west_coast_usa/timeTrial/x', missionTypeData = {} }
                   gameplay_missions_missionManager.startWithFade(OTHER, { a = 1 }, {})""")
    assert lua.eval("STARTED") is True and lua.eval("STARTED_ARGS[1].id") == "west_coast_usa/timeTrial/x"


def test_the_windows_teleport_button_only_moves_the_player(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("core.start('testbed-algarve', true)")
    assert lua.eval("TELEPORT[1]") == pytest.approx(37.0870, abs=1e-3)
    lua.execute("earthAnchorCommit(37.0900, -7.9800)")
    lua.execute("core.onUpdate(5)")
    assert lua.eval("STARTED") is False and lua.eval("core.status()") is not None
    assert logs(lua, "waiting for the roads")


def test_the_start_trigger_follows_the_anchor(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("core.onUpdate(3)")                              # Earth's first anchor was set before we loaded: adopted
    first = [lua.eval("MISSION.startTrigger.pos[%d]" % i) for i in (1, 2, 3)]
    lua.execute("earthAnchorCommit(37.20, -8.20)")               # a rebase / teleport far away: the same place, new numbers
    second = [lua.eval("MISSION.startTrigger.pos[%d]" % i) for i in (1, 2, 3)]
    assert abs(first[0] - second[0]) > 1000 or abs(first[1] - second[1]) > 1000
    lua.execute("earthAnchorCommit(37.0900, -7.9800)")
    back = [lua.eval("MISSION.startTrigger.pos[%d]" % i) for i in (1, 2, 3)]
    assert back == pytest.approx(first, abs=0.01)


def test_a_running_mission_is_stopped_before_ours_starts(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("FOREGROUND = 'beamng_earth/rallyLoop/dakar-testbed'")    # the earlier broken start, still ongoing
    lua.execute("core.start('testbed-algarve')")
    lua.execute("earthAnchorCommit(37.0900, -7.9800)")
    lua.execute("core.onUpdate(5)")
    assert lua.eval("STOPPED") is True and lua.eval("STARTED") is True


def test_launch_without_a_menu_start_uses_the_missions_default_settings(lua):
    launched(lua)
    assert lua.eval("STARTED_ARGS[2].setupModuleVehicles") == 1


def shapes(lua):
    created = lua.eval("CREATED")
    return [str(created[i].fields["shapeName"]) for i in range(1, len(created) + 1)]


def test_props_stand_near_the_player_and_follow_him(lua):
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("CAR = { 0, 0, 0 }")                                  # at the service park, the first leg's start
    lua.execute("core.onUpdate(1)")
    near = shapes(lua)
    assert any("fold_table" in s for s in near) and any("cone.dae" in s for s in near)
    assert not any("signboard_start" in s for s in near)             # the stage start is 19 km away
    n_before = len(near)
    # drive to the stage start: 19 km on, new props there; the first ones are behind and go
    lua.execute("CAR = { 8260, 6996, 50 }")
    lua.execute("core.onUpdate(1)")
    now = shapes(lua)
    assert any("signboard_start" in s for s in now[n_before:])
    assert lua.eval("DELETED") >= n_before


def test_a_rebase_rebuilds_the_props_in_the_new_frame(lua):
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("core.onUpdate(1)")
    before = len(shapes(lua))
    first = lua.eval("CREATED[1].pos[1]")
    lua.execute("earthRebase(37.0900, -7.9800, 37.1000, -7.9700)")  # the origin moves: every position is a new number
    lua.execute("core.onUpdate(1)")
    assert len(shapes(lua)) > before and lua.eval("DELETED") >= before
    assert lua.eval("CREATED[%d].pos[1]" % (before + 1)) != first


def test_props_go_when_the_mission_stops(lua):
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("core.onUpdate(1)")
    made = len(shapes(lua))
    assert made > 0
    lua.execute("core.onAnyMissionChanged('stopped', MISSION)")
    assert lua.eval("DELETED") == made
    lua.execute("core.onUpdate(5)")
    assert len(shapes(lua)) == made                                   # and none are made again


def test_the_route_and_the_car_are_put_on_the_games_ground(lua):
    launched(lua)
    flat = [lua.eval("FILES['/gameplay/missions/beamng_earth/rallyRoadSection/dakar-testbed-road1/race.race.json'].startPositions[1].pos[3]") + 0.5,
            lua.eval("FILES['/gameplay/missions/beamng_earth/rallyStage/dakar-testbed-ss1/race.race.json'].pathnodes[3].pos[3]")]
    lua2 = lua.__class__()                     # (a second world: the same start, but the game's ground is 3.3 m lower)
    lua2.execute(STUBS % {"earth": EARTH_EXT, "dakar": DAKAR_EXT})
    edition = json.load(open(os.path.join(EDITION, "legs.json"), encoding="utf-8"))
    put = lua2.eval("function(k, v) JSON[k] = v end")
    base = "/dakar/editions/testbed-algarve/"
    put(base + "legs.json", conv(lua2, edition))
    for leg in edition["legs"]:
        g = json.load(open(os.path.join(EDITION, "geometry", leg["id"] + ".json")))
        put(base + "geometry/%s.json" % leg["id"], conv(lua2, g))
    lua2.execute("PLAYER_LL = { %r, %r }" % tuple(json.load(open(os.path.join(EDITION, "geometry", "road1.json")))["points"][0][:2]))
    # flat ground 3.3 m under the terrain data at TC0, wherever it is asked (the car's spawn in the lot, a few metres from TC0,
    # stands on that ground: half a metre above it; the markers are put on it too, and may ask first)
    lua2.execute("GROUND = %r; core_terrain = { getTerrainHeight = function(p) return GROUND end }" % (flat[0] - 0.5 - 3.3))
    lua2.execute("core.start('testbed-algarve')")
    lua2.execute("earthAnchorCommit(37.0900, -7.9800)")
    lua2.execute("core.onUpdate(5)")
    low = [lua2.eval("MISSION.startTrigger.pos[3]"),
           lua2.eval("FILES['/gameplay/missions/beamng_earth/rallyStage/dakar-testbed-ss1/race.race.json'].pathnodes[3].pos[3]")]
    assert low[0] == pytest.approx(flat[0] - 3.3, abs=1e-6)             # the car's place, 3.3 m under TC0's height + 0.5
    assert low[1] == pytest.approx(flat[1] - 3.3, abs=1e-6)             # and the whole route with it
    assert logs(lua2, "the route moves -3.3 m")


def test_a_terrain_answer_far_from_the_data_is_not_believed(lua):
    lua.execute("core_terrain = { getTerrainHeight = function(p) return p.z + 100 end }")     # (not the ground: 100 m off the data)
    launched(lua)
    assert logs(lua, "no ground found under the start point")


def test_a_start_outside_ours_is_stopped_and_done_again_properly(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("FOREGROUND = 'beamng_earth/rallyLoop/dakar-testbed'")
    lua.execute("core.onAnyMissionChanged('started', MISSION)")        # the game's own start: no hold, stale files
    assert lua.eval("STARTS") == 0
    lua.execute("core.onUpdate(1)")                                    # (next frame)
    assert lua.eval("STOPPED") is True and lua.eval("STARTS") == 1     # stopped, and started through ours
    assert logs(lua, "started outside the Dakar start") and logs(lua, "restarting the loop through the Dakar start")
    lua.execute("core.onAnyMissionChanged('started', MISSION)")        # ours coming up: left alone
    lua.execute("core.onUpdate(1)")
    assert lua.eval("STARTS") == 1


def test_the_loading_screen_comes_down_and_the_restart_waits_for_the_player_position(lua):
    # A start from the main menu: the stopped mission's flowgraph never took the loading screen down, and
    # Earth did not know the player yet.
    lua.execute("core.onExtensionLoaded()")
    lua.execute("FADEOUTS = 0; server = { fadeoutLoadingScreen = function(skip) FADEOUTS = FADEOUTS + 1; SKIPPED = skip end }")
    lua.execute("FOREGROUND = 'beamng_earth/rallyLoop/dakar-testbed'")
    lua.execute("SAVED_LL = PLAYER_LL; PLAYER_LL = {}")                 # (Earth has no position yet)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("core.onUpdate(1)")
    assert lua.eval("FADEOUTS") == 1 and lua.eval("SKIPPED") is True and lua.eval("STARTS") == 0
    for _ in range(3):
        lua.execute("core.onUpdate(1)")                                  # (asking again, not giving up)
    assert lua.eval("FADEOUTS") == 1 and lua.eval("STARTS") == 0
    assert not logs(lua, "restarting the loop through the Dakar start")
    lua.execute("PLAYER_LL = SAVED_LL")
    lua.execute("core.onUpdate(1)")
    assert logs(lua, "restarting the loop through the Dakar start") and lua.eval("STARTS") == 1
    # (the hold was let go with the stop: Earth puts the player on the ground again, where it is)
    assert logs(lua, "put on the ground again") and lua.eval("TELEPORT[1]") == lua.eval("PLAYER_LL[1]")


def test_a_main_menu_start_loads_the_level_then_teleports_to_the_service_park_and_starts_there(lua):
    # the menu's mission list (startWithLoadingLevel) ran the loop at Earth's random place: no car, no ground
    lua.execute("core.onExtensionLoaded()")
    lua.execute("LEVEL = nil; EARTH_IN_LEVEL = false; ANCHOR_LL_SAVED = ANCHOR_LL; ANCHOR_LL = nil")      # the main menu
    lua.execute("PLAYER_LL = { 48.25, -79.06 }")                       # where the random place will be
    lua.execute("gameplay_missions_missionManager.startWithLoadingLevel(MISSION, nil, nil, 'gameplaySelector')")
    assert lua.eval("LEVEL_STARTS") == 0                               # the stock chain did not run
    assert lua.eval("FREEROAM[1]") == "beamng_earth" and lua.eval("FREEROAM[2]") == "bne_random"    # the pick kept
    assert lua.eval("LOADING.beamngDakar") is True                     # behind one loading screen
    lua.execute("core.onClientEndMission()")                           # (the menu's level going: not ours, kept)
    assert lua.eval("LOADING.beamngDakar") is True
    lua.execute("core.onUpdate(1)")
    assert lua.eval("TELEPORT") is None and lua.eval("STARTS") == 0    # the level is not in yet
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, {}, {})")      # a click meanwhile: told
    assert lua.eval("STARTS") == 0 and "being prepared" in str(lua.eval("MESSAGES[#MESSAGES]"))
    lua.execute("LEVEL = 'beamng_earth'; EARTH_IN_LEVEL = true; ANCHOR_LL = { 48.25, -79.06 }; EARTH_PENDING = { requestId = 3 }")
    lua.execute("core.onUpdate(1)")
    assert lua.eval("TELEPORT") is None                                # Earth's own start teleport still under way
    lua.execute("EARTH_PENDING = nil")
    lua.execute("core.onUpdate(1)")
    assert lua.eval("TELEPORT[1]") == pytest.approx(37.0870, abs=1e-3)  # to the service park
    assert lua.eval("STARTS") == 0 and logs(lua, "the Earth level is in")
    assert lua.eval("LOADING.beamngDakar") is True                     # still behind it while the teleport runs
    lua.execute("ANCHOR_LL = ANCHOR_LL_SAVED; PLAYER_LL = SERVICE_LL; earthAnchorCommit(37.0900, -7.9800)")
    lua.execute("core.onUpdate(5)")                                    # the settle delay: the loop starts there
    assert lua.eval("HELD_AT_START") is False                          # let go for the loop's own fade
    assert lua.eval("STARTS") == 1 and lua.eval("STARTED_ARGS[3].startedFrom") == "gameplaySelector"
    assert lua.eval("STARTED_ARGS[2].setupModuleVehicles") == 1        # the mission's default settings (the rally car)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("core.onUpdate(1)")
    assert not logs(lua, "started outside") and lua.eval("STOPPED") is False


def test_a_menu_start_after_a_loop_was_quit_is_not_held_up(lua):
    # the testbed's loop ran and was quit to the main menu; the Svolte di Popoli started from the menu then waited for us to
    # be idle until it gave up ("the Earth level was not in after 180 s"), though the level and Earth's anchor were in
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("core.onAnyMissionChanged('stopped', MISSION)")         # quit to the menu
    assert logs(lua, "the loop stopped: idle again")
    lua.execute("LEVEL = nil; EARTH_IN_LEVEL = false; ANCHOR_LL = nil; TELEPORT = nil")
    lua.execute("core.onClientEndMission()")
    lua.execute("gameplay_missions_missionManager.startWithLoadingLevel(MISSION_POPOLI, nil, nil, 'gameplaySelector')")
    lua.execute("LEVEL = 'beamng_earth'; EARTH_IN_LEVEL = true; ANCHOR_LL = { 37.09, -7.99 }; PLAYER_LL = { 37.0884, -7.9865 }")
    lua.execute("core.onUpdate(1)")
    assert logs(lua, "the Earth level is in")
    assert lua.eval("TELEPORT[1]") == pytest.approx(42.1749, abs=1e-3)  # on its way to the Popoli service park


def test_a_finished_loop_lets_the_markers_follow_the_anchor_again(lua):
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("core.onAnyMissionChanged('stopped', MISSION)")
    before = lua.eval("LEG_MISSIONS['beamng_earth/rallyStage/dakar-popoli-ss1'].startTrigger.pos[1]")
    lua.execute("earthAnchorCommit(42.1700, 13.8300)")
    after = lua.eval("LEG_MISSIONS['beamng_earth/rallyStage/dakar-popoli-ss1'].startTrigger.pos[1]")
    assert abs(after) < 2000 and after != before


def test_a_held_loading_screen_never_outlives_the_level_or_a_failed_start(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("LEVEL = nil; EARTH_IN_LEVEL = false")
    lua.execute("gameplay_missions_missionManager.startWithLoadingLevel(MISSION, nil, nil, 'gameplaySelector')")
    lua.execute("EARTH_IN_LEVEL = true; EARTH_PENDING = { requestId = 3 }; core.onUpdate(1)")    # ours seen
    lua.execute("core.onClientEndMission()")                           # the player quits it
    assert lua.eval("LOADING.beamngDakar") is False and logs(lua, "let go: the level ended")
    # the Earth level never comes up (no server): given up, let go, said
    lua.execute("EARTH_IN_LEVEL = false; EARTH_PENDING = nil; LEVEL = nil")
    lua.execute("gameplay_missions_missionManager.startWithLoadingLevel(MISSION, nil, nil, 'gameplaySelector')")
    assert lua.eval("LOADING.beamngDakar") is True
    lua.execute("core.onUpdate(200)")
    assert lua.eval("LOADING.beamngDakar") is False and "did not come up" in str(lua.eval("MESSAGES[#MESSAGES]"))


def test_a_main_menu_start_of_another_mission_is_left_alone(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("gameplay_missions_missionManager.startWithLoadingLevel({ missionTypeData = {} }, nil, nil, 'x')")
    assert lua.eval("LEVEL_STARTS") == 1 and lua.eval("FREEROAM") is None


def test_our_own_start_is_never_restarted(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, {}, {})")
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("core.onUpdate(2)")
    assert lua.eval("STARTS") == 1 and not logs(lua, "started outside")


def test_the_landmarks_stand_from_far_away_but_the_small_props_do_not(lua):
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("CAR = { 4000, 3000, 0 }")                              # 5 km from the service park
    lua.execute("core.onUpdate(1)")
    far = shapes(lua)
    assert any("s_tower_01" in x for x in far) and any("feather" in x for x in far)
    assert not any("cone" in x or "fold_table" in x for x in far)


def collisions(lua, needle):
    created = lua.eval("CREATED")
    return [str(created[i].fields["collisionType"]) for i in range(1, len(created) + 1)
            if needle in str(created[i].fields["shapeName"])]


@pytest.mark.skipif(not os.path.isfile(os.path.join(EDITION, "closures", "ss1.json")), reason="run tools/stage_closures.py")
def test_the_closures_of_the_side_roads_stand_near_the_player_on_the_stage_as_tape_the_car_goes_through(lua):
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("CAR = { 0, 0, 0 }")                                   # the service park: the stage is 19 km away
    lua.execute("core.onUpdate(1)")
    assert not any("tape_road_closure" in s for s in shapes(lua))
    lua.execute("CAR = { 8260, 6996, 50 }")                            # the stage start
    lua.execute("core.onUpdate(1)")
    now = shapes(lua)
    tapes = [s for s in now if "tape_road_closure_6m" in s]
    assert len(tapes) >= 3                                                  # the stage start is in a village: side roads
    assert set(collisions(lua, "tape_road_closure")) == {"None"}           # no collision, as the stock stages have it
    assert set(collisions(lua, "cone.dae")) == {"None"}


@pytest.mark.skipif(not os.path.isfile(os.path.join(EDITION, "closures", "ss1.json")), reason="run tools/stage_closures.py")
def test_a_rebase_stands_the_closures_again_in_the_new_frame_and_stopping_the_loop_clears_them(lua):
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("CAR = { 8260, 6996, 50 }")
    lua.execute("core.onUpdate(1)")
    n = len([s for s in shapes(lua) if "tape_road_closure" in s])
    assert n > 0
    lua.execute("earthRebase(37.0900, -7.9800, 37.1000, -7.9700)")
    lua.execute("core.onUpdate(1)")
    after = [s for s in shapes(lua) if "tape_road_closure" in s]
    assert len(after) > n                                              # (new objects made in the new frame)
    lua.execute("local d = DELETED; core.onAnyMissionChanged('stopped', MISSION); CLEARED = DELETED - d")
    assert lua.eval("CLEARED") > 0


def test_an_edition_without_closure_files_stands_no_closures(lua):
    lua.execute("for k in pairs(JSON) do if k:find('closures') then JSON[k] = nil end end")
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("CAR = { 8260, 6996, 50 }")
    lua.execute("core.onUpdate(1)")
    assert any("signboard_start" in s for s in shapes(lua))
    assert not any("tape_road_closure" in s for s in shapes(lua))


def asked(lua):
    """(x, y) of every road check since the last reset, and the player's place."""
    calls = lua.eval("ROADS_CALLS")
    pts = [(calls[i][2], calls[i][3]) for i in range(1, len(calls) + 1)]
    return pts, (lua.eval("PLAYER_XY[1]"), lua.eval("PLAYER_XY[2]"))


def test_the_roads_wait_covers_the_route_ahead_and_nothing_else(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("ROADS_READY = true")
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, {}, {})")
    pts, me = asked(lua)
    assert len(pts) >= 4                                             # the player's own place and samples ahead
    far = max(((x - me[0]) ** 2 + (y - me[1]) ** 2) ** 0.5 for x, y in pts)
    assert 100 < far < 700             # 600 m of route (it winds out of the lot; the next timed stop is 20 km on), not more
    assert all(c[4] == 60 for c in [lua.eval("ROADS_CALLS[%d]" % i) for i in range(1, len(pts) + 1)])


def test_a_tile_off_the_route_does_not_hold_the_player_but_one_on_it_does(lua):
    lua.execute("core.onExtensionLoaded()")
    # the roads are in everywhere except 2 km east of the player, which is off the route's first 600 m
    lua.execute("ROADS_AT = function(x, y) return not (x > PLAYER_XY[1] + 1500) end")
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, {}, {})")
    assert lua.eval("STARTED") is True and lua.eval("PAUSED") is False
    lua.execute("core.stopForeground(); STARTED = false; STARTS = 0")
    # now the ground 300 m along the route is missing: that does hold it
    lua.execute("ROADS_AT = function(x, y) return ((x - PLAYER_XY[1]) ^ 2 + (y - PLAYER_XY[2]) ^ 2) < 150 ^ 2 end")
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, {}, {})")
    assert lua.eval("STARTED") is False and lua.eval("PAUSED") is True


def test_the_corridor_stops_at_the_next_timed_stop_when_it_is_near(lua):
    legs = lua.eval("require('beamngDakar/legs')")
    w = lua.eval("{ { {0,0,0}, {100,0,0}, {200,0,0}, {300,0,0} }, { {300,0,0}, {1300,0,0} } }")
    pts = legs.corridor(w, 5, 0, 600, 120, 25, 400)
    xs = [pts[i][1] for i in range(1, len(pts) + 1)]
    assert xs[0] == 5 and xs[-1] == 300                              # the leg's end is the next stop: 295 m ahead
    assert all(b > a for a, b in zip(xs, xs[1:]))
    standing = legs.corridor(w, 300, 0, 600, 120, 25, 400)           # at that stop: the next one is 1 km on, capped at 600 m
    xs = [standing[i][1] for i in range(1, len(standing) + 1)]
    assert xs[0] == 300 and 880 <= xs[-1] <= 920
    assert legs.corridor(w, 0, 900, 600, 120, 25, 400) is None       # off the route: the caller uses a circle


def test_the_controls_near_the_player_go_on_the_games_ground_once(lua):
    # written at the terrain data's height; Earth has ground only near the player
    launched(lua)
    lua.execute("""
      local rp = RACEPATH
      TCOUT = vec({ 100, 50, 30 }); FAR = vec({ 5000, 0, 30 })
      rp.startPositions.sorted = { { name = 'TC_out', pos = TCOUT }, { name = 'SS_stop_control', pos = FAR } }
      rp.pathnodes.sorted = { { pos = vec({ 90, 50, 30 }) } }
      CAR = { 0, 0, 30 }
      core_terrain = { getTerrainHeight = function(p) return 28.5 end }
    """)
    lua.execute("core.onUpdate(1.5)")
    assert lua.eval("TCOUT.z") == 28.5 and lua.eval("FAR.z") == 30          # near: on the ground; far: left
    assert logs(lua, "TC_out put on the ground: 30.0 m -> 28.5 m") and logs(lua, "race node 1 put on the ground")
    lua.execute("core_terrain = { getTerrainHeight = function(p) return 27 end }; core.onUpdate(1.5)")
    assert lua.eval("TCOUT.z") == 28.5                                       # once per anchor


# ---------------------------------------------------------------------------------------------------------------------
# Two editions: nothing is hardwired to the testbed

def at_popoli(lua):
    """The player at the Svolte di Popoli service park (the first point of its first leg), Earth's anchor near it."""
    g = json.load(open(os.path.join(POPOLI, "geometry", "road1.json")))["points"][0]
    lua.execute("ANCHOR_LL = { 42.1700, 13.8300 }; PLAYER_LL = { %r, %r }" % (g[0], g[1]))
    lua.execute("local w = require('beamngDakar/legs').toWorld({ { %r, %r, %r } }, geodesy.newAnchor(ANCHOR_LL[1], ANCHOR_LL[2]));"
                " PLAYER_XY = { w[1][1], w[1][2] }; CAR = { w[1][1], w[1][2], w[1][3] }" % tuple(g))


def test_the_mission_list_starts_the_second_edition_with_its_own_files(lua):
    lua.execute("core.onExtensionLoaded()")
    at_popoli(lua)
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION_POPOLI, { setupModuleVehicles = 3 }, { startedFrom = 'missionScreen' })")
    assert lua.eval("STARTED") is True and lua.eval("TELEPORT") is None
    assert lua.eval("STARTED_ARGS[1] == MISSION_POPOLI")
    assert lua.eval("MISSION_POPOLI.id") == "beamng_earth/rallyLoop/dakar-popoli"
    keys = sorted(str(k) for k in lua.eval("FILES").keys())
    assert "/gameplay/missions/beamng_earth/rallyRoadSection/dakar-popoli-road1/race.race.json" in keys
    assert "/gameplay/missions/beamng_earth/rallyStage/dakar-popoli-ss1/rally/notebooks/structured.notebook.json" in keys
    assert not any("dakar-testbed" in k for k in keys)               # the other edition's missions are left alone
    assert lua.eval("MISSION_POPOLI.startTrigger.pos[1]") == lua.eval("MISSION_POPOLI.missionTypeData.serviceStallPos[1]")


def test_the_second_edition_far_from_its_service_park_says_where_it_is(lua):
    lua.execute("core.onExtensionLoaded()")                           # (the player stands at the testbed's park)
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION_POPOLI, {}, {})")
    assert lua.eval("STARTED") is False and lua.eval("next(FILES)") is None
    msg = lua.eval("MESSAGES[#MESSAGES]")
    assert "Svolte di Popoli" in msg and "42.1" in msg and "Faro" not in msg


def test_a_teleport_to_the_second_edition_goes_to_its_service_park(lua):
    lua.execute("core.start('svolte-di-popoli', true)")
    assert lua.eval("TELEPORT[1]") == pytest.approx(42.1749, abs=1e-3)
    assert lua.eval("TELEPORT[2]") == pytest.approx(13.8231, abs=1e-3)


def test_every_editions_loop_has_its_start_trigger_on_its_own_service_park(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("core.onUpdate(3)")
    lua.execute("earthAnchorCommit(37.0900, -7.9800)")
    xs = [lua.eval("%s.startTrigger.pos[1]" % m) for m in ("MISSION", "MISSION_POPOLI")]
    assert abs(xs[0]) < 2000 and abs(xs[1]) > 100000               # Popoli is 2000 km from Faro
    lua.execute("earthAnchorCommit(42.1700, 13.8300)")
    near = [lua.eval("MISSION_POPOLI.startTrigger.pos[%d]" % i) for i in (1, 2)]
    assert abs(near[0]) < 2000 and abs(near[1]) < 2000


def test_every_legs_mission_has_its_marker_on_the_time_control_that_opens_it(lua):
    # the liaisons' and stages' info.json say 0, 0, 0; each marker goes on its leg's first point (TC0, TC1, ...)
    lua.execute("core.onExtensionLoaded()")
    lua.execute("core.onUpdate(3)")
    lua.execute("earthAnchorCommit(42.1700, 13.8300)")
    tc = lambda m, i: lua.eval("LEG_MISSIONS['beamng_earth/%s'].startTrigger.pos[%d]" % (m, i))
    road1 = [tc("rallyRoadSection/dakar-popoli-road1", i) for i in (1, 2)]
    ss1 = [tc("rallyStage/dakar-popoli-ss1", i) for i in (1, 2)]
    loop = [lua.eval("MISSION_POPOLI.startTrigger.pos[%d]" % i) for i in (1, 2)]
    for p in (road1, ss1):
        assert abs(p[0]) < 2000 and abs(p[1]) < 2000 and (p[0], p[1]) != (0, 0)
    # the first liaison opens at TC0, a few metres from the loop's marker on the service park; SS1 at TC1, the end of the
    # 169 m transfer, about 100 m away
    assert math.dist(road1, loop) < 40
    assert 60 < math.dist(ss1, road1) < 200
    # the testbed's legs, 2000 km away, are moved too (every edition's)
    far = tc("rallyStage/dakar-testbed-ss1", 1)
    assert abs(far) > 100000
    # and they follow the anchor
    lua.execute("earthAnchorCommit(42.1750, 13.8240)")
    moved = [tc("rallyStage/dakar-popoli-ss1", i) for i in (1, 2)]
    assert math.dist(moved, ss1) > 100


GROUND_NEAR = (
    "LOADED = %s; HINTS = {}; core_terrain = { getTerrainHeight = function(p) "
    "HINTS[string.format('%%.1f,%%.1f', p.x, p.y)] = p.z; "
    "if LOADED and math.abs(p.x) < 5000 and math.abs(p.y) < 5000 then return p.z + 0.42 end end }")


def logged(lua, text):
    return any(text in line for line in lua.eval("LOG").values())


def test_a_marker_stands_on_the_games_ground_not_at_the_routes_height(lua):
    # the route data said 264.08 m at the Svolte di Popoli's TC0, the game's ground is 264.5: the marker was under it
    lua.execute(GROUND_NEAR % "true")
    lua.execute("core.onExtensionLoaded()")
    lua.execute("core.onUpdate(3)")
    lua.execute("earthAnchorCommit(42.1700, 13.8300)")
    for mid in ("rallyRoadSection/dakar-popoli-road1", "rallyStage/dakar-popoli-ss1"):
        x, y, z = (lua.eval("LEG_MISSIONS['beamng_earth/%s'].startTrigger.pos[%d]" % (mid, i)) for i in (1, 2, 3))
        data_z = lua.eval("HINTS['%.1f,%.1f']" % (x, y))
        assert z == pytest.approx(data_z + 0.42)
        # the drawn marker (the game's copy) is on the ground too
        assert lua.eval("LOCATIONS['beamng_earth/%s'][1].pos.z" % mid) == pytest.approx(z)


def test_a_marker_whose_ground_is_not_loaded_yet_goes_down_once_it_is(lua):
    lua.execute(GROUND_NEAR % "false")
    lua.execute("core.onExtensionLoaded()")
    lua.execute("core.onUpdate(3)")
    lua.execute("earthAnchorCommit(42.1700, 13.8300)")
    pos = lambda: lua.eval("LEG_MISSIONS['beamng_earth/rallyStage/dakar-popoli-ss1'].startTrigger.pos[3]")
    data_z = pos()
    clears = lua.eval("POI_CLEARS")
    lua.execute("LOADED = true; core.onUpdate(3)")
    assert pos() == pytest.approx(data_z + 0.42)
    assert logged(lua, "put on the ground") and lua.eval("POI_CLEARS") == clears + 1
    # the far edition's markers (2000 km away) stay waiting without gathering the POI list again every few seconds
    for _ in range(3):
        lua.execute("core.onUpdate(3)")
    assert lua.eval("POI_CLEARS") == clears + 1


def test_the_marker_the_game_draws_moves_with_the_start(lua):
    # the game had already copied the start (getLocations) when Dakar moved it; the marker, the big map and the
    # start check read that copy, and stayed at info.json's 0, 0, 0
    lua.execute("gameplay_missions_missions.getLocations(gameplay_missions_missions.getMissionById("
                "'beamng_earth/rallyStage/dakar-popoli-ss1'))")
    lua.execute("core.onExtensionLoaded()")
    lua.execute("core.onUpdate(3)")
    clears = lua.eval("POI_CLEARS")
    lua.execute("earthAnchorCommit(42.1700, 13.8300)")
    loc = lua.eval("LOCATIONS['beamng_earth/rallyStage/dakar-popoli-ss1'][1].pos")
    start = lua.eval("LEG_MISSIONS['beamng_earth/rallyStage/dakar-popoli-ss1'].startTrigger.pos")
    assert (loc.x, loc.y) == (start[1], start[2]) and (loc.x, loc.y) != (0, 0)
    assert lua.eval("POI_CLEARS") > clears                          # the POI list gathered again


def test_an_add_on_installed_while_the_game_runs_has_its_activities_without_a_reload(lua):
    # the game read its mission list before the add-on was mounted; Ctrl+L was needed to see them
    lua.execute("LISTED = false")
    lua.execute("core.onExtensionLoaded()")
    lua.execute("earthAnchorCommit(42.1700, 13.8300)")
    lua.execute("core.onUpdate(3)")
    assert lua.eval("LIST_CLEARS") == 1 and lua.eval("LISTED")
    ss1 = lua.eval("LEG_MISSIONS['beamng_earth/rallyStage/dakar-popoli-ss1'].startTrigger.pos")
    assert abs(ss1[1]) < 2000 and (ss1[1], ss1[2]) != (0, 0)
    # once only: an edition whose missions are not there does not clear the game's list every few seconds
    lua.execute("MOUNTED = false; gameplay_missions_missions.clearCache(); LIST_CLEARS = 0")
    for _ in range(3):
        lua.execute("core.onUpdate(3)")
    assert lua.eval("LIST_CLEARS") == 0


def test_the_markers_are_placed_again_when_the_game_reads_its_missions_again(lua):
    # a reload of the list (the mod manager ready, another mod) builds the missions again from info.json
    lua.execute("core.onExtensionLoaded()")
    lua.execute("core.onUpdate(3)")
    lua.execute("earthAnchorCommit(42.1700, 13.8300)")
    lua.execute("gameplay_missions_missions.clearCache()")
    assert lua.eval("MISSION_POPOLI.startTrigger.pos") is None
    lua.execute("core.onUpdate(3)")
    p = [lua.eval("MISSION_POPOLI.startTrigger.pos[%d]" % i) for i in (1, 2)]
    assert abs(p[0]) < 2000 and abs(p[1]) < 2000
    ss1 = lua.eval("LEG_MISSIONS['beamng_earth/rallyStage/dakar-popoli-ss1'].startTrigger.pos")
    assert (ss1[1], ss1[2]) != (0, 0)


def test_the_mission_list_is_not_cleared_under_a_running_mission(lua):
    lua.execute("LISTED = false; FOREGROUND = 'beamng_earth/somethingElse/x'")
    lua.execute("core.onExtensionLoaded()")
    lua.execute("core.onUpdate(3)")
    assert lua.eval("LIST_CLEARS") == 0


def test_the_props_are_those_of_the_edition_that_runs(lua):
    # the first loop ran and stopped, then the other starts: its props stand at its service park
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("core.onUpdate(1)")
    assert lua.eval("#CREATED") > 0
    lua.execute("core.onAnyMissionChanged('stopped', MISSION)")
    lua.execute("CREATED = {}")
    at_popoli(lua)
    lua.execute("core.onExtensionLoaded()")
    lua.execute("earthAnchorCommit(42.1700, 13.8300)")
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION_POPOLI, {}, {})")
    lua.execute("core.onAnyMissionChanged('started', MISSION_POPOLI)")
    lua.execute("core.onUpdate(1)")
    created = lua.eval("CREATED")
    props = [created[i] for i in range(1, len(created) + 1)]
    assert props and any("fold_table" in str(p.fields.shapeName) for p in props)
    px, py = lua.eval("PLAYER_XY[1]"), lua.eval("PLAYER_XY[2]")
    assert all(math.hypot(p.pos[1] - px, p.pos[2] - py) < 9500 for p in props)       # (9 km: the landmarks)
    tables = [p for p in props if "fold_table" in str(p.fields.shapeName)]           # (the staging area at TC1 has one too)
    assert min(math.hypot(t.pos[1] - px, t.pos[2] - py) for t in tables) < 100       # the service park table is at the park


def test_the_window_picks_the_edition_its_teleports_go_to(lua):
    # (the window's buttons were the default edition's only: M.start(DEFAULT_EDITION))
    lua.execute("core.onExtensionLoaded()")
    lua.execute("core.onUpdate(3)")
    lua.execute("""
      BUTTONS, CLICK = {}, nil
      ui_imgui = setmetatable({ Begin = function() return true end, End = function() end, Text = function() end,
        SetNextWindowSize = function() end, SetNextWindowPos = function() end, ImVec2 = function() end, Cond_FirstUseEver = 0,
        BoolPtr = function(v) return { [0] = v } end, Checkbox = function() return false end,
        Button = function(label) BUTTONS[#BUTTONS + 1] = label; return label == CLICK end }, {})
    """)
    lua.execute("core.onUpdate(0.1)")
    buttons = lua.eval("BUTTONS")
    labels = [buttons[i] for i in range(1, len(buttons) + 1)]
    assert "Svolte di Popoli" in labels and "> Testbed: Algarve" in labels        # (the testbed is the one picked)
    lua.execute("BUTTONS, CLICK = {}, 'Svolte di Popoli'")
    lua.execute("core.onUpdate(0.1)")                                             # the pick
    lua.execute("BUTTONS, CLICK = {}, 'Teleport to the service park'")
    lua.execute("core.onUpdate(0.1)")
    assert lua.eval("TELEPORT[1]") == pytest.approx(42.1749, abs=1e-3)


# ---------------------------------------------------------------------------------------------------------------------
# Props on the real ground

def staged(lua, suffix):
    """(shape name, z) of the created props whose shape ends with `suffix`."""
    created = lua.eval("CREATED")
    return [(str(created[i].fields["shapeName"]), created[i].pos[3]) for i in range(1, len(created) + 1)
            if str(created[i].fields["shapeName"]).endswith(suffix)]


def test_props_made_far_away_are_put_on_the_real_ground_once_the_player_is_near(lua):
    # Made 1.5 km away the ground there is not loaded: they stand at the terrain data's height (here 4 m above the
    # game's ground). Once the player is within 400 m the ground is known, and each prop is put on it once.
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("LOADED = false; DROP = 4; core_terrain = { getTerrainHeight = function(p) if LOADED then return p.z - DROP end end }")
    lua.execute("CAR = { 8260 + 1500, 6996, 50 }")                       # 1.5 km from TC1 (the stage start)
    lua.execute("core.onUpdate(1)")
    board = "s_rally_signboard_time_control.dae"
    first = staged(lua, board)
    assert len(first) == 1
    lua.execute("LOADED = true; core.onUpdate(1)")                       # the ground is there, the player is not
    assert staged(lua, board)[0][1] == first[0][1] and not logs(lua, "props put on the ground")
    lua.execute("CAR = { 8260 + 300, 6996, 50 }; core.onUpdate(1)")      # 300 m from TC1
    assert staged(lua, board)[0][1] == pytest.approx(first[0][1] - 4, abs=1e-6)
    summary = logs(lua, "props put on the ground")
    assert len(summary) == 1 and " moved (largest 4.0 m)" in summary[0]
    # once: another ground answer later changes nothing
    lua.execute("DROP = 9; core.onUpdate(1)")
    assert staged(lua, board)[0][1] == pytest.approx(first[0][1] - 4, abs=1e-6)
    assert len(logs(lua, "props put on the ground")) == 1


def test_a_prop_whose_ground_is_still_not_loaded_is_asked_again_and_one_made_on_known_ground_is_left(lua):
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("LOADED = false; core_terrain = { getTerrainHeight = function(p) if LOADED then return p.z - 2 end end }")
    lua.execute("CAR = { 8260 + 100, 6996, 50 }; core.onUpdate(1)")      # near, but the ground is not there yet
    board = "s_rally_signboard_time_control.dae"
    z0 = staged(lua, board)[0][1]
    assert not logs(lua, "props put on the ground")
    lua.execute("LOADED = true; core.onUpdate(1)")                       # now it is
    assert staged(lua, board)[0][1] == pytest.approx(z0 - 2, abs=1e-6)
    # props made with the ground known and the player near need no second look
    n = len(logs(lua, "props put on the ground"))
    lua.execute("CREATED = {}; earthRebase(37.0900, -7.9800, 37.1000, -7.9700); core.onUpdate(1)")
    assert staged(lua, board) and len(logs(lua, "props put on the ground")) == n
