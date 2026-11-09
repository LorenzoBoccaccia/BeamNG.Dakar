-- Where the car is, top right of the screen and once a second in the log (GitHub #72): a screenshot then tells the place, and
-- the log line at the same second has the numbers (world x, y, z, latitude and longitude, the line and the metres along it,
-- how far off it, the ground under the car, the speed). Pure Lua: core.lua samples the game and draws.
local M = {}

M.PERIOD_S = 1.0          -- the readout and the log line are refreshed this often
M.NEAR_M = 60             -- a line further than this from the car is not named

-- The nearest point of a polyline pts = { {x, y[, z]} } to (x, y): metres along it, metres off it, and its z there (nil when
-- the points have none). nil for fewer than two points.
function M.along(pts, x, y)
  if not pts or #pts < 2 then return nil end
  local best, bestM, bestZ, run = math.huge, 0, nil, 0
  for i = 1, #pts - 1 do
    local a, b = pts[i], pts[i + 1]
    local dx, dy = b[1] - a[1], b[2] - a[2]
    local len2 = dx * dx + dy * dy
    local len = math.sqrt(len2)
    local t = 0
    if len2 > 0 then t = math.max(0, math.min(1, ((x - a[1]) * dx + (y - a[2]) * dy) / len2)) end
    local px, py = a[1] + t * dx, a[2] + t * dy
    local d = math.sqrt((x - px) ^ 2 + (y - py) ^ 2)
    if d < best then
      best, bestM = d, run + t * len
      bestZ = (a[3] and b[3]) and (a[3] + t * (b[3] - a[3])) or nil
    end
    run = run + len
  end
  return bestM, best, bestZ
end

-- The line nearest the car among lines = { { id = 'ss1', pts = {...} } }: { index, id, m, off, z } or nil (none within NEAR_M).
function M.nearest(lines, x, y)
  local hit
  for i, ln in ipairs(lines or {}) do
    local m, off, z = M.along(ln.pts, x, y)
    if m and off <= M.NEAR_M and (not hit or off < hit.off) then hit = { index = i, id = ln.id, m = m, off = off, z = z } end
  end
  return hit
end

-- The readout's lines from a sample s = { x, y, z, lat, lon, ground, speed (m/s), line = M.nearest(...) }.
function M.text(s)
  local out = { string.format('x %.1f  y %.1f  z %.1f', s.x, s.y, s.z) }
  if s.lat and s.lon then out[#out + 1] = string.format('%.6f, %.6f', s.lat, s.lon) end
  local g = s.ground and string.format('ground %.1f (car %+.1f)', s.ground, s.z - s.ground) or 'ground ?'
  out[#out + 1] = g .. string.format('  %.0f km/h', (s.speed or 0) * 3.6)
  local ln = s.line
  if ln then
    local road = ln.z and string.format('  line z %.1f', ln.z) or ''
    out[#out + 1] = string.format('line %d %s: %.0f m along, %.1f m off', ln.index, ln.id or '?', ln.m, ln.off) .. road
  else
    out[#out + 1] = 'no line within ' .. M.NEAR_M .. ' m'
  end
  return out
end

-- The same in one log line.
function M.logLine(s)
  return 'where: ' .. table.concat(M.text(s), ' | ')
end

return M
