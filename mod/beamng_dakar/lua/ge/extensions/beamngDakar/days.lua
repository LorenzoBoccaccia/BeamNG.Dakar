-- Days: how an edition that runs over several days is played.
--
-- A one-loop edition (the testbed, the Svolte di Popoli) is a legs.json with `legs` only. An edition with `days` is a
-- rally in the real sense: each day is a loop of its own in the game's mission list, with its own service park (the
-- bivouac), TC0, liaison and special, and each day starts where the day before ended:
--
--   { "days": [ { "id": "d04", "label": "Stage 2", "date": "...", "legs": ["S2-in", "S2", "S2-out"] },
--               { "id": "d09", "kind": "rest", "legs": ["R"], ... },
--               { "id": "d02", ..., "legs": ["S1-in", "S1", "T1"], "ferry": "F1" } ],
--     "legs": [ { "id": "S2", "type": "special", ... }, { "id": "F1", "type": "ferry", "from": {...}, "to": {...} }, ... ] }
--
-- The legs of a day that are driven (transfer, special) make its loop, in order: the game's rally loop runs
-- liaison, special (up to four of those pairs) and a return liaison. A leg that is not driven stays out of the loop:
--   ferry  the crossing at the end of a day: the car is taken from the port the day ended at to the port it lands at (core.lua
--          crossFerry: Earth's teleport, as the start of a loop is made), and the next day's loop starts there. Nothing in the
--          loop's time card needs to wait for it: the next day is its own schedule (its first time control is TC<n>B, the one that
--          follows the last of the day before, the loop's startingSSNumber).
--   rest   a day without driving: no loop, no mission. The window shows it, and a day that ends before it names it.
--
-- Pure (no game calls). expand() turns the legs.json into one edition view per day, which core.lua works with exactly as it
-- does with a one-loop edition (its `edition` is "<edition>/<day id>", its `legs` the day's driven legs).
local M = {}

M.CLOSED_M = 1000         -- a day whose last point is within this of its first ends where it began: no end stall of its own
M.END_BEYOND_M = 25       -- the end stall is this far past the last time control, on the road's way on (the car drives into the park)

local sqrt, cos, rad = math.sqrt, math.cos, math.rad

local function legById(base)
  local by = {}
  for _, leg in ipairs(base.legs or {}) do by[leg.id] = leg end
  return by
end

M.DRIVEN = { special = true, transfer = true }

function M.isDriven(leg) return leg ~= nil and M.DRIVEN[leg.type] == true end

-- The id of a day's edition view, and back.
function M.viewId(base, day) return base.edition .. '/' .. day.id end

function M.splitId(id)
  local b, d = tostring(id):match('^([^/]+)/(.+)$')
  if b then return b, d end
  return tostring(id), nil
end

-- Every day of an edition with `days` as an edition view: { edition, baseEdition, day, index, kind = 'loop' | 'rest', title, label,
-- date, missionPrefix, legs (the driven ones), ferry (the ferry leg that ends the day, or nil), rest (a rest day's leg), shaping,
-- teleportStart }. A one-loop edition has no days: nil.
function M.expand(base)
  if type(base) ~= 'table' or type(base.days) ~= 'table' then return nil end
  local by = legById(base)
  local out = {}
  for i, day in ipairs(base.days) do
    local v = {
      edition = M.viewId(base, day), baseEdition = base.edition, day = day, index = i, kind = day.kind or 'loop',
      label = day.label or day.id, date = day.date, bivouac = day.bivouac,
      title = day.title or ((base.title or base.edition) .. ': ' .. (day.label or day.id)),
      missionPrefix = (base.missionPrefix or 'dakar') .. '-' .. day.id,
      legs = {}, shaping = base.shaping, serviceStallBackM = base.serviceStallBackM,
      teleportStart = true,                  -- (the days are far apart: a start from the mission list takes you to the bivouac)
    }
    for _, id in ipairs(day.legs or {}) do
      local leg = by[id]
      if leg then
        if M.isDriven(leg) then v.legs[#v.legs + 1] = leg
        elseif leg.type == 'ferry' then v.ferry = leg
        elseif leg.type == 'rest' then v.restLeg = leg end
      end
    end
    out[#out + 1] = v
  end
  return out
end

-- The view of a day by its id ("<edition>/<day id>"), or nil.
function M.find(base, id)
  for _, v in ipairs(M.expand(base) or {}) do
    if v.edition == id then return v end
  end
  return nil
end

-- The playable days after view `v` in order: ( next playable day or nil, the rest days between ). The player is taken to
-- the next day's bivouac (the same place when no ferry stands between), and a rest day on the way is told about.
function M.following(views, v)
  local rests = {}
  for i = v.index + 1, #views do
    local w = views[i]
    if w.kind == 'rest' then rests[#rests + 1] = w
    else return w, rests end
  end
  return nil, rests
end

function M.playable(views)
  local out = {}
  for _, v in ipairs(views or {}) do
    if v.kind ~= 'rest' then out[#out + 1] = v end
  end
  return out
end

-- Metres between two { lat, lon } (a flat approximation: plenty for "within a kilometre").
function M.metres(a, b)
  local dn = (b[1] - a[1]) * 111320
  local de = (b[2] - a[2]) * 111320 * cos(rad((a[1] + b[1]) / 2))
  return sqrt(dn * dn + de * de)
end

-- Where a day's loop ends, as lat/lon: `first` and `last` are the first and last points of the day's legs ({ lat, lon, h }), `wayOn` a
-- point shortly before the last one (for the heading). Returns nil for a day that ends where it began (the loop's own service stall
-- does: the return liaison ends beside it), else { lat, lon, h, closed = false } at END_BEYOND_M past the last point along the
-- way the road goes on.
function M.endStall(first, last, before)
  if M.metres(first, last) <= M.CLOSED_M then return nil end
  local ck = cos(rad(last[1]))
  local dn, de = last[1] - before[1], (last[2] - before[2]) * ck       -- (degrees of latitude, and of longitude scaled to the same size)
  local l = sqrt(dn * dn + de * de)
  if l < 1e-12 then return { last[1], last[2], last[3] or 0 } end
  local m = M.END_BEYOND_M
  return { last[1] + dn / l * m / 111320, last[2] + de / l * m / (111320 * ck), last[3] or 0 }
end

return M
