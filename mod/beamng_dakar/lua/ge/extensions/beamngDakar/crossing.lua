-- Where the stage road passes over a tunnel: finding the stretches of the line whose ground is not the road's (shape.lua lays them).
--
-- BeamNG.Earth grades the ground under every road to the road's plan (the surface its lane lines and walls are drawn on) but leaves the raw
-- elevation over a covered tunnel (the tunnel's half width and 5 m) as it is. A road laid across that strip is graded up to the strip's
-- edge, raw inside it, and graded again beyond: the carriageway is level across everywhere but there, where the hill's own slope runs
-- through it, up to metres off the road's surface. Nothing in the game carries the road's plan itself (the road mesh is a render-only
-- shape, its files compressed; the navgraph's heights and the line meshes' are read off the ground), so the stretch is found in the
-- terrain, and what the road would have been is what the road is on both sides of it:
--
--   * where: the line crosses a tunnel Earth has built (the points along its axes, the same as the game's tunnel lights, ambience.tunnelLights: 20 m apart
--     at most, the ends of a box a little beyond the last of them). A crossing meets the axis at CROSS_MIN_DEG or more, goes from one side of it to the other, and is
--     at least PORTAL_M along the tunnel from either end. A road that runs into a tunnel, along it or past a portal is none of that: the portals, their holes and
--     the arch raise are Earth's, as ever;
--   * the stretch: the road's plan is level across its carriageway (Earth lays it with one height at each point of the road), so the terrain 2 and 4 m to either side
--     of the line stands at the line's height to within a few cm wherever the grading is the plan (FLAT_M) and, where the raw ground runs through, up to metres off
--     (TILT_M), stepping from one to the other within a metre or two at the strip's edge. The stretch lies between the first flat
--     samples either side of the crossing that are also clear of the strip (STRIP_MIN_M from the axis);
--   * its height: the line's ground at those two samples, straight between. (Measured on the Popoli stage's four crossings: within 0.1 m of the plan.)
-- Pure: the points of the line, the tunnel points and a height function go in, plain tables come out.
local M = {}

M.LINK_M = 24               -- tunnel points this close, in a line, are one tunnel (the lights of a box are at most ~25 m apart; two bores are further)
M.TURN_DEG = 40             -- ... a tunnel does not bend more than this between two points: the next point of a bore is not the other bore's
M.END_EXTEND_M = 10         -- the end light is up to this far inside the end of its box: the tunnel's ends are this much beyond the first and last points
M.MIN_DEG = 45              -- a crossing meets the axis at this angle or more (a road entering a tunnel runs along it)
M.PORTAL_M = 30             -- ... and is this far from either end of the tunnel (Earth's portal holes and the arch raise reach about 12 m in)
M.NEAR_M = 16               -- the line is looked at where it is this near an axis (the keep strip is the tunnel's half width and 5 m: 9.2 m for a motorway's two lanes)
M.STEP_M = 1                -- the line is sampled every this many metres
M.OFFSETS_M = { -4, -2, 2, 4 }   -- the terrain at these distances to the side of the line against the line's: the tilt of the carriageway
M.FLAT_M = 0.08             -- a tilt up to this is the graded road (it was 0.01 to 0.03 m on the Popoli stage, 0.28 m beside a plain slope)
M.TILT_M = 0.3              -- a tilt of this or more is the raw ground running through the road (0.9 to 2.3 m there)
M.STRIP_MIN_M = 8           -- the stretch's ends are this far from the axis at least (a flat sample inside the strip is not the plan) ...
M.STRIP_MAX_M = 18          -- ... and at most this far (else the ground is flat across for some other reason)
M.MAX_STRETCH_M = 70        -- the stretch is at most this long, and at least
M.MIN_STRETCH_M = 4
M.MASK_MARGIN_M = 0.5       -- the strip the stretch is laid over: its half width as measured at the stretch's ends, and this much

local sqrt, abs, floor, min, max, huge = math.sqrt, math.abs, math.floor, math.min, math.max, math.huge
local deg = math.deg or function(r) return r * 180 / math.pi end

-- The tunnels as polylines: { { x = {...}, y = {...}, arc = {...} (m along it at each point), len } }, from the points along their axes
-- { { x, y, z }, ... } (a point alone, a short box, has no direction: left out). Each point is joined to its nearest neighbours within LINK_M
-- on the two sides of it (the two nearest that are in line within TURN_DEG), both ways round; the ends are extended by END_EXTEND_M.
function M.chains(points)
  local n = #points
  local chosen = {}
  local cosLim = -math.cos(math.rad(M.TURN_DEG))        -- the two links of a point are 180 - TURN_DEG apart or more
  for i = 1, n do
    local near = {}
    for j = 1, n do
      if j ~= i then
        local dx, dy = points[j][1] - points[i][1], points[j][2] - points[i][2]
        local d = sqrt(dx * dx + dy * dy)
        if d <= M.LINK_M and d > 0.01 then near[#near + 1] = { j, d, dx / d, dy / d } end
      end
    end
    table.sort(near, function(a, b) return a[2] < b[2] end)
    local c = {}
    if near[1] then
      c[near[1][1]] = true
      for q = 2, #near do
        if near[q][3] * near[1][3] + near[q][4] * near[1][4] <= cosLim then c[near[q][1]] = true; break end
      end
    end
    chosen[i] = c
  end
  local adj = {}
  for i = 1, n do
    adj[i] = {}
    for j in pairs(chosen[i]) do
      if chosen[j] and chosen[j][i] then adj[i][#adj[i] + 1] = j end
    end
  end
  local out, seen = {}, {}
  for i = 1, n do
    if not seen[i] and #adj[i] == 1 then          -- (an end of a path)
      local order, prev, cur = {}, nil, i
      while cur and not seen[cur] do
        seen[cur] = true
        order[#order + 1] = cur
        local nxt
        for _, j in ipairs(adj[cur]) do if j ~= prev and not seen[j] then nxt = j end end
        prev, cur = cur, nxt
      end
      if #order >= 2 then
        local xs, ys = {}, {}
        for k, idx in ipairs(order) do xs[k], ys[k] = points[idx][1], points[idx][2] end
        local function extend(k, k2)       -- the end at k, beyond it along the way from k2
          local dx, dy = xs[k] - xs[k2], ys[k] - ys[k2]
          local l = sqrt(dx * dx + dy * dy)
          return xs[k] + dx / l * M.END_EXTEND_M, ys[k] + dy / l * M.END_EXTEND_M
        end
        local x0, y0 = extend(1, 2)
        local x1, y1 = extend(#xs, #xs - 1)
        table.insert(xs, 1, x0); table.insert(ys, 1, y0)
        xs[#xs + 1], ys[#ys + 1] = x1, y1
        local arc = { 0 }
        for k = 2, #xs do arc[k] = arc[k - 1] + sqrt((xs[k] - xs[k - 1]) ^ 2 + (ys[k] - ys[k - 1]) ^ 2) end
        out[#out + 1] = { x = xs, y = ys, arc = arc, len = arc[#arc] }
      end
    end
  end
  return out
end

-- The nearest point of a chain to (x, y): distance, which side of the axis (the sign of the cross product), metres along the chain, the axis' direction there.
local function nearest(ch, x, y)
  local best, bd, bs, bt, bdx, bdy = huge, 0, 0, 0, 1, 0
  local xs, ys = ch.x, ch.y
  for k = 1, #xs - 1 do
    local ax, ay = xs[k], ys[k]
    local dx, dy = xs[k + 1] - ax, ys[k + 1] - ay
    local l2 = dx * dx + dy * dy
    local t = l2 > 1e-9 and ((x - ax) * dx + (y - ay) * dy) / l2 or 0
    if t < 0 then t = 0 elseif t > 1 then t = 1 end
    local ex, ey = x - (ax + dx * t), y - (ay + dy * t)
    local d2 = ex * ex + ey * ey
    if d2 < best then
      local l = sqrt(l2)
      best, bd = d2, sqrt(d2)
      bs = (dx * (y - ay) - dy * (x - ax)) >= 0 and 1 or -1
      bt = ch.arc[k] + t * l
      bdx, bdy = dx / l, dy / l
    end
  end
  return bd, bs, bt, bdx, bdy
end
M.nearest = nearest

-- The crossings of a line. pts: { { x, y, ... } }; chains: M.chains(); env:
--   ground(x, y) -> the terrain height there before any change, or nil (not in the block: the crossing is not looked at)
--   skip(i) -> true when the stretch of the line from point i to i + 1 is a bridge or a tunnel the line is on (a deck: the ground there is not the road's)
-- Returns { { s0, s1 (metres along the line at the stretch's ends), a, b ({ x, y, z, s, d: where, height, along the line and distance from the axis }),
-- x, y (the closest approach), angle, arcM (metres from the nearer end of the tunnel), chain, strip (the keep strip's half width as measured), samples = { x, y, s, z (the height the road has) } } }.
function M.find(pts, chains, env)
  local out = {}
  if not chains or #chains == 0 or #pts < 2 then return out end
  local cum = { 0 }
  for i = 2, #pts do cum[i] = cum[i - 1] + sqrt((pts[i][1] - pts[i - 1][1]) ^ 2 + (pts[i][2] - pts[i - 1][2]) ^ 2) end
  local reach = M.NEAR_M + M.MAX_STRETCH_M
  for _, ch in ipairs(chains) do
    local bx0, bx1, by0, by1 = huge, -huge, huge, -huge
    for k = 1, #ch.x do
      bx0, bx1, by0, by1 = min(bx0, ch.x[k]), max(bx1, ch.x[k]), min(by0, ch.y[k]), max(by1, ch.y[k])
    end
    bx0, bx1, by0, by1 = bx0 - reach, bx1 + reach, by0 - reach, by1 + reach
    -- the samples of the line (every STEP_M) on the segments that reach the tunnel; a piece is a run of consecutive segments
    local pieces, cur = {}, nil
    for i = 1, #pts - 1 do
      local a, b = pts[i], pts[i + 1]
      if max(a[1], b[1]) >= bx0 and min(a[1], b[1]) <= bx1 and max(a[2], b[2]) >= by0 and min(a[2], b[2]) <= by1 then
        local seg = cum[i + 1] - cum[i]
        if seg > 1e-6 then
          if not cur then cur = { x = {}, y = {}, s = {}, i = {}, dx = {}, dy = {} }; pieces[#pieces + 1] = cur end
          local nst = max(1, floor(seg / M.STEP_M + 0.5))
          local ux, uy = (b[1] - a[1]) / seg, (b[2] - a[2]) / seg
          for q = 0, nst - 1 do
            local f = q / nst
            local m = #cur.x + 1
            cur.x[m], cur.y[m] = a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f
            cur.s[m], cur.i[m], cur.dx[m], cur.dy[m] = cum[i] + seg * f, i, ux, uy
          end
        end
      else
        cur = nil
      end
    end
    for _, pc in ipairs(pieces) do
      local nS = #pc.x
      local dist, side, arc, adx, ady = {}, {}, {}, {}, {}
      for m = 1, nS do dist[m], side[m], arc[m], adx[m], ady[m] = nearest(ch, pc.x[m], pc.y[m]) end
      -- the runs of the piece within NEAR_M of the axis
      local m = 1
      while m <= nS do
        if dist[m] <= M.NEAR_M then
          local r0 = m
          while m + 1 <= nS and dist[m + 1] <= M.NEAR_M do m = m + 1 end
          local r1 = m
          local ok = r0 > 1 and r1 < nS                       -- (the line goes on beyond the run both ways)
          local c = r0
          for q = r0, r1 do if dist[q] < dist[c] then c = q end end
          local cross = abs(pc.dx[c] * ady[c] - pc.dy[c] * adx[c])        -- sine of the angle between the line and the axis
          local angle = deg(math.asin(min(1, cross)))
          ok = ok and angle >= M.MIN_DEG and side[r0] ~= side[r1]
          ok = ok and arc[c] >= M.PORTAL_M and ch.len - arc[c] >= M.PORTAL_M
          if ok and env.skip then
            for q = r0, r1 do if env.skip(pc.i[q]) then ok = false; break end end
          end
          if ok then
            -- the carriageway's tilt at a sample, as it is asked for: how far the terrain beside the line stands off the line's own height (nil: the block does not reach it)
            local tilt, z = {}, {}
            local function look(q)
              if tilt[q] ~= nil then return tilt[q] end
              local zc = env.ground(pc.x[q], pc.y[q])
              tilt[q] = false
              if not zc then return false end
              z[q] = zc
              local nx, ny = -pc.dy[q], pc.dx[q]
              local worst = 0
              for _, o in ipairs(M.OFFSETS_M) do
                local zo = env.ground(pc.x[q] + nx * o, pc.y[q] + ny * o)
                if not zo then return false end
                worst = max(worst, abs(zo - zc))
              end
              tilt[q] = worst
              return worst
            end
            local tc = look(c)
            if tc and tc >= M.TILT_M then
              -- out from the closest approach to the first sample that is flat across and clear of the strip, each way
              local a, b = c, c
              local good = true
              while true do
                local t = look(a)
                if not t then good = false; break end
                if t <= M.FLAT_M and dist[a] >= M.STRIP_MIN_M then break end
                if a <= 1 or pc.s[c] - pc.s[a] > M.MAX_STRETCH_M then good = false; break end
                a = a - 1
              end
              while good do
                local t = look(b)
                if not t then good = false; break end
                if t <= M.FLAT_M and dist[b] >= M.STRIP_MIN_M then break end
                if b >= nS or pc.s[b] - pc.s[c] > M.MAX_STRETCH_M then good = false; break end
                b = b + 1
              end
              good = good and dist[a] <= M.STRIP_MAX_M and dist[b] <= M.STRIP_MAX_M
              local len = pc.s[b] - pc.s[a]
              if good and len >= M.MIN_STRETCH_M and len <= M.MAX_STRETCH_M then
                local za, zb = z[a], z[b]
                local k = (zb - za) / len
                local samples = {}
                -- (the stretch with a few metres past each end, on the line the heights continue along)
                local q0, q1 = max(1, a - 3), min(nS, b + 3)
                for q = q0, q1 do
                  samples[#samples + 1] = { x = pc.x[q], y = pc.y[q], s = pc.s[q], z = za + k * (pc.s[q] - pc.s[a]) }
                end
                out[#out + 1] = {
                  s0 = pc.s[a], s1 = pc.s[b], x = pc.x[c], y = pc.y[c], angle = angle, arcM = min(arc[c], ch.len - arc[c]), chain = ch,
                  a = { x = pc.x[a], y = pc.y[a], z = za, s = pc.s[a], d = dist[a] }, b = { x = pc.x[b], y = pc.y[b], z = zb, s = pc.s[b], d = dist[b] },
                  strip = min((dist[a] + dist[min(a + 1, c)]) / 2, (dist[b] + dist[max(b - 1, c)]) / 2) + M.MASK_MARGIN_M, samples = samples, slope = k,
                }
              end
            end
          end
          m = r1 + 1
        else
          m = m + 1
        end
      end
    end
  end
  table.sort(out, function(p, q) return p.s0 < q.s0 end)
  return out
end

return M
