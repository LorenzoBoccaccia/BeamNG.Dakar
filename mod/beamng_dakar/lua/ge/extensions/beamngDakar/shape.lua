-- Transient ground shaping along a special stage: the terrain block's heights along the curated road line
-- (curate.lua) are changed, in memory, where the elevation data has a defect the road does not, and nowhere else.
--
-- BeamNG.Earth paints the asphalt into the ground photo and draws the centre and edge lines as a mesh 2.5 cm over its own ground
-- (from the user's server: near visual 'detail', geotiles, the default roads-in-ground). The mesh does not follow our
-- terrain: every cell of the road core we move buries the lines (raised) or leaves them floating (lowered). So the road is left
-- exactly as Earth made it, every cell untouched, but for
--   * the ground under an overpass, where the elevation data has the deck baked in as a ridge, and
--   * a big spike on the road line, SPIKE_M or more over the line's own trend over a short window (curate.despike, bumps only): a defect
--     of the data, not relief (a steep climb or a hillside is not one). A dip is never one: it is a sag the road dips into and climbs
--     out of, or a gully under a deck the game's colliders do not show yet, and filling it left the road lines under the ground, and
--   * the ground at either end of a deck, which is met to the deck's top with a smooth ramp that follows the road's ground beyond it, raising or
--     cutting it: a span OpenStreetMap tags as
--     a bridge, and a deck of our own road the game's colliders show (a wide collider whose top continues from the road at its ends, over a
--     gully or a culvert OpenStreetMap does not tag). A narrow collider at road level is never the reason to move the ground (a
--     retaining wall at a hairpin was taken for a deck).
--   * and nothing beside a tunnel: Earth builds a tunnel's tube and portals and shapes the terrain for them (holes at the mouths, the ground
--     raised over a low arch), so a point of the line near one (the geometry's `tunnelsBeside`, OpenStreetMap's tunnel ways, and the tubes
--     Earth has made in the game now) is left as Earth made it.
--   * and, apart from the option for all of the above, the ground of a road across a tunnel (crossing.lua): Earth leaves the raw ground over a covered tunnel
--     where it grades every other road's, so a stage road that passes over one has the hill's slope through its carriageway. Found at run time (the tunnels
--     Earth built, the terrain block), that stretch of the ground is laid to the road's height, over the strip of raw ground only. It runs when the option is off too.
-- Another road's ground is never moved: the geometry's `otherRoads` (OpenStreetMap's other drivable ways near the line, without the stage's own road, bridges and
-- tunnels) are kept clear of beyond the stage road's own footprint (CORE_M): the weight of the stage's taper falls to 0 at the other road's edge (its half width and
-- OTHER_MARGIN_M), over OTHER_FADE_M. A motorway beside the road, a road that crosses it or leaves it keep the ground Earth gave them; the ground under the stage road itself,
-- a crossing included, follows the stage road.
-- Only the stage road is worked on: its surface (CORE_M, the road's half width and a little) and the ground directly under it, with a taper
-- (BLEND_M) just wide enough to blend into the ground beside; other roads, embankments and verges beyond that are Earth's.
-- Where one of those is, the change is a delta, not a flat core: a cell is moved by w * (the line's fixed height - the ground under
-- the line) at the nearest point of the line, w the falloff, so the road's cross slope and camber and what Earth graded are kept and
-- only the defect is taken out. The delta ends in a taper along the line (TAPER_M). Only the stretches fixed and their tapers are
-- worked on, which is what makes the shaping at a swap cheap.
--
-- Nothing here reads or writes a file, and Earth's code is not touched. Roads that are DecalRoads (other Earth settings)
-- are cut again after the shaping (reclip.lua). The block
-- (Earth's one TerrainBlock, beamngEarth/terrain) is replaced at every commit (terrain follow, rebase, teleport): the
-- shaping is done again on the new block's data, and the next commit brings Earth's own ground back by itself. The loop's
-- stop or the window's switch puts the original heights back, when the block is still the one shaped.
--
-- The API is the stock editor's (editor/terraform/terraform.lua): tb:getHeightGrid / setHeightGrid(gx, gy, h) on grid
-- coordinates, tb:updateGrid(vec3 min, vec3 max) over the changed box. Earth measured both (docs/research/
-- 2026-10-12-engine-limits-and-worldgen.md): 0.095 us per sample and 2.5 ms per 16-row slice of a 1024-wide block for the
-- grid update, collision following by the next frame without a rebuild. Which height a grid value is (above the block's
-- own base height or absolute) is measured on the block with the game's own height query before the first write, and
-- the grid's origin and square size likewise: when they do not agree, nothing is written.
--
-- The work is spread over frames: update(budgetMs) does at most that much (plus the grid updates of the boxes it touched,
-- each kept under MAX_BOX_CELLS cells), the segments nearest the player first, and the first slice after a commit gets a
-- bigger budget for the stretch right at the car.
--
-- The ground under a moving car must never change between two physics frames (at a swap the new block brings
-- Earth's own ground back under the car, our shaping landed some frames later, and the car was thrown by the step, up to
-- the shaping's change, twice). So:
--   * blockChanged() shapes the stretch round the car (what it covers in a second, and a margin) at once, in the same Lua call as Earth's swap or load, before
--     the next physics step sees the new block: the new block arrives already shaped where the car is, to the same heights
--     the old block had there (the same ground gives the same profile; only a profile's ends, which are pinned to the
--     ground, differ, and the car is far from the block's edge, where a run of the line ends). The rest is spread over frames.
--   * a later frame of a job, or a restore, leaves the cells within FREEZE_M of a car that moves faster than FREEZE_SPEED
--     as they are, and does them once the car has passed them (or stopped).
-- The change of a cell is capped at MAX_CHANGE_M (OVERPASS_CHANGE_M under an overpass and where the ground meets a deck's end), but a fix
-- that asks for more than that is not left to be cut off at the cap, which leaves a plateau with the defect's steep sides: its delta is
-- scaled as a whole to the cap, so the remainder is spread over the whole stretch and its taper.
--
-- Pure apart from the env it is given (block, clock, ground, player, log); the tests give it a stub TerrainBlock.
local curate = require('beamngDakar/curate')
local crossing = require('beamngDakar/crossing')

local M = {}

M.CORE_M = 5.5              -- the road's half width (legs.ROAD_WIDTH / 2) and half a metre: the whole delta of the line within this ...
M.BLEND_M = 6               -- ... then eased (smoothstep) to nothing over this (it was 6.5 and 12: 37 m of ground moved for a 10 m road)
M.LOG_STRETCHES = 12        -- the log names at most this many stretches fixed and colliders over the line
M.EDGE_CELLS = 8            -- the block's rim is left alone (Earth's sunk edge ring and edge holes are there)
M.BUDGET_MS = 1.5           -- the shaping work of one frame
M.URGENT_BUDGET_MS = 6      -- the first slice after a commit: the road at the car must be right at once ...
M.URGENT_M = 150            -- ... which is the segments within this far of the player
M.MAX_BOX_CELLS = 6000      -- the box handed to one updateGrid (~0.15 us a cell) is at most this
M.MAX_CHANGE_M = 4          -- no cell is moved further than this
M.SPIKE_M = 2.0             -- a point of the line is a spike when its ground is this far off the straight line between the points k steps
                            -- either side of it, the median over k = 1 .. curate.SPIKE_HALF (a crest of 4 percent change per 10 m: 1.3 m).
                            -- Not 1.5: fixing a bump lowers the road and leaves Earth's road lines floating over it, and a hairpin's
                            -- cut on a steep hillside, where the curated line is a few metres off the road and the ground under it is the slope
                            -- beside it, is flagged by 1.5 m. The viaduct's ridge is an overpass fix of its own, not a spike. An edition may set
                            -- its own (legs.json: "shaping": { "spikeM": 2.5 })
M.TUNNEL_KEEP_M = 30        -- a point of the line this near a tunnel's axis (Earth's portal holes and arch raise reach about 12 m from it, the shaping
                            -- CORE_M + BLEND_M from the line, and the line's points are 10 m apart) is left alone, with its segments
M.OTHER_MARGIN_M = 2        -- another road's ground is its carriageway and shoulders (the geometry's `otherRoads`: a half width each) and this much more
M.OTHER_FADE_M = 3          -- (a verge, Earth's wall at a cutting); the stage road's taper eases to nothing over this much before it
M.TUNNEL_CELL_M = 80        -- a tunnel point within a cell of this many metres of a point of the line is near it (the repair of a crossing over a tunnel, crossing.lua)
M.TUNNEL_POLL_S = 2         -- (the tunnels Earth has built are looked at this often for a change near the lines)
M.CROSS_CHANGE_M = 6        -- the ground laid to a road across a tunnel moves this far at most (4.3 m on the Popoli stage; MAX_CHANGE_M is the shaping's)
M.CROSS_FADE_M = 3          -- the road's ground is laid only where the raw ground over the tunnel is: inside its strip, easing out over this much beyond it
M.CROSS_STEP_M = 4          -- the stretch's heights are written as segments of this length
M.TUNNEL_POINT_M = 10       -- (the tubes Earth made in the game are known by points along their axes, 20 m apart at most: this much more for the gap)
M.TAPER_M = 15              -- a fix ends in a taper along the line: the points within this of a spike are fixed with it (the delta
                            -- falls from the spike to the first point beyond, where it is 0)
M.DECK_ABOVE_M = 2.5        -- ... and where a static collider stands this far above the ground on the line (a deck or an overpass)
M.DECK_ACROSS_M = 2         -- a collider is a deck or an overpass only when it is under the line and also this far to either side of it,
M.DECK_ACROSS_TOL_M = 0.6   -- at the same height within this (a road's camber): a wall, a barrier, a rail or a pole beside the line is narrow
M.DECK_MIN_RUN_M = 8        -- and a deck the road is on runs on for this far along the line at least (two points of the 10 m line)
M.DECK_LAND_M = 1.0         -- a wide collider this far over the ground is on a deck (a run of those with one DECK_ABOVE_M over is a deck or an
                            -- overpass; the points beyond it are the road, within this of the ground: where the deck lands)
M.DECK_STEP_M = 1.0         -- a deck's top may bend this much from the line between its neighbours (a hump, a grade that changes: ) ...
                            -- and change by this much more than a road's steepest grade over the step between two points: more is a structure
M.DECK_JOIN_TOL_M = 1.0     -- the run is a deck our road is on when its top where it begins (or ends) continues from the road just beyond it:
M.DECK_JOIN_PER_M = 0.08    -- within this plus this a metre of the gap between the two points (a grade and a hump over 10 m of line); an overpass
                            -- stands metres above the road there
M.DECK_LIP_M = 0.3          -- the deck's end, found by rays every DECK_LIP_STEP_M along the line from the road: where a wide collider
M.DECK_LIP_STEP_M = 1       -- stands this far over the ground (a pavement's few cm are no deck)
M.RAMP_MIN_M = 15           -- the ground meets a deck's end with a ramp this long at least, and as long as the grade change limit asks
M.RAMP_SCORE = 0.85         -- the ramp keeps this part of the grade and the grade change limits (the heights between its samples are a line: some margin)
M.RAMP_MAX_M = 150          -- ... and at most this long
M.RAMP_NEAR_M = 8           -- the ground along a ramp is measured every RAMP_NEAR_STEP_M for this far from the deck's end (the ground right past a
M.RAMP_NEAR_STEP_M = 1      -- deck falls away into the ditch under it within a metre or two), then every RAMP_STEP_M
M.RAMP_STEP_M = 2.5
M.RAMP_EDGE_M = 0.5         -- (a ground that is this far from the next point's, a metre on, at the deck's very end is the ditch's edge)
M.STEP_WARN_M = 0.3         -- the guard after the shaping: the ground along the road within a ramp that steps this much between two points 1 m apart
M.PROFILE_STEP_M = 5        -- is warned about; the log has the profile of each ramp every this many metres
M.LOG_PROFILES = 12
M.DECK_RAY_M = 450          -- the static colliders of Earth's decks exist within a few hundred metres of the car: the line is looked
                            -- at with rays only this near it (a ray a point is the largest cost of the plan)
M.DECK_PIN_INTO_M = 2.5     -- the height of a deck at a span's end is measured this far onto it, and the shaped ground is pinned to it ...
M.DECK_PIN_MAX_M = 3        -- ... when it is within this of the ground there (a deck at road level; else the ground is kept), or within this of the
M.DECK_PIN_DITCH_M = 6      -- road's ground just beyond it when the ground under the deck's end is not more than this far under it (a ditch)
M.OVERPASS_PAD_M = 20       -- the ground under an overpass and this far beyond it (the elevation data has the deck as a ridge wider than
                            -- the deck) is taken as the straight line between the ground beyond: the delta is the ridge, and
M.OVERPASS_CHANGE_M = 8     -- may be moved this far (the cap of MAX_CHANGE_M is for the ground a road is on; a viaduct's ridge is more)
M.SYNC_MARGIN_M = 22        -- at a new block the cells within the stretch the car covers in SYNC_S seconds plus this are shaped at
M.SYNC_S = 0.5              -- once (a 150 m stretch cost 24.6 ms at 35 m/s); the rest follows over the next frames,
M.SYNC_MAX_M = 45           -- long before the car is there (never closer than FREEZE_M and what it covers in FREEZE_S, which is inside
M.SYNC_BUDGET_MS = 7        -- this), at most this far. What lies within the freeze radius is done whatever it costs; the rest of the
                            -- stretch only while the call stays under this many ms (the frames take it up)
M.FREEZE_M = 12             -- a later frame leaves the cells this far from a car that moves faster than FREEZE_SPEED (m/s) ...
M.FREEZE_SPEED = 2
M.FREEZE_S = 0.4            -- ... plus the stretch it covers in this many seconds (the car arrives there within a few frames)
M.RUN_END_REACH_M = 60      -- the end of a run of the line (the block's edge) changes what is found this far in: the spike window (4 points) and the taper
M.CALIBRATE_TOLERANCE_M = 0.25
M.MIN_CHANGE_M = 0.02       -- a cell that would move less than this is left as it is
M.LINE_TOL_M = 0.04         -- Earth's road lines (a mesh 2.5 cm over the old ground) are in step with the ground within this
M.BUCKET_M = 12
M.SAME_PASS_M = 50          -- segments of a line closer than this along it are one pass of the road (a hairpin's legs are two)

local floor, abs, max, min, sqrt = math.floor, math.abs, math.max, math.min, math.sqrt

-- 1 within `core` of the line, easing to 0 at core + blend
function M.weight(d, core, blend)
  if d <= core then return 1 end
  if d >= core + blend then return 0 end
  local t = (d - core) / blend
  return 1 - t * t * (3 - 2 * t)
end

-- The block's grid: { px, py, pz (its position, the south-west corner), sq (square size), n (samples per side) } or nil.
function M.gridInfo(tb, fallback)
  local ok, pos = pcall(function() return tb:getPosition() end)
  local okS, sq = pcall(function() return tb:getSquareSize() end)
  if not (ok and okS and pos and type(sq) == 'number' and sq > 0) then return nil end
  local n
  local okE, ext = pcall(function() return tb:getWorldBox():getExtents() end)
  if okE and ext then n = floor(ext.x / sq + 0.5) end
  if not n and fallback then n = fallback.n end
  if not n or n < 16 then return nil end
  return { px = pos.x, py = pos.y, pz = pos.z, sq = sq, n = n }
end

local E = {}
E.__index = E

-- A string that is the same for the same stretches (their points, the spans and the tunnels left alone, the edition's limit).
local function linesKey(lines)
  local out = {}
  for _, line in ipairs(lines) do
    out[#out + 1] = tostring(#line.pts) .. ':' .. tostring(line.spikeM)
    for _, p in ipairs(line.pts) do out[#out + 1] = string.format('%.2f,%.2f', p[1], p[2]) end
    for _, o in ipairs(line.others or {}) do
      local a, b = o.pts[1], o.pts[#o.pts]
      out[#out + 1] = string.format('o%d:%.1f,%.1f,%.1f,%.1f,%.1f', #o.pts, a[1], a[2], b[1], b[2], o.hw or 0)
    end
    for _, name in ipairs({ 'skip', 'keep' }) do
      local t, ks = line[name], {}
      for k, v in pairs(t or {}) do ks[#ks + 1] = tostring(k) .. '=' .. tostring(v) end
      table.sort(ks)
      out[#out + 1] = name .. ':' .. table.concat(ks, ',')
    end
  end
  return table.concat(out, ';')
end

-- env: block() -> TerrainBlock or nil; now() -> ms (monotonic); clock() -> s (for the status and a car's speed);
-- player() -> { x, y, speed (m/s, optional: else from the last two looks) } or nil;
-- ground(x, y) -> the game's terrain height there or nil; deck(x, y) -> the top of the static colliders there or nil;
-- tunnels() -> { { x, y }, ... } points along the axes of the tunnels Earth has built (optional); log(level, msg); vec3(x, y, z).
function M.new(env)
  return setmetatable({
    env = env, enabled = true, lines = nil, gen = 0, dirty = false, rec = nil, job = nil, fresh = false,
    stat = { cells = 0, maxChange = 0, maxCore = 0, wantedMax = 0, wantedCore = 0, capped = 0, ms = 0, frames = 0, jobs = 0,
      total = 0, skipped = 0, state = 'idle', syncCells = 0, syncMs = 0, deferred = 0 },
  }, E)
end

-- The stretches to shape, in the current world frame: lines = { { pts = { {x, y, z}, ... }, skip = { [i] = true } } }
-- (skip[i]: the stretch from point i to i + 1 is a bridge or a tunnel). Replaces what was set; nil or {} for none.
-- A block that came with its own unshaped ground and was not shaped yet (a swap whose frame was not known) is done at once.
function E:setLines(lines)
  lines = (lines and #lines > 0) and lines or nil
  local key = lines and linesKey(lines) or nil
  -- (the same stages again, on a block already shaped for them or being shaped: the loop's start after the loading screen's shaping, an
  -- anchor commit that did not move them: no second job, which would put the heights back and shape them again)
  if key and key == self.linesKey and self.lines and (self.job or (self.rec and self.rec.gen == self.gen)) and not self.dirty then
    self.lines = lines
    return
  end
  self.lines, self.linesKey, self.lineCells = lines, key, nil
  self.tunNear, self.tunSeen = self:tunnelsNear(), nil
  self.dirty = self:wanted()
  self.job = nil
  if self.dirty and self.fresh then self:shapeNow() end
end

-- A new block's heights are in (Earth's load or swap): what was written to the old data is gone with it. The block's own
-- ground is under the car now, so the stretch round the car is shaped before this returns; `stale` says the
-- lines are not in the frame of this block (a rebase or a teleport is under way: setLines follows with the new ones).
function E:blockChanged(stale)
  self.gen = self.gen + 1
  self.rec = nil
  self.job = nil
  self.dirty = self:wanted()
  self.fresh = true
  self.noSync = false
  if self.dirty and not stale then self:shapeNow() end
end

function E:setEnabled(on)
  on = on and true or false
  if on == self.enabled then return end
  self.enabled = on
  self.job = nil
  -- (switched off, the ground along the road goes back to Earth's, but for a road across a tunnel: that repair is not part of the option, see wanted)
  self.dirty = self:wanted()
end

-- The loop is over: the heights go back and the lines are dropped.
function E:stop()
  self.lines, self.linesKey, self.dirty, self.job, self.lineCells, self.tunNear = nil, nil, false, nil, nil, nil
end

function E:busy() return self.job ~= nil or self.dirty or (self.rec ~= nil and self.rec.n > 0 and not self:wanted()) end
-- Is there anything to write? The general shaping is the option's (self.enabled); the repair of a road's ground where it crosses over a tunnel (crossing.lua) is not:
-- the stage cannot be driven without it, so it stays on whenever the stage's lines are set and a tunnel Earth built is near them.
function E:wanted() return self.lines ~= nil and (self.enabled or (self.tunNear or 0) > 0) end

-- How many of the tunnel points Earth has made (env.tunnels) are near the lines: a change of that number plans the work again (the road tile that has the tunnel comes after the terrain).
function E:tunnelsNear()
  return #self:nearTunnelPoints()
end

-- The tunnel points Earth has made that are near the lines.
function E:nearTunnelPoints()
  local list = self.env.tunnels and self.env.tunnels() or nil
  if not (list and self.lines) then return {} end
  local cells = self.lineCells
  if not cells then                                  -- (once for these lines: a long stage has tens of thousands of points)
    cells = {}
    for _, line in ipairs(self.lines) do
      local lastX, lastY
      for _, p in ipairs(line.pts) do
        local cx, cy = floor(p[1] / M.TUNNEL_CELL_M), floor(p[2] / M.TUNNEL_CELL_M)
        if cx ~= lastX or cy ~= lastY then
          lastX, lastY = cx, cy
          for dx = -1, 1 do for dy = -1, 1 do cells[(cx + dx) * 131072 + cy + dy] = true end end
        end
      end
    end
    self.lineCells = cells
  end
  local out = {}
  for _, t in ipairs(list) do
    if cells[floor(t[1] / M.TUNNEL_CELL_M) * 131072 + floor(t[2] / M.TUNNEL_CELL_M)] then out[#out + 1] = t end
  end
  return out
end

local function log(self, level, msg) if self.env.log then self.env.log(level, msg) end end

-- The player's car: x, y and its speed in m/s (what the env says, else from where it was at the last look), or nil.
function E:car()
  local env = self.env
  local P = env.player and env.player() or nil
  if not P then return nil end
  local speed = P.speed
  local t = env.clock and env.clock() or nil
  local last = self.lastCar
  if speed == nil then
    speed = 0
    if last and t and t > last.t + 1e-6 then
      speed = sqrt((P.x - last.x) ^ 2 + (P.y - last.y) ^ 2) / (t - last.t)
    end
  end
  if t and (not last or t ~= last.t) then self.lastCar = { x = P.x, y = P.y, t = t } end
  return P.x, P.y, speed
end

-- The ground a later frame leaves alone: a disc round a car that moves, or nil. force: restores at the end of the session.
function E:freezeZone(force)
  if force or not self.carX or self.carSpeed <= M.FREEZE_SPEED then return nil end
  local r = M.FREEZE_M + self.carSpeed * M.FREEZE_S
  return { x = self.carX, y = self.carY, r2 = r * r }
end

-- The grid cell nearest a world point, and that vertex's world position.
local function vertex(info, x, y)
  local gx, gy = floor((x - info.px) / info.sq + 0.5), floor((y - info.py) / info.sq + 0.5)
  return gx, gy, info.px + gx * info.sq, info.py + gy * info.sq
end

-- What a grid height must have added to be the game's world height: measured at a few vertices on the line against the
-- game's own height query. Returns the offset, or nil and why.
local function calibrate(self, tb, info, pts)
  local ground = self.env.ground
  if not ground then return info.pz, 'no height query: the grid is taken as above the block base' end
  -- (a vertex on the line and one 15 m east and north of it: a mirrored or shifted grid would show as offsets that differ
  -- wherever the ground is not flat, and a road running along one row alone could not tell)
  local offs, tried = {}, 0
  for _, p in ipairs(pts) do
    for _, d in ipairs({ { 0, 0 }, { 15, 0 }, { 0, 15 } }) do
      local gx, gy, wx, wy = vertex(info, p[1] + d[1], p[2] + d[2])
      if gx > 2 and gy > 2 and gx < info.n - 3 and gy < info.n - 3 then
        local g = ground(wx, wy)
        local okH, h = pcall(function() return tb:getHeightGrid(gx, gy) end)
        tried = tried + 1
        if type(g) == 'number' and okH and type(h) == 'number' then offs[#offs + 1] = g - h end
      end
    end
    if #offs >= 9 or tried >= 24 then break end
  end
  if #offs < 2 then return nil, 'too few vertices to measure the grid against the ground (' .. #offs .. ')' end
  local lo, hi = math.huge, -math.huge
  for _, o in ipairs(offs) do lo, hi = min(lo, o), max(hi, o) end
  if hi - lo > M.CALIBRATE_TOLERANCE_M then
    return nil, string.format('the grid does not match the ground (offsets %.2f to %.2f m)', lo, hi)
  end
  return (lo + hi) / 2
end

-- Is a collider that stands `top` at (x, y) wide enough to carry or cover the road: is there one at the same height (DECK_ACROSS_TOL_M)
-- DECK_ACROSS_M to either side of that point, across the line whose direction there is (dx, dy)? A wall, a barrier, a rail, a pole or a
-- building's corner beside the road is narrow, the deck of a bridge or an overpass is not. The game's ray finds the ground
-- where no collider is, so a narrow one has a different height beside it.
local function carriesAcross(env, x, y, dx, dy, top, hint)
  local l = sqrt(dx * dx + dy * dy)
  if l < 1e-6 then return false end
  local nx, ny = -dy / l * M.DECK_ACROSS_M, dx / l * M.DECK_ACROSS_M
  for _, side in ipairs({ 1, -1 }) do
    local t = env.deck(x + nx * side, y + ny * side, hint)
    if not (t and abs(t - top) <= M.DECK_ACROSS_TOL_M) then return false end
  end
  return true
end

-- What the colliders standing above the line are. `top[i]`: the top of the static colliders at point i (nil: not asked or
-- nothing), wide[i]: it is wide enough to be a deck or an overpass (carriesAcross; asked where it stands DECK_LAND_M over the ground), G[i]: the
-- game's ground there, cum[i]: metres along the line. A maximal run of points whose wide collider stands more than DECK_LAND_M over the ground,
-- with one more than DECK_ABOVE_M over it, runs from where the deck lands on the road before it to where it lands past it, and is either
--   * a deck our road is on: where it begins (or ends) its top continues from the ground just before (after) it, as a road does onto a
--     bridge, however far its middle stands over a gully or a culvert, and its top does not jump from one point to the next beyond what a
--     grade and a hump do (a structure beside the road does): deck[i], left alone, and its ends are met (the ground follows the road
--     we drive: ends[k] = { i, j, joinA, joinB, note } says which ends continue from the road); or
--   * an overpass above the road (the A25 over the Svolte di Popoli's SR17 at 22 and 31 degrees): its top is metres above the road
--     where it begins, nothing continues from the ground: over[i], the road is under it and the ground is shaped as everywhere else.
-- Anything else (a collider that is narrow, a run too short to be a deck, with a jump in its top) is no deck and no overpass: the ground is
-- the ground. A run with no ground known beside it cannot be told, and stays a deck when it is long enough (the old behaviour).
-- The third result says what was found, for the log: { { i, j, why } } (why: 'overpass', 'deck', 'narrow', 'short', 'stepped').
local function classifyDecks(G, top, wide, cum, n)
  local deck, over, notes, ends = {}, {}, {}, {}
  local flagged, ext, high = {}, {}, {}
  for i = 1, n do
    if G[i] and top[i] then
      local above = top[i] - G[i]
      if above > M.DECK_ABOVE_M then
        high[i] = true
        if wide[i] then flagged[i] = true end
      end
      if wide[i] and above > M.DECK_LAND_M then ext[i] = true end
    end
  end
  local i = 1
  while i <= n do                                  -- (what stands over the line but is not wide: a wall, a pole)
    if high[i] and not flagged[i] then
      local j = i
      while j + 1 <= n and high[j + 1] and not flagged[j + 1] do j = j + 1 end
      notes[#notes + 1] = { i, j, 'narrow' }
      i = j + 1
    else
      i = i + 1
    end
  end
  i = 1
  while i <= n do
    if ext[i] then
      local j = i
      while j + 1 <= n and ext[j + 1] do j = j + 1 end
      local any = false
      for q = i, j do if flagged[q] then any = true; break end end
      if any then
        local known, joinA, joinB = 0, false, false
        if i > 1 and G[i - 1] then
          known = known + 1
          if abs(top[i] - G[i - 1]) <= M.DECK_JOIN_TOL_M + M.DECK_JOIN_PER_M * (cum[i] - cum[i - 1]) then joinA = true end
        end
        if j < n and G[j + 1] then
          known = known + 1
          if abs(top[j] - G[j + 1]) <= M.DECK_JOIN_TOL_M + M.DECK_JOIN_PER_M * (cum[j + 1] - cum[j]) then joinB = true end
        end
        if known > 0 and not (joinA or joinB) then
          for q = i, j do over[q] = true end
          notes[#notes + 1] = { i, j, 'overpass' }
        else
          local why = 'deck'
          if cum[j] - cum[i] < M.DECK_MIN_RUN_M then why = 'short' end
          -- a jump: the top changes between two points by more than the steepest road does, or a point is off the line between its
          -- neighbours (a taller structure; a slope and a slight hump are not)
          for q = i, j - 1 do
            if abs(top[q + 1] - top[q]) > curate.MAX_GRADE * (cum[q + 1] - cum[q]) + M.DECK_STEP_M then why = 'stepped' end
          end
          for q = i + 1, j - 1 do
            local t = (cum[q] - cum[q - 1]) / (cum[q + 1] - cum[q - 1])
            if abs(top[q] - (top[q - 1] + (top[q + 1] - top[q - 1]) * t)) > M.DECK_STEP_M then why = 'stepped' end
          end
          notes[#notes + 1] = { i, j, why }
          if why == 'deck' then
            for q = i, j do deck[q] = true end
            ends[#ends + 1] = { i = i, j = j, joinA = joinA, joinB = joinB, note = #notes }
          end
        end
      end
      i = j + 1
    else
      i = i + 1
    end
  end
  return deck, over, notes, ends
end

-- Where a deck's end is and the height it must be met at, for the end of a stretch of the line that goes onto (toward = 1) or off (toward = -1) a deck:
-- the road is at point e, the deck begins (as seen from e) between it and the next point, wherever a wide collider first stands over the
-- ground (rays every DECK_LIP_STEP_M along the line: the end of a deck over a gully is not at a point of the line); the deck's top
-- is measured a little onto it (rays DECK_LIP_STEP_M and DECK_PIN_INTO_M further, wide as a road, within DECK_PIN_MAX_M of the ground there) and
-- taken back to the end along its grade. Returns { e, dir, lx, ly (the end), tl (its
-- distance from e), dp (the deck's top minus the ground there) } or nil. `ground` is the game's ground at (x, y) or nil.
local function meetDeck(self, ground, pts, G, e, toward)
  local env = self.env
  local a, b = pts[e], pts[e + toward]
  if not (env.deck and a and b and G[e]) then return nil end
  if self.carX and (a[1] - self.carX) ^ 2 + (a[2] - self.carY) ^ 2 > M.DECK_RAY_M ^ 2 then return nil end
  local dx, dy = b[1] - a[1], b[2] - a[2]
  local len = sqrt(dx * dx + dy * dy)
  if len < 1e-6 then return nil end
  local ux, uy = dx / len, dy / len
  local t = len
  local s = 0
  while s <= len do
    local x, y = a[1] + ux * s, a[2] + uy * s
    local g = ground(x, y) or G[e]
    local top = env.deck(x, y, g)
    if top and top - g > M.DECK_LIP_M and carriesAcross(env, x, y, ux, uy, top, g) then t = s; break end
    s = s + M.DECK_LIP_STEP_M
  end
  local lx, ly = a[1] + ux * t, a[2] + uy * t
  local gl = ground(lx, ly) or G[e]
  -- the deck's top a little onto it, and DECK_PIN_INTO_M further: the top at its end is the first one taken back along their grade (a deck
  -- on a 6 percent grade is 15 cm lower at its end than 2.5 m onto it, which would leave the ground that much over it). It is a deck at road
  -- level when it is within DECK_PIN_MAX_M of the ground there or of the road's ground at the point e (the ground under a deck's end can be a
  -- ditch: the road beyond it is not)
  local function topAt(s)
    local x, y = a[1] + ux * (t + s), a[2] + uy * (t + s)
    local h = env.deck(x, y, gl)
    if h and (abs(h - gl) <= M.DECK_PIN_MAX_M or (abs(h - G[e]) <= M.DECK_PIN_MAX_M and abs(h - gl) <= M.DECK_PIN_DITCH_M)) and carriesAcross(env, x, y, ux, uy, h, gl) then return h end
    return nil
  end
  local s1 = M.DECK_LIP_STEP_M
  local h1 = topAt(s1)
  if not h1 then return nil end
  local h2 = topAt(s1 + M.DECK_PIN_INTO_M)
  local grade = h2 and (h2 - h1) / M.DECK_PIN_INTO_M or 0          -- (along the line into the deck)
  grade = max(-curate.MAX_GRADE, min(curate.MAX_GRADE, grade))
  local top = h1 - grade * s1
  -- the ramp goes the other way, out from the deck's end: its grade is the opposite one
  return { e = e, dir = toward, lx = lx, ly = ly, tl = t, top = top, gd = -grade }
end

-- The height of a deck at the end of a tagged span of the line: a ray at the point DECK_PIN_INTO_M onto the deck from point idx (toward is
-- the index step to the deck's side), when it is within DECK_PIN_MAX_M of the ground there and as wide as a road (carriesAcross). Else nil.
local function deckEnd(self, pts, G, idx, toward)
  local env = self.env
  local a, b = pts[idx], pts[idx + toward]
  if not (env.deck and a and b and G[idx]) then return nil end
  if self.carX and (a[1] - self.carX) ^ 2 + (a[2] - self.carY) ^ 2 > M.DECK_RAY_M ^ 2 then return nil end
  local dx, dy = b[1] - a[1], b[2] - a[2]
  local l = sqrt(dx * dx + dy * dy)
  if l < 1e-6 then return nil end
  local k = min(M.DECK_PIN_INTO_M, l * 0.5) / l
  local x, y = a[1] + dx * k, a[2] + dy * k
  local top = env.deck(x, y, G[idx])
  if top and abs(top - G[idx]) <= M.DECK_PIN_MAX_M and carriesAcross(env, x, y, dx, dy, top, G[idx]) then return top end
  return nil
end

-- The height of a cubic from (0, p0) with grade m0 to (L, p1) with grade m1 at d (the Hermite curve: no step in the height or the grade at either end)
local function hermite(p0, m0, p1, m1, L, d)
  local t = d / L
  local t2 = t * t
  local t3 = t2 * t
  return (2 * t3 - 3 * t2 + 1) * p0 + (t3 - 2 * t2 + t) * L * m0 + (-2 * t3 + 3 * t2) * p1 + (t3 - t2) * L * m1
end

-- How far over the limits of a road the cubic of hermite() is: 1 is at the grade change limit (curate.MAX_GRADE_CHANGE, at either end, where a
-- cubic's second derivative is largest) or at the grade limit (curate.MAX_GRADE).
local function hermiteScore(p0, m0, p1, m1, L)
  local c0 = (6 * (p1 - p0) - 4 * L * m0 - 2 * L * m1) / (L * L)
  local c1 = (-6 * (p1 - p0) + 2 * L * m0 + 4 * L * m1) / (L * L)
  local score = max(abs(c0), abs(c1)) / curate.MAX_GRADE_CHANGE
  for q = 0, 10 do
    local t = q / 10
    local g = (6 * t * t - 6 * t) * (p0 - p1) / L + (3 * t * t - 4 * t + 1) * m0 + (3 * t * t - 2 * t) * m1
    score = max(score, abs(g) / curate.MAX_GRADE)
  end
  return score
end

-- The ground of the road past a deck's end. The road leaves the deck at pin ({ lx, ly (the deck's end), tl (its distance to the line's
-- point e), top (the deck's top there), gd (its grade going away from the deck) }) and runs on along the line from point e by `step` through the
-- run of points i to j. The ground of that road is measured along it (the block's heights, before any change of ours: every metre for the first
-- RAMP_NEAR_M, the ground right past a deck falls away into the ditch under it, then every RAMP_STEP_M), and the height it should have is the deck's
-- top going on along its grade, joined to the ground at L (the ground there, with its grade) with a cubic, which has no step in the height or the grade
-- at either end; L is the shortest from RAMP_MIN_M up that keeps the grade and the grade change of a road. The delta at each sample is that height minus
-- the ground there: positive where the ground is under it, NEGATIVE where it is over (a hillside, a bank: the ground is cut down to the deck's top,
-- ), 0 from L on. (An earlier version gave the ground past the end the deck's top minus the ground UNDER the deck's end, which is a ditch's edge, and
-- applied it to the road beyond, which Earth had graded to the deck's level: a wall of the ditch's depth across the road.)
-- Returns { dir, tagged, pin, L, n (samples to L), d, x, y, G (the ground), D (the delta), z (the height asked for), sLip (metres along the line at the
-- deck's end), sign (+1: the line's metres grow with d), prof (the points of the log: { d, x, y, earth, target }) } or nil.
local function makeRamp(ground, pts, cum, i, j, pin, step)
  local e = pin.e
  local Q, c = { { pin.lx, pin.ly } }, { 0 }
  local q = e
  while q >= i and q <= j do
    local p, last = pts[q], Q[#Q]
    local seg = sqrt((p[1] - last[1]) ^ 2 + (p[2] - last[2]) ^ 2)
    if seg > 1e-6 then Q[#Q + 1] = { p[1], p[2] }; c[#c + 1] = c[#c] + seg end
    q = q + step
  end
  if #Q < 2 then return nil end
  local function at(d)
    if d <= 0 then
      local u = c[2]
      return Q[1][1] + (Q[2][1] - Q[1][1]) / u * d, Q[1][2] + (Q[2][2] - Q[1][2]) / u * d
    end
    for k = 1, #Q - 1 do
      if d <= c[k + 1] + 1e-9 then
        local t = (d - c[k]) / (c[k + 1] - c[k])
        return Q[k][1] + (Q[k + 1][1] - Q[k][1]) * t, Q[k][2] + (Q[k + 1][2] - Q[k][2]) * t
      end
    end
    return Q[#Q][1], Q[#Q][2]
  end
  local total = min(c[#c], M.RAMP_MAX_M)
  local d, x, y, G = {}, {}, {}, {}
  local dd = 0
  while dd <= total + 1e-6 do
    local px, py = at(dd)
    local g = ground(px, py)
    if not g then break end
    d[#d + 1], x[#x + 1], y[#y + 1], G[#G + 1] = dd, px, py, g
    dd = dd + (dd < M.RAMP_NEAR_M - 1e-6 and M.RAMP_NEAR_STEP_M or M.RAMP_STEP_M)
  end
  local n = #d
  if n < 3 then return nil end
  -- (the ground right at the end can be the ditch's edge under the deck, a step of a metre or more from the road's: the first point has the ground of
  -- the next then, not the edge's depth, which is not the road's to fill)
  local Gd = {}
  for k = 1, n do Gd[k] = G[k] end
  if abs(G[1] - G[2]) > M.RAMP_EDGE_M then Gd[1] = Gd[2] end
  local top, m0 = pin.top, pin.gd
  -- the ground's grade over the 5 m either side of sample k
  local function gradeAt(k)
    local a, b = k, k
    while a > 1 and d[k] - d[a] < 5 do a = a - 1 end
    while b < n and d[b] - d[k] < 5 do b = b + 1 end
    if b == a then return 0 end
    return max(-curate.MAX_GRADE, min(curate.MAX_GRADE, (Gd[b] - Gd[a]) / (d[b] - d[a])))
  end
  local kL, best, bestScore = nil, n, math.huge
  for k = 2, n do
    if d[k] >= M.RAMP_MIN_M or k == n then
      local sc = hermiteScore(top, m0, Gd[k], gradeAt(k), d[k])
      if sc <= M.RAMP_SCORE then kL = k; break end
      if sc < bestScore then best, bestScore = k, sc end
    end
  end
  kL = kL or best
  local L, m1 = d[kL], gradeAt(kL)
  local D, z = {}, {}
  for k = 1, kL do
    z[k] = k == kL and Gd[k] or hermite(top, m0, Gd[kL], m1, L, d[k])
    D[k] = z[k] - Gd[k]
  end
  local r = { dir = pin.dir, tagged = pin.tagged, pin = pin, L = L, n = kL, d = d, x = x, y = y, G = G, D = D, z = z,
    sign = step == 1 and 1 or -1, sLip = step == 1 and (cum[e] - pin.tl) or (cum[e] + pin.tl) }
  -- the points of the log, every PROFILE_STEP_M from two steps before the end to a step past the ramp: the ground there, and the height asked for
  r.prof = {}
  local pd = -2 * M.PROFILE_STEP_M
  while pd <= L + M.PROFILE_STEP_M + 1e-6 do
    local px, py = at(pd)
    local t
    if pd < 0 then t = top + m0 * pd elseif pd <= L then t = hermite(top, m0, Gd[kL], m1, L, pd) end
    r.prof[#r.prof + 1] = { pd, px, py, ground(px, py), t }
    pd = pd + M.PROFILE_STEP_M
  end
  r.at = at
  return r
end

-- The block's height at a world point as it stands now (bilinear between the vertices), or nil.
local function heightAt(tb, info, off, x, y)
  local fx, fy = (x - info.px) / info.sq, (y - info.py) / info.sq
  local gx, gy = floor(fx), floor(fy)
  local tx, ty = fx - gx, fy - gy
  if gx < 0 or gy < 0 or gx + 1 >= info.n or gy + 1 >= info.n then return nil end
  local ok, a, b, c, d = pcall(function()
    return tb:getHeightGrid(gx, gy), tb:getHeightGrid(gx + 1, gy), tb:getHeightGrid(gx, gy + 1), tb:getHeightGrid(gx + 1, gy + 1)
  end)
  if not (ok and type(a) == 'number' and type(b) == 'number' and type(c) == 'number' and type(d) == 'number') then return nil end
  return off + (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty
end

-- Segments of the lines inside the block, as parallel arrays, the segments within the stretch the player is at
-- first. Bridges and tunnels (curated spans, a deck) are not in it; the ground under an overpass is. S.stop[k] is 2 for the segments of a ramp
-- (makeRamp): the cells behind a segment's start, projected on it, are under the deck, or the segment before's, and not its to move.
local function plan(self, tb, info, off)
  local env = self.env
  local reach = M.CORE_M + M.BLEND_M
  local lo = info.sq * M.EDGE_CELLS - reach
  local xmin, xmax = info.px + lo, info.px + info.n * info.sq - lo
  local ymin, ymax = info.py + lo, info.py + info.n * info.sq - lo
  local cx, cy = self.carX, self.carY
  local S = { ax = {}, ay = {}, az = {}, dx = {}, dy = {}, dz = {}, l2 = {}, near = {}, ln = {}, s = {}, cap = {}, stop = {}, n = 0, cuts = {},
    lv = {}, tx = {} }
  local skipped = { span = 0, ground = 0, deck = 0, over = 0, pinned = 0, tunnel = 0 }
  -- the stretches fixed: how many of each kind, their length along the line, the largest delta, and each one's place (list); what the
  -- colliders over the line were taken for (found: a deck left alone, a narrow one, ...), for the log
  local fixes = { n = 0, length = 0, largest = 0, list = {}, found = {}, spread = 0, spreadMax = 0, ramps = {}, covered = 0, crossings = 0 }
  -- the tubes of Earth's tunnels in the game now (points along their axes), for the points of the line beside one
  local tunnelPts = env.tunnels and env.tunnels() or nil
  local keepR2 = (M.TUNNEL_KEEP_M + M.TUNNEL_POINT_M) ^ 2
  -- the tunnels as lines, for the roads that cross over one
  local chains = tunnelPts and #tunnelPts > 0 and crossing.chains(self:nearTunnelPoints()) or nil
  local rawGround = function(x, y)
    if x < xmin or x > xmax or y < ymin or y > ymax then return nil end
    return heightAt(tb, info, off, x, y)
  end
  local rim = M.EDGE_CELLS + 2
  local rayR2 = M.DECK_RAY_M * M.DECK_RAY_M
  -- how far the car is from segment k (squared: the nearest first), which begins at (ax, ay)
  local function nearCar(k, ax, ay)
    if cx then
      local l2 = S.l2[k]
      local t = l2 > 1e-9 and ((cx - ax) * S.dx[k] + (cy - ay) * S.dy[k]) / l2 or 0
      if t < 0 then t = 0 elseif t > 1 then t = 1 end
      local ex, ey = ax + S.dx[k] * t - cx, ay + S.dy[k] * t - cy
      S.near[k] = ex * ex + ey * ey
    else
      S.near[k] = k
    end
  end
  for ln, line in ipairs(self.lines) do
    local pts = line.pts
    -- the game's own ground under each point (the block's heights, before anything of ours; not on the block's rim,
    -- which Earth sinks), and the colliders above it
    local G, top, wide = {}, {}, {}
    for i, p in ipairs(pts) do
      if self.enabled and p[1] >= xmin - 40 and p[1] <= xmax + 40 and p[2] >= ymin - 40 and p[2] <= ymax + 40 then      -- (the option's shaping only)
        local gx, gy = vertex(info, p[1], p[2])
        if gx >= rim and gy >= rim and gx < info.n - rim and gy < info.n - rim then
          local okH, h = pcall(function() return tb:getHeightGrid(gx, gy) end)
          if okH and type(h) == 'number' then
            local g = h + off
            G[i] = g
            if env.deck and (not cx or (p[1] - cx) ^ 2 + (p[2] - cy) ^ 2 <= rayR2) then
              top[i] = env.deck(p[1], p[2], g)
              if top[i] and top[i] - g > M.DECK_LAND_M then     -- (how wide it is, only where something stands over the line)
                local a, b = pts[max(i - 1, 1)], pts[min(i + 1, #pts)]
                wide[i] = carriesAcross(env, p[1], p[2], b[1] - a[1], b[2] - a[2], top[i], g)
              end
            end
          end
        end
      end
    end
    -- the points beside a tunnel (the geometry's tunnelsBeside, and the tubes in the game): the ground there is Earth's, as it made it
    -- for the tunnel; the run of the line is broken at them like at a deck, and nothing of ours reaches them
    local held = {}
    for i, p in ipairs(pts) do
      if G[i] then
        local keep = line.keep and line.keep[i]
        if not keep and tunnelPts then
          for _, t in ipairs(tunnelPts) do
            if (t[1] - p[1]) ^ 2 + (t[2] - p[2]) ^ 2 <= keepR2 then keep = true; break end
          end
        end
        if keep then G[i], top[i], wide[i], held[i] = nil, nil, nil, true end
      end
    end
    -- along the line (m) at each point
    local cum = { 0 }
    for i = 2, #pts do cum[i] = cum[i - 1] + sqrt((pts[i][1] - pts[i - 1][1]) ^ 2 + (pts[i][2] - pts[i - 1][2]) ^ 2) end
    local deck, over, notes, deckRuns = classifyDecks(G, top, wide, cum, #pts)
    -- Where the road goes onto or off a deck of ours the colliders show, the ground meets the deck's top: pins[e] lists the
    -- ways the ground at point e, the last (first) of a run before (after) a deck, is met to it. Not at the end of a tagged span or one the
    -- profile found (line.skip: the ends of those are met further down, or not at all).
    local pins = {}
    local groundHere = function(x, y)
      local gx, gy = vertex(info, x, y)
      if gx >= rim and gy >= rim and gx < info.n - rim and gy < info.n - rim then
        local okH, h = pcall(function() return tb:getHeightGrid(gx, gy) end)
        if okH and type(h) == 'number' then return h + off end
      end
      return nil
    end
    local skipAt = function(k) return line.skip and line.skip[k] end
    local metOf = {}
    for _, d in ipairs(deckRuns) do
      local met = { false, false }
      if d.joinA and d.i > 1 and G[d.i - 1] and not skipAt(d.i - 1) then
        local pin = meetDeck(self, groundHere, pts, G, d.i - 1, 1)
        if pin then pins[pin.e] = pins[pin.e] or {}; table.insert(pins[pin.e], pin); met[1] = true end
      end
      if d.joinB and d.j < #pts and G[d.j + 1] and not skipAt(d.j) then
        local pin = meetDeck(self, groundHere, pts, G, d.j + 1, -1)
        if pin then pins[pin.e] = pins[pin.e] or {}; table.insert(pins[pin.e], pin); met[2] = true end
      end
      metOf[d.note] = met
    end
    for q, nt in ipairs(notes) do
      local f = fixes.found
      f[#f + 1] = { ln = ln, why = nt[3], s0 = cum[nt[1]], s1 = cum[nt[2]], x = pts[nt[1]][1], y = pts[nt[1]][2],
        above = top[nt[1]] - G[nt[1]], met = metOf[q] }
    end
    -- the points under an overpass with the pad past it
    local padded = {}
    local anyOver = false
    for i in pairs(over) do
      anyOver = true
      local q = i
      while q >= 1 and cum[i] - cum[q] <= M.OVERPASS_PAD_M do padded[q] = true; q = q - 1 end
      q = i
      while q <= #pts and cum[q] - cum[i] <= M.OVERPASS_PAD_M do padded[q] = true; q = q + 1 end
    end
    -- What is fixed, as a delta D[i] at each point: the height the line should have there minus the ground under it, 0
    -- where the ground is as it should be, and the ground is not touched at all. The game's own ground is the reference, not the
    -- line's heights: those are the offline terrain data plus one offset, and at the Popoli stage's start they stood 10 m above the
    -- game's ground. A run of points is broken at a bridge or a tunnel span, a deck, and where the ground is not known.
    --   * core points: under an overpass (and the pad past it), where the elevation data has the viaduct's deck in it as a ridge, and the
    --     spikes of the profile (curate.despike with SPIKE_M) with the points within TAPER_M of them, the taper along the line. Their
    --     fixed height is the straight line between the nearest points that are not core: for a spike its own foot, which is why
    --     the delta ends at 0 there;
    --   * the first points of a run next to a bridge OpenStreetMap tags (line.skip[i] == 'tagged'): the ground is met to the deck's height,
    --     measured (a ray, which must hit something as wide as a road), and the delta ramps to 0 over TAPER_M into the run. A span the
    --     profile alone found, and a deck the colliders show, are left alone without that.
    local D, kindAt = {}, {}
    local ramps = {}
    local i = 1
    while i <= #pts do
      if G[i] and not deck[i] then
        local j = i
        while j + 1 <= #pts and G[j + 1] and not deck[j + 1] and not (line.skip and line.skip[j]) do j = j + 1 end
        if j > i then
          -- (a run that ends where the next point has no ground is cut by the block's edge: what is found within RUN_END_REACH_M of
          -- the cut may differ from what the block before found, so the car must be far from it, see shapeNow)
          if i > 1 and not G[i - 1] and not held[i - 1] then S.cuts[#S.cuts + 1] = { pts[i][1], pts[i][2] } end
          if j < #pts and not G[j + 1] and not held[j + 1] then S.cuts[#S.cuts + 1] = { pts[j][1], pts[j][2] } end
          fixes.covered = fixes.covered + cum[j] - cum[i]
          for q = i, j do D[q] = 0 end
          -- the spikes, found on the ground with an overpass's ridge already taken out (it is a fix of its own)
          local H = {}
          for q = i, j do H[#H + 1] = G[q] end
          if anyOver then
            local q = i
            while q <= j do
              if padded[q] then
                local a = q
                while q + 1 <= j and padded[q + 1] do q = q + 1 end
                local b = q
                local from, to = a - 1 >= i and (a - 1) or nil, b + 1 <= j and (b + 1) or nil
                for m = a, b do
                  if from and to then
                    H[m - i + 1] = G[from] + (G[to] - G[from]) * (cum[m] - cum[from]) / (cum[to] - cum[from])
                  elseif from then H[m - i + 1] = G[from]
                  elseif to then H[m - i + 1] = G[to] end
                end
              end
              q = q + 1
            end
          end
          local core, kind = {}, {}
          if j - i >= 2 then
            local flagged = curate.despike(H, { SPIKE_M = line.spikeM or M.SPIKE_M, BUMPS_ONLY = true })
            for _, f in ipairs(flagged) do
              local q = i + f - 1
              local m = q
              while m >= i + 1 and cum[q] - cum[m] <= M.TAPER_M do
                core[m] = true; kind[m] = kind[m] or 'spike'
                m = m - 1
              end
              m = q + 1
              while m <= j - 1 and cum[m] - cum[q] <= M.TAPER_M do   -- (a run's ends are what it is pinned to: not spikes)
                core[m] = true; kind[m] = kind[m] or 'spike'
                m = m + 1
              end
            end
          end
          for q = i, j do if padded[q] then core[q] = true; kind[q] = 'overpass' end end
          -- the fixed height of each run of core points: the straight line between the points either side of it (one side only at
          -- a run's end)
          local q = i
          while q <= j do
            if core[q] then
              local a = q
              while q + 1 <= j and core[q + 1] do q = q + 1 end
              local b = q
              local lo, hi = a - 1 >= i and (a - 1) or nil, b + 1 <= j and (b + 1) or nil
              local want, peak, peakAt, isOver = {}, 0, a, false
              for m = a, b do
                local zf
                if lo and hi then zf = G[lo] + (G[hi] - G[lo]) * (cum[m] - cum[lo]) / (cum[hi] - cum[lo])
                elseif lo then zf = G[lo]
                elseif hi then zf = G[hi] end
                if zf then
                  want[m] = zf - G[m]
                  if abs(want[m]) > peak then peak, peakAt = abs(want[m]), m end
                end
                if kind[m] == 'overpass' then isOver = true end
              end
              -- A fix that asks for more than MAX_CHANGE_M is spread, not cut off at the cap: held at the cap cell by cell it
              -- leaves a plateau at the cap with the defect's own steep sides (a step). The whole stretch's delta is scaled to the cap,
              -- the remainder is the defect's shape smaller, over the same stretch and taper. (An overpass has its own, bigger cap.)
              local scale = 1
              if not isOver and peak > M.MAX_CHANGE_M then
                scale = M.MAX_CHANGE_M / peak
                fixes.spread = fixes.spread + 1
                if peak > fixes.spreadMax then
                  fixes.spreadMax, fixes.spreadAt = peak, { pts[peakAt][1], pts[peakAt][2], cum[peakAt], ln }
                end
              end
              for m = a, b do
                if want[m] then D[m] = want[m] * scale; kindAt[m] = kind[m] end
              end
            end
            q = q + 1
          end
          -- the ends next to a tagged span, and next to a deck of ours the colliders show: the ground of the road beyond the deck's end follows
          -- the deck's top and is joined to Earth's ground there (makeRamp). The ramps are segments of their own (below): the
          -- delta along them is measured on the ground of the road, point by point, not at the line's points
          local function addRamp(pin, step)
            local r = makeRamp(groundHere, pts, cum, i, j, pin, step)
            if not r then return end
            skipped.pinned = skipped.pinned + 1
            r.ln = ln
            ramps[#ramps + 1] = r
            local big, at = 0, 1
            for k = 1, r.n do if abs(r.D[k]) > big then big, at = abs(r.D[k]), k end end
            local s0, s1 = r.sLip, r.sLip + r.sign * r.L
            fixes.n = fixes.n + 1
            fixes['deck end'] = (fixes['deck end'] or 0) + 1
            fixes.length = fixes.length + abs(s1 - s0)
            if big > fixes.largest then fixes.largest = big end
            fixes.list[#fixes.list + 1] = { ln = ln, kind = 'deck end', s0 = min(s0, s1), s1 = max(s0, s1), x = r.x[at], y = r.y[at], d = r.D[at] }
          end
          if i > 1 and line.skip and line.skip[i - 1] == 'tagged' then
            local top = deckEnd(self, pts, G, i, -1)
            if top then addRamp({ e = i, dir = -1, lx = pts[i][1], ly = pts[i][2], tl = 0, top = top, gd = 0, tagged = true }, 1) end
          end
          if j < #pts and line.skip and line.skip[j] == 'tagged' then
            local top = deckEnd(self, pts, G, j, 1)
            if top then addRamp({ e = j, dir = 1, lx = pts[j][1], ly = pts[j][2], tl = 0, top = top, gd = 0, tagged = true }, -1) end
          end
          -- (dir: the index step from the road's point toward the deck; the ramp goes the other way, into the run)
          for _, pin in ipairs(pins[i] or {}) do
            if pin.dir == -1 then addRamp(pin, 1) end
          end
          for _, pin in ipairs(pins[j] or {}) do
            if pin.dir == 1 then addRamp(pin, -1) end
          end
        end
        i = j + 1
      else
        i = i + 1
      end
    end
    -- the stretches fixed: runs of points with a delta (it falls to 0 at the points either side)
    do
      local q = 1
      while q <= #pts do
        if D[q] and abs(D[q]) > 1e-9 then
          local a = q
          while q + 1 <= #pts and D[q + 1] and abs(D[q + 1]) > 1e-9 do q = q + 1 end
          local b = q
          local kind, big, at = 'deck end', 0, a
          for m = a, b do
            if kindAt[m] == 'overpass' then kind = 'overpass'
            elseif kindAt[m] == 'spike' and kind ~= 'overpass' then kind = 'spike' end
            if abs(D[m]) > big then big = abs(D[m]); at = m end
          end
          local x0, x1 = a > 1 and a - 1 or a, b < #pts and b + 1 or b
          fixes.n = fixes.n + 1
          fixes[kind] = (fixes[kind] or 0) + 1
          fixes.length = fixes.length + (cum[x1] - cum[x0])
          if big > fixes.largest then fixes.largest = big end
          -- (where it is, for the log: the user could not tell which stretch was the false one)
          fixes.list[#fixes.list + 1] = { ln = ln, kind = kind, s0 = cum[x0], s1 = cum[x1], x = pts[at][1], y = pts[at][2], d = D[at] }
        end
        q = q + 1
      end
    end
    local along = 0
    for i2 = 1, #pts - 1 do
      local a, b = pts[i2], pts[i2 + 1]
      local segLen = sqrt((b[1] - a[1]) ^ 2 + (b[2] - a[2]) ^ 2)
      local inside = max(a[1], b[1]) >= xmin and min(a[1], b[1]) <= xmax and max(a[2], b[2]) >= ymin and min(a[2], b[2]) <= ymax
      if inside then
        if line.skip and line.skip[i2] then skipped.span = skipped.span + 1
        elseif deck[i2] or deck[i2 + 1] then skipped.deck = skipped.deck + 1
        elseif held[i2] or held[i2 + 1] then skipped.tunnel = skipped.tunnel + 1
        elseif not (D[i2] and D[i2 + 1]) then skipped.ground = skipped.ground + 1
        elseif abs(D[i2]) > 1e-9 or abs(D[i2 + 1]) > 1e-9 then          -- (only where there is something to fix: no other segment is worked on)
          local k = S.n + 1
          S.n = k
          S.ax[k], S.ay[k], S.az[k] = a[1], a[2], D[i2]
          S.dx[k], S.dy[k], S.dz[k] = b[1] - a[1], b[2] - a[2], D[i2 + 1] - D[i2]
          S.l2[k] = S.dx[k] * S.dx[k] + S.dy[k] * S.dy[k]
          S.ln[k], S.s[k] = ln, along
          if padded[i2] or padded[i2 + 1] then
            S.cap[k] = M.OVERPASS_CHANGE_M
            skipped.over = skipped.over + 1
          end
          nearCar(k, a[1], a[2])
        end
      end
      along = along + segLen
    end
    -- the ground of the road past each deck's end (makeRamp): a segment for each step of its samples, from the deck's end outward. The cells behind
    -- a segment's start, projected on it, are not its to move (S.stop 2: under the deck at the first, and what the segment before has)
    for _, r in ipairs(ramps) do
      for q = 1, r.n - 1 do
        local d0, d1 = r.D[q], r.D[q + 1]
        local ax, ay, bx, by = r.x[q], r.y[q], r.x[q + 1], r.y[q + 1]
        if (abs(d0) > 1e-3 or abs(d1) > 1e-3) and max(ax, bx) >= xmin and min(ax, bx) <= xmax and max(ay, by) >= ymin and min(ay, by) <= ymax then
          local k = S.n + 1
          S.n = k
          S.ax[k], S.ay[k], S.az[k] = ax, ay, d0
          S.dx[k], S.dy[k], S.dz[k] = bx - ax, by - ay, d1 - d0
          S.l2[k] = S.dx[k] * S.dx[k] + S.dy[k] * S.dy[k]
          S.ln[k], S.s[k] = ln, r.sLip + r.sign * r.d[q]
          S.cap[k] = M.OVERPASS_CHANGE_M              -- (where the road meets a deck the ground must match it)
          S.stop[k] = 2
          nearCar(k, ax, ay)
        end
      end
    end
    for _, r in ipairs(ramps) do fixes.ramps[#fixes.ramps + 1] = r end
    -- Where the line crosses over a tunnel Earth built, the ground the line is on is raw over the tunnel's strip, not the road's (crossing.lua): it is laid to the road's
    -- height there: segments with absolute heights (S.lv), over the strip only (S.tx), whatever the option for the general shaping says
    if chains and #chains > 0 then
      local found = crossing.find(pts, chains, { ground = rawGround, skip = function(i)
        return (line.skip and line.skip[i]) or deck[i] or deck[i + 1]
      end })
      for _, c in ipairs(found) do
        local axis = { x = {}, y = {} }
        for q = 1, #c.chain.x do
          if (c.chain.x[q] - c.x) ^ 2 + (c.chain.y[q] - c.y) ^ 2 <= 150 ^ 2 then axis.x[#axis.x + 1], axis.y[#axis.y + 1] = c.chain.x[q], c.chain.y[q] end
        end
        local tx = { x = axis.x, y = axis.y, r = c.strip }
        local sm, big, bigAt = c.samples, 0, nil
        local q = 1
        while q < #sm do
          local q2 = min(q + M.CROSS_STEP_M, #sm)
          local a, b = sm[q], sm[q2]
          local k = S.n + 1
          S.n = k
          S.ax[k], S.ay[k], S.az[k] = a.x, a.y, a.z
          S.dx[k], S.dy[k], S.dz[k] = b.x - a.x, b.y - a.y, b.z - a.z
          S.l2[k] = S.dx[k] * S.dx[k] + S.dy[k] * S.dy[k]
          S.ln[k], S.s[k] = ln, a.s
          S.cap[k], S.lv[k], S.tx[k] = M.CROSS_CHANGE_M, true, tx
          nearCar(k, a.x, a.y)
          local g = rawGround(a.x, a.y)
          if g and abs(a.z - g) > big then big, bigAt = abs(a.z - g), a end
          q = q2
        end
        fixes.n = fixes.n + 1
        fixes.crossings = fixes.crossings + 1
        fixes['tunnel crossing'] = (fixes['tunnel crossing'] or 0) + 1
        fixes.length = fixes.length + (c.s1 - c.s0)
        if big > fixes.largest then fixes.largest = big end
        fixes.list[#fixes.list + 1] = { ln = ln, kind = 'tunnel crossing', s0 = c.s0, s1 = c.s1, x = c.x, y = c.y, d = big, angle = c.angle, arcM = c.arcM, strip = c.strip }
      end
    end
  end
  -- the spatial hash: each bucket lists the segments whose reach touches it
  local bs = M.BUCKET_M
  local buckets = {}
  for k = 1, S.n do
    local x0, x1 = min(S.ax[k], S.ax[k] + S.dx[k]) - reach, max(S.ax[k], S.ax[k] + S.dx[k]) + reach
    local y0, y1 = min(S.ay[k], S.ay[k] + S.dy[k]) - reach, max(S.ay[k], S.ay[k] + S.dy[k]) + reach
    for bx = floor(x0 / bs), floor(x1 / bs) do
      for by = floor(y0 / bs), floor(y1 / bs) do
        local key = bx * 131072 + by
        local list = buckets[key]
        if not list then list = {}; buckets[key] = list end
        list[#list + 1] = k
      end
    end
  end
  -- the other roads' ground near what is worked on: the segments of their pieces, each in the buckets it touches (with its half width, the margin and the
  -- fade) where a stage segment reaches too; nothing else is looked at
  if S.n > 0 then
    local oa, ob, od, oe, ol2, orad, obk = {}, {}, {}, {}, {}, {}, {}
    local n = 0
    for _, line in ipairs(self.lines) do
      for _, o in ipairs(line.others or {}) do
        local rad = (o.hw or 3) + M.OTHER_MARGIN_M
        local reachO = rad + M.OTHER_FADE_M
        for q = 1, #o.pts - 1 do
          local a, b = o.pts[q], o.pts[q + 1]
          local x0, x1 = min(a[1], b[1]) - reachO, max(a[1], b[1]) + reachO
          local y0, y1 = min(a[2], b[2]) - reachO, max(a[2], b[2]) + reachO
          local k
          for bx = floor(x0 / bs), floor(x1 / bs) do
            for by = floor(y0 / bs), floor(y1 / bs) do
              local key = bx * 131072 + by
              if buckets[key] then
                if not k then
                  n = n + 1
                  k = n
                  oa[k], ob[k], od[k], oe[k] = a[1], a[2], b[1] - a[1], b[2] - a[2]
                  ol2[k], orad[k] = od[k] * od[k] + oe[k] * oe[k], rad
                end
                local list = obk[key]
                if not list then list = {}; obk[key] = list end
                list[#list + 1] = k
              end
            end
          end
        end
      end
    end
    if n > 0 then S.o = { ax = oa, ay = ob, dx = od, dy = oe, l2 = ol2, r = orad, bk = obk, n = n } end
  end
  local order = {}
  for k = 1, S.n do order[k] = k end
  table.sort(order, function(a, b) return S.near[a] < S.near[b] end)
  return S, buckets, order, skipped, fixes
end

-- 1 where no other road's ground is near the cell, down to 0 on it (its half width and margin), easing in over OTHER_FADE_M beyond.
local function otherFactor(O, x, y)
  local bs = M.BUCKET_M
  local list = O.bk[floor(x / bs) * 131072 + floor(y / bs)]
  if not list then return 1 end
  local fade = M.OTHER_FADE_M
  local f = 1
  for q = 1, #list do
    local k = list[q]
    local l2 = O.l2[k]
    local rx, ry = x - O.ax[k], y - O.ay[k]
    local t = l2 > 1e-9 and (rx * O.dx[k] + ry * O.dy[k]) / l2 or 0
    if t < 0 then t = 0 elseif t > 1 then t = 1 end
    local ex, ey = rx - O.dx[k] * t, ry - O.dy[k] * t
    local r = O.r[k]
    local lim = r + fade
    local d2 = ex * ex + ey * ey
    if d2 < lim * lim then
      local fk = 1 - M.weight(sqrt(d2), r, fade)
      if fk < f then
        f = fk
        if f <= 0 then return 0 end
      end
    end
  end
  return f
end

-- The height a cell at world (x, y), now at `orig` (world height), is given: orig + w * (the delta of the line at the nearest
-- point), w the falloff (the cell keeps its place in the road's cross slope and camber, it moves with the line); nil when
-- no line reaches it. The second result is the square of the cell's distance to the nearest segment (the road core is within CORE_M
-- of the line).
local CK, CD, CZ, CM = {}, {}, {}, {}         -- (scratch: the segments that reach the cell)

-- A road across a tunnel (crossing.lua) is laid over the strip of raw ground above the tunnel only: 1 within the strip (tx.r from the axis), easing to 0 over CROSS_FADE_M.
local function stripFactor(tx, x, y)
  local best = math.huge
  local xs, ys = tx.x, tx.y
  for q = 1, #xs - 1 do
    local ax, ay = xs[q], ys[q]
    local dx, dy = xs[q + 1] - ax, ys[q + 1] - ay
    local l2 = dx * dx + dy * dy
    local t = l2 > 1e-9 and ((x - ax) * dx + (y - ay) * dy) / l2 or 0
    if t < 0 then t = 0 elseif t > 1 then t = 1 end
    local ex, ey = x - (ax + dx * t), y - (ay + dy * t)
    local d2 = ex * ex + ey * ey
    if d2 < best then best = d2 end
  end
  return M.weight(sqrt(best), tx.r, M.CROSS_FADE_M)
end

local function target(S, buckets, x, y, orig)
  local bs = M.BUCKET_M
  local list = buckets[floor(x / bs) * 131072 + floor(y / bs)]
  if not list then return nil end
  local core, blend = M.CORE_M, M.BLEND_M
  local reach2 = (core + blend) * (core + blend)
  local ax, ay, dx, dy, l2s, az, dz, stop = S.ax, S.ay, S.dx, S.dy, S.l2, S.az, S.dz, S.stop
  local lv, tx = S.lv, S.tx
  -- the segments that reach the cell, and the point of each nearest to it
  local m, dmin2, best = 0, math.huge, 0
  for q = 1, #list do
    local k = list[q]
    local l2 = l2s[k]
    local rx, ry = x - ax[k], y - ay[k]
    local t = l2 > 1e-9 and (rx * dx[k] + ry * dy[k]) / l2 or 0
    local st = stop[k]
    local under = st and ((st == 1 and t > 1) or (st == 2 and t < 0))
    if t < 0 then t = 0 elseif t > 1 then t = 1 end
    local ex, ey = rx - dx[k] * t, ry - dy[k] * t
    local d2 = ex * ex + ey * ey
    if d2 < reach2 and not under then
      m = m + 1
      CK[m], CD[m], CZ[m] = k, d2, az[k] + dz[k] * t        -- (the line's delta there)
      CM[m] = 1
      if lv[k] then                                          -- (a road across a tunnel: the heights are the road's own, not a delta: the cell goes to the road's height, over the strip)
        CZ[m] = CZ[m] - orig
        CM[m] = stripFactor(tx[k], x, y)
      end
      if d2 < dmin2 then dmin2, best = d2, m end
    end
  end
  if m == 0 then return nil end
  -- One pass of the road gives a cell one height: its nearest segment's. The segments next to it along the same pass
  -- reach the cell too, but only with their end's delta; another pass of the road further along the line (a hairpin's other
  -- leg) is blended in.
  -- (the usual cell, with one pass of the road near it: every candidate on the same line within SAME_PASS_M along it of the
  -- nearest, which then is the only one that counts, and its weight is the whole weight)
  local lnk, sk = S.ln, S.s
  local kb = CK[best]
  local one = true
  for a = 1, m do
    local ka = CK[a]
    if lnk[ka] ~= lnk[kb] or abs(sk[ka] - sk[kb]) >= M.SAME_PASS_M then one = false; break end
  end
  if one then
    local w = 1
    if dmin2 > core * core then
      w = M.weight(sqrt(dmin2), core, blend)
      -- (beyond the stage road's own footprint another road's ground is not ours: nothing there, eased out toward it)
      if w > 0 and S.o then
        local f = otherFactor(S.o, x, y)
        if f < 1 then S.kept = (S.kept or 0) + 1; w = w * f end
      end
    end
    w = w * CM[best]
    if w <= 0 then return nil end
    return orig + CZ[best] * w, dmin2, kb
  end
  local sw, swz, wmax = 0, 0, 0
  for a = 1, m do
    local ka, da = CK[a], CD[a]
    local nearest = true
    for b = 1, m do
      if b ~= a then
        local kb2, db = CK[b], CD[b]
        if lnk[kb2] == lnk[ka] and abs(sk[kb2] - sk[ka]) < M.SAME_PASS_M and (db < da or (db == da and kb2 < ka)) then
          nearest = false
          break
        end
      end
    end
    if nearest then
      local w = M.weight(sqrt(da), core, blend) * CM[a]
      if w > 0 then
        local w2 = w * w
        w2 = w2 * w2                           -- (the nearest pass of the road counts most where two run close)
        sw = sw + w2
        swz = swz + w2 * CZ[a]
        if w > wmax then wmax = w end
      end
    end
  end
  if wmax <= 0 then return nil end
  if dmin2 > core * core and S.o then
    local f = otherFactor(S.o, x, y)
    if f < 1 then S.kept = (S.kept or 0) + 1; wmax = wmax * f end
    if wmax <= 0 then return nil end
  end
  return orig + (swz / sw) * wmax, dmin2, kb
end

-- The offset a new block's grid needs, from the one measured on the block before (the grid's own convention, above the
-- block's base height or absolute, is the file format's and does not change between swaps: only the base height does). Used
-- when the game's ground cannot be measured against yet, in the call that swaps the block in.
local function predictOffset(self, info)
  local c = self.conv
  if not c then return nil end
  return (c.rel and info.pz or 0) + c.c
end

-- Start a shaping job on the block now in. Returns false when it cannot (the log says why). sync: called from blockChanged.
function E:begin(tb, sync)
  local info = M.gridInfo(tb, self.env.fallbackGrid and self.env.fallbackGrid() or nil)
  if not info then
    log(self, 'W', 'shaping: the terrain block has no grid information')
    self.stat.state = 'no block grid'
    self.dirty = false
    return false
  end
  -- a second job on data we already wrote to goes back to the block's own heights first (the road's height is measured
  -- on them)
  if self.rec and self.rec.gen == self.gen and self.rec.n > 0 then
    self.job = { tb = tb, info = info, rpos = 1, restored = 0, phase = 'restore', next = 'shape', started = self.env.now(),
      ms = 0, frames = 0 }
    self.dirty = false
    self.stat.state = 'restoring'
    return true
  end
  -- the points on the lines inside the block, for the calibration (any vertex on a line will do)
  local sample = {}
  for _, line in ipairs(self.lines) do
    for i = 1, #line.pts, 3 do
      local p = line.pts[i]
      if p[1] > info.px + info.sq * 16 and p[1] < info.px + (info.n - 16) * info.sq and p[2] > info.py + info.sq * 16 and p[2] < info.py + (info.n - 16) * info.sq then
        sample[#sample + 1] = p
      end
    end
  end
  self.dirty = false
  self.fresh = false
  if #sample == 0 then
    self.stat.state = 'no stage in this block'
    self.stat.cells, self.stat.maxChange, self.stat.maxCore = 0, 0, 0
    return false
  end
  local off, why, predicted
  off, why = calibrate(self, tb, info, sample)
  if not off and sync then
    -- (the game's ground may not answer for a block swapped in this very call: the grid offset of the block before is taken,
    -- and the first frame after measures it, see update)
    off = predictOffset(self, info)
    predicted = off ~= nil
    if predicted then why = nil end
  end
  if not off then
    log(self, 'W', 'shaping: nothing written: ' .. tostring(why))
    self.stat.state = 'unverified: ' .. tostring(why)
    return false
  end
  if not predicted then
    -- (kept for the next block's stretch at the car)
    local rel = abs(off - info.pz) <= abs(off)
    self.conv = { rel = rel, c = off - (rel and info.pz or 0) }
  end
  if why then log(self, 'W', 'shaping: ' .. why) end
  local tPlan = self.env.now()
  local S, buckets, order, skipped, fixes = plan(self, tb, info, off)
  tPlan = self.env.now() - tPlan
  self.stat.skipped = skipped.span + skipped.ground + skipped.deck
  local urgentSq = M.URGENT_M * M.URGENT_M
  local urgent = 0
  for _, k in ipairs(order) do if S.near[k] <= urgentSq then urgent = urgent + 1 end end
  -- the rec of this block: the cells we write and the heights they had
  self.rec = { gen = self.gen, n = 0, idx = {}, orig = {}, N = info.n }
  self.job = {
    tb = tb, info = info, off = off, S = S, buckets = buckets, order = order, pos = 1, seen = {},
    gen = self.gen, started = self.env.now(), ms = 0, frames = 0, cells = 0, maxChange = 0, maxCore = 0, wantedMax = 0,
    wantedCore = 0, capped = 0, skipped = skipped, urgentLeft = urgent, phase = 'shape', later = {}, lpos = 1, lnext = {},
    sample = sample, offPredicted = predicted, planMs = tPlan, coreMoved = 0, linesUp = 0, linesDown = 0, fixes = fixes,
  }
  self.stat.state = 'shaping'
  return true
end

local function flush(self, job, box)
  if not box then return end
  local env = self.env
  local v = env.vec3
  local ok, err = pcall(function() job.tb:updateGrid(v(box[1], box[2], 0), v(box[3], box[4], 0)) end)
  if not ok and not self.warnedGrid then
    self.warnedGrid = true
    log(self, 'E', 'shaping: updateGrid failed: ' .. tostring(err))
  end
  -- (what is drawn on the ground of that box is told: the DecalRoads over it are cut again, reclip.lua)
  if ok and env.changed then
    local i = job.info
    pcall(env.changed, i.px + box[1] * i.sq, i.py + box[2] * i.sq, i.px + box[3] * i.sq, i.py + box[4] * i.sq)
  end
end

-- The box of the cells changed since the last updateGrid, grown by one cell; when it would pass MAX_BOX_CELLS it is
-- handed to updateGrid first (the cost of the grid update follows the box, not the cells in it) and a new one begins.
local function touch(self, job, box, gx, gy)
  if not box then return { gx, gy, gx, gy } end
  local x0, y0, x1, y1 = min(box[1], gx), min(box[2], gy), max(box[3], gx), max(box[4], gy)
  if (x1 - x0 + 1) * (y1 - y0 + 1) > M.MAX_BOX_CELLS then
    flush(self, job, box)
    return { gx, gy, gx, gy }
  end
  box[1], box[2], box[3], box[4] = x0, y0, x1, y1
  return box
end

-- After the shaping: for each deck end, one compact line with the profile of the ground along the line every PROFILE_STEP_M (Earth's
-- ground before our change, the height asked for, the ground written, and what a ray from above hits there: the deck, the ground), all as heights over
-- the deck's top line going on past its end; and a check of what was written within the ramp, every metre: a step of STEP_WARN_M or more between two points 1 m apart
-- and a ground that stands STEP_WARN_M or more over (or under) the height asked for are warned about, with where.
local function reportRamps(self, job)
  local list = job.fixes.ramps
  if not list or #list == 0 then return end
  local env, tb, info, off = self.env, job.tb, job.info, job.off
  for q, r in ipairs(list) do
    local top = r.pin.top
    if q <= M.LOG_PROFILES then
      local cols = {}
      for _, p in ipairs(r.prof) do
        local w = heightAt(tb, info, off, p[2], p[3])
        local ray
        if env.deck and w then
          local ok, h = pcall(env.deck, p[2], p[3], w)
          if ok and type(h) == 'number' then ray = h end
        end
        local function f(v) return v and string.format('%+.2f', v - (top + r.pin.gd * p[1])) or '-' end
        cols[#cols + 1] = string.format('%.0f:%s/%s/%s/%s', p[1], f(p[4]), f(p[5]), f(w), f(ray))
      end
      log(self, 'I', string.format('shaping: deck end profile %d (%s%s, line %d, %.0f m along it, x %.0f, y %.0f): the deck top %.2f m at its end ' ..
        '(grade %+.1f%% on past it), ramp %.0f m; metres past the end: earth/target/written/ray over the top line of the deck going on, %s', q, r.dir == -1 and 'exit' or 'approach',
        r.tagged and ', tagged' or '', r.ln, r.sLip, r.pin.lx, r.pin.ly, top, r.pin.gd * 100, r.L, table.concat(cols, ' ')))
    end
    local steps, worstStep, stepAt, miss, missAt = 0, 0, nil, 0, nil
    local prev
    for dq = 1, floor(r.L) do
      local x, y = r.at(dq)
      local w = heightAt(tb, info, off, x, y)
      if w then
        if prev then
          local st = abs(w - prev)
          if st >= M.STEP_WARN_M then steps = steps + 1 end
          if st > worstStep then worstStep, stepAt = st, dq end
        end
        -- (the height asked for at this metre: interpolated between the samples of the ramp)
        local k = 1
        while k < r.n and r.d[k + 1] <= dq do k = k + 1 end
        local z = r.z[k]
        if k < r.n then z = z + (r.z[k + 1] - z) * (dq - r.d[k]) / (r.d[k + 1] - r.d[k]) end
        local m = abs(w - z)
        if m > miss then miss, missAt = m, dq end
        prev = w
      else
        prev = nil
      end
    end
    if worstStep >= M.STEP_WARN_M or miss >= M.STEP_WARN_M then
      local sx, sy = r.at(stepAt or missAt or 0)
      log(self, 'W', string.format('shaping: WARNING the ground past the %s deck end at x %.0f, y %.0f (line %d, %.0f m along it) is not smooth along the ' ..
        'road: %d steps of %.1f m or more between two points 1 m apart (largest %.1f m, %.0f m past the end), and up to %.1f m off the height asked ' ..
        'for (%.0f m past the end)', r.dir == -1 and 'exit' or 'approach', r.pin.lx, r.pin.ly, r.ln, r.sLip, steps, M.STEP_WARN_M, worstStep, stepAt or 0,
        miss, missAt or 0))
    end
  end
end

local function finish(self, job)
  local ms = job.ms
  if job.phase == 'shape' then
    local s = self.stat
    s.cells, s.maxChange, s.maxCore, s.ms, s.frames = job.cells, job.maxChange, job.maxCore, ms, job.frames
    s.wantedMax, s.wantedCore, s.capped = job.wantedMax, job.wantedCore, job.capped
    s.jobs, s.total, s.at = s.jobs + 1, s.total + job.cells, self.env.clock and self.env.clock() or nil
    s.state = job.cells > 0 and 'shaped' or 'nothing to shape'
    s.structures = job.skipped
    s.stretches, s.fixLength, s.fixLargest = job.fixes.n, job.fixes.length, job.fixes.largest
    s.coveredM, s.tunnelSegments = job.fixes.covered, job.skipped.tunnel
    s.crossings = job.fixes.crossings
    s.crossAt = {}
    for _, fx in ipairs(job.fixes.list) do
      if fx.kind == 'tunnel crossing' then s.crossAt[#s.crossAt + 1] = { s0 = fx.s0, s1 = fx.s1, x = fx.x, y = fx.y, angle = fx.angle, arcM = fx.arcM, strip = fx.strip, change = fx.d } end
    end
    -- (where the biggest change is, on the road's core or beside it, and what the cap held back)
    -- (only what is fixed is written, the rest of the road is as Earth made it)
    local f = job.fixes
    log(self, 'I', string.format('shaping: %d stretch%s of the line fixed (%d under an overpass, %d spike%s, %d deck end%s), %.0f m of it with the tapers, ' ..
      'largest change of the line %.1f m; %d cells written along %d segments in %d frames, %.1f ms, largest change %.1f m (%.1f m on the road core, ' ..
      'within %.1f m of the line); the ground asked for up to %.1f m (%.1f m on the core), %d cells held at %.1f m; ' ..
      'left alone: %d span, %d deck, %d no-ground segments; %d segments under an overpass, %d deck ends met; %.0f m of the line examined in the block, ' ..
      '%d segments beside a tunnel left as Earth made them; %d cells beside the road kept clear of other roads; %d of the stretches fixed are a road across a tunnel', f.n, f.n == 1 and '' or 'es',
      f.overpass or 0, f.spike or 0, (f.spike or 0) == 1 and '' or 's', f['deck end'] or 0, (f['deck end'] or 0) == 1 and '' or 's',
      f.length, f.largest, job.cells, job.S.n, job.frames, ms, job.maxChange, job.maxCore, M.CORE_M, job.wantedMax, job.wantedCore,
      job.capped, M.MAX_CHANGE_M, job.skipped.span, job.skipped.deck, job.skipped.ground, job.skipped.over, job.skipped.pinned, f.covered, job.skipped.tunnel, job.S.kept or 0, f.crossings))
    -- (where each stretch is, in metres along its line and in the world, and what the colliders over the line were taken for:
    -- the 'deck ends' of a stage with no bridge were a collider the shaping took for a deck, and the log could not say where)
    reportRamps(self, job)
    if not self.enabled and f.crossings > 0 then
      log(self, 'I', string.format('shaping: the option is off; the ground where the stage crosses over a tunnel is still laid to the road (%d place%s)', f.crossings, f.crossings == 1 and '' or 's'))
    end
    for q, fx in ipairs(f.list) do
      if q > M.LOG_STRETCHES then
        log(self, 'I', string.format('shaping: ... and %d more stretches fixed', #f.list - M.LOG_STRETCHES))
        break
      end
      log(self, 'I', string.format('shaping: stretch %d, %s: %.0f to %.0f m along line %d, largest change %+.1f m at x %.0f, y %.0f%s',
        q, fx.kind, fx.s0, fx.s1, fx.ln, fx.d, fx.x, fx.y, fx.angle and string.format(' (a road across a tunnel at %.0f degrees, %.0f m from its nearer end, over a strip %.1f m either side of its axis)',
          fx.angle, fx.arcM, fx.strip) or ''))
    end
    if f.spread > 0 then
      local at = f.spreadAt
      log(self, 'I', string.format('shaping: %d stretch%s of the line asked for more than the %.0f m cap (largest %.1f m at x %.0f, y %.0f, %.0f m along line %d): ' ..
        'the change is spread over the stretch and its taper, scaled to the cap, not cut off at it', f.spread, f.spread == 1 and '' or 'es',
        M.MAX_CHANGE_M, f.spreadMax, at[1], at[2], at[3], at[4]))
    end
    for q, fd in ipairs(f.found) do
      if q > M.LOG_STRETCHES then
        log(self, 'I', string.format('shaping: ... and %d more colliders over the line', #f.found - M.LOG_STRETCHES))
        break
      end
      local ends = 'its ends are not met'
      if fd.met then
        if fd.met[1] and fd.met[2] then ends = 'the ground meets its top at both ends'
        elseif fd.met[1] then ends = 'the ground meets its top at its start only'
        elseif fd.met[2] then ends = 'the ground meets its top at its end only' end
      end
      local what = { overpass = 'an overpass: the road runs under it', deck = 'a deck the road is on, left alone (' .. ends .. ')',
        narrow = 'not a deck: narrow (a wall, barrier or rail beside the road)', short = 'not a deck: shorter than ' .. M.DECK_MIN_RUN_M .. ' m',
        stepped = 'not a deck: its top steps along the line (a jump, not a slope: a structure beside the road)' }
      log(self, 'I', string.format('shaping: a collider %.1f m over the ground at %.0f to %.0f m along line %d (x %.0f, y %.0f) is %s',
        fd.above, fd.s0, fd.s1, fd.ln, fd.x, fd.y, what[fd.why] or fd.why))
    end
    -- (BeamNG.Earth paints a road's asphalt into the ground, which follows, but its centre and edge lines are a mesh draped on
    -- the ground as it was (server/roadmesh.py, 2.5 cm over it): where the ground moved more than LINE_TOL_M they are buried (raised)
    -- or float (lowered). Since that is only at the stretches fixed: the count says how many cells.)
    if job.linesUp + job.linesDown > 0 then
      log(self, 'I', string.format('shaping: of %d cells of the road core moved, %d moved %.0f cm or more (%d raised, %d lowered): ' ..
        'Earth\'s road lines, a mesh on the ground as it was, stay at the old height there (road lines stranded, at the stretches fixed)', job.coreMoved, job.linesUp + job.linesDown,
        M.LINE_TOL_M * 100, job.linesUp, job.linesDown))
    end
    s.coreMoved, s.linesUp, s.linesDown = job.coreMoved, job.linesUp, job.linesDown
    if job.coreAt then
      local at = job.coreAt
      log(self, 'W', string.format('shaping: the road core was asked to move %.1f m, more than its cap: held at the cap. ' ..
        'Biggest at x %.0f, y %.0f, %.0f m along line %d', job.wantedCore, at[1], at[2], at[3], at[4]))
    end
  else
    self.stat.state = 'restored'
    log(self, 'I', string.format('shaping: the heights are back (%d cells, %d frames, %.1f ms)', job.restored or 0, job.frames, ms))
  end
end

-- The shaping of one cell for a job: a function (gx, gy, wx, wy) that writes the cell's target height (kept in the rec) and
-- says whether it did. The cell moves at most MAX_CHANGE_M (a cap on the change, a continuous function of the height asked
-- for, so the ground does not break where it applies); a change under MIN_CHANGE_M is not written at all.
local function cellShaper(self, job)
  local tb, S, buckets, off = job.tb, job.S, job.buckets, job.off
  local rec = self.rec
  local set = tb.setHeightGrid or tb.setHeight
  local core2, minChange = M.CORE_M * M.CORE_M, M.MIN_CHANGE_M
  local n = job.info.n
  local segCap = S.cap
  return function(gx, gy, wx, wy)
    local h = tb:getHeightGrid(gx, gy)
    local orig = h + off
    local z, d2, kb = target(S, buckets, wx, wy, orig)
    if not z then return false end
    local want = z - orig
    local a = abs(want)
    local onCore = d2 <= core2
    local cap = segCap[kb] or M.MAX_CHANGE_M            -- (more under an overpass)
    if a > job.wantedMax then job.wantedMax = a end
    if onCore and a > job.wantedCore then
      job.wantedCore = a
      -- (where the core was asked for more than the cap: the log says: x, y, metres along the line, which line)
      if a > cap then job.coreAt = { wx, wy, S.s[kb], S.ln[kb] } end
    end
    if a > cap then
      z = orig + (want > 0 and cap or -cap)
      a = cap
      job.capped = job.capped + 1
    end
    if a < minChange then return false end
    if onCore and not S.lv[kb] then                     -- (the lines Earth drew on the old ground stay where they were; on a road across a tunnel they are on the road's own height: that is where the ground goes)
      job.coreMoved = job.coreMoved + 1
      if a >= M.LINE_TOL_M then
        if z > orig then job.linesUp = job.linesUp + 1 else job.linesDown = job.linesDown + 1 end
      end
    end
    local rel = z - off
    if rel < 0 then rel = 0 end
    set(tb, gx, gy, rel)
    rec.n = rec.n + 1
    rec.idx[rec.n], rec.orig[rec.n] = gx + gy * n, h
    job.cells = job.cells + 1
    if a > job.maxChange then job.maxChange = a end
    if onCore and a > job.maxCore then job.maxCore = a end
    return true
  end
end

-- one slice of shaping: the segments of the job's order up to `limit`, the cells inside `region` only when it is given; fr: the
-- ground round a moving car, whose cells are held back (job.later) and done by runLater. Returns true when every segment has
-- been through.
local function runShape(self, job, budget, limit, fr, region)
  local env, info = self.env, job.info
  local S, order = job.S, job.order
  local cell = job.cell
  if not cell then cell = cellShaper(self, job); job.cell = cell end
  local later = job.later
  local reach = M.CORE_M + M.BLEND_M
  local n, sq, px, py = info.n, info.sq, info.px, info.py
  local lim = M.EDGE_CELLS
  local t0 = env.now()
  local box = nil
  limit = limit or #order
  while job.pos <= limit do
    local k = order[job.pos]
    local cur = job.cur
    if not cur then
      local ax, ay = S.ax[k], S.ay[k]
      local bx, by = ax + S.dx[k], ay + S.dy[k]
      cur = {
        gx = max(lim, floor((min(ax, bx) - reach - px) / sq)), gx1 = min(n - 1 - lim, floor((max(ax, bx) + reach - px) / sq) + 1),
        y0 = max(lim, floor((min(ay, by) - reach - py) / sq)), y1 = min(n - 1 - lim, floor((max(ay, by) + reach - py) / sq) + 1),
      }
      job.cur = cur
    end
    while cur.gx <= cur.gx1 do
      local gx = cur.gx
      local wx = px + gx * sq
      for gy = cur.y0, cur.y1 do
        local idx = gx + gy * n
        local wy = py + gy * sq
        if region and (wx - region.x) ^ 2 + (wy - region.y) ^ 2 > region.r2 then
          -- (not in the stretch done at once: left for the frames)
        elseif not job.seen[idx] then
          job.seen[idx] = true
          if fr and (wx - fr.x) ^ 2 + (wy - fr.y) ^ 2 <= fr.r2 then
            later[#later + 1] = idx                       -- (the car is on it: when it has passed)
          elseif cell(gx, gy, wx, wy) then
            box = touch(self, job, box, gx, gy)
          end
        end
      end
      cur.gx = gx + 1
      if env.now() - t0 >= budget then break end
    end
    if cur.gx > cur.gx1 then
      job.cur = nil
      job.pos = job.pos + 1
      if job.urgentLeft > 0 then job.urgentLeft = job.urgentLeft - 1 end
    end
    if env.now() - t0 >= budget then break end
  end
  flush(self, job, box)
  return job.pos > #order
end

-- The cells a moving car held back, once it has passed (or stopped): in passes over the list, the ones it still covers
-- kept for the next. Returns true when none is left.
local function runLater(self, job, budget, fr)
  local later = job.later
  if #later == 0 then return true end
  local env, info = self.env, job.info
  local cell = job.cell
  local n, sq, px, py = info.n, info.sq, info.px, info.py
  local t0 = env.now()
  local box = nil
  local count = 0
  while job.lpos <= #later do
    local idx = later[job.lpos]
    local gx, gy = idx % n, floor(idx / n)
    local wx, wy = px + gx * sq, py + gy * sq
    if fr and (wx - fr.x) ^ 2 + (wy - fr.y) ^ 2 <= fr.r2 then
      job.lnext[#job.lnext + 1] = idx
    elseif cell(gx, gy, wx, wy) then
      box = touch(self, job, box, gx, gy)
    end
    job.lpos = job.lpos + 1
    count = count + 1
    if count % 64 == 0 and env.now() - t0 >= budget then break end
  end
  flush(self, job, box)
  if job.lpos > #later then
    job.later, job.lnext, job.lpos = job.lnext, {}, 1
  end
  return #job.later == 0
end

-- Puts the heights back, except the cells under a moving car (fr), which wait for it to pass. Returns true when all are back.
local function runRestore(self, job, budget, fr)
  local env, tb, info = self.env, job.tb, job.info
  local rec = self.rec
  local set = tb.setHeightGrid or tb.setHeight
  local n = rec.N
  local t0 = env.now()
  local box = nil
  job.restored = job.restored or 0
  job.rpos = job.rpos or 1
  job.rkeep = job.rkeep or {}
  local list = job.rlist                           -- nil: the first pass, over every cell written; else the ones a car covered
  local total = list and #list or rec.n
  local count = 0
  while job.rpos <= total do
    local pos = list and list[job.rpos] or job.rpos
    local idx = rec.idx[pos]
    local gx, gy = idx % n, floor(idx / n)
    if fr and (info.px + gx * info.sq - fr.x) ^ 2 + (info.py + gy * info.sq - fr.y) ^ 2 <= fr.r2 then
      job.rkeep[#job.rkeep + 1] = pos
    else
      set(tb, gx, gy, rec.orig[pos])
      box = touch(self, job, box, gx, gy)
      job.restored = job.restored + 1
    end
    job.rpos = job.rpos + 1
    count = count + 1
    if count % 64 == 0 and env.now() - t0 >= budget then break end
  end
  flush(self, job, box)
  if job.rpos > total then
    if #job.rkeep == 0 then return true end
    job.rlist, job.rkeep, job.rpos = job.rkeep, {}, 1
  end
  return false
end

-- The engine's edit was refused: stop for good rather than fail every frame (Earth's next commit has its own ground).
local function fail(self, err)
  log(self, 'E', 'shaping failed and is switched off: ' .. tostring(err))
  self.job, self.rec, self.enabled, self.dirty = nil, nil, false, false
  self.stat.state = 'failed: ' .. tostring(err)
end

-- At a new block, before the next physics step: the stretch of the lines round the car, all of it. Called from blockChanged, so in the same Lua call as Earth's swap or load. What is left carries on over
-- the frames as a job.
function E:shapeNow()
  local env = self.env
  if not (self:wanted() and self.dirty and not self.noSync) then return end
  local tb = env.block and env.block() or nil
  if not tb then return end
  self.carX, self.carY, self.carSpeed = self:car()
  local cx, cy, speed = self.carX, self.carY, self.carSpeed
  if not cx then return end                          -- (no car: nothing is driven on; the frames do it)
  local t0 = env.now()
  local ok = self:begin(tb, true)
  local job = self.job
  if not (ok and job and job.phase == 'shape') then
    -- (the game's ground did not answer for the new block yet, so the grid could not be measured: the next frame does it)
    if tostring(self.stat.state):find('^unverified') then self.dirty, self.noSync = true, true end
    return
  end
  local reach = M.CORE_M + M.BLEND_M
  -- Two passes (the one pass over a disc of 150 m cost 24.6 ms at 35 m/s, a visible hitch). The cells a later frame
  -- would leave alone for the moving car (within the freeze radius, and a margin) are done whatever they cost, those are what
  -- keeps the ground under the car as the old block had it; then the rest of the stretch (to R) only while the call stays under
  -- SYNC_BUDGET_MS: the frames do what is left, well before the car is there.
  local Rm = speed > M.FREEZE_SPEED and (M.FREEZE_M + speed * M.FREEZE_S + 3) or 0
  local R = max(Rm, min(M.SYNC_MAX_M, M.SYNC_MARGIN_M + speed * M.SYNC_S))
  local S, order = job.S, job.order
  local function within(r)                          -- (every cell within r of the car is reached by a segment within r + reach)
    local lim2, last = (r + reach) ^ 2, 0
    for q = 1, #order do
      if S.near[order[q]] <= lim2 then last = q else break end
    end
    return last
  end
  local okRun, err = true, nil
  if Rm > 0 then
    okRun, err = pcall(runShape, self, job, math.huge, within(Rm), nil, { x = cx, y = cy, r2 = Rm * Rm })
    job.pos, job.cur = 1, nil
  end
  if okRun then
    local left = max(M.SYNC_BUDGET_MS - (env.now() - t0), 0)
    okRun, err = pcall(runShape, self, job, left, within(R), nil, { x = cx, y = cy, r2 = R * R })
  end
  if not okRun then fail(self, err); return end
  -- (the segments are gone through again by the frames, for the cells outside the stretch: the ones done are `seen`)
  job.pos, job.cur = 1, nil
  local urgentSq, urgent = M.URGENT_M * M.URGENT_M, 0
  for _, k in ipairs(order) do if S.near[k] <= urgentSq then urgent = urgent + 1 end end
  job.urgentLeft = urgent
  local ms = env.now() - t0
  job.ms = job.ms + ms
  local st = self.stat
  st.syncCells, st.syncMs = job.cells, ms
  log(self, 'I', string.format('shaping: the block is new: %d cells within %.0f m of the car (%.1f m/s) shaped at once, %.1f ms (the plan %.1f ms)%s',
    job.cells, R, speed, ms, job.planMs or 0, job.offPredicted and ' (the grid offset of the block before)' or ''))
  -- a run of the line cut by the block's edge has its end pinned to the ground there, which moves the profile some way in: the
  -- stretch at the car must be further from it, or it would not be the height the old block had there
  local clear = R + reach + M.RUN_END_REACH_M
  for _, c in ipairs(S.cuts) do
    local d = sqrt((c[1] - cx) ^ 2 + (c[2] - cy) ^ 2)
    if d < clear then
      log(self, 'W', string.format('shaping: a run of the line ends at the block\'s edge %.0f m from the car (needs %.0f m): the ground there ' ..
        'may differ from the old block\'s', d, clear))
      break
    end
  end
end

-- Every frame (GE onUpdate): begins a job when the block or the lines changed, then does its work for at most budgetMs.
-- force: the cells under a moving car are done too (the session ends).
function E:update(budgetMs, force)
  local env = self.env
  -- (the tunnels Earth has built come with the road tiles, after the terrain: when there are more (or fewer) near the lines, the work is planned again)
  if self.lines and not self.job and not self.dirty and env.tunnels and env.clock then
    local t = env.clock()
    if not self.tunSeen or t - self.tunSeen >= M.TUNNEL_POLL_S then
      self.tunSeen = t
      local n = self:tunnelsNear()
      if n ~= (self.tunNear or 0) then
        self.tunNear = n
        self.dirty = self:wanted()
      end
    end
  end
  if not (self.job or self.dirty or (self.rec and self.rec.n > 0)) then return end       -- (idle: not even the block lookup)
  local tb = env.block and env.block() or nil
  if not tb then
    self.job, self.rec = nil, nil
    if self.dirty then self.stat.state = 'no terrain block' end
    return
  end
  self.carX, self.carY, self.carSpeed = self:car()
  if not self.job then
    local needRestore = self.rec and self.rec.n > 0 and self.rec.gen == self.gen and not self:wanted()
    if needRestore then
      local info = M.gridInfo(tb)
      if info then
        self.job = { tb = tb, info = info, rpos = 1, restored = 0, phase = 'restore', started = env.now(), ms = 0, frames = 0 }
        self.stat.state = 'restoring'
      else
        self.rec = nil
      end
    elseif self.dirty and self:wanted() then
      if not self:begin(tb) then return end
    else
      return
    end
  end
  local job = self.job
  if job.gen and job.gen ~= self.gen then self.job = nil; return end
  -- the stretch at the car was shaped with the grid offset of the block before; now that the game's ground has the new block,
  -- measure it. If it is not what was assumed, what was written is put back and the job done again on the measured one.
  if job.offPredicted and not job.verified then
    job.verified = true
    local off2 = calibrate(self, tb, job.info, job.sample)
    if off2 and abs(off2 - job.off) > M.CALIBRATE_TOLERANCE_M then
      log(self, 'W', string.format('shaping: the new block\'s grid offset is %.2f m, not the %.2f m of the block before: shaped again', off2, job.off))
      self.conv, self.job, self.dirty = nil, nil, true
      return
    end
  end
  local t0 = env.now()
  local budget = budgetMs or M.BUDGET_MS
  if job.urgentLeft and job.urgentLeft > 0 and job.phase == 'shape' then budget = max(budget, M.URGENT_BUDGET_MS) end
  local fr = self:freezeZone(force)
  local ok, done = pcall(function()
    if job.phase == 'restore' then return runRestore(self, job, budget, fr) end
    local shapeDone = job.pos > #job.order or runShape(self, job, budget, nil, fr)
    if not shapeDone then return false end
    return runLater(self, job, max(budget - (env.now() - t0), 0), fr)
  end)
  if not ok then
    fail(self, done)
    return
  end
  job.ms = job.ms + (env.now() - t0)
  job.frames = job.frames + 1
  self.stat.deferred = job.later and #job.later or (job.rkeep and #job.rkeep or 0)
  if done then
    if job.phase == 'restore' and job.next == 'shape' then
      -- (the heights are the block's own again: the next frame shapes from them)
      self.rec, self.job, self.dirty = nil, nil, true
      return
    end
    finish(self, job)
    if job.phase == 'restore' then self.rec = nil end
    self.job = nil
    self.stat.deferred = 0
  end
end

-- cells shaped by the last job, the largest change (m), when (the env clock), the state, and the running totals
function E:status()
  local s = self.stat
  local live = self.job and self.job.phase == 'shape' and self.job or nil
  return {
    enabled = self.enabled, state = s.state, cells = live and live.cells or s.cells,
    maxChange = live and live.maxChange or s.maxChange, maxCore = live and live.maxCore or s.maxCore,
    wantedMax = live and live.wantedMax or s.wantedMax, wantedCore = live and live.wantedCore or s.wantedCore,
    capped = live and live.capped or s.capped, ms = s.ms, frames = s.frames, jobs = s.jobs, total = s.total,
    skipped = s.skipped, at = s.at, working = self.job ~= nil, syncCells = s.syncCells, syncMs = s.syncMs, deferred = s.deferred,
    stretches = s.stretches or 0, fixLength = s.fixLength or 0, fixLargest = s.fixLargest or 0, coveredM = s.coveredM or 0,
    tunnelSegments = s.tunnelSegments or 0, crossings = s.crossings or 0, crossAt = s.crossAt or {},
    coreMoved = s.coreMoved or 0, linesStranded = (s.linesUp or 0) + (s.linesDown or 0),
  }
end

return M
