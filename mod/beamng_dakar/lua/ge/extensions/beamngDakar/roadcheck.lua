-- Roads under props (GitHub #59): a prop whose footprint is within a road's half width plus a margin of the road is not placed.
--
-- The props are TSStatic shapes without collision (props.lua): a table that stands on the road leading out of the service park is
-- scenery the car drives through. The game's navgraph (the stock `map` extension: map.getMap().nodes, each with `pos`, `radius`
-- and `links`, a link's edge data naming its `inNode` and the `inRadius` / `outRadius` and `inPos` / `outPos` at its ends) is built
-- by BeamNG.Earth from the roads it streams, a few tiles at a time as the player drives, so a prop made far from the player is made
-- before the roads round it are known. The caller (core.lua) checks a prop when it makes it and again, within reach of the player,
-- whenever the navgraph changed.
--
-- This file only does the geometry. An Index holds road segments { a, b, half width at each end } in a hash of CELL_M cells; a
-- segment is in every cell its box grows into by its widest half width plus the margin, so a point finds all the roads that can
-- hold it in its own cell. Our own stage and transfer road is another Index (the legs' lines at OWN_HALF_M): it counts as a road
-- even where the navgraph has not got it yet. Pure: no game calls.
local M = {}

M.MARGIN_M = 0.75            -- a prop's footprint stays this far outside a road's half width
M.OWN_HALF_M = 2.5           -- our own road's half width: a stage road's carriageway (the boards at 5.5 m, the start sensors at 4.2 m and
                             -- the cones at 4.8 m beside it stand outside this and the margin; the navgraph's own width decides where it is wider)
M.Z_TOL_M = 6                -- a navgraph road this far above or below a prop whose ground is known is a bridge or a tunnel, not under it
M.CELL_M = 32
M.STEP_M = 0.75              -- the footprint is tried every this many metres

local floor, ceil, max, min, sqrt, cos, sin, rad = math.floor, math.ceil, math.max, math.min, math.sqrt, math.cos, math.sin, math.rad

local Index = {}
Index.__index = Index

function M.new()
  return setmetatable({ cells = {}, n = 0, ax = {}, ay = {}, az = {}, bx = {}, by = {}, bz = {}, ha = {}, hb = {} }, Index)
end

local function key(cx, cy) return cx * 131072 + cy end

-- A road segment from a (x, y, z) to b with its half width at each end.
function Index:add(ax, ay, az, bx, by, bz, ha, hb)
  local k = self.n + 1
  self.n = k
  self.ax[k], self.ay[k], self.az[k], self.bx[k], self.by[k], self.bz[k], self.ha[k], self.hb[k] = ax, ay, az, bx, by, bz, ha, hb
  local pad = max(ha, hb) + M.MARGIN_M
  local c = M.CELL_M
  for cx = floor((min(ax, bx) - pad) / c), floor((max(ax, bx) + pad) / c) do
    for cy = floor((min(ay, by) - pad) / c), floor((max(ay, by) + pad) / c) do
      local kk = key(cx, cy)
      local list = self.cells[kk]
      if not list then list = {}; self.cells[kk] = list end
      list[#list + 1] = k
    end
  end
end

-- A line of world points ({x, y, z, ...} each) with one half width all along it.
function Index:addLine(pts, half)
  for i = 1, #pts - 1 do
    local a, b = pts[i], pts[i + 1]
    self:add(a[1], a[2], a[3] or 0, b[1], b[2], b[3] or 0, half, half)
  end
end

local function radiusOf(edge, id, node)
  local r = type(edge) == 'table' and edge[(edge.inNode == id) and 'inRadius' or 'outRadius'] or nil
  if type(r) ~= 'number' then r = node.radius end
  return type(r) == 'number' and r or 2
end

local function posOf(edge, id, node)
  local p = type(edge) == 'table' and edge[(edge.inNode == id) and 'inPos' or 'outPos'] or nil
  p = p or node.pos
  return p.x or p[1], p.y or p[2], p.z or p[3] or 0
end

-- The roads of the stock navgraph (map.getMap().nodes): every link once (a link both nodes hold is only taken from the
-- lesser id; one only one node holds, from that node), its half width at each end from the edge data, else the node's radius.
function Index:addGraph(nodes)
  for id, a in pairs(nodes) do
    if a.pos and a.links then
      for nid, edge in pairs(a.links) do
        local b = nodes[nid]
        if b and b.pos and (tostring(id) < tostring(nid) or not (b.links and b.links[id])) then
          local ax, ay, az = posOf(edge, id, a)
          local bx, by, bz = posOf(edge, nid, b)
          self:add(ax, ay, az, bx, by, bz, radiusOf(edge, id, a), radiusOf(edge, nid, b))
        end
      end
    end
  end
end

-- How far the point (x, y) is outside the nearest road's half width plus the margin (negative: on a road), with the distance to
-- that road's centre line and its half width there; nil when no road is in reach. z (optional): the point's ground, to leave out
-- a road this far above or below it.
function Index:probe(x, y, z)
  if self.n == 0 then return nil end
  local list = self.cells[key(floor(x / M.CELL_M), floor(y / M.CELL_M))]
  if not list then return nil end
  local best, bestD, bestH, bestEx, bestEy, bestDx, bestDy
  local ax, ay, bx, by = self.ax, self.ay, self.bx, self.by
  for q = 1, #list do
    local k = list[q]
    local dx, dy = bx[k] - ax[k], by[k] - ay[k]
    local l2 = dx * dx + dy * dy
    local t = l2 > 1e-9 and ((x - ax[k]) * dx + (y - ay[k]) * dy) / l2 or 0
    if t < 0 then t = 0 elseif t > 1 then t = 1 end
    local ex, ey = x - (ax[k] + dx * t), y - (ay[k] + dy * t)
    local d2 = ex * ex + ey * ey
    local half = self.ha[k] + (self.hb[k] - self.ha[k]) * t
    local lim = half + M.MARGIN_M
    local d = sqrt(d2)
    local gap = d - lim
    if not best or gap < best then
      local ok = true
      if z then                                   -- (a bridge over the prop, a tunnel under it)
        local rz = self.az[k] + (self.bz[k] - self.az[k]) * t
        ok = math.abs(rz - z) <= M.Z_TOL_M
      end
      if ok then best, bestD, bestH, bestEx, bestEy, bestDx, bestDy = gap, d, half, ex, ey, dx, dy end
    end
  end
  if not best then return nil end
  -- (the vector from the centre line's nearest point to (x, y), and the direction of that road: where the prop would be pushed out)
  return best, bestD, bestH, bestEx, bestEy, bestDx, bestDy
end

-- The points of a prop's footprint: a rectangle of half extents hx (along the model's x) and hy (along its y) turned by yaw
-- (degrees counter-clockwise from +x: the model's x axis then points that way), tried every STEP_M and at its corners.
function M.samples(x, y, yaw, hx, hy)
  local c, s = cos(rad(yaw)), sin(rad(yaw))
  local nx, ny = max(1, ceil(2 * hx / M.STEP_M)), max(1, ceil(2 * hy / M.STEP_M))
  local out = {}
  for i = 0, nx do
    local a = -hx + 2 * hx * i / nx
    for j = 0, ny do
      local b = -hy + 2 * hy * j / ny
      out[#out + 1] = { x + a * c - b * s, y + a * s + b * c }
    end
  end
  return out
end

-- Is the prop { x, y, yaw } with footprint fp = { hx, hy } on a road of `graph` or of `own` (either may be nil)? Returns the
-- distance from the footprint's nearest point to the road's centre line and the road's half width there, or nil. z: the prop's
-- ground when it is known (it leaves out the graph's bridges and tunnels).
function M.hit(prop, fp, graph, own, z)
  if not fp or not ((graph and graph.n > 0) or (own and own.n > 0)) then return nil end
  local worst, wd, wh
  for _, p in ipairs(M.samples(prop.x, prop.y, prop.yaw or 0, fp[1], fp[2])) do
    for _, idx in ipairs({ graph or false, own or false }) do
      if idx then
        local gap, d, half = idx:probe(p[1], p[2], idx == graph and z or nil)
        if gap and gap < 0 and (not worst or gap < worst) then worst, wd, wh = gap, d, half end
      end
    end
  end
  if worst then return wd, wh end
  return nil
end

M.PUSH_STEP_M = 0.25

-- A prop that is on a road, moved out of it (GitHub #61): along the normal of the road it is deepest in, away from the centre line (on
-- the side `prop.side` says, 1 right and -1 left of the direction of travel, when it stands on the centre line itself), in steps of
-- PUSH_STEP_M, up to maxPush, until its footprint is clear of every road and the margin. Returns the new x, y and how far it moved, or
-- nil when it was not on a road or no push up to maxPush clears it.
function M.relocate(prop, fp, graph, own, z, maxPush)
  if not fp then return nil end
  local best
  for _, idx in ipairs({ graph or false, own or false }) do
    if idx then
      local gap, d, half, ex, ey, rdx, rdy = idx:probe(prop.x, prop.y, idx == graph and z or nil)
      if gap and (not best or gap < best.gap) then best = { gap = gap, ex = ex, ey = ey, dx = rdx, dy = rdy } end
    end
  end
  if not best or not M.hit(prop, fp, graph, own, z) then return nil end
  local len = sqrt(best.ex * best.ex + best.ey * best.ey)
  local ux, uy
  if len > 0.05 then
    ux, uy = best.ex / len, best.ey / len
  else
    local l = sqrt(best.dx * best.dx + best.dy * best.dy)
    if l < 1e-9 then return nil end
    local side = prop.side or 1
    ux, uy = side * best.dy / l, -side * best.dx / l
  end
  local moved = M.PUSH_STEP_M
  local p = { x = prop.x, y = prop.y, yaw = prop.yaw, kind = prop.kind }
  while moved <= (maxPush or 0) + 1e-9 do
    p.x, p.y = prop.x + ux * moved, prop.y + uy * moved
    if not M.hit(p, fp, graph, own, z) then return p.x, p.y, moved end
    moved = moved + M.PUSH_STEP_M
  end
  return nil
end

return M
