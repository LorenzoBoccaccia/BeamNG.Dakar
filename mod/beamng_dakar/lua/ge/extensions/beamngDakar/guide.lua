-- Guide: where the liaison's ground arrows lead. Pure Lua (core.lua asks the game and draws).
--
-- The game's rally loop draws the arrows of a liaison with core_groundMarkers.setPath: from the car through the leg's race
-- nodes to the next time control, planned over the navgraph. BeamNG.Earth's navgraph only covers about a kilometre round
-- the player, and the planner, given a target outside the graph, goes to the graph point nearest to it in a straight line
-- (the cost of the rest is the fourth power of the distance: no road length or drivability can weigh against it) and
-- finishes with one straight segment to the target. A time control nine kilometres away made the arrows go wherever the
-- roads came closest to it, over the shoulders and between buildings in the last stretch, and the way there was not the
-- planned liaison road.
--
-- So the planner is given a target it can reach: the farthest point of the planned liaison line ahead of the car that the
-- local graph covers (up to LOOKAHEAD_M along the line), moved on as the car advances, with the road classes of the
-- navgraph that are not for driving through (service ways, tracks, living streets) made dear. The target is a single point,
-- not a chain of waypoints: a car that misses a point of the line, or leaves it for another road, is still led, from where
-- it is, to the next reachable point of the line ahead. The distance the time-control panel reads (the planner's, to its
-- target) is the target's plus the line's rest, M.rest.
local M = {}

M.LOOKAHEAD_M = 1500     -- how far along the line the target may be
M.SAMPLE_M = 50          -- line points tried for the target at least this far apart
M.ON_GRAPH_M = 40        -- a line point this close to a graph node is on the graph (its edges are at most 80 m long)
M.MISS_LIMIT = 3         -- samples in a row off the graph that end the search (a gap in the graph, a junction's offset: not yet)
M.ON_LINE_M = 60         -- a car this close to the line is on it: its place along the line is the one near the last
M.BACK_M = 80            -- ...and may be this much behind the last (the car turned, the line's spread)
M.RETARGET_M = 150       -- the target is moved on in steps of this much line
M.FINAL_BEFORE_M = 10    -- the arrows end this far before the stop (the game's own route end offset)

-- What the planner is told (core_groundMarkers.setPath options): the navgraph's drivability is 1 on the main road classes,
-- 0.8-0.9 on residential and tertiary, 0.5 on living streets, 0.4 on service ways, 0.2 on tracks and busways. At or below
-- the cut-off a road costs PENALTY_BELOW times its length: used where there is no other way (a parking lot, a lane at
-- the start) and not as a shortcut.
M.PLANNER = { cutOffDrivability = 0.5, penaltyAboveCutoff = 1, penaltyBelowCutoff = 10 }

-- A polyline pts = { {x, y[, z]} }: { pts, cum = metres along at each point, total }. nil for fewer than two points.
function M.line(pts)
  if not pts or #pts < 2 then return nil end
  local cum = { 0 }
  for i = 2, #pts do
    local a, b = pts[i - 1], pts[i]
    cum[i] = cum[i - 1] + math.sqrt((b[1] - a[1]) ^ 2 + (b[2] - a[2]) ^ 2)
  end
  return { pts = pts, cum = cum, total = cum[#pts] }
end

-- The point s metres along the line (clamped to it): x, y, z (z nil when the points have none).
function M.pointAt(line, s)
  local cum, pts = line.cum, line.pts
  local n = #pts
  s = math.max(0, math.min(line.total, s))
  -- (binary search: the segment holding s)
  local lo, hi = 1, n - 1
  while lo < hi do
    local mid = math.floor((lo + hi) / 2)
    if cum[mid + 1] < s then lo = mid + 1 else hi = mid end
  end
  local a, b = pts[lo], pts[lo + 1]
  local len = cum[lo + 1] - cum[lo]
  local f = len > 0 and (s - cum[lo]) / len or 0
  local z = (a[3] and b[3]) and (a[3] + f * (b[3] - a[3])) or nil
  return a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2]), z
end

-- The nearest point to (x, y) of the line's stretch from s0 to s1 metres along it: metres along, metres off. The first
-- segment found wins a tie.
function M.along(line, x, y, s0, s1)
  local pts, cum = line.pts, line.cum
  local best, bestS = math.huge, nil
  for i = 1, #pts - 1 do
    if cum[i + 1] >= s0 and cum[i] <= s1 then
      local a, b = pts[i], pts[i + 1]
      local dx, dy = b[1] - a[1], b[2] - a[2]
      local len2 = dx * dx + dy * dy
      local t = 0
      if len2 > 0 then t = math.max(0, math.min(1, ((x - a[1]) * dx + (y - a[2]) * dy) / len2)) end
      local d = math.sqrt((x - a[1] - t * dx) ^ 2 + (y - a[2] - t * dy) ^ 2)
      if d < best then best, bestS = d, cum[i] + t * (cum[i + 1] - cum[i]) end
    end
  end
  if not bestS then return nil end
  return bestS, best
end

-- Where the car is along the line: near the last place (prev: metres along, or nil) while it is on the line, else the
-- nearest point of the whole line (it left the line, or has not been on it yet: the arrows lead it to the nearest).
-- Returns metres along and metres off.
function M.progress(line, x, y, prev)
  if prev then
    local s, off = M.along(line, x, y, prev - M.BACK_M, prev + M.LOOKAHEAD_M)
    if s and off <= M.ON_LINE_M then return s, off end
  end
  return M.along(line, x, y, 0, line.total)
end

-- The arrows' target for a car s0 metres along the line: the farthest sample within LOOKAHEAD_M that onGraph(x, y) accepts
-- (the line's end when it is reached with the graph under it), else where the car is along the line.
-- Returns { s, x, y, z, final } (final: the line's end).
function M.target(line, s0, onGraph)
  local best, misses = nil, 0
  local s = s0
  local reachedEnd = false
  while true do
    s = math.min(s + M.SAMPLE_M, line.total)
    if s - s0 > M.LOOKAHEAD_M then break end
    local x, y, z = M.pointAt(line, s)
    local final = s >= line.total - 1e-6
    if onGraph(x, y) then
      best, misses = { s = s, x = x, y = y, z = z, final = final }, 0
    elseif best then
      misses = misses + 1
      if misses >= M.MISS_LIMIT then break end
    end
    if final then
      reachedEnd = true
      break
    end
  end
  if reachedEnd and (not best or misses < M.MISS_LIMIT) and (not best or not best.final) then
    -- the end is within reach and the stretch to it is on the graph, give or take a gap: the end itself
    local x, y, z = M.pointAt(line, line.total)
    best = { s = line.total, x = x, y = y, z = z, final = true }
  end
  if not best then
    local x, y, z = M.pointAt(line, s0)
    best = { s = s0, x = x, y = y, z = z, final = s0 >= line.total - 1e-6 }
  end
  return best
end

-- Has the target moved enough (along the line, either way) to set the arrows again?
function M.moved(cur, new)
  if not cur then return true end
  return cur.final ~= new.final or math.abs(new.s - cur.s) >= M.RETARGET_M
end

-- The line left after the arrows' target, which the panel's distance adds to the planner's.
function M.rest(line, target)
  if target.final then return 0 end
  return math.max(0, line.total - target.s)
end

-- The planner's path ({ pos, distToTarget }) and distance, each node's distance to the target made distance to the end of the
-- line (what is left of it beyond the target, `rest`, added): idempotent, so it is called after every re-plan the planner
-- makes on its own (its distance calculation forgets what is beyond its target). rp: core_groundMarkers.routePlanner.
-- Returns the metres added.
function M.addRest(rp, rest)
  if not rp or type(rp.path) ~= 'table' or #rp.path == 0 or not rest or rest <= 0 then return 0 end
  local have = rp.path[#rp.path].distToTarget or 0
  local add = rest - have
  if add < 1 then return 0 end
  for _, wp in ipairs(rp.path) do wp.distToTarget = (wp.distToTarget or 0) + add end
  rp.distance = (rp.distance or 0) + add
  return add
end

return M
