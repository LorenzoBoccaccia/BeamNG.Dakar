-- Stage curation: a special stage's source line (lat, lon, ground height), cleaned into the line the loop
-- is built on and the ground is shaped along (its plan: since the ground is Earth's own but where a defect of the data is
-- fixed, so its heights are for the driveline, the race path and the pace notes, which are put on the ground where the car is).
--
-- Pure (no game calls). The source is what tools/fetch_route.py wrote: OSRM's route over OpenStreetMap roads, with the
-- ground height of a terrain tile under every point. Both have faults a rally road does not: the router makes spurs and
-- zigzags at its via points, and the terrain data, a pixel every 38 m or so, puts a hillside's noise into the road's
-- profile (the Svolte di Popoli's profile climbs 55 percent in places; its real road averages 6). The line comes out
--   - without out-and-back spurs, repeated points and one-point zigzags (the plan),
--   - at an even step, with its height profile cleaned: single spikes taken out, the grade held to MAX_GRADE and its
--     change to MAX_GRADE_CHANGE, never more than MAX_DEV_M from what the source said,
--   - with the stretches that are a bridge or a tunnel marked (M.line's spans) so the ground is left alone there.
-- The plan is not smoothed: outside a repair every point is on the source line, so what the road does round a corner is
-- what the source says (the pace notes read it).
--
-- A bridge or a tunnel shows in the source profile as an excursion the road could not make: the terrain under a viaduct
-- is the valley (the profile dives and climbs back at a grade no road has), the terrain over a tunnel is the hill. Where
-- two steep runs of opposite sign close within SPAN_MAX_M and the profile departs from the straight line between their
-- ends by SPAN_MIN_DEPTH_M, that is a span; its height is that straight line (the deck, the tunnel floor). The source has
-- no tags of what is a bridge: a geometry file may carry "structures" ({ from, to, kind } in metres along the line) from
-- OpenStreetMap, and those spans are used as they are.
local M = {}

M.STEP_M = 10                 -- the curated line's point spacing
M.DUP_M = 0.3                 -- source points closer than this to the last kept one are repeats
-- an out-and-back spur (the router's detour to a via point): the line comes back within STUB_CLOSE_M of where it was
-- after at least STUB_MIN_LEN_M, along what is a thin spur (its length about twice its reach), within STUB_WINDOW points
M.STUB_CLOSE_M = 2.5
M.STUB_MIN_LEN_M = 6
M.STUB_WINDOW = 12
-- a one-point zigzag: a turn of KINK_TURN_DEG or more between neighbours that turn the other way by KINK_SIDE_DEG or more
M.KINK_TURN_DEG = 40
M.KINK_SIDE_DEG = 12
M.KINK_OFFSET_M = 2.5         -- ... and that point is at least this far off the line between its neighbours
-- a spike in the profile: a height this far from the median of the points within SPIKE_HALF steps either side
M.SPIKE_HALF = 4
M.SPIKE_M = 2.5
M.SPIKES_PER_KM_ROUGH = 4     -- more spikes than this per km: the source profile is noise, not a road
-- a bridge or a tunnel (see the head of the file)
M.SPAN_GRADE = 0.30
M.SPAN_MAX_M = 600
M.SPAN_MIN_DEPTH_M = 8
-- the road-like limits of the cleaned profile: grade (rise over run), its change per metre of road (0.004 is a crest or
-- a dip of 4 percent over 10 m), and how far from the source's height the profile may go
M.SMOOTH_SIGMA_STEPS = 2
M.MAX_GRADE = 0.20
M.MAX_GRADE_CHANGE = 0.004
M.MAX_DEV_M = 5
M.ITERATIONS = 600

local sqrt, abs, floor, cos, rad, deg = math.sqrt, math.abs, math.floor, math.cos, math.rad, math.deg
local atan2 = math.atan2 or function(y, x) return math.atan(y, x) end
local sort = table.sort

-- metres per degree of latitude and longitude at a latitude (WGS84 series)
local function scales(lat)
  local p = rad(lat)
  local mLat = 111132.92 - 559.82 * cos(2 * p) + 1.175 * cos(4 * p)
  local mLon = 111412.84 * cos(p) - 93.5 * cos(3 * p)
  return mLat, mLon
end

local function sign(v) return v > 0 and 1 or (v < 0 and -1 or 0) end

local function median(list)
  local c = {}
  for i = 1, #list do c[i] = list[i] end
  sort(c)
  local n = #c
  if n % 2 == 1 then return c[(n + 1) / 2] end
  return (c[n / 2] + c[n / 2 + 1]) / 2
end

-- the signed turn (degrees, positive to the left) at each interior vertex of a plan
local function turns(X, Y)
  local t = {}
  for i = 2, #X - 1 do
    local a1 = atan2(Y[i] - Y[i - 1], X[i] - X[i - 1])
    local a2 = atan2(Y[i + 1] - Y[i], X[i + 1] - X[i])
    local d = deg(a2 - a1)
    d = d - 360 * floor((d + 180) / 360)
    t[i] = d
  end
  return t
end

-- Source points without the repeats, as plan arrays X, Y and heights H (the last point always kept).
local function dedupe(X, Y, H, dup)
  local ox, oy, oh = { X[1] }, { Y[1] }, { H[1] }
  for i = 2, #X do
    local dx, dy = X[i] - ox[#ox], Y[i] - oy[#oy]
    if sqrt(dx * dx + dy * dy) >= dup then
      ox[#ox + 1], oy[#oy + 1], oh[#oh + 1] = X[i], Y[i], H[i]
    elseif i == #X and #ox > 1 then
      ox[#ox], oy[#oy], oh[#oh] = X[i], Y[i], H[i]
    end
  end
  return ox, oy, oh
end

-- Out-and-back spurs taken out: from the point where the line leaves to the one where it is back, only the first stays.
local function removeStubs(X, Y, H, o)
  local ox, oy, oh, removed = {}, {}, {}, 0
  local i, n = 1, #X
  while i <= n do
    ox[#ox + 1], oy[#oy + 1], oh[#oh + 1] = X[i], Y[i], H[i]
    local jump = nil
    local plen, reach = 0, 0
    for j = i + 1, math.min(n, i + o.STUB_WINDOW) do
      plen = plen + sqrt((X[j] - X[j - 1]) ^ 2 + (Y[j] - Y[j - 1]) ^ 2)
      local d = sqrt((X[j] - X[i]) ^ 2 + (Y[j] - Y[i]) ^ 2)
      if d < o.STUB_CLOSE_M and j > i + 1 and plen >= o.STUB_MIN_LEN_M and reach >= 3 and plen <= 2.6 * reach + 4 and j < n then
        jump = j
        break
      end
      if d > reach then reach = d end
    end
    if jump then
      removed = removed + 1
      i = jump + 1            -- (the point the line is back at goes with the spur: it is where we are)
    else
      i = i + 1
    end
  end
  return ox, oy, oh, removed
end

-- The line at an even step: arrays X, Y, H and S (metres along it), the last point the source's last.
local function resample(X, Y, H, step)
  local S = { 0 }
  for i = 2, #X do S[i] = S[i - 1] + sqrt((X[i] - X[i - 1]) ^ 2 + (Y[i] - Y[i - 1]) ^ 2) end
  local total = S[#S]
  local rx, ry, rh, rs = {}, {}, {}, {}
  local k = 1
  local nSteps = floor(total / step + 1e-9)
  local function at(s)
    while k < #S - 1 and S[k + 1] < s do k = k + 1 end
    local d = S[k + 1] - S[k]
    local f = d > 1e-9 and (s - S[k]) / d or 0
    return X[k] + (X[k + 1] - X[k]) * f, Y[k] + (Y[k + 1] - Y[k]) * f, H[k] + (H[k + 1] - H[k]) * f
  end
  for q = 0, nSteps do
    local s = q * step
    rx[#rx + 1], ry[#ry + 1], rh[#rh + 1] = at(s)
    rs[#rs + 1] = s
  end
  if total - rs[#rs] < step * 0.25 then
    rx[#rx], ry[#ry], rh[#rh], rs[#rs] = X[#X], Y[#Y], H[#H], total
  else
    rx[#rx + 1], ry[#ry + 1], rh[#rh + 1], rs[#rs + 1] = X[#X], Y[#Y], H[#H], total
  end
  return rx, ry, rh, rs
end

-- One-point zigzags (on the source points, before the line is resampled: the resampling would round the apex off) put on
-- the line between their neighbours. Returns how many.
local function fixKinks(X, Y, o)
  local fixed = 0
  for _ = 1, 4 do
    local t = turns(X, Y)
    local any = false
    for i = 3, #X - 2 do
      local ti = t[i]
      if abs(ti) >= o.KINK_TURN_DEG and sign(t[i - 1]) == -sign(ti) and abs(t[i - 1]) >= o.KINK_SIDE_DEG
          and sign(t[i + 1]) == -sign(ti) and abs(t[i + 1]) >= o.KINK_SIDE_DEG then
        -- (how far it is off the line between its neighbours: a zigzag is metres, a tightly drawn bend is not)
        local cx, cy = X[i + 1] - X[i - 1], Y[i + 1] - Y[i - 1]
        local cl = sqrt(cx * cx + cy * cy)
        local off = cl > 1e-6 and abs((X[i] - X[i - 1]) * cy - (Y[i] - Y[i - 1]) * cx) / cl or 0
        if off >= o.KINK_OFFSET_M then
          X[i], Y[i] = (X[i - 1] + X[i + 1]) / 2, (Y[i - 1] + Y[i + 1]) / 2
          fixed, any = fixed + 1, true
          t = turns(X, Y)
        end
      end
    end
    if not any then break end
  end
  return fixed
end

-- Points whose height is a spike (a height further than SPIKE_M from the median of its neighbourhood) put on the line
-- between the nearest points that are not. Returns the indexes flagged and the largest departure.
local function removeSpikes(H, o)
  local n = #H
  local flagged, worst = {}, 0
  local isSpike = {}
  for i = 2, n - 1 do
    local win = {}
    for j = math.max(1, i - o.SPIKE_HALF), math.min(n, i + o.SPIKE_HALF) do win[#win + 1] = H[j] end
    local dev = H[i] - median(win)
    if abs(dev) > o.SPIKE_M then
      isSpike[i] = true
      flagged[#flagged + 1] = i
      if abs(dev) > worst then worst = abs(dev) end
    end
  end
  if #flagged > 0 then
    local out = {}
    for i = 1, n do out[i] = H[i] end
    for _, i in ipairs(flagged) do
      local a, b = i - 1, i + 1
      while isSpike[a] do a = a - 1 end
      while isSpike[b] do b = b + 1 end
      a, b = math.max(a, 1), math.min(b, n)
      out[i] = H[a] + (H[b] - H[a]) * (i - a) / math.max(b - a, 1)
    end
    for i = 1, n do H[i] = out[i] end
  end
  return flagged, worst
end

-- Bridges and tunnels from the profile: { { i0, i1, kind } } by point index, and H changed to the straight line across
-- them. A run is the consecutive steps steeper than SPAN_GRADE the same way (one step of gap allowed).
local function findSpans(H, step, o)
  local n = #H
  local g = {}
  for i = 1, n - 1 do g[i] = (H[i + 1] - H[i]) / step end
  local runs = {}
  local i = 1
  while i <= n - 1 do
    if abs(g[i]) > o.SPAN_GRADE then
      local j, s = i, sign(g[i])
      while true do
        if j + 1 <= n - 1 and abs(g[j + 1]) > o.SPAN_GRADE and sign(g[j + 1]) == s then j = j + 1
        elseif j + 2 <= n - 1 and abs(g[j + 2]) > o.SPAN_GRADE and sign(g[j + 2]) == s then j = j + 2
        else break end
      end
      runs[#runs + 1] = { a = i, b = j + 1, s = s }       -- (points a .. b: the run's rise)
      i = j + 1
    else
      i = i + 1
    end
  end
  local spans = {}
  local k = 1
  while k < #runs do
    local A, B = runs[k], runs[k + 1]
    if B.s == -A.s and (B.b - A.a) * step <= o.SPAN_MAX_M then
      local a0, b1 = A.a, B.b
      local depth, dsign = 0, 0
      for m = a0, b1 do
        local line = H[a0] + (H[b1] - H[a0]) * (m - a0) / (b1 - a0)
        local dd = H[m] - line
        if abs(dd) > depth then depth, dsign = abs(dd), sign(dd) end
      end
      if depth >= o.SPAN_MIN_DEPTH_M then
        local h0, h1 = H[a0], H[b1]
        for m = a0, b1 do H[m] = h0 + (h1 - h0) * (m - a0) / (b1 - a0) end
        spans[#spans + 1] = { i0 = a0, i1 = b1, kind = dsign < 0 and 'bridge' or 'tunnel', depth = depth }
        k = k + 2
      else
        k = k + 1
      end
    else
      k = k + 1
    end
  end
  return spans
end

local function gaussian(H, sigma)
  local k = math.max(1, floor(sigma * 3))
  local w, sum = {}, 0
  for q = -k, k do w[q] = math.exp(-0.5 * (q / sigma) ^ 2); sum = sum + w[q] end
  local n, out = #H, {}
  for i = 1, n do
    local acc = 0
    for q = -k, k do
      local j = i + q
      local v
      -- (past either end the profile continues as the same slope, so a climb is not bent at its ends)
      if j < 1 then v = 2 * H[1] - H[math.min(n, 2 - j)]
      elseif j > n then v = 2 * H[n] - H[math.max(1, 2 * n - j)]
      else v = H[j] end
      acc = acc + v * w[q]
    end
    out[i] = acc / sum
  end
  return out
end

-- The profile held to the limits: the change of grade, then the grade, relaxed point by point until nothing moves, with
-- the first and last points and the locked ones (a bridge's or tunnel's inside) where they are and no point further
-- than MAX_DEV_M from ref.
local function relax(H, ref, locked, step, o)
  local n = #H
  local cm, gm = o.MAX_GRADE_CHANGE * step * step, o.MAX_GRADE * step
  for _ = 1, o.ITERATIONS do
    local moved = 0
    for i = 2, n - 1 do
      if not locked[i] then
        local d2 = H[i - 1] - 2 * H[i] + H[i + 1]
        if abs(d2) > cm then
          local dl = (abs(d2) - cm) / 2 * sign(d2)
          H[i] = H[i] + dl
          moved = moved + abs(dl)
        end
      end
    end
    for i = 1, n - 1 do
      local dg = H[i + 1] - H[i]
      if abs(dg) > gm then
        local e = (abs(dg) - gm) * sign(dg)
        local lockA, lockB = i == 1 or locked[i], i + 1 == n or locked[i + 1]
        if lockA and lockB then e = 0
        elseif lockA then H[i + 1] = H[i + 1] - e
        elseif lockB then H[i] = H[i] + e
        else H[i] = H[i] + e / 2; H[i + 1] = H[i + 1] - e / 2 end
        moved = moved + abs(e)
      end
    end
    for i = 1, n do
      if i == 1 or i == n or locked[i] then
        H[i] = ref[i]
      else
        if H[i] > ref[i] + o.MAX_DEV_M then H[i] = ref[i] + o.MAX_DEV_M
        elseif H[i] < ref[i] - o.MAX_DEV_M then H[i] = ref[i] - o.MAX_DEV_M end
      end
    end
    if moved < 1e-4 then break end
  end
end

-- the largest grade and grade change (per metre) of a profile
local function extremes(H, step)
  local gmax, cmax = 0, 0
  for i = 1, #H - 1 do
    local g = abs(H[i + 1] - H[i]) / step
    if g > gmax then gmax = g end
  end
  for i = 2, #H - 1 do
    local c = abs(H[i - 1] - 2 * H[i] + H[i + 1]) / (step * step)
    if c > cmax then cmax = c end
  end
  return gmax, cmax
end

-- The distance of each curated point to the source line (the plan's displacement): a window of source segments follows
-- the point along, so this is linear in the length of the line.
local function planShift(X, Y, SX, SY)
  local worst, lo = 0, 1
  local m = #SX
  for i = 1, #X do
    local best, bestAt = math.huge, lo
    for j = math.max(1, lo - 40), math.min(m - 1, lo + 80) do
      local ax, ay, bx, by = SX[j], SY[j], SX[j + 1], SY[j + 1]
      local dx, dy = bx - ax, by - ay
      local l2 = dx * dx + dy * dy
      local t = l2 > 1e-9 and ((X[i] - ax) * dx + (Y[i] - ay) * dy) / l2 or 0
      t = t < 0 and 0 or (t > 1 and 1 or t)
      local px, py = ax + dx * t, ay + dy * t
      local d = sqrt((X[i] - px) ^ 2 + (Y[i] - py) ^ 2)
      if d < best then best, bestAt = d, j end
    end
    lo = bestAt
    if best > worst then worst = best end
  end
  return worst
end

-- The curated line of a special's source points.
--   points: { { lat, lon, h }, ... }   opts: any of the limits above, and structures = { { from, to, kind }, ... } in
--   metres along the line (what OpenStreetMap says is a bridge or a tunnel).
-- Returns { points = { { lat, lon, h } ... }, s = { metres along the line }, spans = { { i0, i1, s0, s1, kind } },
-- report = { ... } } or nil, why.
function M.line(points, opts)
  if type(points) ~= 'table' or #points < 2 then return nil, 'a line needs at least 2 points' end
  local o = {}
  for k, v in pairs(M) do if type(v) == 'number' then o[k] = v end end
  for k, v in pairs(opts or {}) do o[k] = v end
  local step = o.STEP_M
  local lat0, lon0 = points[1][1], points[1][2]
  local mLat, mLon = scales(lat0)
  local SX, SY, SH = {}, {}, {}
  for i, p in ipairs(points) do
    SX[i], SY[i], SH[i] = (p[2] - lon0) * mLon, (p[1] - lat0) * mLat, p[3] or 0
  end
  local srcLen = 0
  for i = 2, #SX do srcLen = srcLen + sqrt((SX[i] - SX[i - 1]) ^ 2 + (SY[i] - SY[i - 1]) ^ 2) end
  if srcLen < step * 2 then return nil, 'the line is shorter than ' .. (step * 2) .. ' m' end

  local X, Y, H = dedupe(SX, SY, SH, o.DUP_M)
  local stubs
  X, Y, H, stubs = removeStubs(X, Y, H, o)
  if #X < 2 then return nil, 'nothing left of the line' end
  local kinks = fixKinks(X, Y, o)
  local rx, ry, rh, rs = resample(X, Y, H, step)

  -- the height profile
  local src = {}                                  -- what the source said at each point (spikes and all)
  for i = 1, #rh do src[i] = rh[i] end
  local gSrc, cSrc = extremes(src, step)
  local H1 = {}
  for i = 1, #rh do H1[i] = rh[i] end
  local spikeIdx, spikeMax = removeSpikes(H1, o)
  local spikeEvents = 0                           -- (a spike is a run of flagged points: the source's one bad point reaches 3)
  for q, i in ipairs(spikeIdx) do
    if q == 1 or i ~= spikeIdx[q - 1] + 1 then spikeEvents = spikeEvents + 1 end
  end
  local spans = findSpans(H1, step, o)
  for _, st in ipairs(opts and opts.structures or {}) do
    local i0, i1 = floor(st.from / step + 0.5) + 1, floor(st.to / step + 0.5) + 1
    i0, i1 = math.max(1, i0), math.min(#H1, i1)
    if i1 > i0 then
      for m = i0, i1 do H1[m] = H1[i0] + (H1[i1] - H1[i0]) * (m - i0) / (i1 - i0) end
      spans[#spans + 1] = { i0 = i0, i1 = i1, kind = st.kind or 'bridge', tagged = true }
    end
  end
  sort(spans, function(a, b) return a.i0 < b.i0 end)
  local locked = {}
  for _, sp in ipairs(spans) do
    sp.s0, sp.s1 = rs[sp.i0], rs[sp.i1]
    for m = sp.i0 + 1, sp.i1 - 1 do locked[m] = true end
  end
  local ref = {}
  for i = 1, #H1 do ref[i] = H1[i] end
  local H2 = gaussian(H1, o.SMOOTH_SIGMA_STEPS)
  for _, sp in ipairs(spans) do                    -- (the smoothing must not bend a deck or a tunnel floor)
    for m = sp.i0 + 1, sp.i1 - 1 do H2[m] = ref[m] end
  end
  relax(H2, ref, locked, step, o)
  local gOut, cOut = extremes(H2, step)

  -- back to lat/lon
  local out, flaggedSet = {}, {}
  for _, i in ipairs(spikeIdx) do flaggedSet[i] = true end
  for _, sp in ipairs(spans) do for m = sp.i0, sp.i1 do flaggedSet[m] = true end end
  local maxChange, maxKept, sumSq = 0, 0, 0
  for i = 1, #rx do
    out[i] = { lat0 + ry[i] / mLat, lon0 + rx[i] / mLon, H2[i] }
    local d = abs(H2[i] - src[i])
    sumSq = sumSq + d * d
    if d > maxChange then maxChange = d end
    if not flaggedSet[i] and d > maxKept then maxKept = d end
  end
  local lenM = rs[#rs]
  local report = {
    sourcePoints = #points, points = #out, lengthM = lenM,
    stubs = stubs, kinks = kinks,
    spikes = spikeEvents, spikePoints = #spikeIdx, spikeMaxM = spikeMax,
    spikesPerKm = spikeEvents / math.max(lenM / 1000, 0.001),
    spans = #spans,
    gradeSource = gSrc, gradeOut = gOut, gradeChangeSource = cSrc, gradeChangeOut = cOut,
    heightChangeMax = maxChange, heightChangeKeptMax = maxKept, heightChangeRms = sqrt(sumSq / #rx),
    planShiftMax = planShift(rx, ry, SX, SY),
  }
  report.rough = report.spikesPerKm >= o.SPIKES_PER_KM_ROUGH
  return { points = out, s = rs, spans = spans, report = report, step = step }
end

-- The spikes of a height profile H (points evenly spaced): the indexes flagged, the largest departure, and the profile with them put on
-- the line between the nearest points that are not. A point is a spike when it stands more than opts.SPIKE_M off the straight line
-- between the points k steps either side of it, taken for k = 1 .. SPIKE_HALF (fewer near a profile's ends, so that the points compared
-- are on both sides) and the median of those departures: a steady grade is no departure at all and a crest or a hillside only a little
-- (4 percent change of grade per 10 m, the limit of a road, is 1.3 m), where the median-of-heights test of M.profile misses a spike
-- smaller than the climb's step and flags the points at a profile's end on a steep climb. The ground shaping (shape.lua) uses it, on
-- the game's own ground under the line, to find the defects of the elevation data it fixes, with a lower SPIKE_M; it
-- used to smooth that profile whole (M.profile), which moved every cell of the road and stranded Earth's road lines.
function M.despike(H, opts)
  local o = {}
  for k, v in pairs(M) do if type(v) == 'number' then o[k] = v end end
  for k, v in pairs(opts or {}) do o[k] = v end
  local n = #H
  local out = {}
  for i = 1, n do out[i] = H[i] end
  local flagged, isSpike, worst = {}, {}, 0
  for i = 2, n - 1 do
    local devs = {}
    for k = 1, math.min(o.SPIKE_HALF, i - 1, n - i) do devs[#devs + 1] = H[i] - (H[i - k] + H[i + k]) / 2 end
    local dev = median(devs)
    if abs(dev) > o.SPIKE_M then
      isSpike[i] = true
      flagged[#flagged + 1] = i
      if abs(dev) > worst then worst = abs(dev) end
    end
  end
  for _, i in ipairs(flagged) do
    local a, b = i - 1, i + 1
    while isSpike[a] do a = a - 1 end
    while isSpike[b] do b = b + 1 end
    a, b = math.max(a, 1), math.min(b, n)
    out[i] = H[a] + (H[b] - H[a]) * (i - a) / math.max(b - a, 1)
  end
  return flagged, worst, out
end

-- Is the stretch between curated points i and i + 1 inside a bridge or a tunnel?
function M.spanAt(result, i)
  for _, sp in ipairs(result.spans) do
    if i >= sp.i0 and i + 1 <= sp.i1 then return sp end
  end
  return nil
end

-- One log line for a curated line.
function M.summary(name, r)
  local p = r.report
  return string.format('%s curated: %d -> %d points, %.1f km; plan: %d spur%s, %d zigzag%s removed, line moved %.1f m at most; ' ..
    'profile: %d spike%s (%.1f per km%s, largest %.1f m), %d bridge/tunnel span%s; grade %.0f%% -> %.0f%%, grade change %.1f%% -> ' ..
    '%.1f%% per 10 m; largest height change %.1f m (%.1f m outside spikes and spans), rms %.2f m',
    name, p.sourcePoints, p.points, p.lengthM / 1000, p.stubs, p.stubs == 1 and '' or 's', p.kinks, p.kinks == 1 and '' or 's',
    p.planShiftMax, p.spikes, p.spikes == 1 and '' or 's', p.spikesPerKm, p.rough and ', ROUGH' or '', p.spikeMaxM, p.spans,
    p.spans == 1 and '' or 's', p.gradeSource * 100, p.gradeOut * 100, p.gradeChangeSource * 1000, p.gradeChangeOut * 1000,
    p.heightChangeMax, p.heightChangeKeptMax, p.heightChangeRms)
end

return M
