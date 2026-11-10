-- BeamNG.Dakar's extension (beamngDakar_core): runs an edition's legs as BeamNG rally loop missions on the
-- BeamNG.Earth level.
--
-- It loads after BeamNG.Earth and changes nothing in it. It taps functions Earth calls through module tables:
--   beamngEarth/rebase.moveVehicles(xf, geodesy)   phase 1 of a rebase: the cars move, xf is the whole frame change
--   beamngEarth/mpanchor.announceOwn(lat, lon)     every anchor commit (teleport or rebase), after the world is in place
--   beamngEarth/terrain.load / .swap               a new terrain block is in (the ground shaping, shape.lua)
--   beamngEarth/gps.onRouteDownload(msg)           the server's corridor prefetch progress (preload.lua)
-- Each tap calls the original first/last and never lets an error of ours into Earth's rebase.
-- What Earth 1.9.0's modding API (beamngEarth_api) offers goes through it instead (earthapi.lua): the
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
local guide = require('beamngDakar/guide')
local days = require('beamngDakar/days')

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
  -- gone, and the stage corridor is shaped again on the new
  -- (optional: without them the loop runs as before, no shaping and no preload progress)
  { module = 'beamngEarth/terrain',  fn = 'load', result = 'onTerrainLoaded', optional = true },
  { module = 'beamngEarth/terrain',  fn = 'swap', result = 'onTerrainSwapped', optional = true },
  -- the corridor prefetch's progress, which Earth shows on its map
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
  loaded = {},              -- edition id -> { edition, geometry, closures }: every edition read so far
  markersWaiting = {},      -- mission markers whose ground was not loaded when they were placed (placeOnGround)
  phase = 'idle',           -- idle | teleporting | settling | running
  launchAt = nil,
  rebase = nil,             -- { xf, before } between phase 1 and the anchor commit of a rebase
  adapt = false,
  shapeOn = true,           -- the transient ground shaping along the stages (the window's switch)
  rebases = 0,
  bias = 0,                -- the metres the loop's heights are moved to the game's ground, measured at the last launch
  preloadGate = nil,        -- { since }: a menu start's launch waits for the stage corridor prefetch
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

-- The driveline's heights. The game builds the road line the loop measures and tracks from drivelineSpline.json
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

-- The edition's legs.json, read once ("<edition>" is the folder; a day's id "<edition>/<day id>" names the same folder).
local function readBase(id)
  local baseId = days.splitId(id)
  st.bases = st.bases or {}
  if st.bases[baseId] == nil then st.bases[baseId] = jsonReadFile(EDITIONS_DIR .. baseId .. '/legs.json') or false end
  return st.bases[baseId] or nil, baseId
end

-- The geometry of an edition of days, a leg's file read the first time it is asked for: the 2027 route's files are tens of
-- megabytes in all, and the window, the mission list and the start triggers of every day only need where a leg starts
-- (index.json, tools/fetch_route.py). Only the legs the day runs have a file to read.
local function lazyGeometry(dir, edition)
  local own = {}
  for _, leg in ipairs(edition.legs) do own[leg.id] = true end
  return setmetatable({}, { __index = function(t, id)
    if not own[id] then return nil end
    local g = jsonReadFile(dir .. 'geometry/' .. id .. '.json')
    if g then rawset(t, id, g) end
    return g
  end })
end

local function loadEdition(id)
  local base, baseId = readBase(id)
  local dir = EDITIONS_DIR .. baseId .. '/'
  if not base then return nil, 'no ' .. dir .. 'legs.json (run tools/install.py)' end
  local edition, lazy = base, false
  local _, dayId = days.splitId(id)
  if base.days then
    -- an edition of days: the view of one day (its loop), not the whole route
    edition = dayId and days.find(base, id) or nil
    if not edition then return nil, id .. ' is not a day of ' .. baseId end
    if edition.kind == 'rest' then return nil, edition.label .. ' is a rest day: nothing to drive' end
    lazy = true
  end
  local geometry, index = {}, nil
  if lazy then
    geometry = lazyGeometry(dir, edition)
    index = jsonReadFile(dir .. 'index.json') or nil
    for _, leg in ipairs(edition.legs) do
      -- (the index says a leg has its file; without one the file is read here to know it is there, and let go again)
      local there = index and index.legs and index.legs[leg.id] or (not index and geometry[leg.id])
      if not there then return nil, 'no geometry for leg ' .. leg.id .. ' (run tools/fetch_route.py)' end
    end
    if not index then for k in pairs(geometry) do rawset(geometry, k, nil) end end
  else
    for _, leg in ipairs(edition.legs) do
      local g = jsonReadFile(dir .. 'geometry/' .. leg.id .. '.json')
      if not g then return nil, 'no geometry for leg ' .. leg.id .. ' (run tools/fetch_route.py)' end
      geometry[leg.id] = g
    end
  end
  -- where the side roads of a special stage are closed off (tools/stage_closures.py); optional: an edition
  -- without the files just has no closures
  local closures = {}
  for _, leg in ipairs(edition.legs) do
    if leg.type == 'special' then closures[leg.id] = jsonReadFile(dir .. 'closures/' .. leg.id .. '.json') end
  end
  return edition, geometry, closures, index, lazy and dir or nil
end

-- An edition is read once and kept: the mission list, the window and the start triggers of every edition's loop ask for
-- them again and again (a second edition must not cost the first its start marker). A day's geometry is let go when
-- another day becomes the current one (unloadDays).
local function loadCached(id)
  local c = st.loaded[id]
  if c then return c end
  local edition, geometry, closures, index, dir = loadEdition(id)
  if not edition then return nil, geometry end
  c = { edition = edition, geometry = geometry, closures = closures, index = index or nil, dir = dir }
  st.loaded[id] = c
  return c
end

-- Where a leg starts ({ lat, lon, h }): the index's, else the geometry's first point.
local function legStart(c, leg)
  local ix = c.index and c.index.legs and c.index.legs[leg.id]
  if ix and ix.start then return ix.start end
  local g = c.geometry[leg.id]
  return g and g.points and g.points[1] or nil
end

-- The geometry (and the curated lines) of the days other than `keep` are let go: a day's legs are read again when it is the current
-- one. One-loop editions keep theirs.
local function unloadDays(keep)
  for id, c in pairs(st.loaded) do
    if c.dir and id ~= keep then
      c.geometry = lazyGeometry(c.dir, c.edition)
      c.curated, c.curatedSummary = nil, nil
    end
  end
end

-- The day after edition view `ed` and the rest days between them (days.following); nil for a one-loop edition and the last day.
local function dayAfter(ed)
  local base = ed and ed.baseEdition and readBase(ed.edition)
  local views = base and days.expand(base)
  if not (views and ed.index and views[ed.index]) then return nil end
  return days.following(views, views[ed.index])
end

-- The curated line of each special stage of the current edition (curate.lua), made once per edition: the
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
  unloadDays(id)
  st.edition, st.geometry, st.closures, st.index = c.edition, c.geometry, c.closures, c.index
  return true
end

-- The ids of the installed editions (the folders of EDITIONS_DIR with a legs.json), found once: an edition of days is one id per
-- day that has a loop ("<edition>/<day id>"). When the game cannot list the folder, the editions already read and the default one.
local function editionIds()
  if st.editionIds then return st.editionIds end
  local ids, seen = {}, {}
  local function add(id)
    if not id then return end
    local base, baseId = readBase(id)
    if not base then return end
    if base.days then
      for _, v in ipairs(days.expand(base)) do
        if v.kind ~= 'rest' and not seen[v.edition] then seen[v.edition] = true; ids[#ids + 1] = v.edition end
      end
    elseif not seen[baseId] then
      seen[baseId] = true; ids[#ids + 1] = baseId
    end
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

-- Where an edition's service park is: where the car starts in the lot (servicePark.spawn of its first leg's
-- geometry), else the first leg's first point (the current edition unless one is given). An edition of days has it in its index
-- (the first leg's geometry is read only when the day is the current one).
local function servicePark(edition, geometry, index)
  edition = edition or st.edition
  index = index == nil and st.index or index
  local first = edition.legs[1]
  local ix = index and index.legs and index.legs[first.id]
  if ix and (ix.spawn or ix.start) then
    local p = ix.spawn or ix.start
    return p[1], p[2], p[3]
  end
  local g = (geometry or st.geometry)[first.id]
  local lot = g.servicePark
  local pt = (lot and lot.spawn and { lot.spawn.lat, lot.spawn.lon, lot.spawn.h }) or g.points[1]
  return pt[1], pt[2], pt[3]
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

-- Earth's GPS and the loop's own route guidance are one planner: both draw their arrows through core_groundMarkers, and
-- the loop's time-control panel reads the distance to the next control from it (vehicleStoppedNearPlane: the ground
-- marker's path length, whenever the car is not at the control). Earth's GPS keeps its destination across level starts, puts
-- its arrows on the planner again every time the navgraph is applied (each terrain block swap) and adds the rest of its
-- planned road route to the planner's distance: a destination left from earlier (the other edition's service park, 2640 km
-- away by road) replaced the loop's route at the next block swap and the panel read "TC1 2639.72km". A running loop owns the
-- ground markers: Earth's GPS is cleared when the loop starts, and again when one is set while it runs (the one Dakar itself
-- set to lead the player to a service park, st.startGps, is for a loop that is not running).
local function claimGroundMarkers(why)
  local g = earthapi.gpsActive()
  if not g then return false end
  local s = st.startGps
  if s and math.abs((g.lat or 0) - s[1]) < 1e-6 and math.abs((g.lon or 0) - s[2]) < 1e-6 then return false end
  if not earthapi.gpsClear() then return false end
  ownLog('I', string.format('Earth GPS to %s (%.5f, %.5f, %s) cleared %s: the loop draws its own route to the time controls',
    tostring(g.name or 'a place'), g.lat or 0, g.lon or 0,
    g.remaining and string.format('%.0f km to go', g.remaining / 1000) or 'no route yet', why))
  if not st.gpsTold then
    st.gpsTold = true
    earthapi.notify('Dakar: the Earth GPS was switched off; the loop shows its own route to the time controls.', 8)
  end
  return true
end

-- A main menu start keeps the game's loading screen up (our own request tag: it stays while any tag holds it) from the
-- click until the loop's own start fade, so the level load, Earth's first place, the teleport to the service park, the
-- terrain and the roads all happen behind it (no freeroam in between). Let go when the loop starts, when
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
  -- the roads props are kept off: the game's navgraph and our own stage and transfer road, and a version that
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
M.ensureEdition = ensureEdition      -- (the window's pick; the tests')
M.editionIds = editionIds

local PROP_SNAP_M = 400            -- a prop this close to the player is put on the real ground
local PROP_SNAP_MIN_M = 0.2          -- (and not touched for less)

local function setPropPose(o, x, y, z, yaw)
  -- yaw counter-clockwise from +x, as the game's own quaternion has it (props.poseQuat; Earth's setPosRot quaternion)
  local qx, qy, qz, qw = props.poseQuat(yaw)
  o:setPosRot(x, y, z, qx, qy, qz, qw)
end

-- A prop made far from the player gets no ground: the terrain there is not loaded, and it stands at the terrain data's
-- height plus the last difference seen, which floated or sank props by metres (the Svolte di Popoli data is ~10 m above
-- the game's ground at the stage start). It is marked as not on the ground yet, and propSnapTick puts it on
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
-- No props on roads: the game's navgraph (map.getMap().nodes, built by Earth from the roads it streams, a few tiles
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
function M.onNavgraphReloaded()
  scenery.roads.graphV = scenery.roads.graphV + 1
  if st.guide then st.guide.dirty = true end      -- (the arrows' path is on the old graph: planned again at the next check)
end

-- ---------------------------------------------------------------------------------------------------------------------
-- Liaison guidance (guide.lua): the arrows lead to a point of the planned liaison line the local navgraph reaches, not to a
-- time control far outside it. The game's route node (createRallyGroundMarker) plans the arrows from the car through the
-- race nodes to the time control and raises onCreatedRallyGroundMarkerRoute; for a liaison of ours this takes the planner over
-- from there: one target on the line ahead of the car, moved on as it drives (so a car that leaves the line, or misses a point
-- of it, is led back to the line ahead, from where it is), with the minor ways dear, and the distance the panel reads
-- (the planner's path length) kept as the distance to the control.

local GUIDE_EVERY_S = 1.0
local GUIDE_MATCH_M = 40          -- the game's route ends this close to a liaison's end (it stops 10 m short of the control)

-- A function (x, y) -> is a navgraph node within guide.ON_GRAPH_M? Built once per check from the nodes as they are then.
local function guideOnGraph()
  local nodes = navNodes()
  local xs, ys, n = {}, {}, 0
  for _, nd in pairs(nodes or {}) do
    local p = nd.pos
    if p then n = n + 1; xs[n], ys[n] = p.x or p[1], p.y or p[2] end
  end
  local r2 = guide.ON_GRAPH_M * guide.ON_GRAPH_M
  return function(x, y)
    for i = 1, n do
      local dx, dy = xs[i] - x, ys[i] - y
      if dx * dx + dy * dy <= r2 then return true end
    end
    return false
  end
end

-- A leg's line in the current frame (guide.line of its world points); built again when the anchor changes.
local function guideLine(leg)
  return guide.line(legs.toWorld(legPoints(leg), st.anchor, st.bias))
end

local function guideApply(g)
  local gm = rawget(_G, 'core_groundMarkers')
  if gm and g.rest then guide.addRest(gm.routePlanner, g.rest) end
end

-- Set the arrows' path for the car where it is: g.cur is the target in force.
local function guideSet(g, gm, p, tgt, off)
  local x, y, z = tgt.x, tgt.y, tgt.z
  if tgt.final then x, y, z = guide.pointAt(g.line, g.line.total - guide.FINAL_BEFORE_M) end
  local list = { vec3(x, y, z or p[3]) }
  local options = {}
  for k, v in pairs(guide.PLANNER) do options[k] = v end
  gm.setPath(list, options)
  g.set, g.cur, g.dirty, g.foreign = gm.endWP, tgt, false, false
  g.rest = guide.rest(g.line, tgt)
  guideApply(g)
  local onLine = off <= guide.ON_LINE_M
  if g.onLine ~= onLine or not g.logged then
    g.onLine, g.logged = onLine, true
    ownLog('I', string.format('liaison %s guidance: %s; target %.0f m along its line (%.0f m of it), %.0f m rest', g.leg,
      onLine and 'on the line' or string.format('%.0f m off the line', off), tgt.s, g.line.total, g.rest))
  end
end

local function guideTick()
  local g = st.guide
  if not g then return end
  if st.phase ~= 'running' then st.guide = nil; return end
  if st.clock - (g.at or -10) < GUIDE_EVERY_S then return end
  g.at = st.clock
  local gm = rawget(_G, 'core_groundMarkers')
  if not (gm and gm.setPath) then st.guide = nil; return end
  if gm.endWP ~= g.set then
    -- Not ours any more. Another path (Earth's GPS, until claimGroundMarkers clears it, or the game's next route, which
    -- raises the hook and starts over) leaves ours to be put back when it goes; none at all while ours was in force is the game
    -- ending the guidance (the control was reached).
    if gm.endWP ~= nil then g.foreign = true; return end
    if not g.foreign then st.guide = nil; return end
    g.dirty = true
  end
  local p = playerPos()
  if not p then return end
  if g.anchor ~= st.anchor then
    g.anchor, g.line, g.s0, g.cur, g.dirty = st.anchor, guideLine(g.legObj), nil, nil, true
    if not g.line then st.guide = nil; return end
  end
  local s0, off = guide.progress(g.line, p[1], p[2], g.s0)
  if not s0 then return end
  g.s0 = s0
  local tgt = guide.target(g.line, s0, guideOnGraph())
  if g.dirty or guide.moved(g.cur, tgt) then
    guideSet(g, gm, p, tgt, off)
  else
    guideApply(g)
  end
end

-- The planner made a new path of its own (the car was off the path): its distance calculation forgets what is beyond the target.
function M.onRecalculatedRoute()
  if st.guide and st.guide.set then guideApply(st.guide) end
end

function M.onCreatedRallyGroundMarkerRoute()
  local ok, err = pcall(function()
    if st.phase ~= 'running' or not (st.anchor and st.edition and st.geometry) then return end
    local gm = rawget(_G, 'core_groundMarkers')
    local wps = gm and gm.endWP
    local last = wps and wps[#wps]
    if not last then return end
    local lx, ly = last.x or last[1], last.y or last[2]
    local best, bestD
    for _, leg in ipairs(st.edition.legs) do
      if leg.type == 'transfer' and st.geometry[leg.id] then
        local line = guideLine(leg)
        if line then
          local e = line.pts[#line.pts]
          local d = math.sqrt((e[1] - lx) ^ 2 + (e[2] - ly) ^ 2)
          if d <= GUIDE_MATCH_M and (not bestD or d < bestD) then best, bestD = { leg = leg, line = line }, d end
        end
      end
    end
    if not best then st.guide = nil; return end        -- (not a liaison of ours: the game's own route stays)
    st.guide = { leg = best.leg.id, legObj = best.leg, line = best.line, anchor = st.anchor, dirty = true, at = -10, set = wps }
    guideTick()
  end)
  if not ok then
    st.guide = nil
    ownLog('E', "liaison guidance failed, the game's own route stays: " .. tostring(err))
  end
end

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
-- on a road is not dropped (the first run deleted the start's boards and sensors, the navgraph having the SR17 5 m wide):
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
    -- the first leg's way in from the car's spawn in the lot (geometry servicePark.lead, ending at TC0): the service park's props
    -- stand along it, before the control, where the car drives through them
    scenery.leadByLeg = {}
    local first = st.edition.legs[1]
    local lot = first and st.geometry[first.id] and st.geometry[first.id].servicePark
    if lot and lot.lead and #lot.lead >= 2 then scenery.leadByLeg[first.id] = legs.toWorld(lot.lead, st.anchor) end
  end
  if not scenery.defs then
    -- the split checkpoints' boards stand where legs.raceNodes puts the splits in the race path
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
  local placed = props.place(scenery.defs, scenery.worldByLeg, scenery.leadByLeg)
  -- the closures across the side roads of the special stages: the same kind of prop, from lat/lon per anchor
  if scenery.sitesFor ~= st.closures then
    scenery.sites, scenery.sitesFor = props.closureSites(st.edition, st.closures), st.closures
  end
  for _, p in ipairs(props.placeClosures(scenery.sites, function(pts) return legs.toWorld(pts, st.anchor) end)) do
    placed[#placed + 1] = p
  end
  -- the service booth beside the car's spot in the lot: gazebos, tables and tools from the first leg's geometry
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
  -- not on a road; the ground of a prop within reach of the player is known, which tells a bridge over it
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
-- Ground shaping (shape.lua): while the loop is on, the terrain block's heights along the curated special
-- stages are changed, in memory, only where the elevation data has a defect the road does not (the ridge under an overpass, a big
-- spike or dip on the line, the ground at a deck's end: ) and by no more than shape.MAX_CHANGE_M; everywhere else the road is
-- as Earth made it (its lines are a mesh over its own ground). Again for every block Earth brings in (its load / swap
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

-- The DecalRoads of Earth's road group over ground the shaping changed are cut to it again (reclip.lua).
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
    -- at a new block
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
    -- the tunnels Earth has built in the game now: points along their axes (Earth's sound spaces, its tunnel lights), the ground
    -- beside them is Earth's own (the portals' holes, the arch raised over a low tube). Nil when Earth has none or has no such call.
    tunnels = function()
      local am = earthModule('ambience')
      if not (am and type(am.tunnelLights) == 'function') then return nil end
      local ok, list = pcall(am.tunnelLights)
      if ok and type(list) == 'table' and #list > 0 then return list end
      return nil
    end,
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
      -- . The edition may set the spike limits of the shaping, legs.json "shaping": { "spikeM": 2.5 })
      for i = 1, #w - 1 do
        local sp = curate.spanAt(r, i)
        if sp then skip[i] = sp.tagged and 'tagged' or true end
      end
      -- (tunnelsBeside: metres along the line within reach of a tunnel's axis, tools/structures.py; Earth built the tunnel and shaped the ground for it)
      local keep
      local beside = st.geometry[leg.id] and st.geometry[leg.id].tunnelsBeside
      if beside and r.s then
        keep = {}
        for i = 1, #w do
          for _, t in ipairs(beside) do
            if r.s[i] and r.s[i] >= t.from and r.s[i] <= t.to then keep[i] = true; break end
          end
        end
      end
      -- (otherRoads: the pieces of the other roads near the line, from OpenStreetMap, tools/structures.py: their ground is not ours to move)
      local others
      local roads = st.geometry[leg.id] and st.geometry[leg.id].otherRoads
      if roads and #roads > 0 then
        others = {}
        for _, o in ipairs(roads) do
          local pts = {}
          for k, p in ipairs(o.pts) do
            local x, y = st.anchor:geodeticToWorld(p[1], p[2], 0)
            pts[k] = { x, y }
          end
          others[#others + 1] = { pts = pts, hw = o.hw }
        end
      end
      local cfg = st.edition.shaping
      lines[#lines + 1] = { pts = w, skip = skip, keep = keep, others = others, spikeM = cfg and cfg.spikeM }
    end
  end
  return lines
end

-- The loop is on (and the frame is known): shape along its stages; or off: put the ground back.
local function shapeRefresh()
  local s = getShaper()
  if scenery.active then s:setLines(shapeLines()) else s:stop() end
end

-- The ground is shaped along the stages while the loading screen is still up, before the stage starts, as far ahead as the terrain
-- block Earth has loaded reaches: the drive then has little left to shape around the car (the blocks that come later are shaped as
-- they arrive, as ever). The screen is held anyway, so a frame gives the work PRESHAPE_BUDGET_MS instead of the usual 1.5; it is waited
-- for PRESHAPE_MAX_S at most, and never into the loading screen's own watchdog (LOADING_MAX_S).
local PRESHAPE_BUDGET_MS = 10
local PRESHAPE_MAX_S = 20
local PRESHAPE_MARGIN_S = 30

-- Starts the shaping of the stages on the block that is in (a launch behind the loading screen). Returns true when there is work to wait for.
local function preshapeBegin()
  if not st.shapeOn or not st.loadingSince then return false end
  local left = LOADING_MAX_S - (st.clock - st.loadingSince) - PRESHAPE_MARGIN_S
  if left <= 1 then
    ownLog('W', 'stage ground not shaped behind the loading screen: it has been held for ' .. string.format('%.0f s', st.clock - st.loadingSince))
    return false
  end
  local lines = shapeLines()
  if not lines or #lines == 0 then return false end
  local s = getShaper()
  s:setLines(lines)
  s:update(PRESHAPE_BUDGET_MS)                  -- (the job begins now: no block, or none of the stage in it, is told at once)
  local state = s:status().state
  if not (s:busy() or state == 'shaped' or state == 'nothing to shape') or state == 'no terrain block' then
    ownLog('I', 'stage ground not shaped behind the loading screen: ' .. tostring(state))
    return false
  end
  st.preshape = { since = st.clock, maxS = math.min(PRESHAPE_MAX_S, left), jobs = s:status().jobs }
  ownLog('I', string.format('shaping the stage ground behind the loading screen (%.0f s at most)', st.preshape.maxS))
  return true
end

-- Is the shaping behind the loading screen through (or out of time)? Says what was done, once.
local function preshapeSettled()
  local ps = st.preshape
  if not ps then return true end
  local s = shaper
  local took = st.clock - ps.since
  local busy = s and s:busy()
  if busy and took < ps.maxS then return false end
  st.preshape = nil
  if not s then return true end
  local sh = s:status()
  if busy then
    ownLog('W', string.format('the stage ground is not all shaped after %.0f s behind the loading screen: the rest is shaped as the car comes to it', took))
  end
  ownLog('I', string.format('stage ground shaped behind the loading screen: %.0f m of the stage line in the terrain block, %d stretches fixed (%.0f m with the tapers), ' ..
    '%d cells written, %.1f ms of work in %d frames, %.1f s of loading screen%s', sh.coveredM or 0, sh.stretches or 0, sh.fixLength or 0, sh.cells or 0,
    sh.ms or 0, sh.frames or 0, took, busy and ' (not finished)' or ''))
  return true
end

local function shapeTick()
  if shaper then shaper:update(st.preshape and PRESHAPE_BUDGET_MS or nil) end
  if reclipper then reclipper:update() end
end

-- Earth put a new block's heights in (terrain.load = importMaps, terrain.swap = a .ter file; the tap hands over the
-- original's first result): what we wrote to the old data is gone with it, so the stage is shaped again on the new.
-- The tap runs in the same Lua call as Earth's swap or load, so what blockChanged shapes at the car is in before the next physics
-- step sees the new block. During a rebase or a teleport the stages are not yet in the new block's frame (the anchor
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
-- add-on's zip): read once Earth has them (onUpdate), and again whenever the player saves them. The
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
  -- (the road is Earth's own but where a defect of the data is fixed: how many stretches that is)
  return string.format('Ground: %s%s; %d stretch%s fixed, %d cells, largest change %.1f m, last %s', sh.enabled and '' or 'switched off, ',
    sh.state, sh.stretches, sh.stretches == 1 and '' or 'es', sh.cells, sh.maxChange, ago)
end

local placeTrigger       -- (below) puts the loop's start trigger on the service park in the current frame

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
    st.guide = nil
    sceneryClear()
    pcall(shapeRefresh)
    -- The loop is over, however it ended (finished, abandoned, quit to the menu): we are idle again. Left at 'running', the
    -- next start from the mission list waited for idle until it gave up, and the markers stopped following the anchor.
    if st.phase == 'running' then
      st.phase = 'idle'
      ownLog('I', 'the loop stopped: idle again')
      if placeTrigger then pcall(placeTrigger) end
    end
    -- a day of an edition of days finished (M.onAnyMissionWillChange saw the car at its end): what comes next
    local ds, ed = st.dayStop, st.edition
    st.dayStop, st.dayEnd = nil, nil
    if ds and ds.finished and ed and ed.edition == ds.edition then
      local ok, err = pcall(M.completeDay, ed)
      if not ok then ownLog('E', 'finishing the day failed: ' .. tostring(err)) end
    end
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
    if st.ferryLanding then
      local nxt = st.ferryLanding
      st.ferryLanding = nil
      tell(string.format('Dakar: landed. %s%s starts here: start it from the mission list.', nxt.label or nxt.title,
        nxt.date and (' (' .. nxt.date .. ')') or ''))
    end
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
-- ground, in the live race path, once per anchor.
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
  if where.metres > START_RADIUS_M and st.edition.teleportStart then
    -- an edition of days: each day's bivouac is far from the last (a different country, even), so starting a day from the
    -- mission list takes you there first, as a menu start does, and starts it with the settings the list passed
    ownLog('I', string.format('start of %s: %.0f km from its service park, teleporting there first', tostring(mission.id), where.metres / 1000))
    st.pending = { mission = mission, userSettings = userSettings, startingOptions = startingOptions }
    M.start(edition)
    if st.phase ~= 'teleporting' then st.pending = nil end
    return true
  end
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
--. Instead: the level loads as freeroam (as its own tile does, with the spawn point the player picked),
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

-- Where the car is: sampled every hud.PERIOD_S, logged, and drawn top right every frame (an imgui window is
-- drawn each frame or it is gone). The lines are the curated stages (numbered as the shaping numbers them), then the liaisons.
local function hudLines()
  if not (st.anchor and st.edition and st.geometry) then return nil end
  local out = {}
  for _, leg in ipairs(st.edition.legs) do
    local r = curatedLines()[leg.id]
    if r then out[#out + 1] = { id = leg.id, pts = legs.toWorld(r.points, st.anchor, st.bias) } end
  end
  -- (the liaisons after them: the stages keep their numbers)
  for _, leg in ipairs(st.edition.legs) do
    if leg.type ~= 'special' and st.geometry[leg.id] then
      out[#out + 1] = { id = leg.id, pts = legs.toWorld(legPoints(leg), st.anchor, st.bias) }
    end
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
-- The editions the window lists: one group per folder, an edition of days with its days (the ids editionIds finds, in order).
local function windowGroups()
  local groups, byKey = {}, {}
  for _, id in ipairs(editionIds()) do
    local baseId = days.splitId(id)
    local g = byKey[baseId]
    if not g then
      local base = readBase(id)
      g = { key = baseId, title = base and base.title or baseId, ids = {} }
      byKey[baseId] = g
      groups[#groups + 1] = g
    end
    g.ids[#g.ids + 1] = id
  end
  return groups
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
      -- the edition the service park and the buttons are for; not changed under a loop that is on
      local groups = windowGroups()
      if #groups > 1 and not scenery.active then
        for _, g in ipairs(groups) do
          local here = st.edition and days.splitId(st.edition.edition) == g.key
          if im.Button((here and '> ' or '') .. g.title) then ensureEdition(here and st.edition.edition or g.ids[1]) end
        end
      end
      -- an edition of days: the day the buttons are for, the ones either side of it and what comes between (a rest day, the ferry)
      local cur = st.edition
      if cur and cur.baseEdition and not scenery.active then
        local g
        for _, grp in ipairs(groups) do if grp.key == cur.baseEdition then g = grp end end
        local at = 1
        for i, id in ipairs(g and g.ids or {}) do if id == cur.edition then at = i end end
        if g and at > 1 and im.Button('<') then ensureEdition(g.ids[at - 1]) end
        if g and at > 1 then im.SameLine() end
        im.Text(string.format('%s  %s  (day %d of %d)', cur.label or cur.edition, cur.date or '', at, g and #g.ids or 1))
        if g and at < #g.ids then
          im.SameLine()
          if im.Button('>') then ensureEdition(g.ids[at + 1]) end
        end
        local nxt, rests = dayAfter(cur)
        if rests and rests[1] then im.Text(string.format('After it: %s (%s), no driving.', rests[1].label, rests[1].bivouac or '')) end
        if cur.ferry and cur.ferry.to then
          if im.Button('Take the ferry now: teleport to ' .. tostring(cur.ferry.to.name or 'the landing')) then M.crossFerry(cur, nxt) end
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
    -- the transient ground shaping along the stages (shape.lua): off puts the original ground back
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
  st.bias = bias                               -- (the route's heights only: the ground is Earth's own, shaped only at a defect)
  -- the curated stages: what was found and changed, per special
  local curated = curatedLines()
  local c = st.loaded[st.edition.edition]
  for _, leg in ipairs(st.edition.legs) do
    if c and c.curatedSummary and c.curatedSummary[leg.id] then ownLog('I', 'stage curation: ' .. c.curatedSummary[leg.id]) end
  end
  local res, err = legs.materialize(st.edition, st.geometry, st.anchor, missionDirOf, bias, curated)
  if not res then return ownLog('E', 'materialize failed: ' .. tostring(err)) end
  for _, line in ipairs(res.noteSummaries or {}) do ownLog('I', 'pace notes ' .. line) end
  for path, data in pairs(res.files) do
    if not jsonWriteFile(path, data, true) then return ownLog('E', 'could not write ' .. path) end
  end
  stopForeground()
  st.gpsTold = false
  claimGroundMarkers('at the start of the loop')
  local id = loopMissionId(st.edition)
  local mission = gameplay_missions_missions.getMissionById(id)
  if not mission then return ownLog('E', 'mission ' .. id .. ' not found (is the mod installed?)') end
  -- the loop's own positions (the service stall, the start trigger) are world numbers in the current frame too; the car is
  -- put there by the mission, half a metre above the ground (the route already lies on it).
  local pl = res.placement
  -- (the stall where the loop puts the car, kept as lat/lon: a day that ends elsewhere has it set back here, see setStall)
  local sLat, sLon, sH = st.anchor:worldToGeodetic(pl.pos[1], pl.pos[2], pl.pos[3])
  -- (the car starts in the parking lot with TC0 a few metres ahead of it; without a lot the stall stands back
  -- from TC0: on the game's ground there, else at the height of the data)
  local gzStall = groundAt(pl.pos[1], pl.pos[2], pl.pos[3])
  ownLog('I', string.format('%s %.0f m before TC0, ground %s', pl.inLot and 'the car starts in the lot,' or 'service stall',
    pl.backM or 0, gzStall and string.format('%.1f m', gzStall) or 'not found (height of the data)'))
  -- every control's heading against the road's, the joins and the spawn
  for _, line in ipairs(legs.probeLines(res)) do ownLog('I', line) end
  pl.pos = { pl.pos[1], pl.pos[2], (gzStall or pl.pos[3]) + 0.5 }
  mission.missionTypeData.serviceStallPos = pl.pos
  -- (two orientations: the stall's is the car's own, set on the vehicle as it is by the loop's move node; the start trigger's
  -- is the way the car faces as quatFromDir has it, which the game turns into a car's: legs.loopPlacement)
  mission.missionTypeData.serviceStallRot = pl.rot
  mission.startTrigger.pos, mission.startTrigger.rot = pl.pos, pl.dirRot
  if mission.fgVariables then
    mission.fgVariables.serviceStallPos, mission.fgVariables.serviceStallRot = pl.pos, pl.rot
  end
  -- an edition of days: where the day ends (a day that does not end where it began has its end stall elsewhere, see dayStallTick)
  st.dayEnd = nil
  if st.edition.baseEdition then
    local lastLeg = st.edition.legs[#st.edition.legs]
    local firstPts, lastPts = legPoints(st.edition.legs[1]), legPoints(lastLeg)
    local n = #lastPts
    local stall = days.endStall(firstPts[1], lastPts[n], lastPts[math.max(1, n - 3)])
    local lat, lon, h = sLat, sLon, sH
    local de = { separate = stall ~= nil, mission = mission, at = 'start', startLL = { lat, lon, h },
      returnMissionId = 'beamng_earth/' .. missionType(lastLeg) .. '/' .. missionName(st.edition, lastLeg) }
    if stall then de.lat, de.lon, de.h = stall[1], stall[2], stall[3] else de.lat, de.lon, de.h = lat, lon, h end
    st.dayEnd, st.stallAt = de, nil
    ownLog('I', string.format('%s ends %s (%.5f, %.5f)', st.edition.label or st.edition.edition,
      stall and 'at its own end stall' or 'where it began', de.lat, de.lon))
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
  if not ok then
    ownLog('E', 'the mission manager refused the start: ' .. tostring(err))
    st.ourStart = false
  end
end

-- The route for the current anchor, then the game's start.
function M.runLaunch()
  local ok, err = pcall(launch)
  if not ok then ownLog('E', 'launch failed: ' .. tostring(err)) end
  releaseLoadingScreen('the launch ended without starting the loop')     -- (no-op when launch let it go)
  if not st.ourStart and not scenery.active then pcall(shapeRefresh) end   -- (no loop: what was shaped behind the loading screen goes back)
end

-- The corridor prefetch of the edition's special stages (preload.lua): Earth's server fetches the whole
-- corridor of a start-to-finish route into its caches. The stages are asked for one after the other (a newer route
-- supersedes the older on the server). Returns true while the first stage's corridor is not in.
local function startPreload()
  local reqs = {}
  for _, leg in ipairs(st.edition.legs) do
    if leg.type == 'special' then
      -- (a stage of hundreds of kilometres: the first preload.MAX_M of it; the rest is streamed as the car comes to it)
      local pts = preload.slice(legPoints(leg), preload.MAX_M)
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
  local okS, shaping = pcall(preshapeBegin)
  if not okS then ownLog('E', 'shaping behind the loading screen failed: ' .. tostring(shaping)); shaping = false; st.preshape = nil end
  if (hold or shaping) and st.loadingSince then
    st.preloadGate = { since = st.clock }
    st.phase = 'preload'
    if hold then
      ownLog('I', string.format('the loading screen stays up until the stage corridor is fetched (at most %d s)', preload.CAP_S))
    end
    return
  end
  if hold then ownLog('I', 'the stage corridor is fetched in the background (no loading screen to hold)') end
  M.runLaunch()
end

-- The loading-screen gate, each frame: the answers and progress, and the launch once the first stage is in (or gave up).
local function preloadTick()
  local p = st.preload
  if p then p:tick() end
  if st.preloadGate and (not p or p:gateOpen()) and preshapeSettled() then
    st.preloadGate = nil
    if p then ownLog('I', 'the stage corridor is settled: launching') end
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
  st.preloadGate, st.pending, st.preshape = nil, nil, nil
  if not scenery.active then pcall(shapeRefresh) end
  if st.phase == 'preload' then st.phase = 'idle' end
  releaseLoadingScreen(why)
end
M.cancelPreloadGate = cancelPreloadGate

-- Teleport to the edition's service park (Earth picks the anchor) and, unless noLaunch, start the loop there. `at` ({ lat, lon,
-- name }): teleport there instead (the ferry's landing: the next day's service park, found by crossFerry from the leg's own data).
function M.start(editionId, noLaunch, at)
  if not ensureTaps() then return ownLog('E', 'BeamNG.Earth is not loaded (or has changed): taps missing') end
  local ok, err = ensureEdition(editionId or DEFAULT_EDITION)
  if not ok then return ownLog('E', err) end
  local edition = st.edition
  local lat, lon = servicePark()
  local name = 'Dakar: ' .. (edition.title or edition.edition)
  if at then lat, lon, name = at.lat, at.lon, at.name or name end
  ownLog('I', string.format('teleporting to %s (%.5f, %.5f)', at and at.name or edition.title or edition.edition, lat, lon))
  st.phase, st.noLaunch = 'teleporting', noLaunch and true or false
  local took, why = earthapi.teleport(lat, lon, name)
  if not took then
    st.phase = 'idle'
    return tell('Dakar: Earth would not teleport: ' .. tostring(why))
  end
  clearStartGps()
end

-- ---------------------------------------------------------------------------------------------------------------------
-- Days (days.lua): where a day ends, the ferry between days, the rest day

local DAY_END_RADIUS_M = 250       -- a loop that stopped with the car this close to where its day ends was finished (a stall is a park)
local STALL_EVERY_S = 1.0

-- The service stall is where the game puts the car at the start of the loop and where it wants it at the end (the flowgraph's
-- `serviceStallPos`, the manager's service-in entry). A day that ends in another place than it began (Stage 4: Bouarfa to
-- Zagora) has two: the start's until the day's last liaison begins, then the end's, set as the mission's own variable
-- (changeBase, through addOrSetVariable) and in the manager's schedule and event list, which took the position when the schedule was made.
-- Both are lat/lon here, put in the frame as it is now, so a day driven across rebases ends in the right place. `which`: 'start' | 'end'.
local function setStall(which)
  local de = st.dayEnd
  if not (de and st.anchor) then return false end
  local lat, lon, h
  if which == 'end' then lat, lon, h = de.lat, de.lon, de.h else lat, lon, h = de.startLL[1], de.startLL[2], de.startLL[3] end
  local x, y, z = st.anchor:geodeticToWorld(lat, lon, h)
  local gz = groundAt(x, y, z)
  local pos = { x, y, (gz or (z + (st.bias or 0))) + 0.5 }
  local mission = de.mission
  if mission then
    mission.missionTypeData.serviceStallPos = pos
    if mission.fgVariables then mission.fgVariables.serviceStallPos = pos end
    if mission.addOrSetVariable then
      local ok, err = pcall(mission.addOrSetVariable, mission, 'serviceStallPos', pos)
      if not ok then ownLog('W', 'the day\'s service stall was not set in the flowgraph: ' .. tostring(err)) end
    end
  end
  local mgr = rawget(_G, 'gameplay_rallyLoop')
  local rm = mgr and mgr.getManager and mgr.getManager()
  for _, entry in ipairs(rm and rm.schedule or {}) do
    if entry.missionType == 'serviceIn' then entry.serviceStallPos = pos end
  end
  for _, ev in ipairs(rm and rm.events or {}) do
    if ev.type == 'service_in' then ev.pos = pos end
  end
  de.at = which
  ownLog('I', string.format('service stall for %s: %s (%.5f, %.5f)', st.edition.label or st.edition.edition, which, lat, lon))
  return true
end

-- A day that ends elsewhere: the end stall comes into force when the loop is on the return liaison (or already past it), the start's
-- again while it is before it (a restart of the loop puts the car back at the start).
local function dayStallTick()
  local de = st.dayEnd
  if not (de and de.separate and scenery.active) or st.clock - (st.stallAt or -10) < STALL_EVERY_S then return end
  st.stallAt = st.clock
  local mgr = rawget(_G, 'gameplay_rallyLoop')
  local rm = mgr and mgr.getManager and mgr.getManager()
  local cur = rm and rm.getCurrentMissionId and rm:getCurrentMissionId()
  if not cur then return end
  local want = (cur == 'serviceIn' or cur == de.returnMissionId) and 'end' or 'start'
  if de.at ~= want then setStall(want) end
end

-- The loop is about to stop (the manager is still there): was the day finished? Not abandoned, at its end and past its last liaison.
-- The player's "Continue" on the end screen stops the mission this way; leaving the loop anywhere else does not finish the day.
function M.onAnyMissionWillChange(state, mission, abandoned)
  if state ~= 'stopped' then return end
  local ours = type(mission) == 'table' and mission.missionTypeData and mission.missionTypeData.dakarEdition
  if not ours then return end
  st.dayStop = nil
  local de = st.dayEnd
  if not (de and st.anchor and st.edition and st.edition.edition == ours) then return end
  local mgr = rawget(_G, 'gameplay_rallyLoop')
  local rm = mgr and mgr.getManager and mgr.getManager()
  local cur = rm and rm.getCurrentMissionId and rm:getCurrentMissionId()
  local car = playerPos()
  local x, y = st.anchor:geodeticToWorld(de.lat, de.lon, de.h)
  local near = car and math.sqrt((car[1] - x) ^ 2 + (car[2] - y) ^ 2) <= DAY_END_RADIUS_M
  local past = cur == nil or cur == 'serviceIn'
  st.dayStop = { edition = ours, finished = (not abandoned) and near and past or false }
  ownLog('I', string.format('%s stops: %s (car %s its end, loop at %s)', tostring(ours),
    st.dayStop.finished and 'the day is finished' or 'the day is not finished', near and 'at' or 'away from', tostring(cur)))
end

-- A day is finished: what comes next. A ferry ends the day (crossFerry); else the next day starts where this one ended, a rest day
-- on the way is told about, and the window points at the next day.
function M.completeDay(ed)
  ed = ed or st.edition
  if not ed then return false end
  st.completed = st.completed or {}
  st.completed[ed.edition] = true
  local nxt, rests = dayAfter(ed)
  local text = (ed.label or ed.title or ed.edition) .. ' is finished.'
  if ed.ferry then return M.crossFerry(ed, nxt, text) end
  if not nxt then tell(text .. ' That was the last day of the route.'); return true end
  local rest = rests and rests[1]
  ensureEdition(nxt.edition)
  tell(string.format('%s %sNext: %s starts here%s.', text, rest and string.format('%s (%s): no driving. ', rest.label, rest.date or '') or '',
    nxt.label or nxt.title, nxt.bivouac and (', at ' .. nxt.bivouac) or ''))
  return true
end

-- The ferry that ends a day: no driving, the car is taken from the departure port to the landing and the next day's loop starts from
-- there. The teleport is the one a loop's start makes (Earth picks the anchor; the commit then settles the world and the next day is
-- the window's edition), noLaunch: the player starts the next day from the mission list. `nxt`: the next day's view (dayAfter).
function M.crossFerry(ed, nxt, prefix)
  ed = ed or st.edition
  nxt = nxt or dayAfter(ed)
  local f = ed and ed.ferry
  if not (f and f.to and f.to.lat and f.to.lon and nxt) then
    tell('Dakar: there is no ferry to take after ' .. tostring(ed and (ed.label or ed.edition)) .. '.')
    return false
  end
  local where = f.to.name or 'the port'
  tell(string.format('%sOvernight ferry from %s to %s%s: the car is taken across.', prefix and (prefix .. ' ') or 'Dakar: ',
    f.from and f.from.name or 'the departure port', where, f.crossing and (' (' .. f.crossing .. ')') or ''))
  st.ferryLanding = nxt
  st.completed = st.completed or {}
  st.completed[f.id or 'ferry'] = true
  M.start(nxt.edition, true, { lat = f.to.lat, lon = f.to.lon, name = 'Dakar: ferry to ' .. where })
  if st.phase ~= 'teleporting' then st.ferryLanding = nil; return false end
  return true
end

-- A mission's start moved. The game copies startTrigger.pos into its locations cache the first time it reads a mission
-- (missions.lua getLocations), and the marker, the big map and the start check all read that copy: moving startTrigger
-- alone left them where info.json put them, 0, 0, 0. The copy moves too. The mission is tagged with the frame
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

-- A marker stands on the game's ground, not at the route data's height: the two differ (0.4 m under the ground at the
-- Svolte di Popoli's service park, a few metres elsewhere), and a marker under it is not seen, nor entered (the game's
-- start check measures the distance in 3D). Where the ground is not loaded yet (a far edition, a block still coming) the
-- marker keeps the data's height and waits in st.markersWaiting; missionsTick puts it down once the ground is there.
local function placeOnGround(mission, x, y, z)
  local gz = groundAt(x, y, z)
  moveStart(mission, x, y, gz or z)
  if not gz then
    st.markersWaiting[#st.markersWaiting + 1] = { mission = mission, x = x, y = y, z = z, frame = st.anchor }
  end
end

local function groundWaitingMarkers()
  local moved = 0
  for i = #st.markersWaiting, 1, -1 do
    local w = st.markersWaiting[i]
    if w.mission.dakarFrame ~= w.frame then
      table.remove(st.markersWaiting, i)                -- (placed again since, or built again by the game)
    else
      local gz = groundAt(w.x, w.y, w.z)
      if gz then
        moveStart(w.mission, w.x, w.y, gz)
        table.remove(st.markersWaiting, i)
        moved = moved + 1
      end
    end
  end
  if moved > 0 then
    if gameplay_rawPois and gameplay_rawPois.clear then gameplay_rawPois.clear() end
    ownLog('I', string.format('%d mission marker%s put on the ground (loaded there now)', moved, moved == 1 and '' or 's'))
  end
end

-- The loops' start triggers (where the game puts a mission's marker) stand on their service parks in the current frame,
-- so they move with every anchor change; not while a loop runs (launch put its trigger on the real ground).
placeTrigger = function()
  if st.phase == 'running' or not (st.anchor and gameplay_missions_missions) then return end
  st.markersWaiting = {}
  -- every edition's loop has its marker on its own service park, not only the one in use
  for _, id in ipairs(editionIds()) do
    local c = loadCached(id)
    local mission = c and gameplay_missions_missions.getMissionById(loopMissionId(c.edition))
    if mission and mission.startTrigger then
      local lat, lon, h = servicePark(c.edition, c.geometry, c.index or false)
      placeOnGround(mission, st.anchor:geodeticToWorld(lat, lon, h))
    end
    -- every leg's own mission (a liaison or a special stage) has its marker on the time control that opens it, the leg's
    -- first point (TC0 for the first liaison, TC1 for SS1, ...); their info.json says 0, 0, 0, the world's origin
    for _, leg in ipairs(c and c.edition.legs or {}) do
      local p = legStart(c, leg)
      local lm = p and gameplay_missions_missions.getMissionById('beamng_earth/' .. missionType(leg) .. '/' .. missionName(c.edition, leg))
      if lm and lm.startTrigger then
        placeOnGround(lm, st.anchor:geodeticToWorld(p[1], p[2], p[3]))
      end
    end
  end
  -- the markers and the big map are drawn from the game's POI list: gathered again (as Earth does for its gas stations)
  if gameplay_rawPois and gameplay_rawPois.clear then gameplay_rawPois.clear() end
end

-- Every few seconds: the game's mission list is read once, and cleared only when the mod manager is ready, so an add-on
-- installed while the game runs (BeamNG.Earth 1.9.0's Add-ons page) had no activities until Ctrl+L. Our loop
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
  if st.markersWaiting[1] and st.phase ~= 'running' then groundWaitingMarkers() end
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
    if scenery.active or st.phase == 'running' then pcall(claimGroundMarkers, 'while the loop runs') end
  end
  local okD, errD = pcall(dayStallTick)
  if not okD and not st.dayWarned then st.dayWarned = true; ownLog('E', 'day end stall failed: ' .. tostring(errD)) end
  local okU, errU = pcall(guideTick)
  if not okU then
    st.guide = nil
    if not st.guideWarned then st.guideWarned = true; ownLog('E', 'liaison guidance failed: ' .. tostring(errU)) end
  end
  local okH, errH = pcall(shapeTick)
  if not okH and not st.shapeWarned then st.shapeWarned = true; ownLog('E', 'ground shaping failed: ' .. tostring(errH)) end
  local okP, errP = pcall(preloadTick)
  if not okP then
    st.preloadGate, st.preload, st.preshape = nil, nil, nil
    if not scenery.active then pcall(shapeRefresh) end
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
      -- over a running level. Taking it down is what the flowgraph would have done (a no-op when it is gone).
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
      -- it. Earth's own teleport to where the player is holds and grounds it again.
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
