-- Legs: an edition's route in lat/lon, turned into the files BeamNG's rally loop reads, for one BeamNG.Earth anchor.
--
-- Pure (no game calls): the anchor is a BeamNG.Earth geodesy Anchor (geodeticToWorld), the geometry a decoded
-- geometry/<leg>.json ({points = {{lat, lon, h}, ...}}). Everything the loop reads is a world position in the current
-- frame, so this runs again for every new anchor; the lat/lon is the truth and the files are a cache of it.
local pacenotes = require('beamngDakar/pacenotes')
local M = {}

M.DRIVELINE_STEP_M = 30     -- driveline node spacing (the source geometry is <= 20 m)
M.DRIVELINE_TOL_M = 1       -- ...less where the road turns: the line stays within this of the source points (the game's own
                            -- driveline simplification has the same, 1 m); 30 m chords cut a right-angle corner by 12 m
-- What the driveline spline is called in its file. The game turns the spline into the road line the loop measures and
-- tracks, and sets each point's height from the terrain block (zSnap.conformSplineGeometry): where Earth's block does not
-- reach (it covers a couple of kilometres round the car) the terrain answers the block's base height, so a stage's far end
-- ran hundreds of metres under the race nodes and the pace notes, which keep the heights of the data. core.lua taps that
-- one call and keeps the spline's own heights for a spline with this name.
M.DRIVELINE_NAME = 'BeamNG.Dakar driveline'
M.ROAD_WIDTH = 10
-- The route builder wants what the stock loops have (measured on Italy and Utah): the driveline starts 15-25 m before
-- the start position and runs on past the stop position (0-18 m), and the last race node sits 16-150 m before the stop
-- position. A driveline that ends exactly on the stop position, with the last node on it too, gave "finishLineDistOffset
-- is nil" and no schedule.
M.LEAD_IN_M = 22
M.RUN_OUT_M = 12
M.LAST_NODE_BEFORE_STOP_M = 30
-- The race nodes (M.raceNodes): the first this far along (on the start), a liaison's this wide; a
-- special's split checkpoints every SPLIT_EVERY_M (the stock stages split every 1.5-3 km), none within
-- SPLIT_MIN_TO_STOP_M of its stop, this wide (the stock 10 m, wider: the source road may be off the drawn one)
M.LIAISON_FIRST_NODE_M = 0
M.LIAISON_NODE_RADIUS = 20
M.SPLIT_EVERY_M = 3000
M.SPLIT_MIN_TO_STOP_M = 1000
M.STAGE_NODE_RADIUS = 25
-- The notebook's name: every stock voicepack lists 'structured' among the notebooks it plays (rally/voicepack.lua resolves
-- <mission>/rally/notebooks/<name>.notebook.json from that list, and finds no voicepack at all when none of the names exists)
M.NOTEBOOK = 'structured'

-- (the game runs LuaJIT, which has math.atan2; Lua 5.3+ has the two-argument math.atan instead)
local sqrt, sin, cos = math.sqrt, math.sin, math.cos
local atan2 = math.atan2 or function(y, x) return math.atan(y, x) end

local function dist(a, b)
  local dx, dy, dz = a[1] - b[1], a[2] - b[2], a[3] - b[3]
  return sqrt(dx * dx + dy * dy + dz * dz)
end

local function unit(dx, dy, dz)
  local l = sqrt(dx * dx + dy * dy + dz * dz)
  if l < 1e-9 then return { 0, 1, 0 } end
  return { dx / l, dy / l, dz / l }
end

-- BeamNG's quaternions turn the other way round: the game's `q * v` (lua/common/mathlib.lua, LuaQuat.__mul) is
-- v - w t + q x t with t = 2 q x v, the rotation by the conjugate of the textbook formula. A turn about +z of `a` radians
-- (x = y = 0, z = sin(a/2), w = cos(a/2)) therefore takes +y to (sin a, cos a): the compass bearing a, clockwise seen from
-- above. This is the game's own formula, so a reading of an orientation made with it does not depend on how this file
-- built it.
local function rotate(q, v)
  local qx, qy, qz, qw = q[1], q[2], q[3], q[4]
  local tx, ty, tz = 2 * (qy * v[3] - qz * v[2]), 2 * (qz * v[1] - qx * v[3]), 2 * (qx * v[2] - qy * v[1])
  return {
    v[1] - qw * tx + (qy * tz - qz * ty),
    v[2] - qw * ty + (qz * tx - qx * tz),
    v[3] - qw * tz + (qx * ty - qy * tx),
  }
end
M.rotate = rotate

-- The orientation (quatFromDir's) whose +y points along (dx, dy): the way the game stores a time control's or a start
-- position's direction of travel (the plane tests of the rally nodes read +y as the way on: rallySuperCountdown,
-- vehicleStoppedNearPlane), and a mission's start trigger's.
local function yawQuat(dx, dy)
  local th = atan2(dx, dy)
  return { 0, 0, sin(th / 2), cos(th / 2) }
end
M.yawQuat = yawQuat

-- The orientation a vehicle object has when it faces along (dx, dy): a vehicle's front is its local -y, so +y points the
-- other way. What rallyMoveVehicleTo (the node that puts the car on the service stall) hands to setPositionRotation as it
-- is; spawn.safeTeleport turns quatFromDir's by half a turn first for the same reason.
local function carQuat(dx, dy)
  return yawQuat(-dx, -dy)
end
M.carQuat = carQuat

-- geometry points -> world points {x, y, z} (and the lat/lon kept beside them: the truth they came from).
-- zBias (metres) is added to every height: the terrain data's heights differ from the game's ground by a few metres, and
-- the route has to lie on the ground the game has (measured at the start by core.lua).
function M.toWorld(points, anchor, zBias)
  local out = {}
  for i, p in ipairs(points) do
    local x, y, z = anchor:geodeticToWorld(p[1], p[2], p[3])
    out[i] = { x, y, z + (zBias or 0), lat = p[1], lon = p[2], h = p[3] }
  end
  return out
end

-- The part of the route a player at (px, py) needs ground under first: sample points (every `step` metres, starting at the
-- player) along the legs from the nearest route point to the next timed stop (the end of a leg, further than `minStop`
-- metres ahead: one the player stands at does not count), or `maxM` metres, whichever comes first. wlegs: the legs'
-- world points in route order (toWorld). nil when the player is further than `snapM` from the route.
function M.corridor(wlegs, px, py, maxM, step, minStop, snapM)
  local bk, bi, bd = nil, nil, math.huge
  for k, w in ipairs(wlegs) do
    for i, p in ipairs(w) do
      local d = (p[1] - px) ^ 2 + (p[2] - py) ^ 2
      if d < bd then bk, bi, bd = k, i, d end
    end
  end
  if not bk or math.sqrt(bd) > (snapM or 400) then return nil end
  local out, acc, lastAt = { { px, py } }, 0, 0
  local k, i = bk, bi
  local prev = wlegs[k][i]
  while true do
    if i >= #wlegs[k] then                       -- the end of a leg: a timed stop
      if acc >= minStop or k >= #wlegs then break end
      k, i = k + 1, 1                            -- (the one we stand at: on to the next leg, which starts here)
      prev = wlegs[k][1]
    else
      i = i + 1
      local p = wlegs[k][i]
      local len = math.sqrt((p[1] - prev[1]) ^ 2 + (p[2] - prev[2]) ^ 2)
      if acc + len >= maxM then                  -- the cap falls inside this segment: stop exactly there
        local f = len > 1e-9 and (maxM - acc) / len or 0
        prev = { prev[1] + (p[1] - prev[1]) * f, prev[2] + (p[2] - prev[2]) * f }
        break
      end
      acc = acc + len
      prev = p
      if acc - lastAt >= step then
        out[#out + 1] = { p[1], p[2] }
        lastAt = acc
      end
    end
  end
  out[#out + 1] = { prev[1], prev[2] }           -- (where it stopped: the timed stop, or the end of the stretch)
  return out
end

-- The distance from point p to the segment a-b.
local function distToSegment(p, a, b)
  local vx, vy, vz = b[1] - a[1], b[2] - a[2], b[3] - a[3]
  local vv = vx * vx + vy * vy + vz * vz
  local t = vv < 1e-12 and 0 or math.max(0, math.min(1, ((p[1] - a[1]) * vx + (p[2] - a[2]) * vy + (p[3] - a[3]) * vz) / vv))
  return dist(p, { a[1] + vx * t, a[2] + vy * t, a[3] + vz * t })
end

-- Every point at least `step` metres from the last one kept (the first and the last always kept), and the ones in `keep`
-- (a set of indices: the points the race path is built on, see M.driveline), and where leaving points out would cut a corner:
-- a point is kept when a straight line from the last kept point to the next one would pass more than `tol` metres from
-- the points left out (M.DRIVELINE_TOL_M).
local function thin(pts, step, keep, tol)
  local out, lastAt, acc = { pts[1] }, 1, 0
  for i = 2, #pts - 1 do
    acc = acc + dist(pts[i], pts[i - 1])
    local take = acc >= step or (keep and keep[i])
    if not take and tol then
      for j = lastAt + 1, i do
        if distToSegment(pts[j], pts[lastAt], pts[i + 1]) > tol then take = true; break end
      end
    end
    if take then
      out[#out + 1] = pts[i]
      lastAt, acc = i, 0
    end
  end
  out[#out + 1] = pts[#pts]
  return out
end

local function tangents(pts)
  local t = {}
  for i = 1, #pts do
    local a, b = pts[math.max(1, i - 1)], pts[math.min(#pts, i + 1)]
    t[i] = unit(b[1] - a[1], b[2] - a[2], b[3] - a[3])
  end
  return t
end

local function length(pts)
  local l = 0
  for i = 2, #pts do l = l + dist(pts[i], pts[i - 1]) end
  return l
end

-- The points up to the last one at least `m` metres (along the path) before the end.
local function trimEnd(pts, m)
  local acc = 0
  for i = #pts - 1, 1, -1 do
    acc = acc + dist(pts[i], pts[i + 1])
    if acc >= m then
      local out = {}
      for k = 1, i do out[k] = pts[k] end
      return out
    end
  end
  return { pts[1], pts[math.min(2, #pts)] }
end

-- The index of the path's first point at least `m` metres along it (`before`: its last point at most `m` along).
function M.indexAlong(pts, m, before)
  local acc = 0
  if m <= 0 then return 1 end
  for i = 2, #pts do
    local nxt = acc + dist(pts[i], pts[i - 1])
    if nxt >= m then return (before and nxt > m) and i - 1 or i end
    acc = nxt
  end
  return #pts
end

-- The path with a point `before` metres behind its start and one `after` metres past its end, on the straight lines
-- the first and last segments make.
local function extendEnds(pts, before, after)
  local out = {}
  local a, b = pts[1], pts[2]
  local t0 = unit(a[1] - b[1], a[2] - b[2], a[3] - b[3])
  out[1] = { a[1] + t0[1] * before, a[2] + t0[2] * before, a[3] + t0[3] * before }
  for i, p in ipairs(pts) do out[i + 1] = p end
  local y, z = pts[#pts - 1], pts[#pts]
  local t1 = unit(z[1] - y[1], z[2] - y[2], z[3] - y[3])
  out[#out + 1] = { z[1] + t1[1] * after, z[2] + t1[2] * after, z[3] + t1[3] * after }
  return out
end

-- drivelineSpline.json for a leg (rally/drivelineSpline.json): nodes, forward tangents, widths, properties.
-- `keep` (a set of indices into wpts): the points the thinning keeps whatever the spacing. The race nodes are on the line
-- the game measures the race distance and the progress along, and a node 30 m from the road's last corner but where thinning
-- cut it off was 12 m beside the line: the game projects it on the line's straight extension, further along, and the
-- liaison of the Popoli loop read 0.10 km for 0.14. Where the road turns the nodes come closer (M.DRIVELINE_TOL_M).
function M.driveline(leg, wpts, keep)
  local pts = extendEnds(thin(wpts, M.DRIVELINE_STEP_M, keep, M.DRIVELINE_TOL_M), M.LEAD_IN_M, M.RUN_OUT_M)
  local tn = tangents(pts)
  local nodes, nmls, widths = {}, {}, {}
  for i, p in ipairs(pts) do
    nodes[i] = { p[1], p[2], p[3] }
    nmls[i] = tn[i]
    widths[i] = M.ROAD_WIDTH
  end
  local lenM = length(wpts)
  local speed = leg.speedLimitKph or (leg.type == 'transfer' and 80 or 100)
  return {
    name = M.DRIVELINE_NAME, version = 1, isLoop = false,
    nodes = nodes, nmls = nmls, widths = widths,
    properties = {
      speedLimitKph = speed,
      liaisonAllocatedTimeMins = math.max(1, math.ceil(lenM / 1000 / speed * 60)),
      useRaycast = false, vertRayRaise = 2,
    },
    stats = { raceDistanceKms = math.floor(lenM) / 1000, surfacePercentages = { tarmac = 100 } },
  }
end

local function startPosition(name, oldId, p, tn)
  local q = yawQuat(tn[1], tn[2])
  return { group = 'start', name = name, oldId = oldId, pos = { p[1], p[2], p[3] }, rot = q }
end

-- race.race.json for a leg. A transfer's start positions are TC_in / TC_out, a special's SS_start_line /
-- SS_stop_control (what rallyManager:getTCInPos ... getSSStopControlPos look up by name).
-- Where a leg's race nodes go, in metres along it, and which are splits. The route builder fixes every race node and,
-- off its line, re-plans to the next one, which counts as passed only inside its radius: a node on the other
-- carriageway, under a building or on a wonky stretch of the source road meant "do a U-turn" whichever way. So the
-- race path is only what timing needs: a node on the start (the stock first node sits on the start position, where the
-- car is), on a special a split checkpoint every SPLIT_EVERY_M (none closer than SPLIT_MIN_TO_STOP_M to the stop), and
-- the node the route builder needs LAST_NODE_BEFORE_STOP_M before the stop; all wide. The driveline gives the distance
-- and the arrows; on a special the player keeps to the road through the closures at its exits and the pace notes
-- (liaisons, specials).
function M.raceNodes(leg, wpts)
  local total = length(wpts)
  local out = { { at = M.LIAISON_FIRST_NODE_M, split = false } }
  if leg.type == 'special' then
    local k = 1
    while k * M.SPLIT_EVERY_M <= total - M.SPLIT_MIN_TO_STOP_M do
      out[#out + 1] = { at = k * M.SPLIT_EVERY_M, split = true }
      k = k + 1
    end
  end
  out[#out + 1] = { at = math.max(0, total - M.LAST_NODE_BEFORE_STOP_M), split = false }
  return out, leg.type == 'special' and M.STAGE_NODE_RADIUS or M.LIAISON_NODE_RADIUS
end

-- inDir / outDir (optional, unit {dx, dy, dz}): the direction the control at the start and at the end faces, where the leg
-- is joined to the one before / after it (M.joins: the one direction both legs take there); else the leg's own first and last
-- segment.
-- The indices of the route's own points the race nodes stand on: the first at or past each node's distance, <= 20 m on (the
-- lat/lon truth the rebase probe checks); the last one at or before it, so it stays short of the stop.
function M.raceNodeIndices(leg, wpts)
  local spec = M.raceNodes(leg, wpts)
  local idx = {}
  for i, s in ipairs(spec) do idx[i] = M.indexAlong(wpts, s.at, i == #spec) end
  return idx
end

function M.race(leg, wpts, inDir, outDir)
  local spec, radius = M.raceNodes(leg, wpts)
  local pts, tn, splits = {}, {}, {}
  for i, s in ipairs(spec) do
    local k = M.indexAlong(wpts, s.at, i == #spec)
    pts[i] = wpts[k]
    local a, b = wpts[math.max(1, k - 1)], wpts[math.min(#wpts, k + 1)]    -- (the road's way there)
    tn[i] = unit(b[1] - a[1], b[2] - a[2], b[3] - a[3])
    splits[i] = s.split
  end
  local ends = tangents({ wpts[1], wpts[2], wpts[#wpts - 1], wpts[#wpts] })
  local tFirst, tLast = inDir or ends[1], outDir or ends[4]
  local nodes, segments = {}, {}
  for i, p in ipairs(pts) do
    nodes[i] = {
      customFields = { names = {}, tags = {}, types = {}, values = {} },
      mode = 'manual', name = 'Pathnode ' .. i, navRadiusScale = 1,
      normal = tn[i], oldId = i, pos = { p[1], p[2], p[3] }, radius = radius,
      recovery = -1, reverseRecovery = -1, sidePadding = { 1, 3 }, useAsSplit = splits[i], visible = true,
    }
  end
  for i = 1, #pts - 1 do
    segments[i] = { capsules = {}, from = i, mode = 'waypoint', name = 'Segment ' .. i .. '->' .. (i + 1), oldId = #pts + 4 + i, to = i + 1 }
  end
  local first, last = #pts + 1, #pts + 2
  local nameA, nameB = 'TC_in', 'TC_out'
  if leg.type == 'special' then nameA, nameB = 'SS_start_line', 'SS_stop_control' end
  return {
    authors = 'BeamNG.Dakar', date = 0, defaultLaps = 1, defaultStartPosition = first, description = '',
    difficulty = 0, endNode = #pts, startNode = 1, name = '', hideMission = false,
    classification = { allowRollingStart = false, branching = false, closed = false, reversible = false },
    forwardPrefabs = {}, reversePrefabs = {}, prefabs = {}, pacenotes = {},
    reverseStartPosition = -1, rollingReverseStartPosition = -1, rollingStartPosition = -1,
    pathnodes = nodes, segments = segments,
    startPositions = {
      startPosition(nameA, first, wpts[1], tFirst),
      startPosition(nameB, last, wpts[#wpts], tLast),
    },
  }
end

-- The loop's own world positions (the service stall, where the mission puts the car, and its start trigger).
--
-- When the first leg starts in a parking lot (tools/fetch_route.py: geometry servicePark.lead, the car's way from its spawn on
-- an aisle of the lot to TC0, a few metres ahead of it and inside the lot) the stall is the spawn, facing along
-- the lead: toward the exit, TC0 ahead of the car and nothing behind it to come back for. Otherwise it is backM metres behind
-- where the first leg begins (TC0), on the line of its first segment, facing it: the stock loops park the car 65-73 m from
-- TC0 (Italy ngrc25 65.3 m, Utah ngrc26 72.8 m); on TC0 itself the loop timed the player at once. The height
-- is TC0's or the lead's: the caller puts it on the game's ground there.
--
-- Two orientations, for the two places the game reads them: `rot` is the car's own (the stall: rallyMoveVehicleTo sets it on
-- the vehicle object as it is, and a vehicle's front is its -y), `dirRot` the way the car faces as quatFromDir has it (+y
-- -- along it: the mission's start trigger, which spawn.safeTeleport and the missions' POI list turn into a car's). The stock
-- loops' stalls turn their cars' backs to TC0 (Italy's 131 degrees off the way to it, Utah's 108), which is what the
-- dashboard's "Reverse out and reach TC0" says (app.rallyDashboard.vue, hard-coded for TC0 whatever the stall); ours faces
-- TC0, and the game's text stays as it is.
M.STALL_BACK_M = 70
M.SPAWN_FACING_M = 6        -- the car faces the way the lead goes over its first metres, not toward its first vertex
function M.loopPlacement(firstWpts, backM, lead)
  if lead and #lead >= 2 then
    local p, q = lead[1], lead[#lead]
    for i = 2, #lead do
      if dist(lead[i], p) >= M.SPAWN_FACING_M then q = lead[i]; break end
    end
    local t = unit(q[1] - p[1], q[2] - p[2], 0)
    return { pos = { p[1], p[2], p[3] }, rot = carQuat(t[1], t[2]), dirRot = yawQuat(t[1], t[2]), backM = length(lead), inLot = true }
  end
  local p = firstWpts[1]
  local t = unit(firstWpts[2][1] - p[1], firstWpts[2][2] - p[2], 0)
  local b = backM or M.STALL_BACK_M
  return { pos = { p[1] - t[1] * b, p[2] - t[2] * b, p[3] }, rot = carQuat(t[1], t[2]), dirRot = yawQuat(t[1], t[2]), backM = b }
end

-- ---------------------------------------------------------------------------------------------------------------------
-- The joins: the end of a leg and the start of the next are one place, whatever the legs are (TC0 -> liaison ->
-- TC1 -> special -> TC2 -> liaison -> TC3 -> special ..., liaisons after liaisons, specials after specials). The player is
-- driven into it from behind and goes on the way the next leg goes, so the control that ends a leg (TC_out, SS_stop_control)
-- and the one that starts the next (TC_in, SS_start_line) face the same way: along the road there. tools/joins.py makes the
-- geometry agree (the last metres of a leg and the first of the next are one straight run); this faces the controls by it.
M.JOIN_WINDOW_M = 10        -- the way a leg arrives / leaves is its last / first this many metres
M.JOIN_GAP_M = 30           -- two legs are joined when the second starts within this of where the first ends
M.JOIN_ALIGNED_DEG = 60     -- and their ways agree to within this: else the controls keep to their own leg and the probe says so

-- The unit direction (x, y, 0) a path leaves its first point (atEnd false) or arrives at its last point (atEnd true) in,
-- over the first / last JOIN_WINDOW_M metres.
local function dirAlong(wpts, atEnd)
  local n = #wpts
  local a, b
  if atEnd then
    b = wpts[n]
    for i = n - 1, 1, -1 do
      a = wpts[i]
      if dist(a, b) >= M.JOIN_WINDOW_M then break end
    end
  else
    a = wpts[1]
    for i = 2, n do
      b = wpts[i]
      if dist(a, b) >= M.JOIN_WINDOW_M then break end
    end
  end
  return unit(b[1] - a[1], b[2] - a[2], 0)
end
M.dirAlong = dirAlong

local function headingDeg(d) return math.deg(atan2(d[2], d[1])) end
local function turnDeg(a, b)
  return math.deg(math.acos(math.max(-1, math.min(1, a[1] * b[1] + a[2] * b[2]))))
end

-- The joins of a list of legs' world points: [i] = { from = i, to = i + 1, gap (m), arrive, leave (unit directions), diff
-- (degrees between them), dir (the direction both controls face: the mean of the two; nil when the legs are not joined or do
-- not agree) }.
function M.joins(wl)
  local out = {}
  for i = 1, #wl - 1 do
    local a, b = wl[i], wl[i + 1]
    local arrive, leave = dirAlong(a, true), dirAlong(b, false)
    local gap = dist(a[#a], b[1])
    local diff = turnDeg(arrive, leave)
    local dir
    if gap <= M.JOIN_GAP_M and diff <= M.JOIN_ALIGNED_DEG then dir = unit(arrive[1] + leave[1], arrive[2] + leave[2], 0) end
    out[i] = { from = i, to = i + 1, gap = gap, arrive = arrive, leave = leave, diff = diff, dir = dir }
  end
  return out
end

-- The heading (degrees counter-clockwise from +x, as props.yawFor has it) of the way a stored orientation points its local
-- +y (a time control's or a start position's way on), and of the way a vehicle with it faces (its front is local -y): both
-- read with the game's own rotation (M.rotate), not by undoing what the builders do, so a convention that is wrong in both
-- is seen as wrong.
local function facingDeg(q)
  local v = rotate(q, { 0, 1, 0 })
  return math.deg(atan2(v[2], v[1]))
end
local function carFacingDeg(q)
  local v = rotate(q, { 0, -1, 0 })
  return math.deg(atan2(v[2], v[1]))
end
local function offDeg(a, b)
  return math.abs((a - b + 180) % 360 - 180)
end

-- The log lines of what materialize made, for the probe at launch: every control's heading against the road's over its
-- first / last metres, every join's arriving and leaving ways, the car's spawn against the first metres of the road.
function M.probeLines(res)
  local out = {}
  for _, m in ipairs(res.markers or {}) do
    out[#out + 1] = string.format('DAKAR-PROBE marker %s %s: faces %.1f deg, the road there %.1f deg, off by %.1f',
      m.leg, m.name, m.facing, m.road, m.off)
  end
  for _, j in ipairs(res.joins or {}) do
    out[#out + 1] = string.format('DAKAR-PROBE join %s -> %s: %.1f m apart, arrives %.1f deg, leaves %.1f deg, off by %.1f%s',
      j.fromId, j.toId, j.gap, headingDeg(j.arrive), headingDeg(j.leave), j.diff,
      j.dir and '' or ' (not one road: each control faces its own leg)')
  end
  local pl = res.placement
  if pl and pl.road then
    out[#out + 1] = string.format('DAKAR-PROBE spawn: faces %.1f deg, the road there %.1f deg, off by %.1f; TC0 %.0f m ahead%s',
      pl.facing, pl.road, offDeg(pl.facing, pl.road), pl.backM or 0, pl.inLot and ', inside the lot' or ' (the stall stands back from it)')
  end
  return out
end
M.facingDeg = facingDeg
M.carFacingDeg = carFacingDeg

-- Everything for an edition at an anchor: { files = { [path under the mission folders] = table },
-- placement = loopPlacement, lengths = { [leg id] = metres }, corners = { [special leg id] = the corners its notebook calls },
-- noteSummaries = one log line per special about its notebook (pacenotes.summary),
-- joins = M.joins (with the leg ids as fromId / toId), markers = every control's heading against its road (M.probeLines) }.
-- `missionDirOf(leg)` names a leg's mission folder.
-- `curated` (curate.lua): { [special leg id] = a curated line }; a special with one is built from it (the
-- race path, the driveline and the pace notes all from the same cleaned line the ground is shaped to), else from its
-- source geometry.
function M.materialize(edition, geometry, anchor, missionDirOf, zBias, curated)
  local files, lengths, notes, markers, summaries = {}, {}, {}, {}, {}
  local wl = {}
  for i, leg in ipairs(edition.legs) do
    local g = geometry[leg.id]
    if not g then return nil, 'no geometry for leg ' .. tostring(leg.id) end
    local c = curated and curated[leg.id]
    wl[i] = M.toWorld(c and c.points or g.points, anchor, zBias)
    if #wl[i] < 2 then return nil, 'leg ' .. leg.id .. ' has fewer than 2 points' end
  end
  local joins = M.joins(wl)
  for i, j in ipairs(joins) do j.fromId, j.toId = edition.legs[i].id, edition.legs[i + 1].id end
  for i, leg in ipairs(edition.legs) do
    local wpts = wl[i]
    -- (the one direction of a join, else the leg's own over its first / last metres: the last segment of a route can hook)
    local inDir = (i > 1 and joins[i - 1].dir) or dirAlong(wpts, false)
    local outDir = (i < #wl and joins[i].dir) or dirAlong(wpts, true)
    local dir = missionDirOf(leg)
    local race = M.race(leg, wpts, inDir, outDir)
    files[dir .. '/race.race.json'] = race
    local keep = {}
    for _, k in ipairs(M.raceNodeIndices(leg, wpts)) do keep[k] = true end
    files[dir .. '/rally/drivelineSpline.json'] = M.driveline(leg, wpts, keep)
    -- (every control faces along the road: the heading it was given against the road's own over the first / last metres)
    for k, own in ipairs({ dirAlong(wpts, false), dirAlong(wpts, true) }) do
      local sp = race.startPositions[k]
      local facing = facingDeg(sp.rot)
      markers[#markers + 1] = { leg = leg.id, name = sp.name, facing = facing, road = headingDeg(own), off = offDeg(facing, headingDeg(own)) }
    end
    -- a special is a fixed road, so its corners are known: the co-driver calls them. A liaison has no notes.
    -- The notebook holds world positions on the same route (and ground bias) as the race path, so it is written per anchor.
    if leg.type == 'special' then
      files[dir .. '/rally/notebooks/' .. M.NOTEBOOK .. '.notebook.json'], notes[leg.id] = pacenotes.notebook(wpts)
    end
    lengths[leg.id] = length(wpts)
    if notes[leg.id] then summaries[#summaries + 1] = leg.id .. ': ' .. pacenotes.summary(notes[leg.id], lengths[leg.id]) end
  end
  -- the first leg may start in a parking lot (geometry servicePark.lead, tools/fetch_route.py): the car's way to TC0 in it
  local lot = geometry[edition.legs[1].id].servicePark
  local lead = lot and lot.lead and M.toWorld(lot.lead, anchor, zBias) or nil
  local placement = M.loopPlacement(wl[1], edition.serviceStallBackM, lead)
  placement.facing = carFacingDeg(placement.rot)      -- (the car's real front, as the game turns the stall's orientation)
  placement.road = headingDeg(lead and dirAlong(lead, false) or dirAlong(wl[1], false))
  return { files = files, placement = placement, lengths = lengths, corners = notes, joins = joins, markers = markers,
    noteSummaries = summaries }
end

return M
