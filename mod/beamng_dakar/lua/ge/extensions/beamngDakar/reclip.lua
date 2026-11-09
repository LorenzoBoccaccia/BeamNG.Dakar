-- DecalRoads over ground that was shaped. A DecalRoad is cut to the terrain when it is made or regenerated and is not told
-- when the terrain under it changes; BeamNG.Earth's own re-clip comes from its importing or moving a block. After a shaping job the
-- DecalRoads of Earth's road group inside the changed boxes are regenerated, so they lie on the new ground.
--
-- Where this matters depends on how Earth draws the roads. With its default (a road's asphalt painted into the ground, its centre and edge
-- lines a mesh draped per geotile, server/roadmesh.py) there are no DecalRoads on a geotile's road and nothing here has work; the mesh
-- cannot be re-cut. With the carriageways as meshes of their own off, tiles Earth has no mesh for (its anchor tiles) and Earth 1.8.2's
-- road tiles, they are DecalRoads, and these follow.
--
-- Throttled: the boxes are gathered while a job runs; a sweep over the road group (looking at REGION_PER_FRAME objects a frame, regenerating
-- at most MAX_REGEN_FRAME of them a frame) starts when the shaping is idle and at least SWEEP_EVERY_S after the last one ended. Pure
-- apart from the env (group, clock, idle, log), which the tests stub.
local M = {}

M.SWEEP_EVERY_S = 1.0       -- a sweep starts no sooner than this after the last one ended
M.PER_FRAME = 150           -- objects of the group looked at in a frame
M.MAX_REGEN_FRAME = 3       -- DecalRoads regenerated in a frame (~0.45 ms each for a 100 node road, Earth's measurement)
M.MAX_BOXES = 32            -- more boxes than this are merged into their union
M.MARGIN_M = 2              -- a road within this of a box counts as over it
M.CLASS = 'DecalRoad'

local R = {}
R.__index = R

-- env: group() -> { count = f() -> n, at = f(i) -> object (1-based) } or nil; object: { class = f() -> string, box = f() -> x0, y0, x1, y1
-- (world), regenerate = f() }; clock() -> s; idle() -> true when no shaping job is running; log(level, msg).
function M.new(env)
  return setmetatable({ env = env, boxes = {}, sweep = nil, lastEnd = -1e9, stat = { sweeps = 0, regenerated = 0, looked = 0 } }, R)
end

local function union(a, b)
  return { math.min(a[1], b[1]), math.min(a[2], b[2]), math.max(a[3], b[3]), math.max(a[4], b[4]) }
end

-- The ground inside this world box changed (what the shaper did to a block's heights, or put back).
function R:note(x0, y0, x1, y1)
  local b = self.boxes
  b[#b + 1] = { math.min(x0, x1), math.min(y0, y1), math.max(x0, x1), math.max(y0, y1) }
  if #b > M.MAX_BOXES then
    local u = b[1]
    for i = 2, #b do u = union(u, b[i]) end
    self.boxes = { u }
  end
end

function R:pending() return #self.boxes > 0 or self.sweep ~= nil end

-- Drop what is waiting (the loop stopped: the ground is Earth's again and it re-cuts what it makes).
function R:clear() self.boxes, self.sweep = {}, nil end

local function over(boxes, x0, y0, x1, y1, m)
  for _, b in ipairs(boxes) do
    if x1 >= b[1] - m and x0 <= b[3] + m and y1 >= b[2] - m and y0 <= b[4] + m then return true end
  end
  return false
end

-- Every frame. Returns the DecalRoads regenerated in this call.
function R:update()
  local env = self.env
  local now = env.clock()
  local sw = self.sweep
  if not sw then
    if #self.boxes == 0 or now - self.lastEnd < M.SWEEP_EVERY_S or (env.idle and not env.idle()) then return 0 end
    local g = env.group()
    if not g then self.boxes = {}; return 0 end              -- (no road group: Earth has made no roads)
    sw = { boxes = self.boxes, group = g, i = 1, n = g.count(), done = 0, looked = 0 }
    self.boxes, self.sweep = {}, sw
  end
  local regen, looked = 0, 0
  while sw.i <= sw.n and looked < M.PER_FRAME do
    local o = sw.group.at(sw.i)
    if o and o.class() == M.CLASS then
      local x0, y0, x1, y1 = o.box()
      if x0 and over(sw.boxes, x0, y0, x1, y1, M.MARGIN_M) then
        if regen >= M.MAX_REGEN_FRAME then break end         -- (the rest next frame)
        local ok, err = pcall(o.regenerate)
        if ok then regen, sw.done = regen + 1, sw.done + 1
        elseif not self.warned then self.warned = true; env.log('W', 'reclip: a DecalRoad would not regenerate: ' .. tostring(err)) end
      end
    end
    sw.i, looked = sw.i + 1, looked + 1
  end
  sw.looked = sw.looked + looked
  self.stat.looked = self.stat.looked + looked
  if sw.i > sw.n then
    self.sweep, self.lastEnd = nil, now
    self.stat.sweeps = self.stat.sweeps + 1
    self.stat.regenerated = self.stat.regenerated + sw.done
    if sw.done > 0 then
      env.log('I', string.format('reclip: %d DecalRoads over the shaped ground cut again (of %d objects of the road group)', sw.done, sw.looked))
    end
  end
  return regen
end

function R:status() return { sweeps = self.stat.sweeps, regenerated = self.stat.regenerated, working = self.sweep ~= nil, boxes = #self.boxes } end

return M
