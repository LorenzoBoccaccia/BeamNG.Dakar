-- BeamNG.Dakar's extension (beamngDakar_core): runs an edition's legs as BeamNG rally loop missions on the
-- BeamNG.Earth level.
--
-- It loads after BeamNG.Earth and changes nothing in it. It taps functions Earth calls through module tables:
--   beamngEarth/rebase.moveVehicles(xf, geodesy)   phase 1 of a rebase: the cars move, xf is the whole frame change
--   beamngEarth/mpanchor.announceOwn(lat, lon)     every anchor commit (teleport or rebase), after the world is in place
--   beamngEarth/terrain.load / .swap               a new terrain block is in (the ground shaping, shape.lua, GitHub #41)
--   beamngEarth/gps.onRouteDownload(msg)           the server's corridor prefetch progress (preload.lua, GitHub #41)
-- Each tap calls the original first/last and never lets an error of ours into Earth's rebase.
-- What Earth 1.9.0's modding API (beamngEarth_api) offers goes through it instead (earthapi.lua, GitHub #51): the
-- player's position, the teleport, the GPS, the messages on screen and the add-on's options; Earth 1.8.2 has none of
-- it and gets the same from its internals. The taps above and the other internals have no API equivalent.
--
-- Console (the game's Lua console, GE):
--   beamngDakar_core.start('testbed-algarve')   teleport to the edition's first point, write the mission files for that
--                                              anchor and start the loop ('svolte-di-popoli' is the other edition)
--   beamngDakar_core.setAdapt(true)             at a rebase, move the rally race path with Earth's transform (experiment)
--   beamngDakar_core.setShaping(false)          the transient ground shaping along the stages (on by default)
--   beamngDakar_core.status()
-- Every rebase writes DAKAR-PROBE lines to the game log: where the rally data is before and after, against the lat/lon truth.
local M = {}
M.dependencies = { 'ui_imgui' }

local logTag = 'beamngDakar'
local unpack = table.unpack or unpack
local legs = require('beamngDakar/legs')
local props = require('beamngDakar/props')
local roadcheck = require('beamngDakar/roadcheck')
local curate = require('beamngDakar/curate')
local shape = require('beamngDakar/shape')
local reclip = require('beamngDakar/reclip')
local preload = require('beamngDakar/preload')
local earthapi = require('beamngDakar/earthapi')
local hud = require('beamngDakar/hud')

local EDITIONS_DIR = '/dakar/editions/'                  -- in the mod (tools/install.py copies editions/ there)
local MISSIONS_DIR = '/gameplay/missions/beamng_earth/'  -- mission ids are <level>/<type>/<name>; the level is Earth's
local DEFAULT_EDITION = 'testbed-algarve'
local START_RADIUS_M = 300                              -- the mission list starts the loop only this close to the service park
local ROADS_AHEAD_M = 600                              -- the ground along the route to the next timed stop, at most this far...
local ROADS_STEP_M = 120                                -- ...sampled this often...
local ROADS_HALF_WIDTH_M = 60                           -- ...each sample's road tiles within this (readyAround's radius) must be in
local ROADS_STOP_MIN_M = 25                             -- (a timed stop closer than this is the one the player is at)
local ROADS_RADIUS_M = 150                              -- off the route (or no route data): the roads this close to the player
local ROADS_WAIT_MAX_S = 60                             -- never held longer than this (a tile the server cannot build)
local LAUNCH_DELAY_S = 4                                -- after the teleport's anchor commit: let the car and ground settle
local MENU_START_WAIT_S = 180                           -- a main menu start waits this long for the Earth level to come up

-- module = a Lua module (package.loaded), global = a game extension's table. before/after: our hook called around the
-- original. intercept: our hook is asked first and, returning true, takes the call over (the original does not run).
-- result: our hook is called after the original with its first return value, then the arguments.
local TAPS = {
  { module = 'beamngEarth/rebase',   fn = 'moveVehicles', before = 'onRebaseBegin' },
  { module = 'beamngEarth/mpanchor', fn = 'announceOwn',  after = 'onAnchor' },
  -- a terrain block's new heights are in (Earth's load = importMaps, swap = a .ter file): what we wrote to the old data is
  -- gone, and the stage corridor is shaped again on the new (GitHub #41)
  -- (optional: without them the loop runs as before, no shaping and no preload progress)
  { module = 'beamngEarth/terrain',  fn = 'load', result = 'onTerrainLoaded', optional = true },
  { module = 'beamngEarth/terrain',  fn = 'swap', result = 'onTerrainSwapped', optional = true },
  -- the corridor prefetch's progress, which Earth shows on its map (GitHub #41)
  { module = 'beamngEarth/gps',      fn = 'onRouteDownload', after = 'onRouteDownload', optional = true },
  -- the mission list starts a loop through these two; ours is held until the route exists (interceptStart)
  { global = 'gameplay_missions_missionManager', fn = 'startWithFade', intercept = 'interceptStart' },
  { global = 'gameplay_missions_missionManager', fn = 'start',         intercept = 'interceptStart' },
  -- the main menu's mission list (level not loaded) starts one through this: the level, then ours (interceptLevelStart)
  { global = 'gameplay_missions_missionManager', fn = 'startWithLoadingLevel', intercept = 'interceptLevelStart' },
}

local st = {
  clock = 0,
  anchor = nil,             -- the current Earth anchor (a geodesy Anchor)
  edition = nil, geometry = nil, closures = nil, truth = nil,
  loaded = {},              -- edition id -> { edition, geometry, closures }: every edition read so far (GitHub #40)
  phase = 'idle',           -- idle | teleporting | settling | running
  launchAt = nil,
  rebase = nil,             -- { xf, before } between phase 1 and the anchor commit of a rebase
  adapt = false,
  shapeOn = true,           -- the transient ground shaping along the stages (the window's switch, GitHub #41)
  rebases = 0,
  bias = 0,                -- the metres the loop's heights are moved to the game's ground, measured at the last launch
  preloadGate = nil,        -- { since }: a menu start's launch waits for the stage corridor prefetch (GitHub #41)
}

local function ownLog(level, msg) log(level, logTag, msg) end

-- ---------------------------------------------------------------------------------------------------------------------
-- Taps

local function callOurs(name, ...)
  local d = rawget(_G, 'beamngDakar_core')
  if d and d[name] then
    local ok, err = pcall(d[name], ...)
    if not ok then log('E', logTag, name .. ' failed: ' .. tostring(err)) end
  end
end

local function tapTarget(t)
  if t.module then return package.loaded[t.module], t.module end
  return rawget(_G, t.global), t.global
end

-- Our hook asked whether it takes the call over: true when it did (and it did not fail).
local function askOurs(name, ...)
  local d = rawget(_G, 'beamngDakar_core')
  if d and d[name] then
    local ok, took = pcall(d[name], ...)
    if not ok then log('E', logTag, name .. ' failed: ' .. tostring(took)); return false end
    return took and true or false
  end
  return false
end

local function installTap(t)
  local mod, name = tapTarget(t)
  if type(mod) ~= 'table' then return false, name .. ' not loaded' end
  local orig = mod[t.fn]
  if type(orig) ~= 'function' then return false, name .. '.' .. t.fn .. ' is not a function (the game or Earth changed?)' end
  local key = '__dakarOrig_' .. t.fn
  if mod[key] then return true end                     -- already tapped (the wrapper finds our extension by name)
  mod[key] = orig
  ownLog('I', 'tap installed on ' .. name .. '.' .. t.fn)
  mod[t.fn] = function(...)
    if t.intercept and askOurs(t.intercept, ...) then return end
    if t.before then callOurs(t.before, ...) end
    -- (the originals return at most a few values: moveVehicles two, announceOwn none; no table.pack in LuaJIT)
    local a, b, c, d = orig(...)
    if t.after then callOurs(t.after, ...) end
    if t.result then callOurs(t.result, a, ...) end
    return a, b, c, d
  end
  return true
end

local function removeTap(t)
  local mod = tapTarget(t)
  local key = '__dakarOrig_' .. t.fn
  if type(mod) == 'table' and mod[key] then
    mod[t.fn] = mod[key]
    mod[key] = nil
  end
end

local function ensureTaps()
  local all = true
  for _, t in ipairs(TAPS) do
    local ok, err = installTap(t)
    if not ok then
      if not t.optional then all = false end
      if not st['warned_' .. t.fn] then st['warned_' .. t.fn] = true; ownLog('W', 'tap not installed: ' .. err) end
    end
  end
  return all
end

-- The driveline's heights (GitHub #66). The game builds the road line the loop measures and tracks from drivelineSpline.json
-- and sets every point's height from the terrain block (zSnap.conformSplineGeometry). Earth's block covers a couple of
-- kilometres round the car; beyond it the terrain answers the block's base height, a constant hundreds of metres under a
-- mountain road. The race nodes, the start positions and the pace notes keep the heights of the data, so each of them the
-- game puts into the line (a pace note's two waypoints, a node) made a detour of twice that difference: the progress bar of
-- the 7.3 km Popoli stage ran to 48.6 km and the briefing's distance, which projects the nodes on the line in 3D, read
-- minus 3.26 km. A driveline of ours (its name, legs.DRIVELINE_NAME) is conformed with the heights of its own nodes instead,
-- the ones everything else in the loop has. Any other spline goes to the game's own.
local ZSNAP, GEOM = '/lua/ge/extensions/editor/rallyEditor/zSnap', 'editor/toolUtilities/geom'
local ZSNAP_KEY = '__dakarOrig_conformSplineGeometry'

local function ensureDrivelineHeights()
  local okZ, zsnap = pcall(require, ZSNAP)
  local okG, geom = pcall(require, GEOM)
  if not (okZ and type(zsnap) == 'table' and okG and type(geom) == 'table' and type(geom.catmullRomFree) == 'function'
      and type(zsnap.conformSplineGeometry) == 'function') then
    if not st.warnedZsnap then
      st.warnedZsnap = true
      ownLog('W', 'the driveline heights are not ours (the game changed zSnap or geom?): the briefing may show wrong distances')
    end
    return false
  end
  if zsnap[ZSNAP_KEY] then return true end
  local orig = zsnap.conformSplineGeometry
  zsnap[ZSNAP_KEY] = orig
  ownLog('I', 'tap installed on zSnap.conformSplineGeometry')
  zsnap.conformSplineGeometry = function(spline, useRaycast, minNumDivisions, minSpacing, rayRaise)
    if type(spline) == 'table' and spline.name == legs.DRIVELINE_NAME then
      local ok, err = pcall(geom.catmullRomFree, spline, minNumDivisions)
      if ok then return end
      ownLog('E', 'the driveline could not be conformed to its own heights: ' .. tostring(err))
    end
    return orig(spline, useRaycast, minNumDivisions, minSpacing, rayRaise)
  end
  return true
end

local function removeDrivelineHeights()
  local zsnap = package.loaded[ZSNAP]
  if type(zsnap) == 'table' and zsnap[ZSNAP_KEY] then
    zsnap.conformSplineGeometry = zsnap[ZSNAP_KEY]
    zsnap[ZSNAP_KEY] = nil
  end
end

-- ---------------------------------------------------------------------------------------------------------------------
-- Edition data

local function missionName(edition, leg) return (edition.missionPrefix or 'dakar') .. '-' .. leg.id end
local function missionType(leg) return leg.type == 'special' and 'rallyStage' or 'rallyRoadSection' end
local function missionDirOf(leg) return MISSIONS_DIR .. missionType(leg) .. '/' .. missionName(st.edition, leg) end

local function loadEdition(id)
  local dir = EDITIONS_DIR .. id .. '/'
  local edition = jsonReadFile(dir .. 'legs.json')
  if not edition then return nil, 'no ' .. dir .. 'legs.json (run tools/install.py)' end
  local geometry = {}
  for _, leg in ipairs(edition.legs) do
    local g = jsonReadFile(dir .. 'geometry/' .. leg.id .. '.json')
    if not g then return nil, 'no geometry for leg ' .. leg.id .. ' (run tools/fetch_route.py)' end
    geometry[leg.id] = g
  end
  -- where the side roads of a special stage are closed off (tools/stage_closures.py, GitHub #33); optional: an edition
  -- without the files just has no closures
  local closures = {}
  for _, leg in ipairs(edition.legs) do
    if leg.type == 'special' then closures[leg.id] = jsonReadFile(dir .. 'closures/' .. leg.id .. '.json') end
  end
  return edition, geometry, closures
end

-- An edition is read once and kept: the mission list, the window and the start triggers of every edition's loop ask for
-- them again and again (GitHub #40: a second edition must not cost the first its start marker).
local function loadCached(id)
  local c = st.loaded[id]
  if c then return c end
  local edition, geometry, closures = loadEdition(id)
  if not edition then return nil, geometry end
  c = { edition = edition, geometry = geometry, closures = closures }
  st.loaded[id] = c
  return c
end

-- The curated line of each special stage of the current edition (curate.lua, GitHub #41), made once per edition: the
-- source line cleaned of spurs, zigzags and profile spikes and held to road-like grades. Everything a special is built
-- from (driveline, race path, pace notes, props, the ground shaping) uses it; the source geometry stays the truth the
-- lat/lon came from. A line that cannot be curated stays as the source. Returns { [leg id] = curate.line result }.
local function curatedLines()
  local c = st.edition and st.loaded[st.edition.edition]
  if not c then return {} end
  if c.curated then return c.curated end
  c.curated, c.curatedSummary = {}, {}
  for _, leg in ipairs(st.edition.legs) do
    local g = st.geometry[leg.id]
    if leg.type == 'special' and g then
      local ok, r, why = pcall(curate.line, g.points, { structures = g.structures })
      if ok and r then
        c.curated[leg.id] = r
        c.curatedSummary[leg.id] = curate.summary(leg.id, r)
      else
        ownLog('W', 'stage ' .. leg.id .. ' not curated, the source line is used: ' .. tostring(ok and why or r))
      end
    end
  end
  return c.curated
end

-- A leg's points { lat, lon, h }: the curated line for a special, else the source geometry.
local function legPoints(leg)
  local r = curatedLines()[leg.id]
  return r and r.points or st.geometry[leg.id].points
end

-- The edition the extension works on (st.edition, st.geometry): the one whose loop is started, or the window's pick.
local function ensureEdition(id)
  id = id or DEFAULT_EDITION
  if st.edition and st.edition.edition == id and st.geometry then return true end
  local c, err = loadCached(id)
  if not c then return false, err end
  st.edition, st.geometry, st.closures = c.edition, c.geometry, c.closures
  return true
end

-- The ids of the installed editions (the folders of EDITIONS_DIR with a legs.json), found once. When the game cannot
-- list the folder, the editions already read and the default one.
local function editionIds()
  if st.editionIds then return st.editionIds end
  local ids, seen = {}, {}
  local function add(id)
    if id and not seen[id] and jsonReadFile(EDITIONS_DIR .. id .. '/legs.json') then seen[id] = true; ids[#ids + 1] = id end
  end
  local fs = rawget(_G, 'FS')
  if fs and fs.directoryList then
    local ok, list = pcall(fs.directoryList, fs, EDITIONS_DIR, false, true)
    if ok and type(list) == 'table' then
      for _, path in ipairs(list) do add(tostring(path):match('([^/\\]+)/?$')) end
    end
  end
  add(DEFAULT_EDITION)
  for id in pairs(st.loaded) do add(id) end
  table.sort(ids)
  if #ids > 0 then st.editionIds = ids end
  return ids
end

-- Where an edition's service park is: where the car starts in the lot (GitHub #55: servicePark.spawn of its first leg's
-- geometry), else the first leg's first point (the current edition unless one is given).
local function servicePark(edition, geometry)
  local g = (geometry or st.geometry)[(edition or st.edition).legs[1].id]
  local lot = g.servicePark
  local first = (lot and lot.spawn and { lot.spawn.lat, lot.spawn.lon, lot.spawn.h }) or g.points[1]
  return first[1], first[2], first[3]
end

local function earthState()
  local earth = rawget(_G, 'beamngEarth_core')
  return earth and earth._state and earth._state() or nil
end

local COMPASS = { 'north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west' }

-- Where the player is against the edition's service park: metres and the way to go (a flat approximation, plenty for
-- "300 m" and "which way"). nil when there is no player position yet.
local function whereIsTheStart()
  local plLat, plLon = earthapi.position()
  if not (plLat and st.edition and st.geometry) then return nil end
  local lat, lon = servicePark()
  local dn = (lat - plLat) * 111320
  local de = (lon - plLon) * 111320 * math.cos(math.rad((lat + plLat) / 2))
  local deg = math.deg((math.atan2 or math.atan)(de, dn)) % 360
  return { metres = math.sqrt(dn * dn + de * de), way = COMPASS[math.floor((deg + 22.5) / 45) % 8 + 1], lat = lat, lon = lon }
end

local function earthModule(name) return package.loaded['beamngEarth/' .. name] end

-- The legs' world points for the current anchor (made again when it changes).
local function worldLegs()
  if not (st.anchor and st.edition and st.geometry) then return nil end
  local c = st.worldLegs
  if c and c.anchor == st.anchor and c.edition == st.edition then return c.legs end
  local list = {}
  for _, leg in ipairs(st.edition.legs) do list[#list + 1] = legs.toWorld(legPoints(leg), st.anchor) end
  st.worldLegs = { anchor = st.anchor, edition = st.edition, legs = list }
  return list
end

-- Are the roads the player needs in? Not a circle round the player: the stretch of route from where they are to the next
-- timed stop (capped at ROADS_AHEAD_M), each sample's road tiles (Earth's roads.readyAround, colliders made), so a tile
-- off that path that the server is slow to build cannot hold them. Off the route: the circle ROADS_RADIUS_M. Yes when
-- there is nothing to wait for: no roads module, roads off, no server, or no player position.
local function roadsAround()
  local es = earthState()
  local pl = es and es.player
  local roads = earthModule('roads')
  if not (es and pl and es.anchorId and roads and roads.readyAround) then return true end
  local net = earthModule('net')
  if net and net.isConnected and not net.isConnected() then return true end
  local wl = worldLegs()
  local pts = wl and legs.corridor(wl, pl.x, pl.y, ROADS_AHEAD_M, ROADS_STEP_M, ROADS_STOP_MIN_M)
  if not pts then return roads.readyAround(es.anchorId, pl.x, pl.y, ROADS_RADIUS_M) and true or false end
  for _, p in ipairs(pts) do
    if not roads.readyAround(es.anchorId, p[1], p[2], ROADS_HALF_WIDTH_M) then return false end
  end
  return true
end

local function tell(msg)
  ownLog('I', msg)
  earthapi.notify(msg, 12)
end

-- Earth's GPS set to a service park the player is far from (Earth 1.9.0's API; nothing before it), taken off again when
-- the loop starts, if it still leads there.
local function clearStartGps()
  if st.startGps then earthapi.gpsClearIf(st.startGps[1], st.startGps[2]) end
  st.startGps = nil
end

-- A main menu start keeps the game's loading screen up (our own request tag: it stays while any tag holds it) from the
-- click until the loop's own start fade, so the level load, Earth's first place, the teleport to the service park, the
-- terrain and the roads all happen behind it (GitHub #25: no freeroam in between). Let go when the loop starts, when
-- the start gives up, and by the watchdog in onUpdate.
local LOADING_TAG = 'beamngDakar'
local LOADING_MAX_S = 420
local function holdLoadingScreen()
  local gs = rawget(_G, 'core_gamestate')
  if st.loadingSince or not (gs and gs.requestEnterLoadingScreen) then return end
  local ok, err = pcall(gs.requestEnterLoadingScreen, LOADING_TAG)
  if not ok then return ownLog('W', 'could not hold the loading screen: ' .. tostring(err)) end
  st.loadingSince = st.clock
  ownLog('I', 'loading screen held until the loop starts')
end
local function releaseLoadingScreen(why)
  if not st.loadingSince then return end
  st.loadingSince = nil
  local gs = rawget(_G, 'core_gamestate')
  if gs and gs.requestExitLoadingScreen then pcall(gs.requestExitLoadingScreen, LOADING_TAG) end
  ownLog('I', 'loading screen let go: ' .. why)
end
M.releaseLoadingScreen = releaseLoadingScreen

-- The player is held (Earth's own pause, simhold) until the roads around are in, then after() runs: it is impossible to
-- find the way while the road is wilderness, and the loop's clock must not run over it. Ends by itself after
-- ROADS_WAIT_MAX_S, when the player resumes the game, or from the window's button.
local function endRoadsWait(why)
  local w = st.roadsWait
  if not w then return end
  st.roadsWait = nil
  st.phase = 'idle'
  local sh = earthModule('simhold')
  local resumed = why == 'the player resumed the game'          -- (their call: we do not unpause what they unpaused)
  if w.paused and not resumed and sh and sh.pause then sh.pause(false) end
  ownLog('I', string.format('roads wait over after %.0f s: %s', st.clock - w.since, why))
  if not resumed then w.after() end
end
M.endRoadsWait = endRoadsWait

local function holdForRoads(after)
  if roadsAround() then return after() end
  local sh = earthModule('simhold')
  st.roadsWait = { since = st.clock, after = after }
  st.phase = 'roads'
  if sh and sh.pause and not (sh.paused and sh.paused()) then
    sh.pause(true)
    st.roadsWait.paused = true
  end
  tell('Waiting for the roads around you to load; the game is paused until they are in (the pause key skips it).')
end

-- ---------------------------------------------------------------------------------------------------------------------
-- The probe: where the rally data is, against the lat/lon truth of the current anchor

local function v3(p) return { p.x or p[1], p.y or p[2], p.z or p[3] } end

local function dist3(a, b)
  local dx, dy, dz = a[1] - b[1], a[2] - b[2], a[3] - b[3]
  return math.sqrt(dx * dx + dy * dy + dz * dz)
end

local function rallyPositions()
  local out = {}
  local rm = gameplay_rally and gameplay_rally.getRallyManager and gameplay_rally.getRallyManager()
  if not rm then return out end
  local rp = rm.getRacePath and rm:getRacePath()
  if not rp then return out end
  for _, sp in ipairs((rp.startPositions and rp.startPositions.sorted) or {}) do
    out['start:' .. tostring(sp.name)] = v3(sp.pos)
  end
  local nodes = rp.pathnodes and rp.pathnodes.sorted
  if nodes and #nodes > 0 then
    out['pathnode:first'] = v3(nodes[1].pos)
    out['pathnode:last'] = v3(nodes[#nodes].pos)
  end
  return out
end

-- Every point of every leg, in the current frame, from lat/lon (the race nodes are not on the leg ends: the last one
-- sits short of the stop position).
local function truthPoints(anchor)
  local pts = {}
  if not (st.geometry and st.edition) then return pts end
  for _, leg in ipairs(st.edition.legs) do
    local g = legPoints(leg)
    for i, p in ipairs(g) do
      local x, y, z = anchor:geodeticToWorld(p[1], p[2], p[3])
      local tag = (i == 1 and ':first') or (i == #g and ':last') or (':' .. i)
      pts[#pts + 1] = { leg.id .. tag, { x, y, z } }
    end
  end
  return pts
end

local function nearestTruth(truth, p)
  local best, bestD = nil, math.huge
  for _, t in ipairs(truth) do
    local d = dist3(t[2], p)
    if d < bestD then best, bestD = t[1], d end
  end
  return best, bestD
end

local function playerPos()
  local veh = getPlayerVehicle and getPlayerVehicle(0)
  return veh and v3(veh:getPosition()) or nil
end

local function probeReport(tag, positions, anchor)
  local truth = truthPoints(anchor)
  local names = {}
  for k in pairs(positions) do names[#names + 1] = k end
  table.sort(names)
  for _, k in ipairs(names) do
    local name, d = nearestTruth(truth, positions[k])
    ownLog('I', string.format('DAKAR-PROBE %s %s nearest truth %s residual %.2f m', tag, k, tostring(name), d))
  end
end

-- ---------------------------------------------------------------------------------------------------------------------
-- Scenery: the loop's props (props.lua), only near the player, made again whenever the frame changes

local SCENERY_RADIUS_M = 2500        -- props this close to the player exist
local LANDMARK_RADIUS_M = 9000       -- the tall landmarks (props `far`) from this far
local SCENERY_RECHECK_M = 400        -- after the player moved this far: look again (new props ahead, old ones behind)
local SCENERY_EVERY_S = 0.5
local scenery = { objs = {}, active = false, dirty = false, centre = nil, anchor = nil, worldByLeg = nil, bias = nil,
  defs = nil, checked = -10,
  -- the roads props are kept off (GitHub #59): the game's navgraph and our own stage and transfer road, and a version that
  -- counts the changes of either, to check the props again
  roads = { graph = nil, own = nil, graphV = 0, builtV = -1, ownFor = nil, version = 0, nodeCount = nil, countedAt = -10 },
  roadLogged = {},
}

local function sceneryClear()
  for _, ob in pairs(scenery.objs) do
    local o = scenetree and scenetree.findObjectById(ob.id)
    if o then o:delete() end
  end
  scenery.objs, scenery.centre, scenery.anchor, scenery.edition = {}, nil, nil, nil
  scenery.roadLogged = {}
end

-- The game's ground at (x, y): the terrain block's own height (core_terrain; the roads are drawn on it and it is graded to
-- them near the player), else a ray down onto static collision (the top surface: a roof where a building stands), else
-- nil (the ground is not loaded there yet). hint is the terrain data's height, which the game's ground is within a few
-- metres of (measured 3.3 m at the testbed start); a value more than HINT_MAX_M from it is not believed (an empty block answers 0).
local HINT_MAX_M = 25
local function groundAt(x, y, hint)
  local ct = rawget(_G, 'core_terrain')
  if ct and ct.getTerrainHeight and vec3 then
    local ok, h = pcall(ct.getTerrainHeight, vec3(x, y, hint or 0))
    if ok and type(h) == 'number' and h == h and (not hint or math.abs(h - hint) <= HINT_MAX_M) and h > -1000 then return h end
  end
  if castRayStatic and vec3 then
    local top = (hint or 0) + 200
    local hit = castRayStatic(vec3(x, y, top), vec3(0, 0, -1), 400)
    if hit and hit < 400 then return top - hit end
  end
  return nil
end
M.groundAt = groundAt

local PROP_SNAP_M = 400            -- a prop this close to the player is put on the real ground (GitHub #49)
local PROP_SNAP_MIN_M = 0.2          -- (and not touched for less)

local function setPropPose(o, x, y, z, yaw)
  local h = math.rad(yaw) / 2                   -- yaw counter-clockwise from +x, as Earth's setPosRot quaternion has it
  o:setPosRot(x, y, z, 0, 0, -math.sin(h), math.cos(h))
end

-- A prop made far from the player gets no ground: the terrain there is not loaded, and it stands at the terrain data's
-- height plus the last difference seen, which floated or sank props by metres (the Svolte di Popoli data is ~10 m above
-- the game's ground at the stage start, GitHub #49). It is marked as not on the ground yet, and propSnapTick puts it on
-- the ground once the player is close. A prop made with the ground known, close to the player, is done at once.
local function makeProp(p, car)
  local gz = groundAt(p.x, p.y, p.z)
  local known = gz ~= nil
  if gz then scenery.bias = gz - p.z else gz = p.z + (scenery.bias or 0) end   -- (no ground there yet: the last difference)
  local o = createObject('TSStatic')
  o:setField('shapeName', 0, p.shape)
  o:setField('collisionType', 0, 'None')
  o:setField('decalType', 0, 'None')
  o.canSave = false
  setPropPose(o, p.x, p.y, gz, p.yaw)
  o:registerObject('')
  scenetree.MissionGroup:addObject(o)
  local near = car and (p.x - car[1]) ^ 2 + (p.y - car[2]) ^ 2 <= PROP_SNAP_M * PROP_SNAP_M
  scenery.objs[p.id] = { id = o:getID(), x = p.x, y = p.y, z = gz, zData = p.z, yaw = p.yaw, snapped = known and near or false, p = p,
    roadV = scenery.roads.version }
end

-- ---------------------------------------------------------------------------------------------------------------------
-- No props on roads (GitHub #59): the game's navgraph (map.getMap().nodes, built by Earth from the roads it streams, a few tiles
-- at a time) and our own stage and transfer road are the roads (roadcheck.lua); a prop whose footprint is within a road's half
-- width and a margin of one is not made, or deleted when the roads came later. The closure tapes stand across side roads on
-- purpose and have no footprint (props.footprint).

local NAV_RECOUNT_S = 2             -- the navgraph's node count is looked at this often, in case a change went without its hook

local function navNodes()
  local mp = rawget(_G, 'map')
  if not (mp and mp.getMap) then return nil end
  local ok, m = pcall(mp.getMap)
  return ok and type(m) == 'table' and m.nodes or nil
end

-- The navgraph was rebuilt (the game's hook, which Earth's map.reset raises): the roads are read again at the next check.
function M.onNavgraphReloaded() scenery.roads.graphV = scenery.roads.graphV + 1 end

-- The indexes of the roads, read again where the navgraph or our road changed (the version counts it).
local function roadsReady()
  local R = scenery.roads
  if R.builtV ~= R.graphV then
    R.graph = roadcheck.new()
    local nodes = navNodes()
    if nodes then pcall(R.graph.addGraph, R.graph, nodes) end
    R.builtV = R.graphV
    R.version = R.version + 1
  end
  if R.ownFor ~= scenery.worldByLeg and scenery.worldByLeg and st.edition then
    R.own = roadcheck.new()
    for _, leg in ipairs(st.edition.legs) do
      local w = scenery.worldByLeg[leg.id]
      if w and #w >= 2 then R.own:addLine(w, roadcheck.OWN_HALF_M) end
    end
    R.ownFor = scenery.worldByLeg
    R.version = R.version + 1
  end
  return R
end

-- Where the road is under a prop: the distance of its footprint's nearest point to the road's centre line and the road's half
-- width, or nil. z: the prop's ground when it is known.
local function roadHit(p, z)
  local fp = props.footprint(p.kind)
  if not fp then return nil end
  -- (a failure of the check is no reason to keep the props from standing: said once, and they stand)
  local ok, d, half = pcall(function()
    local R = roadsReady()
    -- (the booth's props were made clear of the lot's aisles and of the drive offline, tools/booth.py: the drive is not a road to them)
    return roadcheck.hit(p, fp, R.graph, (not p.booth) and R.own or nil, z)
  end)
  if not ok then
    if not st.roadWarned then st.roadWarned = true; ownLog('E', 'road check failed: ' .. tostring(d)) end
    return nil
  end
  return d, half
end

local function roadSkipLog(p, hit, what)
  if scenery.roadLogged[p.id] then return end
  scenery.roadLogged[p.id] = true
  ownLog('I', string.format('road check: %s %s: its footprint is %.1f m from the centre line of a road of half width %.1f m (margin %.2f m)',
    p.id, what, hit[1], hit[2], roadcheck.MARGIN_M))
end

-- A prop that does a job (the start and finish boards, the sensors, the time control boards, the tower, the cones: props.pushable) found
-- on a road is not dropped (GitHub #61: the first run deleted the start's boards and sensors, the navgraph having the SR17 5 m wide):
-- it is pushed out along the road's normal until its footprint clears the road and the margin, at most props.PUSH_MAX_M. Returns the new
-- x, y and how far, or nil (not pushable, or no push clears it: then it goes like the decorations).
local function roadPush(p, z)
  if not props.pushable(p.kind) then return nil end
  local ok, nx, ny, moved = pcall(function()
    local R = roadsReady()
    return roadcheck.relocate(p, props.footprint(p.kind), R.graph, (not p.booth) and R.own or nil, z, props.PUSH_MAX_M)
  end)
  if ok and nx then return nx, ny, moved end
  return nil
end

local function roadMoveLog(p, hit, moved)
  if scenery.roadLogged[p.id] then return end
  scenery.roadLogged[p.id] = true
  ownLog('I', string.format('road check: %s moved %.2f m out of the road: its footprint was %.1f m from the centre line of a road of half width %.1f m (margin %.2f m)',
    p.id, moved, hit[1], hit[2], roadcheck.MARGIN_M))
end

-- The navgraph's node count changed without its hook having told us: counted as a rebuild.
local function roadsCount()
  local R = scenery.roads
  if st.clock - R.countedAt < NAV_RECOUNT_S then return end
  R.countedAt = st.clock
  local nodes = navNodes()
  if not nodes then return end
  local n = 0
  for _ in pairs(nodes) do n = n + 1 end
  if R.nodeCount ~= nil and n ~= R.nodeCount then R.graphV = R.graphV + 1 end
  R.nodeCount = n
end

-- Props the player is within PROP_SNAP_M of are looked at again whenever the roads changed since they were last checked: the
-- navgraph may not have had the roads where a prop was made, far away. A gazebo goes whole (all of a `group`).
local function roadTick(car)
  if not car or next(scenery.objs) == nil then return end
  roadsCount()
  local R = roadsReady()
  local dead = nil
  for id, ob in pairs(scenery.objs) do
    if ob.roadV ~= R.version and ob.p and (ob.x - car[1]) ^ 2 + (ob.y - car[2]) ^ 2 <= PROP_SNAP_M * PROP_SNAP_M then
      ob.roadV = R.version
      local z = ob.snapped and ob.z or nil
      local d, half = roadHit(ob.p, z)
      if d then
        local nx, ny, moved = roadPush(ob.p, z)
        if nx then
          -- (pushed out where it stands: on the ground there, if the game has it, else as high as it was)
          ob.x, ob.y, ob.p.x, ob.p.y = nx, ny, nx, ny
          local gz = groundAt(nx, ny, ob.zData)
          if gz then ob.z = gz end
          local o = scenetree.findObjectById(ob.id)
          if o then setPropPose(o, nx, ny, ob.z, ob.yaw) end
          roadMoveLog(ob.p, { d, half }, moved)
        else
          dead = dead or {}; dead[ob.p.group or id] = { d, half }
        end
      end
    end
  end
  if not dead then return end
  for id, ob in pairs(scenery.objs) do
    local hit = ob.p and dead[ob.p.group or id]
    if hit then
      local o = scenetree.findObjectById(ob.id)
      if o then o:delete() end
      scenery.objs[id] = nil
      roadSkipLog(ob.p, hit, 'deleted')
    end
  end
end

-- Props the player has come within PROP_SNAP_M of are put on the ground the game has there, once each (they are made
-- again, and so checked again, at the next anchor: a new frame builds them anew). One log line says what it did.
local function propSnapTick(car)
  if not car then return end
  local checked, moved, worst = 0, 0, 0
  for _, ob in pairs(scenery.objs) do
    if not ob.snapped and (ob.x - car[1]) ^ 2 + (ob.y - car[2]) ^ 2 <= PROP_SNAP_M * PROP_SNAP_M then
      local gz = groundAt(ob.x, ob.y, ob.zData)
      if gz then                                    -- (no ground there yet: asked again next time)
        ob.snapped = true
        checked = checked + 1
        local dz = math.abs(gz - ob.z)
        if dz >= PROP_SNAP_MIN_M then
          local o = scenetree.findObjectById(ob.id)
          if o then
            setPropPose(o, ob.x, ob.y, gz, ob.yaw)
            moved, worst = moved + 1, math.max(worst, dz)
          end
          ob.z = gz
        end
      end
    end
  end
  if checked > 0 then
    ownLog('I', string.format('props put on the ground: %d checked, %d moved (largest %.1f m)', checked, moved, worst))
  end
end

local function sceneryReconcile()
  if not (scenery.active and st.anchor and st.edition and st.geometry) then return end
  local car = playerPos()
  if not car then return end
  if scenery.anchor ~= st.anchor or scenery.edition ~= st.edition then
    -- a new frame (teleport or rebase): every prop is somewhere else now; another edition: they are other props
    for _, ob in pairs(scenery.objs) do
      local o = scenetree.findObjectById(ob.id)
      if o then o:delete() end
    end
    scenery.objs, scenery.worldByLeg, scenery.anchor, scenery.edition, scenery.defs = {}, nil, st.anchor, st.edition, nil
  end
  if not scenery.worldByLeg then
    scenery.worldByLeg = {}
    for _, leg in ipairs(st.edition.legs) do scenery.worldByLeg[leg.id] = legs.toWorld(legPoints(leg), st.anchor) end
  end
  if not scenery.defs then
    -- the split checkpoints' boards stand where legs.raceNodes puts the splits in the race path (GitHub #37)
    local splits = {}
    for _, leg in ipairs(st.edition.legs) do
      local list = {}
      for _, s in ipairs((legs.raceNodes(leg, scenery.worldByLeg[leg.id]))) do
        if s.split then list[#list + 1] = s.at end
      end
      splits[leg.id] = list
    end
    scenery.defs = props.layout(st.edition, splits)
  end
  local want = {}
  local placed = props.place(scenery.defs, scenery.worldByLeg)
  -- the closures across the side roads of the special stages (#33): the same kind of prop, from lat/lon per anchor
  if scenery.sitesFor ~= st.closures then
    scenery.sites, scenery.sitesFor = props.closureSites(st.edition, st.closures), st.closures
  end
  for _, p in ipairs(props.placeClosures(scenery.sites, function(pts) return legs.toWorld(pts, st.anchor) end)) do
    placed[#placed + 1] = p
  end
  -- the service booth beside the car's spot in the lot (GitHub #57): gazebos, tables and tools from the first leg's geometry
  if scenery.boothFor ~= st.geometry or scenery.boothEdition ~= st.edition then
    scenery.booth, scenery.boothFor, scenery.boothEdition = props.boothSites(st.edition, st.geometry), st.geometry, st.edition
  end
  for _, p in ipairs(props.placeBooth(scenery.booth, function(pts) return legs.toWorld(pts, st.anchor) end)) do
    placed[#placed + 1] = p
  end
  for _, p in ipairs(placed) do
    local dx, dy = p.x - car[1], p.y - car[2]
    local r = p.radius or (p.far and LANDMARK_RADIUS_M or SCENERY_RADIUS_M)
    if dx * dx + dy * dy <= r * r then want[p.id] = p end
  end
  -- not on a road (GitHub #59); the ground of a prop within reach of the player is known, which tells a bridge over it
  local dead = nil
  for id, p in pairs(want) do
    local ob = scenery.objs[id]
    local z = ob and ob.snapped and ob.z or nil
    if not z and (p.x - car[1]) ^ 2 + (p.y - car[2]) ^ 2 <= PROP_SNAP_M * PROP_SNAP_M then z = groundAt(p.x, p.y, p.z) end
    local d, half = roadHit(p, z)
    if d then
      local nx, ny, moved = roadPush(p, z)
      if nx then
        roadMoveLog(p, { d, half }, moved)
        p.x, p.y = nx, ny
      else
        dead = dead or {}; dead[p.group or id] = { d, half }
      end
    end
  end
  if dead then
    for id, p in pairs(want) do
      local hit = dead[p.group or id]
      if hit then want[id] = nil; roadSkipLog(p, hit, scenery.objs[id] and 'deleted' or 'not placed') end
    end
  end
  for id, ob in pairs(scenery.objs) do             -- behind us now
    if not want[id] then
      local o = scenetree.findObjectById(ob.id)
      if o then o:delete() end
      scenery.objs[id] = nil
    end
  end
  for id, p in pairs(want) do
    if not scenery.objs[id] then makeProp(p, car) end
  end
  scenery.centre, scenery.dirty = { car[1], car[2] }, false
end

local function sceneryTick()
  if not scenery.active or st.clock - scenery.checked < SCENERY_EVERY_S then return end
  scenery.checked = st.clock
  local car = playerPos()
  local moved = scenery.centre and car and (math.abs(car[1] - scenery.centre[1]) + math.abs(car[2] - scenery.centre[2])) or math.huge
  if scenery.dirty or scenery.anchor ~= st.anchor or scenery.edition ~= st.edition or moved > SCENERY_RECHECK_M then sceneryReconcile() end
  propSnapTick(car)
  roadTick(car)
end

-- ---------------------------------------------------------------------------------------------------------------------
-- Ground shaping (shape.lua, GitHub #41): while the loop is on, the terrain block's heights along the curated special
-- stages are changed, in memory, only where the elevation data has a defect the road does not (the ridge under an overpass, a big
-- spike or dip on the line, the ground at a deck's end: GitHub #63) and by no more than shape.MAX_CHANGE_M; everywhere else the road is
-- as Earth made it (its lines are a mesh over its own ground, GitHub #61). Again for every block Earth brings in (its load / swap
-- are tapped). The stages' world points are made again for every anchor; the originals go back when the loop stops.
-- The curated line's heights (curate.lua) are not what the ground is shaped to: the ground is Earth's, the heights are the offline data.

local function newClock()
  local hp = rawget(_G, 'hptimer')
  if hp then
    local ok, t = pcall(hp)
    if ok and t and t.stopAndReset then
      local acc = 0
      t:stopAndReset()
      return function() acc = acc + t:stopAndReset(); return acc end
    end
  end
  return function() return os.clock() * 1000 end
end

local function terrainHeightAt(x, y)
  local ct = rawget(_G, 'core_terrain')
  if not (ct and ct.getTerrainHeight and vec3) then return nil end
  local ok, h = pcall(ct.getTerrainHeight, vec3(x, y, 0))
  if ok and type(h) == 'number' and h == h and h > -1000 then return h end
  return nil
end

-- The DecalRoads of Earth's road group over ground the shaping changed are cut to it again (reclip.lua, GitHub #61).
local shaper, reclipper
local function getReclipper()
  if reclipper then return reclipper end
  reclipper = reclip.new({
    clock = function() return st.clock end,
    idle = function() return not (shaper and shaper:busy()) end,
    group = function()
      local r = earthModule('roads')
      local g = scenetree and scenetree.findObject((r and r.GROUP) or 'bngEarthRoads')
      if not (g and g.getCount and g.getObject) then return nil end
      return {
        count = function() return g:getCount() end,
        at = function(i)
          local o = g:getObject(i - 1)
          if not o then return nil end
          return {
            class = function() return o.getClassName and o:getClassName() or o.className end,
            box = function()
              local b = o:getWorldBox()
              local c, e = b:getCenter(), b:getExtents()
              return c.x - e.x / 2, c.y - e.y / 2, c.x + e.x / 2, c.y + e.y / 2
            end,
            regenerate = function() o:regenerate() end,
          }
        end,
      }
    end,
    log = ownLog,
  })
  return reclipper
end

local function getShaper()
  if shaper then return shaper end
  shaper = shape.new({
    changed = function(x0, y0, x1, y1) getReclipper():note(x0, y0, x1, y1) end,
    block = function()
      local t = earthModule('terrain')
      if not (t and t.get) then return nil end
      local ok, tb = pcall(t.get)
      return ok and tb or nil
    end,
    fallbackGrid = function()
      local t = earthModule('terrain')
      return t and t.SIZE and { n = t.SIZE } or nil
    end,
    now = newClock(),
    clock = function() return st.clock end,
    -- the player's car, and its speed: the shaping leaves the ground round a moving car alone and shapes the stretch round it at once
    -- at a new block (GitHub #59)
    player = function()
      local veh = getPlayerVehicle and getPlayerVehicle(0)
      local p = veh and v3(veh:getPosition()) or nil
      if not p then return nil end
      local car = { x = p[1], y = p[2] }
      if veh.getVelocity then
        local ok, v = pcall(veh.getVelocity, veh)
        if ok and v and v.x then car.speed = math.sqrt(v.x * v.x + v.y * v.y) end
      end
      return car
    end,
    ground = terrainHeightAt,
    -- the top of the static colliders (a bridge deck) above a point on the line
    deck = function(x, y, hint)
      if not (castRayStatic and vec3) then return nil end
      local top = (hint or 0) + 200
      local hit = castRayStatic(vec3(x, y, top), vec3(0, 0, -1), 400)
      if hit and hit < 400 then return top - hit end
      return nil
    end,
    log = ownLog, vec3 = vec3,
  })
  shaper:setEnabled(st.shapeOn)
  return shaper
end

-- The curated special stages in the current frame, at the loop's heights: { { pts = { {x, y, z} }, skip = { [i] = true } } }
-- (skip: the stretch is a bridge or a tunnel the ground is left alone under; 'tagged' when OpenStreetMap says so).
local function shapeLines()
  if not (st.anchor and st.edition and st.geometry) then return nil end
  local lines = {}
  for _, leg in ipairs(st.edition.legs) do
    local r = curatedLines()[leg.id]
    if r then
      local w = legs.toWorld(r.points, st.anchor, st.bias)
      local skip = {}
      -- ('tagged': OpenStreetMap says it is a bridge; the ground at its ends is met to the deck, which a span the profile alone found is not:
      -- GitHub #65. The edition may set the spike limits of the shaping, legs.json "shaping": { "spikeM": 2.5 })
      for i = 1, #w - 1 do
        local sp = curate.spanAt(r, i)
        if sp then skip[i] = sp.tagged and 'tagged' or true end
      end
      local cfg = st.edition.shaping
      lines[#lines + 1] = { pts = w, skip = skip, spikeM = cfg and cfg.spikeM }
    end
  end
  return lines
end

-- The loop is on (and the frame is known): shape along its stages; or off: put the ground back.
local function shapeRefresh()
  local s = getShaper()
  if scenery.active then s:setLines(shapeLines()) else s:stop() end
end

local function shapeTick()
  if shaper then shaper:update() end
  if reclipper then reclipper:update() end
end

-- Earth put a new block's heights in (terrain.load = importMaps, terrain.swap = a .ter file; the tap hands over the
-- original's first result): what we wrote to the old data is gone with it, so the stage is shaped again on the new.
-- The tap runs in the same Lua call as Earth's swap or load, so what blockChanged shapes at the car is in before the next physics
-- step sees the new block (GitHub #59). During a rebase or a teleport the stages are not yet in the new block's frame (the anchor
-- commit that follows sets them): shapeRefresh shapes the stretch at the car then.
local function blockChanged()
  local stale = st.rebase ~= nil or st.phase == 'teleporting'
  getShaper():blockChanged(stale)
end
function M.onTerrainLoaded(ok) if ok == true then blockChanged() end end
function M.onTerrainSwapped(ok) if ok == true then blockChanged() end end

-- The window's switch (and the console): shaping off puts the original heights back.
function M.setShaping(on)
  st.shapeOn = on and true or false
  getShaper():setEnabled(st.shapeOn)
  return st.shapeOn
end

-- The add-on's options (Earth 1.9.0: Settings > Add-ons > Options, declared in beamngEarth_addon.json at the top of the
-- add-on's zip, GitHub #51): read once Earth has them (onUpdate), and again whenever the player saves them. The
-- window's switch stays what it was, for Earth 1.8.2 and the unpacked install.
local function applyOptions(values)
  if type(values) == 'table' and type(values.groundShaping) == 'boolean' then M.setShaping(values.groundShaping) end
end

function M.onBeamNGEarthAddonOptions(info)
  if type(info) == 'table' and info.id == earthapi.ADDON_ID then applyOptions(info.values) end
end

local function shapeStatusText()
  local sh = shaper and shaper:status()
  if not sh then return 'Ground: no terrain block seen yet' end
  local ago = sh.at and string.format('%.0f s ago', st.clock - sh.at) or 'not yet'
  -- (GitHub #63: the road is Earth's own but where a defect of the data is fixed: how many stretches that is)
  return string.format('Ground: %s%s; %d stretch%s fixed, %d cells, largest change %.1f m, last %s', sh.enabled and '' or 'switched off, ',
    sh.state, sh.stretches, sh.stretches == 1 and '' or 'es', sh.cells, sh.maxChange, ago)
end

-- The loop is on: its props stand; it stopped (finished, abandoned, a level change): they go.
function M.onAnyMissionChanged(state, mission)
  local ours = type(mission) == 'table' and mission.missionTypeData and mission.missionTypeData.dakarEdition
  if not ours then return end
  if state == 'started' then
    if st.ourStart then
      st.ourStart = false
    else
      -- the loop was started some way we did not hold (the log shows no start request): its files are whatever an earlier
      -- start left, for another anchor, and the car is at a placeholder. Stop it and start it properly, next frame.
      ownLog('W', 'the loop started outside the Dakar start: stopping it and starting it again properly')
      st.restartAt, st.restartMission = st.clock + 0.5, mission
    end
    scenery.active, scenery.dirty = true, true
    pcall(shapeRefresh)
  elseif state == 'stopped' then
    st.ourStart = false
    scenery.active = false
    sceneryClear()
    pcall(shapeRefresh)
  end
end

-- ---------------------------------------------------------------------------------------------------------------------
-- Rebase taps

-- Phase 1 of a rebase (Earth is about to move the cars): xf is the frame change.
function M.onRebaseBegin(xf)
  st.rebase = { xf = xf, before = rallyPositions(), carBefore = playerPos() }
  ownLog('I', string.format('DAKAR-PROBE rebase #%d begins, rally positions held: %d', st.rebases + 1, (function()
    local n = 0 for _ in pairs(st.rebase.before) do n = n + 1 end return n end)()))
end

local placeTrigger       -- (below) puts the loop's start trigger on the service park in the current frame

-- Earth's own anchor commit (a teleport's or a rebase's): the world is in the new frame now.
function M.onAnchor(lat, lon)
  local geodesy = require('beamngEarth/geodesy')
  local old = st.anchor
  st.anchor = geodesy.newAnchor(lat, lon)

  local rb = st.rebase
  if rb then
    st.rebase = nil
    st.rebases = st.rebases + 1
    local after = rallyPositions()
    local car = playerPos()
    ownLog('I', string.format('DAKAR-PROBE rebase #%d committed, anchor %.5f, %.5f; car %s -> %s', st.rebases, lat, lon,
      rb.carBefore and string.format('%.1f,%.1f,%.1f', unpack(rb.carBefore)) or '?',
      car and string.format('%.1f,%.1f,%.1f', unpack(car)) or '?'))
    probeReport('stock', after, st.anchor)       -- if the rally data stayed in the old frame the residuals are the shift
    if st.adapt then
      M.adaptRacePath(rb.xf)
      probeReport('adapted', rallyPositions(), st.anchor)
    end
  elseif st.phase == 'teleporting' then
    st.phase = 'settling'
    st.launchAt = st.clock + LAUNCH_DELAY_S
    ownLog('I', string.format('anchor %.5f, %.5f after the teleport; %s in %d s', lat, lon,
      st.noLaunch and 'waiting for the roads' or 'launching', LAUNCH_DELAY_S))
  end
  if placeTrigger then placeTrigger() end
  if scenery.active then pcall(shapeRefresh) end       -- (the stages' world points are in the new frame now)
end

-- Experiment: move the live race path with Earth's transform.
function M.adaptRacePath(xf)
  local rm = gameplay_rally and gameplay_rally.getRallyManager and gameplay_rally.getRallyManager()
  local rp = rm and rm:getRacePath()
  if not rp then return end
  local function move(p)
    local x, y, z = xf:apply(p.x, p.y, p.z)
    p:set(x, y, z)
  end
  for _, sp in ipairs((rp.startPositions and rp.startPositions.sorted) or {}) do move(sp.pos) end
  for _, n in ipairs((rp.pathnodes and rp.pathnodes.sorted) or {}) do move(n.pos) end
end

function M.setAdapt(on) st.adapt = on and true or false; return st.adapt end

-- The controls (TC_in / TC_out, SS_start_line / SS_stop_control) and the race nodes are written at the terrain data's
-- height (plus the difference measured at the start): Earth has no ground far from the player and no height query
-- for a far place. Once the player is within GROUND_SNAP_M of one and Earth has the ground there, it is put on that
-- ground, in the live race path, once per anchor (GitHub #31).
local GROUND_SNAP_M = 400
local GROUND_SNAP_EVERY_S = 1.0
local GROUND_SNAP_MIN_M = 0.2
local CONTROL_NAMES = { TC_in = true, TC_out = true, SS_start_line = true, SS_stop_control = true }
local function groundSnapTick()
  if st.phase ~= 'running' or st.clock - (st.snapChecked or -10) < GROUND_SNAP_EVERY_S then return end
  st.snapChecked = st.clock
  local rm = gameplay_rally and gameplay_rally.getRallyManager and gameplay_rally.getRallyManager()
  local rp = rm and rm.getRacePath and rm:getRacePath()
  local car = playerPos()
  if not (rp and car) then return end
  if st.snapAnchor ~= st.anchor then st.snapAnchor, st.snapped = st.anchor, {} end
  local function snap(label, p)
    if not (p and p.x) or st.snapped[p] then return end
    if (p.x - car[1]) ^ 2 + (p.y - car[2]) ^ 2 > GROUND_SNAP_M * GROUND_SNAP_M then return end
    local gz = groundAt(p.x, p.y, p.z)
    if not gz then return end                      -- (no ground there yet: asked again next time)
    st.snapped[p] = true
    if math.abs(gz - p.z) >= GROUND_SNAP_MIN_M then
      ownLog('I', string.format('%s put on the ground: %.1f m -> %.1f m', label, p.z, gz))
      if p.set then p:set(p.x, p.y, gz) else p.z = gz end
    end
  end
  for _, sp in ipairs((rp.startPositions and rp.startPositions.sorted) or {}) do
    if CONTROL_NAMES[sp.name] then snap(sp.name, sp.pos) end
  end
  for i, n in ipairs((rp.pathnodes and rp.pathnodes.sorted) or {}) do snap('race node ' .. i, n.pos) end
end

-- The mission list's Start (missionScreen -> missionManager.startWithFade) for one of our loops. Its files are frame
-- numbers for an anchor that is only known where the player is, and the placeholder start sits under the ground, so
-- the start is held: the player is not moved (they drive to the service park). Within START_RADIUS_M of it the route
-- is written for the current anchor and the original start runs with exactly what the list passed (the settings the
-- player picked, the car included); farther away it says where the service park is and does nothing else.
-- Returns true when it holds the call.
function M.interceptStart(mission, userSettings, startingOptions)
  if st.passThrough or type(mission) ~= 'table' then return false end
  local edition = mission.missionTypeData and mission.missionTypeData.dakarEdition
  if not edition then return false end
  ownLog('I', 'start requested for ' .. tostring(mission.id))
  if st.menuStart or st.phase == 'teleporting' or st.phase == 'settling' or st.phase == 'roads' or st.phase == 'preload' then  -- (a click while we work)
    tell('Dakar: the loop is being prepared and starts by itself in a moment.')
    return true
  end
  local ok, err = ensureEdition(edition)
  if not ok then tell('Dakar: ' .. tostring(err)); return true end
  local where = whereIsTheStart()
  if not where then tell('Dakar: the player position is not known yet; try again in a moment.'); return true end
  if where.metres > START_RADIUS_M then
    local gps = earthapi.gpsSet(where.lat, where.lon, st.edition.title or edition)
    st.startGps = gps and { where.lat, where.lon } or nil
    tell(string.format('%s starts at its service park (%.4f, %.4f), %.1f km %s of you. %sDrive there and start it ' ..
      'again (the BeamNG.Dakar window can teleport you for testing).', st.edition.title or edition, where.lat, where.lon,
      where.metres / 1000, where.way, gps and 'Earth\'s GPS leads there. ' or ''))
    return true
  end
  clearStartGps()
  local es = earthState()
  local a = es and es.anchor
  if a and not (st.anchor and st.anchor.lat == a.lat and st.anchor.lon == a.lon) then st.anchor = a end
  if not st.anchor then tell('Dakar: the Earth anchor is not known yet; try again in a moment.'); return true end
  ownLog('I', 'mission list start of ' .. tostring(mission.id) .. ' at the service park: writing the route for the current anchor')
  st.pending = { mission = mission, userSettings = userSettings, startingOptions = startingOptions }
  holdForRoads(function() M.launch() end)
  return true
end

-- The main menu's mission list (missionTiles -> missionManager.startWithLoadingLevel) for one of our loops. The stock
-- way loads the level at the level select's spawn (Earth's random place, another continent), spawns the mission car at
-- (0, 0) before any ground is there and runs the whole loop in that spot: no car, no terrain, a stopped mission
-- (GitHub #23). Instead: the level loads as freeroam (as its own tile does, with the spawn point the player picked),
-- and once Earth has its first anchor and the player, the start teleports to the service park and starts the loop
-- there (M.start; the list's settings, nil from the menu: the mission's defaults). Returns true when it holds the call.
function M.interceptLevelStart(mission, userSettings, level, startedFrom)
  if st.passThrough or type(mission) ~= 'table' then return false end
  local edition = mission.missionTypeData and mission.missionTypeData.dakarEdition
  if not edition then return false end
  local ok, err = ensureEdition(edition)
  if not ok then ownLog('E', 'menu start of ' .. tostring(mission.id) .. ': ' .. tostring(err)); return false end
  if st.menuStart then return true end                                  -- (one is being prepared already)
  local name = level or (mission.startTrigger and mission.startTrigger.level) or 'beamng_earth'
  local okL, current = pcall(function() return getCurrentLevelIdentifier and getCurrentLevelIdentifier() end)
  local fr = rawget(_G, 'freeroam_freeroam')
  if not (okL and current == name) and not (fr and fr.startFreeroamByName) then
    ownLog('E', 'menu start of ' .. tostring(mission.id) .. ': freeroam_freeroam is not loaded; the stock start runs')
    return false
  end
  holdLoadingScreen()                         -- (first: the level's own loading screen ends inside ours)
  st.menuStart = { mission = mission, userSettings = userSettings, edition = edition, since = st.clock,
    startingOptions = startedFrom and { startedFrom = startedFrom } or nil }
  ownLog('I', 'menu start of ' .. tostring(mission.id) .. ': loading ' .. name .. ' behind the loading screen, then the ' ..
    'service park')
  if not (okL and current == name) then
    -- (startFreeroamByName saves the spawn point it is given as the level's default: the one already picked, kept)
    local li = package.loaded['beamngEarth/levelinfo']
    local okS, sp = pcall(function() return li and li.chosen and li.chosen() end)
    local okF, started = pcall(fr.startFreeroamByName, name, okS and sp or nil)
    if not (okF and started) then
      st.menuStart = nil
      releaseLoadingScreen('the level did not start')
      ownLog('E', 'menu start of ' .. tostring(mission.id) .. ': the level ' .. name .. ' did not start (' ..
        tostring(okF and 'not found' or started) .. '); the stock start runs')
      return false
    end
  end
  return true
end

-- A held menu start (interceptLevelStart), each frame: once the Earth level is in (its first anchor committed, the
-- player known, no anchor request or random place pending), teleport to the service park and start the loop there.
local function menuStartTick()
  local ms = st.menuStart
  if not ms then return end
  local es = earthState()
  local pl = es and es.player
  if es and es.inOurLevel then ms.levelSeen = true end
  -- (earthapi.ready: with Earth 1.9.0's API, its own check that a teleport is taken: the ground under the player is in)
  if es and es.inOurLevel and es.anchor and pl and pl.lat and not es.pending and not es.randomStart and st.phase == 'idle'
      and earthapi.ready() then
    st.menuStart = nil
    st.pending = { mission = ms.mission, userSettings = ms.userSettings, startingOptions = ms.startingOptions }
    ownLog('I', 'the Earth level is in: teleporting to the service park and starting the loop there')
    M.start(ms.edition)
    if st.phase ~= 'teleporting' then releaseLoadingScreen('the teleport did not start') end
  elseif st.clock - ms.since > MENU_START_WAIT_S then
    st.menuStart = nil
    releaseLoadingScreen('the Earth level did not come up')
    ownLog('W', string.format('menu start given up: the Earth level was not in after %d s', MENU_START_WAIT_S))
    tell('Dakar: the Earth level did not come up (is the companion server running?); start the loop again from the ' ..
      'mission list or the BeamNG.Dakar window.')
  end
end
-- The small window (on the Earth level): Start, Abandon, the experiment switch, and what is going on.
local im
local adaptPtr, shapePtr

-- Where the car is (GitHub #72): sampled every hud.PERIOD_S, logged, and drawn top right every frame (an imgui window is
-- drawn each frame or it is gone). The lines are the curated stages and liaisons, as the shaping numbers them.
local function hudLines()
  if not (st.anchor and st.edition and st.geometry) then return nil end
  local out = {}
  for _, leg in ipairs(st.edition.legs) do
    local r = curatedLines()[leg.id]
    if r then out[#out + 1] = { id = leg.id, pts = legs.toWorld(r.points, st.anchor, st.bias) } end
  end
  return out
end

local function hudTick()
  if st.clock - (st.hudAt or -10) < hud.PERIOD_S then return end
  -- (not while the loop is being put down: the car is moved and the ground asked for at TC0 then; the last readout stays)
  if st.phase ~= 'idle' and st.phase ~= 'running' then return end
  st.hudAt = st.clock
  local ok, level = pcall(function() return getCurrentLevelIdentifier and getCurrentLevelIdentifier() end)
  if not (ok and level == 'beamng_earth') then st.hudText = nil; return end
  local veh = getPlayerVehicle and getPlayerVehicle(0)
  if not veh then st.hudText = nil; return end
  local p = veh:getPosition()
  local v = veh.getVelocity and veh:getVelocity()
  local lat, lon = earthapi.position()
  local s = { x = p.x, y = p.y, z = p.z, lat = lat, lon = lon, ground = groundAt(p.x, p.y, p.z),
    speed = v and v:length() or 0, line = hud.nearest(hudLines(), p.x, p.y) }
  st.hudText = hud.text(s)
  ownLog('I', hud.logLine(s))
end

local function drawHud()
  if not st.hudText then return end
  im = im or rawget(_G, 'ui_imgui')
  if not im then return end
  local w = 1920
  local okW, size = pcall(function() return im.GetIO().DisplaySize end)
  if okW and size and size.x and size.x > 0 then w = size.x end
  im.SetNextWindowPos(im.ImVec2(w - 12, 12), im.Cond_Always, im.ImVec2(1, 0))
  im.SetNextWindowBgAlpha(0.6)
  local flags = im.WindowFlags_NoTitleBar + im.WindowFlags_AlwaysAutoResize + im.WindowFlags_NoInputs
    + im.WindowFlags_NoSavedSettings + im.WindowFlags_NoFocusOnAppearing
  if im.Begin('BeamNG.Dakar position', nil, flags) then
    for _, line in ipairs(st.hudText) do im.Text(line) end
  end
  im.End()
end
local function drawWindow()
  im = im or rawget(_G, 'ui_imgui')
  if not im then return end
  local ok, level = pcall(function() return getCurrentLevelIdentifier and getCurrentLevelIdentifier() end)
  if ok and level and level ~= 'beamng_earth' then return end
  adaptPtr = adaptPtr or im.BoolPtr(st.adapt)
  im.SetNextWindowSize(im.ImVec2(330, 0), im.Cond_FirstUseEver)
  im.SetNextWindowPos(im.ImVec2(20, 120), im.Cond_FirstUseEver)
  if im.Begin('BeamNG.Dakar') then
    im.Text('State: ' .. st.phase .. '   rebases seen: ' .. st.rebases)
    if st.anchor then im.Text(string.format('Anchor: %.4f, %.4f', st.anchor.lat, st.anchor.lon)) end
    local n = 0
    for _ in pairs(scenery.objs) do n = n + 1 end
    im.Text('Props standing: ' .. n .. (scenery.bias and string.format('   ground offset %.1f m', scenery.bias) or ''))
    if st.phase == 'idle' or st.phase == 'running' then
      -- the edition the service park and the buttons are for (GitHub #40); not changed under a loop that is on
      local ids = editionIds()
      if #ids > 1 and not scenery.active then
        for _, id in ipairs(ids) do
          local c = loadCached(id)
          if c and im.Button(((st.edition and st.edition.edition == id) and '> ' or '') .. (c.edition.title or id)) then ensureEdition(id) end
        end
      end
      local where = whereIsTheStart()
      if where then
        im.Text(string.format('Service park: %.4f, %.4f', where.lat, where.lon))
        im.Text(where.metres < 1000 and string.format('%.0f m %s of you', where.metres, where.way)
          or string.format('%.1f km %s of you', where.metres / 1000, where.way))
      end
      local ed = st.edition
      if ed then
        im.Text('Drive there, then start ' .. (ed.title or ed.edition) .. ' from the mission list.')
        if im.Button('Teleport to the service park') then M.start(ed.edition, true) end
        if im.Button('Teleport there and start') then
          st.pending = nil
          M.start(ed.edition)
        end
      end
    elseif st.phase == 'roads' and st.roadsWait then
      im.Text(string.format('Waiting for the roads around you... %.0f s', st.clock - st.roadsWait.since))
      if im.Button('Do not wait') then endRoadsWait('skipped from the window') end
    elseif st.phase == 'preload' then
      local ps = st.preload and st.preload:state()
      im.Text(ps and string.format('Fetching the stage corridor (%s): %d of %d', ps.phase, ps.done, ps.total) or 'Fetching the stage corridor...')
    else
      im.Text('Working...')
    end
    if im.Button('Stop the running mission') then
      M.stopForeground()
      M.cancelPreloadGate('stopped from the window')
      st.phase = 'idle'
      st.pending = nil
    end
    adaptPtr[0] = st.adapt
    if im.Checkbox('Move rally data at rebases (experiment)', adaptPtr) then st.adapt = adaptPtr[0] end
    -- the transient ground shaping along the stages (shape.lua, GitHub #41): off puts the original ground back
    shapePtr = shapePtr or im.BoolPtr(st.shapeOn)
    shapePtr[0] = st.shapeOn
    if im.Checkbox('Shape the ground along the stages', shapePtr) then M.setShaping(shapePtr[0]) end
    im.Text(shapeStatusText())
    local ps = st.preload and st.preload:state()
    if ps and st.phase ~= 'preload' then
      im.Text(string.format('Stage corridor %s: %s%s', ps.name, ps.phase, ps.total > 0 and string.format(' (%d of %d)', ps.done, ps.total) or ''))
    end
  end
  im.End()
end

-- ---------------------------------------------------------------------------------------------------------------------
-- Starting a loop

local function loopMissionId(edition) return 'beamng_earth/rallyLoop/' .. (edition.missionPrefix or 'dakar') end

-- A mission already running (an earlier test) must go first: the manager refuses to start one that is ongoing.
-- (attemptAbandonMissionWithFade takes the mission object; this stops whatever is in the foreground at once.)
local function stopForeground()
  local mm = gameplay_missions_missionManager
  if mm and mm.getForegroundMissionId and mm.getForegroundMissionId() and mm.stopForegroundMissionInstantly then
    ownLog('I', 'stopping the running mission ' .. tostring(mm.getForegroundMissionId()))
    mm.stopForegroundMissionInstantly()
    return true
  end
  return false
end
M.stopForeground = stopForeground

local function launch()
  ensureDrivelineHeights()                     -- (before the files are written: the game reads them when the loop starts)
  -- The route (race path, driveline, time controls) is written at the height of the ground the game has: the terrain data
  -- differs from it by a few metres (a start 3 m under the ground was seen), so the difference at the first point is
  -- measured here and added to every height of the loop.
  local first = legPoints(st.edition.legs[1])[1]
  local x0, y0, z0 = st.anchor:geodeticToWorld(first[1], first[2], first[3])
  local gz0 = groundAt(x0, y0, z0)
  local bias = 0
  if gz0 then
    bias = gz0 - z0
    ownLog('I', string.format('DAKAR-PROBE ground at the start: terrain data %.1f m, game ground %.1f m, the route moves %.1f m', z0, gz0, bias))
  else
    ownLog('W', 'no ground found under the start point (the terrain is not loaded there yet); the route stays at the terrain data height')
  end
  st.bias = bias                               -- (the route's heights only: the ground is Earth's own, shaped only at a defect, GitHub #63)
  -- the curated stages (GitHub #41): what was found and changed, per special
  local curated = curatedLines()
  local c = st.loaded[st.edition.edition]
  for _, leg in ipairs(st.edition.legs) do
    if c and c.curatedSummary and c.curatedSummary[leg.id] then ownLog('I', 'stage curation: ' .. c.curatedSummary[leg.id]) end
  end
  local res, err = legs.materialize(st.edition, st.geometry, st.anchor, missionDirOf, bias, curated)
  if not res then return ownLog('E', 'materialize failed: ' .. tostring(err)) end
  for path, data in pairs(res.files) do
    if not jsonWriteFile(path, data, true) then return ownLog('E', 'could not write ' .. path) end
  end
  stopForeground()
  local id = loopMissionId(st.edition)
  local mission = gameplay_missions_missions.getMissionById(id)
  if not mission then return ownLog('E', 'mission ' .. id .. ' not found (is the mod installed?)') end
  -- the loop's own positions (the service stall, the start trigger) are world numbers in the current frame too; the car is
  -- put there by the mission, half a metre above the ground (the route already lies on it).
  local pl = res.placement
  -- (the car starts in the parking lot with TC0 a few metres ahead of it, GitHub #55; without a lot the stall stands back
  -- from TC0, GitHub #27: on the game's ground there, else at the height of the data)
  local gzStall = groundAt(pl.pos[1], pl.pos[2], pl.pos[3])
  ownLog('I', string.format('%s %.0f m before TC0, ground %s', pl.inLot and 'the car starts in the lot,' or 'service stall',
    pl.backM or 0, gzStall and string.format('%.1f m', gzStall) or 'not found (height of the data)'))
  -- every control's heading against the road's, the joins and the spawn (GitHub #55)
  for _, line in ipairs(legs.probeLines(res)) do ownLog('I', line) end
  pl.pos = { pl.pos[1], pl.pos[2], (gzStall or pl.pos[3]) + 0.5 }
  mission.missionTypeData.serviceStallPos = pl.pos
  mission.missionTypeData.serviceStallRot = pl.rot
  mission.startTrigger.pos, mission.startTrigger.rot = pl.pos, pl.rot
  if mission.fgVariables then
    mission.fgVariables.serviceStallPos, mission.fgVariables.serviceStallRot = pl.pos, pl.rot
  end
  st.phase = 'running'
  ownLog('I', string.format('starting %s (%d legs, anchor %.5f, %.5f)', id, #st.edition.legs, st.anchor.lat, st.anchor.lon))
  -- Started from the mission list: its own arguments, as held (the player's settings, the car). Started from our
  -- window or the console: the mission's default user settings, as the list would pass them (they pick the rally car
  -- from its vehicle list, the voice pack, the penalties); without them the player's own car stays in the field.
  local pending = st.pending
  st.pending = nil
  local settings = pending and pending.userSettings
  if not settings then
    settings = {}
    for k, v in pairs(mission.defaultUserSettings or {}) do settings[k] = v end
  end
  st.ourStart = true                          -- (onAnyMissionChanged: a start that is not ours gets the safety net)
  st.passThrough = true                       -- our own tap lets this one through
  releaseLoadingScreen('the loop starts (its own fade takes over)')
  local ok, err = pcall(gameplay_missions_missionManager.startWithFade, mission, settings, pending and pending.startingOptions)
  st.passThrough = false
  if not ok then ownLog('E', 'the mission manager refused the start: ' .. tostring(err)) end
end

-- The route for the current anchor, then the game's start.
function M.runLaunch()
  local ok, err = pcall(launch)
  if not ok then ownLog('E', 'launch failed: ' .. tostring(err)) end
  releaseLoadingScreen('the launch ended without starting the loop')     -- (no-op when launch let it go)
end

-- The corridor prefetch of the edition's special stages (preload.lua, GitHub #41): Earth's server fetches the whole
-- corridor of a start-to-finish route into its caches. The stages are asked for one after the other (a newer route
-- supersedes the older on the server). Returns true while the first stage's corridor is not in.
local function startPreload()
  local reqs = {}
  for _, leg in ipairs(st.edition.legs) do
    if leg.type == 'special' then
      local pts = legPoints(leg)
      reqs[#reqs + 1] = { name = leg.id, lat0 = pts[1][1], lon0 = pts[1][2], lat1 = pts[#pts][1], lon1 = pts[#pts][2], line = pts }
    end
  end
  if #reqs == 0 then return false end
  st.preload = preload.new({
    send = function(msg)
      local net = earthModule('net')
      return net and net.isConnected and net.isConnected() and net.send and net.send(msg) and true or false
    end,
    take = function(id)
      local earth = rawget(_G, 'beamngEarth_core')
      return earth and earth.takeRoute and earth.takeRoute(id) or nil
    end,
    now = function() return st.clock end,
    log = ownLog,
  })
  st.preload:start(reqs)
  return not st.preload:gateOpen()
end

-- The mission list's start, at the service park (interceptStart): ask Earth's server for the stages' corridors; a menu
-- start (the game's loading screen is up under our tag) waits for the first stage's corridor to be in, for the cap at
-- most, then writes the route and starts the loop. An in-game start does not wait: the download runs behind it.
function M.launch()
  local okC, errC = pcall(curatedLines)
  if not okC then ownLog('E', 'stage curation failed: ' .. tostring(errC)) end
  local okP, hold = pcall(startPreload)
  if not okP then ownLog('E', 'stage preload failed: ' .. tostring(hold)); hold = false end
  if hold and st.loadingSince then
    st.preloadGate = { since = st.clock }
    st.phase = 'preload'
    ownLog('I', string.format('the loading screen stays up until the stage corridor is fetched (at most %d s)', preload.CAP_S))
    return
  end
  if hold then ownLog('I', 'the stage corridor is fetched in the background (no loading screen to hold)') end
  M.runLaunch()
end

-- The loading-screen gate, each frame: the answers and progress, and the launch once the first stage is in (or gave up).
local function preloadTick()
  local p = st.preload
  if not p then return end
  p:tick()
  if st.preloadGate and p:gateOpen() then
    st.preloadGate = nil
    ownLog('I', 'the stage corridor is settled: launching')
    M.runLaunch()
  end
end

-- Earth's route_download push (tapped: gps.onRouteDownload)
function M.onRouteDownload(msg)
  if st.preload then st.preload:onDownload(msg) end
end

-- Give up a launch that waits for the preload (the window's stop, the level ending).
local function cancelPreloadGate(why)
  if not st.preloadGate then return end
  st.preloadGate, st.pending = nil, nil
  if st.phase == 'preload' then st.phase = 'idle' end
  releaseLoadingScreen(why)
end
M.cancelPreloadGate = cancelPreloadGate

-- Teleport to the edition's service park (Earth picks the anchor) and, unless noLaunch, start the loop there.
function M.start(editionId, noLaunch)
  if not ensureTaps() then return ownLog('E', 'BeamNG.Earth is not loaded (or has changed): taps missing') end
  local ok, err = ensureEdition(editionId or DEFAULT_EDITION)
  if not ok then return ownLog('E', err) end
  local edition = st.edition
  local lat, lon = servicePark()
  ownLog('I', string.format('teleporting to %s (%.5f, %.5f)', edition.title or edition.edition, lat, lon))
  st.phase, st.noLaunch = 'teleporting', noLaunch and true or false
  local took, why = earthapi.teleport(lat, lon, 'Dakar: ' .. (edition.title or edition.edition))
  if not took then
    st.phase = 'idle'
    return tell('Dakar: Earth would not teleport: ' .. tostring(why))
  end
  clearStartGps()
end

-- A mission's start moved. The game copies startTrigger.pos into its locations cache the first time it reads a mission
-- (missions.lua getLocations), and the marker, the big map and the start check all read that copy: moving startTrigger
-- alone left them where info.json put them, 0, 0, 0 (GitHub #1). The copy moves too. The mission is tagged with the frame
-- it was placed in: a mission object without the tag is one the game has built again from info.json (missionsTick).
local function moveStart(mission, x, y, z)
  mission.startTrigger.pos = { x, y, z }
  mission.dakarFrame = st.anchor
  local get = gameplay_missions_missions.getLocations
  local ok, locs = false, nil
  if get then ok, locs = pcall(get, mission) end
  for _, l in ipairs(ok and type(locs) == 'table' and locs or {}) do
    if l.type == 'coordinates' then l.pos = vec3(x, y, z) end
  end
end

-- The loops' start triggers (where the game puts a mission's marker) stand on their service parks in the current frame,
-- so they move with every anchor change; not while a loop runs (launch put its trigger on the real ground).
placeTrigger = function()
  if st.phase == 'running' or not (st.anchor and gameplay_missions_missions) then return end
  -- every edition's loop has its marker on its own service park, not only the one in use (GitHub #40)
  for _, id in ipairs(editionIds()) do
    local c = loadCached(id)
    local mission = c and gameplay_missions_missions.getMissionById(loopMissionId(c.edition))
    if mission and mission.startTrigger then
      local lat, lon, h = servicePark(c.edition, c.geometry)
      moveStart(mission, st.anchor:geodeticToWorld(lat, lon, h))
    end
    -- every leg's own mission (a liaison or a special stage) has its marker on the time control that opens it, the leg's
    -- first point (TC0 for the first liaison, TC1 for SS1, ...); their info.json says 0, 0, 0, the world's origin (GitHub #76)
    for _, leg in ipairs(c and c.edition.legs or {}) do
      local g = c.geometry[leg.id]
      local p = g and g.points and g.points[1]
      local lm = p and gameplay_missions_missions.getMissionById('beamng_earth/' .. missionType(leg) .. '/' .. missionName(c.edition, leg))
      if lm and lm.startTrigger then
        moveStart(lm, st.anchor:geodeticToWorld(p[1], p[2], p[3]))
      end
    end
  end
  -- the markers and the big map are drawn from the game's POI list: gathered again (as Earth does for its gas stations)
  if gameplay_rawPois and gameplay_rawPois.clear then gameplay_rawPois.clear() end
end

-- Every few seconds: the game's mission list is read once, and cleared only when the mod manager is ready, so an add-on
-- installed while the game runs (BeamNG.Earth 1.9.0's Add-ons page) had no activities until Ctrl+L (GitHub #1). Our loop
-- missing from it clears it, once (not while a mission runs: that holds its own mission object). And whenever the game
-- has built its mission objects again (a reload of the list), they came from info.json: the markers are placed again.
local function missionsTick()
  local mm = gameplay_missions_missions
  if not (mm and mm.getMissionById) then return end
  local loopId
  for _, id in ipairs(editionIds()) do
    local c = loadCached(id)
    if c then loopId = loopMissionId(c.edition); break end
  end
  if not loopId then return end
  local mission = mm.getMissionById(loopId)
  local mgr = gameplay_missions_missionManager
  local busy = mgr and mgr.getForegroundMissionId and mgr.getForegroundMissionId()
  if not mission and not st.missionListRead and mm.clearCache and not busy then
    st.missionListRead = true
    mm.clearCache()
    if gameplay_rawPois and gameplay_rawPois.clear then gameplay_rawPois.clear() end
    mission = mm.getMissionById(loopId)
    ownLog('I', 'the game\'s mission list had no Dakar missions (installed while the game ran): read again, '
      .. (mission and 'found' or 'still missing'))
  end
  if mission and st.anchor and mission.dakarFrame ~= st.anchor then placeTrigger() end
end

function M.status()
  return dumps({ phase = st.phase, edition = st.edition and st.edition.edition, rebases = st.rebases, adapt = st.adapt,
    shaping = shaper and shaper:status() or nil, preload = st.preload and st.preload:state() or nil,
    anchor = st.anchor and { st.anchor.lat, st.anchor.lon } or nil })
end

-- ---------------------------------------------------------------------------------------------------------------------
-- Extension hooks

function M.onUpdate(dt)
  st.clock = st.clock + dt
  pcall(drawWindow)
  pcall(drawHud)
  local ok, err = pcall(sceneryTick)
  if not ok and not st.sceneryWarned then st.sceneryWarned = true; ownLog('E', 'scenery failed: ' .. tostring(err)) end
  if st.clock - (st.tapCheck or -10) > 2 then       -- Earth may reload its modules: tap again when it does
    st.tapCheck = st.clock
    ensureTaps()
    if not st.edition then ensureEdition(DEFAULT_EDITION) end      -- (the window needs one)
    if not st.optionsRead then
      local values = earthapi.options()
      if values then st.optionsRead = true; applyOptions(values) end
    end
    if not st.anchor then                                            -- (Earth's first anchor was set before we loaded)
      local es = earthState()
      if es and es.anchor then st.anchor = es.anchor; placeTrigger() end
    end
    local okT, errT = pcall(missionsTick)
    if not okT and not st.missionsWarned then st.missionsWarned = true; ownLog('E', 'mission markers failed: ' .. tostring(errT)) end
  end
  local okH, errH = pcall(shapeTick)
  if not okH and not st.shapeWarned then st.shapeWarned = true; ownLog('E', 'ground shaping failed: ' .. tostring(errH)) end
  local okP, errP = pcall(preloadTick)
  if not okP then
    st.preloadGate, st.preload = nil, nil
    if st.phase == 'preload' then st.phase = 'idle' end
    releaseLoadingScreen('the stage preload failed')
    ownLog('E', 'stage preload failed: ' .. tostring(errP))
  end
  local okG, errG = pcall(groundSnapTick)
  if not okG and not st.snapWarned then st.snapWarned = true; ownLog('E', 'ground snap failed: ' .. tostring(errG)) end
  -- (after the snap: the readout only reads the ground and the car)
  local okW, errW = pcall(hudTick)
  if not okW and not st.hudWarned then st.hudWarned = true; ownLog('E', 'position readout failed: ' .. tostring(errW)) end
  local okM, errM = pcall(menuStartTick)
  if not okM then st.menuStart = nil; releaseLoadingScreen('the menu start failed'); ownLog('E', 'menu start failed: ' .. tostring(errM)) end
  if st.loadingSince and st.clock - st.loadingSince > LOADING_MAX_S then releaseLoadingScreen('held too long (watchdog)') end
  if st.phase == 'settling' and st.launchAt and st.clock >= st.launchAt then
    st.launchAt = nil
    local noLaunch = st.noLaunch
    st.noLaunch = false
    holdForRoads(function() if not noLaunch then M.launch() end end)
    if st.phase == 'settling' then st.phase = 'idle'; st.pending = nil end     -- (launch gave up: the log says why)
  end
  if st.restartAt and st.clock >= st.restartAt then
    local mission = st.restartMission
    if not st.restartStopped then
      st.restartStopped = true
      pcall(M.stopForeground)
      st.phase = 'idle'
      -- A start from the main menu loads the level through the manager's task chain; the loading screen is taken down by
      -- the mission's own flowgraph (fadeFromBlack / hideLoadingScreen), which stopping the mission skipped: it stayed up
      -- over a running level (GitHub #19). Taking it down is what the flowgraph would have done (a no-op when it is gone).
      if server and server.fadeoutLoadingScreen then
        local ok, err = pcall(server.fadeoutLoadingScreen, true)
        ownLog('I', 'loading screen taken down' .. (ok and '' or (': ' .. tostring(err))))
      end
    end
    -- Earth does not know where the player is until its first anchor (some seconds after the level): asking too early
    -- gave up for good; ask again until it does.
    if mission and not whereIsTheStart() and (st.restartTries or 0) < 60 then
      st.restartTries = (st.restartTries or 0) + 1
      st.restartAt = st.clock + 1.0
    else
      st.restartAt, st.restartMission, st.restartStopped, st.restartTries = nil, nil, nil, nil
      -- Stopping the stray loop let go of Earth's drop hold while the ground was still being built: the player fell through
      -- it (GitHub #21). Earth's own teleport to where the player is holds and grounds it again.
      local plLat, plLon = earthapi.position()
      if mission and plLat then
        ownLog('I', 'the player put on the ground again (Earth teleport to where it is)')
        pcall(earthapi.teleport, plLat, plLon, 'Dakar: back on the ground', true)      -- (silent: not in the recent places)
      end
      if mission then
        ownLog('I', 'restarting the loop through the Dakar start')
        M.interceptStart(mission, nil, nil)
      end
    end
  end
  local w = st.roadsWait
  if w then
    local sh = earthModule('simhold')
    if w.paused and sh and sh.paused and not sh.paused() then
      endRoadsWait('the player resumed the game')
    elseif roadsAround() then
      endRoadsWait('the roads are in')
    elseif st.clock - w.since > ROADS_WAIT_MAX_S then
      endRoadsWait('gave up after ' .. ROADS_WAIT_MAX_S .. ' s')
    end
  end
end

function M.onExtensionLoaded()
  ensureTaps()
  ownLog('I', 'loaded')
end

-- The level ends (the player quit to the menu) while a menu start holds the loading screen: our tag must not outlive it
-- (the next level would never show). Not the level the menu start itself replaces (before ours was seen).
function M.onClientEndMission()
  if st.loadingSince and not (st.menuStart and not st.menuStart.levelSeen) then
    st.menuStart, st.pending = nil, nil
    cancelPreloadGate('the level ended')
    releaseLoadingScreen('the level ended')
  end
end

function M.onExtensionUnloaded()
  releaseLoadingScreen('Dakar unloaded')
  sceneryClear()
  if shaper then                                 -- (the ground back, at once: nothing of ours is left to do it later)
    pcall(function() shaper:stop() end)
    for _ = 1, 400 do
      if not shaper:busy() then break end
      shaper:update(50, true)
    end
  end
  for _, t in ipairs(TAPS) do removeTap(t) end
  removeDrivelineHeights()
end

return M
