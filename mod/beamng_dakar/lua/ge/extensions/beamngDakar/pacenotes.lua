-- Pace notes (GitHub #34): the corners of a special stage, read from its geometry, written as the stock structured
-- notebook the rally loop reads (<mission>/rally/notebooks/structured.notebook.json).
--
-- Pure (no game calls). The points are world points {x, y, z} (legs.toWorld), x east and y north, so a turn to the left
-- is a positive change of heading, which is also how the game's own corner measurement names a direction.
--
-- What the game does with a notebook (BeamNG.drive lua/ge/extensions/gameplay/rally, read, not run): a pacenote is two
-- waypoints on the road, "corner start" and "corner end", and a structured item { type = 'corner' } in slot 4. The game
-- measures the road between the two waypoints itself (a circle fitted through the driveline there) and takes the
-- direction, the sharpness (the circle's diameter, 1 = hardest to 6 = flattest, the bounds of the stock english
-- numbering) and the length word ("long", "extra long") from that, and fills the distance calls between notes from the
-- gaps. So the job here is where the corners are: their start and end. Everything else written (the descriptor of a
-- hairpin or a square corner, "opens" and "tightens") is the same judgement a stock notebook author makes by hand, from
-- the same geometry.
local M = {}

M.STEP_M = 5               -- the route is looked at every 5 m (the source geometry is <= 20 m)
M.SMOOTH_STEPS = 3         -- curvature is the change of heading over this many steps either side (30 m or so)
M.ENTER_RADIUS_M = 170     -- a corner begins where the road turns tighter than this ...
M.EXIT_RADIUS_M = 260      -- ... and goes on until it relaxes past this (so one bend is one corner, not several)
M.MIN_ANGLE_DEG = 14       -- a bend that turns less than this is not a corner
M.MERGE_GAP_M = 20         -- two bends the same way this close are one corner
M.TRIM = 0.05              -- the corner runs from the first 5% of its turn to the last 5% (the smoothing spreads it)
M.MIN_LENGTH_M = 15        -- the game wants a few road points between the two waypoints to measure the corner
M.MIN_GAP_M = 3            -- between one note's end and the next one's start
M.START_CLEAR_M = 5        -- nothing before this (the start line) ...
M.END_CLEAR_M = 10         -- ... and nothing this close to the end: the route builder drops what lies past the stop
M.WAYPOINT_RADIUS = 8      -- the stock notebooks' waypoint radius

-- The stock english 1-6 numbering (compositors/styles/english/numbering/one_to_six.lua): the diameter of the circle the
-- corner is fitted with, in metres, up to which each number is called.
M.DIAMETER_BOUNDS = { 35, 65, 116, 190, 260 }

local sqrt, abs, floor, min, max = math.sqrt, math.abs, math.floor, math.min, math.max
local pi = math.pi
local atan2 = math.atan2 or function(y, x) return math.atan(y, x) end

-- 1 (a hairpin's circle) .. 6 (nearly straight) for a corner fitted with this diameter
function M.severity(diameterM)
  for i, bound in ipairs(M.DIAMETER_BOUNDS) do
    if diameterM < bound then return i end
  end
  return #M.DIAMETER_BOUNDS + 1
end

-- The route as points every `step` metres along the ground (the last one is the route's end), with the length along it
-- (s), x, y, z, and the heading of the road at each point (theta, radians, continuous: it does not wrap at +-pi).
local function resample(pts, step)
  local X, Y, Z, S = { pts[1][1] }, { pts[1][2] }, { pts[1][3] }, { 0 }
  local acc, nextS = 0, step
  for i = 2, #pts do
    local a, b = pts[i - 1], pts[i]
    local d = sqrt((b[1] - a[1]) ^ 2 + (b[2] - a[2]) ^ 2)
    while d > 0 and nextS <= acc + d + 1e-9 do
      local f = (nextS - acc) / d
      X[#X + 1], Y[#Y + 1], Z[#Z + 1], S[#S + 1] = a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f,
        a[3] + (b[3] - a[3]) * f, nextS
      nextS = nextS + step
    end
    acc = acc + d
  end
  if acc - S[#S] > step * 0.25 then
    local l = pts[#pts]
    X[#X + 1], Y[#Y + 1], Z[#Z + 1], S[#S + 1] = l[1], l[2], l[3], acc
  end
  local n = #X
  local h = {}                                   -- the heading of each step
  for j = 1, n - 1 do
    local v = atan2(Y[j + 1] - Y[j], X[j + 1] - X[j])
    if j > 1 then v = v - 2 * pi * floor((v - h[j - 1]) / (2 * pi) + 0.5) end
    h[j] = v
  end
  local theta = {}
  for i = 1, n do
    theta[i] = i == 1 and h[1] or (i == n and h[n - 1] or (h[i - 1] + h[i]) / 2)
  end
  return { n = n, x = X, y = Y, z = Z, s = S, h = h, theta = theta, length = S[n] }
end

-- the index below the length s (and the fraction of the way to the next point)
local function locate(r, s)
  s = max(0, min(r.length, s))
  local lo, hi = 1, r.n
  while hi - lo > 1 do
    local mid = floor((lo + hi) / 2)
    if r.s[mid] <= s then lo = mid else hi = mid end
  end
  local d = r.s[hi] - r.s[lo]
  return lo, hi, d > 1e-9 and (s - r.s[lo]) / d or 0
end

-- The route's point at length s along it {x, y, z}
local function pointAt(r, s)
  local lo, hi, f = locate(r, s)
  return { r.x[lo] + (r.x[hi] - r.x[lo]) * f, r.y[lo] + (r.y[hi] - r.y[lo]) * f, r.z[lo] + (r.z[hi] - r.z[lo]) * f }
end

-- ... and its direction of travel (a unit vector)
local function directionAt(r, s)
  local a, b = pointAt(r, s - M.STEP_M / 2), pointAt(r, s + M.STEP_M / 2)
  local dx, dy, dz = b[1] - a[1], b[2] - a[2], b[3] - a[3]
  local l = sqrt(dx * dx + dy * dy + dz * dz)
  if l < 1e-9 then return { 0, 1, 0 } end
  return { dx / l, dy / l, dz / l }
end

local function thetaAt(r, s)
  local lo, hi, f = locate(r, s)
  return r.theta[lo] + (r.theta[hi] - r.theta[lo]) * f
end

-- Signed curvature (1/m, positive to the left) at each point: the change of heading over a few steps either side.
local function curvature(r)
  local k, K = {}, M.SMOOTH_STEPS
  for i = 1, r.n do
    local a, b = max(1, i - K), min(r.n - 1, i + K - 1)
    if b > a then
      local span = (r.s[b] + r.s[b + 1]) / 2 - (r.s[a] + r.s[a + 1]) / 2
      k[i] = span > 1e-9 and (r.h[b] - r.h[a]) / span or 0
    else
      k[i] = 0
    end
  end
  return k
end

-- The runs of points on which the road turns one way tighter than ENTER_RADIUS_M (with the hysteresis), merged when
-- two the same way are close together. { sign, i0, i1 } each (sign 1 = left).
local function runs(r, kappa)
  local kEnter, kExit = 1 / M.ENTER_RADIUS_M, 1 / M.EXIT_RADIUS_M
  local out, cur = {}, nil
  for i = 1, r.n do
    local k = kappa[i]
    local sign = k > 0 and 1 or -1
    if cur then
      if sign == cur.sign and abs(k) >= kExit then
        cur.i1 = i
      else
        out[#out + 1], cur = cur, nil
      end
    end
    if not cur and abs(k) >= kEnter then cur = { sign = sign, i0 = i, i1 = i } end
  end
  if cur then out[#out + 1] = cur end
  local merged = {}
  for _, run in ipairs(out) do
    local last = merged[#merged]
    if last and last.sign == run.sign and r.s[run.i0] - r.s[last.i1] <= M.MERGE_GAP_M then
      last.i1 = run.i1
    else
      merged[#merged + 1] = run
    end
  end
  return merged
end

-- Where along the route (length) the heading has turned `frac` of the way from the point `from` to the point `to`
-- (indexes), searched from `from` towards `to`. Interpolated between points.
local function crossing(r, from, to, frac)
  local t0 = r.theta[from]
  local want = frac * abs(r.theta[to] - t0)
  local step = to > from and 1 or -1
  local prev = from
  for i = from + step, to, step do
    local turned = abs(r.theta[i] - t0)
    if turned >= want then
      local before = abs(r.theta[prev] - t0)
      local f = turned > before and (want - before) / (turned - before) or 1
      return r.s[prev] + (r.s[i] - r.s[prev]) * f
    end
    prev = i
  end
  return r.s[to]
end

-- Hairpins and square corners are called by name, not by number; the bounds are what a stock notebook author calls so.
local function descriptor(angle, radius)
  if angle >= 130 then
    if radius < 9 then return 'tightHairpin' end
    if radius < 22 then return 'hairpin' end
    return 'openHairpin'
  end
  if angle >= 70 and radius <= 16 then return 'square' end
  return nil
end

-- "Opens" or "tightens": the second half of a long corner much wider or tighter than the first.
local function shapeOf(r, s0, s1, sign)
  if s1 - s0 < 40 then return nil end
  local sm = (s0 + s1) / 2
  local ta, tb = sign * (thetaAt(r, sm) - thetaAt(r, s0)), sign * (thetaAt(r, s1) - thetaAt(r, sm))
  local minTurn = 6 * pi / 180
  if ta < minTurn or tb < minTurn then return nil end
  local ratio = ((s1 - sm) / tb) / ((sm - s0) / ta)      -- the second half's radius over the first's
  if ratio <= 0.6 then return 'tightens' end
  if ratio >= 1.7 then return 'opens' end
  return nil
end

-- The corners of a route, in order of travel: { { s0, s1 (length along the route, m), direction = 'left' | 'right',
-- angle (degrees turned), radius (m, over the whole corner), severity (1-6), descriptor, shape, length }, ... }, and the
-- resampled route they are measured on (for placing the waypoints).
function M.scan(pts)
  if #pts < 3 then return {}, nil end
  local r = resample(pts, M.STEP_M)
  if r.n < 3 then return {}, r end
  local K = M.SMOOTH_STEPS
  local rs = runs(r, curvature(r))
  local corners = {}
  for k, run in ipairs(rs) do
    -- the corner starts and ends in the run's smoothing: look at the turn a little further out, but not into a neighbour
    local prevEnd, nextStart = rs[k - 1] and rs[k - 1].i1 or 0, rs[k + 1] and rs[k + 1].i0 or r.n + 1
    local lo = max(1, run.i0 - min(K, floor((run.i0 - prevEnd) / 2)))
    local hi = min(r.n, run.i1 + min(K, floor((nextStart - run.i1) / 2)))
    local turn = r.theta[hi] - r.theta[lo]
    local angle = abs(turn) * 180 / pi
    if hi > lo and angle >= M.MIN_ANGLE_DEG then
      local s0, s1 = crossing(r, lo, hi, M.TRIM), crossing(r, hi, lo, M.TRIM)
      if s1 <= s0 then s0, s1 = r.s[lo], r.s[hi] end
      local sweep = abs(turn) * (1 - 2 * M.TRIM)
      -- The radius is the circle through the corner's two ends that turns the road by the sweep (the game fits a circle
      -- too). Not the length over the sweep: the source points are 10-20 m apart and the heading changes at each, so
      -- the length between the first and last change overstates the arc and the radius with it.
      local a, b = pointAt(r, s0), pointAt(r, s1)
      local chord = sqrt((b[1] - a[1]) ^ 2 + (b[2] - a[2]) ^ 2)
      local radius = sweep < pi * 0.8 and chord / (2 * math.sin(sweep / 2)) or (s1 - s0) / sweep
      local c = {
        s0 = s0, s1 = s1, sign = run.sign, direction = run.sign > 0 and 'left' or 'right',
        angle = angle, radius = radius, severity = M.severity(2 * radius),
      }
      c.descriptor = descriptor(c.angle, radius)
      if not c.descriptor then c.shape = shapeOf(r, s0, s1, run.sign) end
      corners[#corners + 1] = c
    end
  end
  -- room for the game to measure each corner (a few road points between its two waypoints) ...
  for _, c in ipairs(corners) do
    local short = M.MIN_LENGTH_M - (c.s1 - c.s0)
    if short > 0 then c.s0, c.s1 = c.s0 - short / 2, c.s1 + short / 2 end
  end
  -- ... and between one corner and the next, where they run into each other
  for i = 2, #corners do
    local a, b = corners[i - 1], corners[i]
    if b.s0 < a.s1 + M.MIN_GAP_M then
      local mid = (a.s1 + b.s0) / 2
      a.s1, b.s0 = mid - M.MIN_GAP_M / 2, mid + M.MIN_GAP_M / 2
    end
  end
  local kept = {}
  for _, c in ipairs(corners) do
    c.s0, c.s1 = max(M.START_CLEAR_M, c.s0), min(r.length - M.END_CLEAR_M, c.s1)
    if c.s1 - c.s0 >= 6 then
      c.length = c.s1 - c.s0
      kept[#kept + 1] = c
    end
  end
  return kept, r
end

local function waypoint(r, name, wpType, s, id)
  return { name = name, normal = directionAt(r, s), oldId = id, pos = pointAt(r, s), radius = M.WAYPOINT_RADIUS, waypointType = wpType }
end

local function pacenote(r, c, i)
  local id = 3 * i - 1                      -- (the stock ids: the note, then its two waypoints)
  local corner = { type = 'corner' }
  if c.descriptor then corner.descriptor = c.descriptor end
  if c.shape then corner.shape = c.shape end
  return {
    audioMode = 1, distanceBeforeModifier = false, ignoreDistanceCalls = false, includeLinkWord = true, isolate = false,
    -- (not `calibration`: that is where the rally editor keeps what its author hears, for calibrating the numbering)
    metadata = { dakar = {
      direction = c.direction, severity = c.severity, radiusM = floor(c.radius * 10 + 0.5) / 10,
      angleDeg = floor(c.angle + 0.5), lengthM = floor(c.length + 0.5), atM = floor(c.s0 + 0.5),
    } },
    name = 'Pacenote ' .. i, oldId = id,
    pacenoteWaypoints = {
      waypoint(r, 'corner start', 'cornerStart', c.s0, id + 1),
      waypoint(r, 'corner end', 'cornerEnd', c.s1, id + 2),
    },
    pk = string.format('%08x', (i * 2654435761) % 4294967296),
    slowCorner = false, slowCornerReleaseType = 30, todo = false, triggerType = 1,
    structured = { items = { ['4'] = corner }, schemaVersion = 3 },
  }
end

-- The notebook for a route (world points): named 'structured', the one every stock voicepack lists last, so any
-- voicepack picks it up. Returns the notebook table and the corners it was made from.
function M.notebook(pts)
  local corners, r = M.scan(pts)
  local notes = {}
  for i, c in ipairs(corners) do notes[i] = pacenote(r, c, i) end
  return {
    audioMode = 4, authors = 'BeamNG.Dakar', created_at = 0, updated_at = 0, description = '', metadata = {},
    name = 'structured', pacenotes = notes, version = '4',
  }, corners
end

return M
