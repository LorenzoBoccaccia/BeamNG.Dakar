-- What Dakar asks of BeamNG.Earth, through its modding API (beamngEarth_api, Earth 1.9.0, docs/ADDONS.md) when Earth has
-- it, else the way it was done before: Earth's own core state and the game's message call (Earth 1.8.2, GitHub #51).
-- One module for the choice, so core.lua says what it wants and the two ways stay in one small place. Only what the API
-- has an equivalent of is here: position, teleport, GPS destination, a message on screen, the add-on's options. The
-- taps (core.lua TAPS) and the other internals (anchor ids, roads.readyAround, simhold, the route download) have no API
-- equivalent and stay as they are.
--
-- The API is looked up at every call: Earth's extensions load before ours, but may be reloaded, and a missing or older
-- Earth is simply "no API". Only version 1 is used (names and fields change only with a new major version).
local M = {}

-- The id Earth's Add-ons page gives this add-on (the repository's name in lower case: addons.py _repo_id); its options
-- are asked for, and announced, under it.
M.ADDON_ID = 'beamng-dakar'

local function api(feature)
  local a = rawget(_G, 'beamngEarth_api')
  if type(a) ~= 'table' or type(a.version) ~= 'function' then return nil end
  local ok, major = pcall(a.version)
  if not ok or major ~= 1 then return nil end
  if feature and not (type(a.has) == 'function' and a.has(feature)) then return nil end
  return a
end

local function earthState()
  local earth = rawget(_G, 'beamngEarth_core')
  return earth and earth._state and earth._state() or nil
end

-- Which way Earth is asked: 'api' (1.9.0) or 'internals' (1.8.2), for the log and the tests.
function M.via(feature) return api(feature) and 'api' or 'internals' end

-- lat, lon of the player, or nil before Earth knows where they are.
function M.position()
  local a = api('pose')
  if a then return a.position() end
  local es = earthState()
  local pl = es and es.player
  if pl and pl.lat and pl.lon then return pl.lat, pl.lon end
  return nil
end

-- Can Earth take a teleport now? The API says when it cannot (the ground under the player is still loading); before
-- the API there is no such question, a teleport is simply tried.
function M.ready()
  local a = api()
  return a == nil or a.ready() == true
end

-- Teleport to lat, lon; true when Earth took it, else false and why (the API says why; before it, only 'not available').
-- The API's teleport is the map's Go and shows in Earth's recent places; Earth 1.8.2's was called with record = false
-- (the places are the player's, not ours), and keeps being. silent: the teleport that only puts the player back on the
-- ground where they are, which must never show there: Earth's own call, with the API too (it cannot be told not to).
function M.teleport(lat, lon, name, silent)
  local a = not silent and api('teleport') or nil
  if a then
    local ok, took, why = pcall(a.teleport, lat, lon, name)
    if not ok then return false, tostring(took) end
    return took == true, why
  end
  local earth = rawget(_G, 'beamngEarth_core')
  if not (earth and earth.teleport) then return false, 'beamngEarth_core.teleport is not available' end
  earth.teleport(lat, lon, name, { record = false })
  return true
end

-- Set Earth's GPS to a place (a route on the map and arrows on the road): true when it did. Nothing before the API.
function M.gpsSet(lat, lon, name)
  local a = api('gps')
  if not a then return false end
  local ok, took = pcall(a.gpsSet, lat, lon, name)
  return ok and took == true
end

-- Clear the GPS only if it still leads where we set it (the player may have chosen another place since).
function M.gpsClearIf(lat, lon)
  local a = api('gps')
  local g = a and a.gps()
  if g and math.abs((g.lat or 0) - lat) < 1e-6 and math.abs((g.lon or 0) - lon) < 1e-6 then a.gpsClear() end
end

-- A message on screen for seconds.
function M.notify(text, seconds)
  local a = api('notify')
  if a and a.notify(text, seconds) then return true end
  local ui = rawget(_G, 'ui_message')
  if ui then pcall(ui, text, seconds, 'dakar', 'info') end
  return ui ~= nil
end

-- The add-on's options as the player saved them on Settings > Add-ons > Options ({key = value}), or nil: no API (an
-- Earth before 1.9.0), or the add-on list has not come yet. The declaration is beamngEarth_addon.json at the top of the
-- add-on's zip.
function M.options()
  local a = api('options')
  return a and a.addonOptions(M.ADDON_ID) or nil
end

return M
