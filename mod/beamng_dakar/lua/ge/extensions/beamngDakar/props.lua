-- Props: the service park and the start, finish and time-control furniture of a loop, defined along each leg and placed
-- for one BeamNG.Earth anchor.
--
-- The stock loops carry these as a hand-placed prefab in one frame. Here a prop is "s metres along leg X, lateral metres
-- to the right of the road, facing so" and the world position is made from the leg's lat/lon geometry for the anchor in
-- force: Earth moves its origin every ~10 km and a prop left in an old frame would stand in the wrong place. The caller
-- (core.lua) keeps only the props near the player and builds them again at every anchor change.
--
-- Pure (no game calls). The shapes are the ones the stock Italy loop uses, and for the stage closures the stock rally
-- road-closure tape, and for the service booth the stock rally gazebo and the garage's tool boxes.
local M = {}

local RALLY = '/art/shapes/race/rally/rally_assets/'
local GARAGE = '/art/shapes/garage_and_dealership/garage/'
M.SHAPES = {
  start_board  = RALLY .. 's_rally_signboard_start.dae',
  finish_entry = RALLY .. 's_rally_signboard_finish_entry.dae',
  finish_board = RALLY .. 's_rally_signboard_finish.dae',
  radio_point  = RALLY .. 's_rally_signboard_radio_point.dae',
  -- a split checkpoint on a special: the radio point and medical point boards, the warning boards before
  -- them (as the stock stages stand them)
  radio_warning   = RALLY .. 's_rally_signboard_radio_point_warning.dae',
  medical_point   = RALLY .. 's_rally_signboard_medical_point.dae',
  medical_warning = RALLY .. 's_rally_signboard_medical_warning.dae',
  start_sensor = RALLY .. 's_rally_start_sensor.dae',
  table        = RALLY .. 's_rally_fold_table_01.dae',
  chair        = RALLY .. 's_rally_fold_chair_01.dae',
  cone         = '/art/shapes/race/cone.dae',
  flag         = '/art/shapes/garage_and_dealership/s_flag_floor_portrait_02.dae',     -- 8 m
  flag_tear    = '/art/shapes/garage_and_dealership/s_flag_floor_teardrop_02.dae',
  -- the time control of the stock loops: the Italy loop stands the board at every TC with a table and chairs
  -- beside it, its entry board 8 to 50 m before, and flags down the approach
  tc_board     = RALLY .. 's_rally_signboard_time_control.dae',
  tc_entry     = RALLY .. 's_rally_signboard_time_control_entry.dae',
  -- the landmarks (tall, to be seen from far; `far` in the layout): 9.7 m feather flags and a 15 m tower
  feather      = '/art/shapes/garage_and_dealership/s_flag_floor_feather_01.dae',
  tower        = '/art/shapes/objects/s_tower_01.dae',
  -- the closure across a side road of a special stage: the rally road-closure tape the stock stages close their exits
  -- with (red and white, 6 m long from a post at the model's origin along its -y, 1.2 m high; the stock prefabs place it
  -- unscaled and without a collision mesh, and so do we: it is scenery the car can break through)
  closure_tape = RALLY .. 's_tape_road_closure_6m.dae',
  -- the service booth where the car starts. The stock rally loops stand their service parks as rows of 3 x 6 m
  -- gazebos (art/prefabs/p_rally_gazebo_mod_<cover>_3x6.prefab.json of the game: the 3 x 6 cover at the prefab's origin, the
  -- 3 x 3 frame 1.5 m back along the length and the 3 x 3 frame add-on 1.5 m forward, all one orientation) with the folding
  -- table and chair, tool boxes, fire extinguishers, cones and floor flags of the stock service areas
  gazebo_cover_blastr  = RALLY .. 's_rally_gazebo_mod_cover_blastr_3x6.dae',
  gazebo_cover_dkblue  = RALLY .. 's_rally_gazebo_mod_cover_dkblue_3x6.dae',
  gazebo_cover_ngrc    = RALLY .. 's_rally_gazebo_mod_cover_ngrc_3x6.dae',
  gazebo_cover_rotopad = RALLY .. 's_rally_gazebo_mod_cover_rotopad_3x6.dae',
  gazebo_frame         = RALLY .. 's_rally_gazebo_mod_frame_3x3.dae',
  gazebo_addon         = RALLY .. 's_rally_gazebo_mod_frame_addon_3x3.dae',
  toolbox_big          = GARAGE .. 's_big_toolbox_01.dae',
  toolbox_small        = GARAGE .. 's_small_toolbox_01.dae',
  tire_rack            = GARAGE .. 's_tire_rack.dae',
  extinguisher         = '/art/shapes/garage_and_dealership/Clutter/clutter_workshop_fire_extinguisher.dae',
}

-- The ground a prop's shape covers, for the check that keeps props off roads (roadcheck.lua): half its length along the
-- model's x and half its width along the model's y, in metres (estimates from the shapes' sizes; the gazebo's cover is 3 x 6 m
-- with its long side along x and its frames 3 x 3 m). A kind without one is not checked: the closure tape stands across a side
-- road on purpose.
M.FOOTPRINTS = {
  start_board = { 1.0, 0.3 }, finish_entry = { 1.0, 0.3 }, finish_board = { 1.0, 0.3 }, radio_point = { 1.0, 0.3 },
  radio_warning = { 1.0, 0.3 }, medical_point = { 1.0, 0.3 }, medical_warning = { 1.0, 0.3 }, tc_board = { 1.0, 0.3 },
  tc_entry = { 1.0, 0.3 },
  start_sensor = { 0.3, 0.3 }, table = { 0.9, 0.45 }, chair = { 0.35, 0.35 }, cone = { 0.3, 0.3 },
  flag = { 0.3, 0.3 }, flag_tear = { 0.3, 0.3 }, feather = { 0.3, 0.3 }, tower = { 1.5, 1.5 },
  gazebo_cover_blastr = { 3.0, 1.5 }, gazebo_cover_dkblue = { 3.0, 1.5 }, gazebo_cover_ngrc = { 3.0, 1.5 },
  gazebo_cover_rotopad = { 3.0, 1.5 }, gazebo_frame = { 1.5, 1.5 }, gazebo_addon = { 1.5, 1.5 },
  toolbox_big = { 0.6, 0.35 }, toolbox_small = { 0.4, 0.25 }, tire_rack = { 1.0, 0.5 }, extinguisher = { 0.2, 0.2 },
}

-- The footprint of a prop kind { half length, half width }, or nil when it is not kept off roads.
function M.footprint(kind) return M.FOOTPRINTS[kind] end

-- The props that do a job where they stand (the first run of the road check deleted the stage's own start boards and
-- sensors, the park tower and the staging cones, because the game's navgraph gave the SR17 a half width of 5 m and the boards stand 5.5 m
-- from its line): a start without its boards is wrong. A prop of these kinds that is on a road is pushed out along the road's normal, up
-- to PUSH_MAX_M, until its footprint clears the road and the margin; only when that fails is it dropped. The rest (tables, chairs,
-- flags, the gazebos, the tool boxes) are decoration and are simply not placed.
M.PUSHABLE = {
  start_board = true, finish_entry = true, finish_board = true, radio_point = true, radio_warning = true, medical_point = true,
  medical_warning = true, tc_board = true, tc_entry = true, start_sensor = true, tower = true, cone = true,
}
M.PUSH_MAX_M = 4
function M.pushable(kind) return M.PUSHABLE[kind] == true end

local sqrt, atan2, deg = math.sqrt, math.atan2 or function(y, x) return math.atan(y, x) end, math.deg

-- facing: 'driver' = the model's front (+y) toward the oncoming driver (the stock boards at a stage start face against
-- the direction of travel), 'along' = front along the travel direction, 'out' = front to the side of the road.
-- `shared` (optional, a leg id): the prop is only there when the leg it is on does not start where that leg ends, see
-- M.place (the end of a transfer and the start of the special after it are one place, dressed once).
local function def(out, leg, tag, kind, s, lateral, facing, fromEnd, far, shared)
  out[#out + 1] = { id = leg.id .. ':' .. tag, leg = leg.id, kind = kind, shape = M.SHAPES[kind], s = s, lateral = lateral,
    facing = facing or 'driver', fromEnd = fromEnd or false, far = far or false, shared = shared }
  return out[#out]
end

-- A landmark: a tower beside the road and feather flags on both sides of it, all `far` (made at a long distance, so the
-- player can see where to go from kilometres away). side: -1 left, 1 right, or 'auto' (M.place picks the outer side of the
-- leg's last bend, outerSide); reach: how far from the road the tower stands.
local function landmark(out, leg, tag, s, side, fromEnd, reach, shared)
  reach = reach or 16
  local tower = def(out, leg, tag .. '_tower', 'tower', s, side == 'auto' and reach or side * reach, 'driver', fromEnd, true, shared)
  if side == 'auto' then tower.autoSide = true end
  def(out, leg, tag .. '_feather_l', 'feather', s - 6, -9, 'driver', fromEnd, true, shared)
  def(out, leg, tag .. '_feather_r', 'feather', s - 6, 9, 'driver', fromEnd, true, shared)
  def(out, leg, tag .. '_feather_l2', 'feather', s + 10, -9, 'driver', fromEnd, true, shared)
  def(out, leg, tag .. '_feather_r2', 'feather', s + 10, 9, 'driver', fromEnd, true, shared)
end
M.landmark = landmark

local function cones(out, leg, tag, s0, s1, step, lateral, fromEnd, shared)
  local n = 0
  for s = s0, s1, step do
    n = n + 1
    def(out, leg, tag .. n, 'cone', s, lateral, 'along', fromEnd, false, shared)
  end
end

-- The staging area of a time control: the board where the control is, a table and chairs beside it, the entry
-- board before it, flags and cones down the approach and a landmark to see it from afar. `fromEnd` says the approach lies
-- before the leg's end (the end of a transfer: s counts back from the control); otherwise it is the first metres of the
-- leg itself, past the control (the start of a special that no transfer leads to).
--   The player reported driving a transfer to 140 m short of TC1 and not seeing where it was: the service park at the
-- start of the loop was dressed, the end of the transfer was not.
M.STAGING_ENTRY_M = 50           -- the entry board this far before the time control (the stock loops: 8 to 50 m)
M.STAGING_APPROACH_M = 90        -- the flags and cones line the approach this far
M.STAGING_LANDMARK_S = 10        -- the landmark's tower this far from the control, on the approach side
M.STAGING_LANDMARK_REACH = 16    -- and this far from the road
local function staging(out, leg, tag, fromEnd, opts, shared)
  opts = opts or {}
  if fromEnd then def(out, leg, tag .. '_tc', 'tc_board', 0, 5.5, 'driver', fromEnd, false, shared) end
  -- the timekeepers' table, as the stock ones: a table with two chairs behind it, beside the road
  def(out, leg, tag .. '_table', 'table', 8, 9, 'out', fromEnd, false, shared)
  def(out, leg, tag .. '_chair1', 'chair', 6.6, 9.6, 'out', fromEnd, false, shared)
  def(out, leg, tag .. '_chair2', 'chair', 9.4, 9.6, 'out', fromEnd, false, shared)
  -- flags down the approach, a portrait flag and a teardrop flag in turn, as the stock stages stand them
  local n = 0
  for s = 30, M.STAGING_APPROACH_M, 10 do
    n = n + 1
    def(out, leg, tag .. '_flag' .. n, (n % 2 == 1) and 'flag' or 'flag_tear', s, 7.5, 'driver', fromEnd, false, shared)
  end
  if not fromEnd then return end
  -- (the start of a special has its own boards (the start boards are its time control), cones and landmark: only the
  -- table and the flags are added there)
  def(out, leg, tag .. '_tc_entry', 'tc_entry', M.STAGING_ENTRY_M, 5.5, 'driver', fromEnd, false, shared)
  cones(out, leg, tag .. '_cone_r', 6, M.STAGING_APPROACH_M, 6, 6.5, fromEnd, shared)
  cones(out, leg, tag .. '_cone_l', 6, M.STAGING_APPROACH_M, 6, -6.5, fromEnd, shared)
  local side = (opts.side == 'left' and -1) or (opts.side == 'right' and 1) or 'auto'
  landmark(out, leg, tag, opts.landmarkS or M.STAGING_LANDMARK_S, side, fromEnd, opts.reach or M.STAGING_LANDMARK_REACH, shared)
end

-- The props of a loop: a list of { id, leg, kind, shape, s, lateral, facing, fromEnd }. s counts from the leg's start
-- (from its end when fromEnd); lateral is metres to the right of the direction of travel. splitsByLeg (optional):
-- [leg id] = the metres along a special of its split checkpoints (legs.raceNodes), where the radio and medical point
-- boards stand, their warning boards SPLIT_WARNING_M before.
M.SPLIT_WARNING_M = 100
function M.layout(edition, splitsByLeg)
  local out = {}
  local prev
  for _, leg in ipairs(edition.legs) do
    -- the transfer that leads to this leg: the time control before a special is the end of the transfer, where its staging
    -- area stands, so a special that starts where it ends is not dressed again (M.place checks the distance)
    local from = prev and prev.type == 'transfer' and prev.id or nil
    prev = leg
    if leg.type == 'transfer' then
      -- the service park at the start of a transfer: a table and chairs, flags, cones, the time-control board. A transfer
      -- that starts where another ends (liaisons one after the other) is dressed by the end of that one (its
      -- staging area below), as a special that starts where a transfer ends is, so it is not dressed twice.
      def(out, leg, 'tc_in', 'radio_point', 0, 5.5, 'driver', false, false, from)
      def(out, leg, 'table', 'table', 8, 9, 'out', false, false, from)
      def(out, leg, 'chair1', 'chair', 6.6, 9.6, 'out', false, false, from)
      def(out, leg, 'chair2', 'chair', 9.4, 9.6, 'out', false, false, from)
      def(out, leg, 'flag1', 'flag', 2, 7.5, 'driver', false, false, from)
      def(out, leg, 'flag2', 'flag', 14, 7.5, 'driver', false, false, from)
      cones(out, leg, 'cone_r', 0, 30, 6, 6.5, false, from)
      cones(out, leg, 'cone_l', 0, 30, 6, -6.5, false, from)
      landmark(out, leg, 'park', 16, -1, false, nil, from)    -- (the service park: seen from far)
      -- the staging area at the end of the transfer, the time control before the next leg; legs.json can say
      -- on which side the landmark stands (staging.side 'left' / 'right'), how far from the control (staging.landmarkS)
      -- and from the road (staging.reach); by default it is on the outer side of the last bend
      staging(out, leg, 'stagein', true, leg.staging)
    elseif leg.type == 'special' then
      -- the start: boards and the start sensor on both sides, cones leading in
      def(out, leg, 'start_l', 'start_board', 0, -5.5, 'driver')
      def(out, leg, 'start_r', 'start_board', 0, 5.5, 'driver')
      def(out, leg, 'sensor_l', 'start_sensor', 0, -4.2, 'driver')
      def(out, leg, 'sensor_r', 'start_sensor', 0, 4.2, 'driver')
      -- (the stage start's landmark and its timekeepers' table and flags are the staging area of the transfer before it
      -- when it ends here: it carries them, so they are not stood twice)
      landmark(out, leg, 'start', 24, 1, false, nil, from)    -- (the stage start)
      staging(out, leg, 'stagestart', false, nil, from)
      cones(out, leg, 'start_cone_r', 12, 36, 12, 4.8)
      cones(out, leg, 'start_cone_l', 12, 36, 12, -4.8)
      -- the finish: the entry board before the line, the finish boards on the line, cones along the last metres
      def(out, leg, 'fin_entry', 'finish_entry', 250, 5.5, 'driver', true)
      def(out, leg, 'fin_l', 'finish_board', 0, -5.5, 'driver', true)
      def(out, leg, 'fin_r', 'finish_board', 0, 5.5, 'driver', true)
      landmark(out, leg, 'finish', 30, 1, true)               -- (the finish)
      cones(out, leg, 'fin_cone_r', 12, 48, 12, 4.8, true)
      cones(out, leg, 'fin_cone_l', 12, 48, 12, -4.8, true)
      -- the split checkpoints: radio and medical point boards side by side, their warnings before
      for i, at in ipairs((splitsByLeg and splitsByLeg[leg.id]) or {}) do
        def(out, leg, 'split' .. i .. '_radio', 'radio_point', at, 5.5, 'driver')
        def(out, leg, 'split' .. i .. '_medical', 'medical_point', at, 7.5, 'driver')
        def(out, leg, 'split' .. i .. '_radio_w', 'radio_warning', at - M.SPLIT_WARNING_M, 5.5, 'driver')
        def(out, leg, 'split' .. i .. '_medical_w', 'medical_warning', at - M.SPLIT_WARNING_M, 7.5, 'driver')
      end
    end
  end
  return out
end

-- Cumulative distance along world points ({x, y, z, ...} each).
local function cumulative(w)
  local c = { 0 }
  for i = 2, #w do
    local dx, dy, dz = w[i][1] - w[i - 1][1], w[i][2] - w[i - 1][2], w[i][3] - w[i - 1][3]
    c[i] = c[i - 1] + sqrt(dx * dx + dy * dy + dz * dz)
  end
  return c
end
M.cumulative = cumulative

-- The point `s` metres along (clamped), and the unit direction of travel there in x, y.
local function pointAt(w, c, s)
  if s <= 0 then s = 0 end
  if s >= c[#c] then s = c[#c] end
  local lo, hi = 1, #c
  while hi - lo > 1 do
    local mid = math.floor((lo + hi) / 2)
    if c[mid] <= s then lo = mid else hi = mid end
  end
  local span = c[hi] - c[lo]
  local f = span > 1e-9 and (s - c[lo]) / span or 0
  local a, b = w[lo], w[hi]
  local x, y, z = a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3] + (b[3] - a[3]) * f
  local dx, dy = b[1] - a[1], b[2] - a[2]
  local l = sqrt(dx * dx + dy * dy)
  if l < 1e-9 then dx, dy = 0, 1 else dx, dy = dx / l, dy / l end
  return x, y, z, dx, dy
end

-- Yaw in degrees counter-clockwise from +x for the model to face as asked, given the direction of travel (dx, dy).
local function yawFor(facing, dx, dy)
  local heading = deg(atan2(dy, dx))
  if facing == 'along' then return heading - 90 end      -- front (+y) along the travel direction
  if facing == 'out' then return heading + 180 end       -- front to the right of the road (toward the roadside)
  return heading + 90                                     -- 'driver': front against the travel direction
end
M.yawFor = yawFor

-- The side of the road a landmark at the end of a leg stands on, -1 (left) or 1 (right): the outer side of the bend the
-- road makes over its last OUTER_SIDE_M metres. From the approach the tower is seen across the bend there, and a hill
-- road's inner side is the bank it is cut into, where a tower would stand against a slope or in the trees (the
-- player could not see where the TC1 of the Svolte di Popoli was, just after a bend and a short tunnel). The heading
-- change is taken between chords of 40 m, the geometry's points being as close as 10 m. A road that stays straight (under
-- OUTER_SIDE_MIN_DEG of turn) gets the left, the side of the service park's landmark.
M.OUTER_SIDE_M = 150
M.OUTER_SIDE_MIN_DEG = 10
local function chord(w, c, s0, s1)
  local x0, y0 = pointAt(w, c, s0)
  local x1, y1 = pointAt(w, c, s1)
  return x1 - x0, y1 - y0
end
local function outerSide(w, c)
  local len = c[#c]
  local s0 = math.max(0, len - M.OUTER_SIDE_M)
  local ax, ay = chord(w, c, s0, math.min(len, s0 + 40))
  local bx, by = chord(w, c, math.max(0, len - 40), len)
  local turn = deg(atan2(ax * by - ay * bx, ax * bx + ay * by))   -- counter-clockwise (left) is positive
  if math.abs(turn) < M.OUTER_SIDE_MIN_DEG then return -1 end
  return turn > 0 and 1 or -1                                  -- a left bend: its outside is the right
end
M.outerSide = outerSide

-- Two legs share a place when the second starts within SHARED_M of where the first ends (a transfer to the time control
-- before the special: usually the same point, a few metres between them where the route builder snapped them apart).
M.SHARED_M = 60
local function sharedPlace(worldByLeg, d)
  local a, b = worldByLeg[d.shared], worldByLeg[d.leg]
  if not (a and b and #a >= 2 and #b >= 2) then return false end
  local e, f = a[#a], b[1]
  local dx, dy = e[1] - f[1], e[2] - f[2]
  return sqrt(dx * dx + dy * dy) <= M.SHARED_M
end

-- The layout in the frame of one anchor: { id, leg, kind, shape, x, y, z, yaw } with z the terrain data's height
-- (the caller puts it on the game's ground). worldByLeg: { [leg id] = list of {x, y, z, ...} } (legs.toWorld). A prop that
-- belongs to another leg's end as well (`shared`) is left out where that leg ends at this one's start.
function M.place(defs, worldByLeg)
  local cum, sideOf, out = {}, {}, {}
  for _, d in ipairs(defs) do
    local w = worldByLeg[d.leg]
    if w and #w >= 2 and not (d.shared and sharedPlace(worldByLeg, d)) then
      cum[d.leg] = cum[d.leg] or cumulative(w)
      local c = cum[d.leg]
      local s = d.fromEnd and (c[#c] - d.s) or d.s
      local x, y, z, dx, dy = pointAt(w, c, s)
      local lateral = d.lateral
      if d.autoSide then
        sideOf[d.leg] = sideOf[d.leg] or outerSide(w, c)
        lateral = lateral * sideOf[d.leg]
      end
      x, y = x + lateral * dy, y - lateral * dx              -- right of the direction of travel: (dy, -dx)
      out[#out + 1] = { id = d.id, leg = d.leg, kind = d.kind, shape = d.shape, x = x, y = y, z = z,
        yaw = yawFor(d.facing, dx, dy), far = d.far, side = lateral < 0 and -1 or 1 }
    end
  end
  return out
end

-- ---------------------------------------------------------------------------------------------------------------------
-- The closures of a special stage's side roads (tools/stage_closures.py writes where they go: editions/<ed>/
-- closures/<leg>.json, {closures = {{lat, lon, h, bearing, s, class, width}, ...}}, bearing the side road's direction away from
-- the stage in degrees clockwise from north, width the metres across to close). Each stands across its side road as the
-- stock stages do it: one length of road-closure tape, two side by side for a wide road.

M.CLOSURE_TAPE_M = 6.0               -- the tape shape's length
M.CLOSURE_RADIUS_M = 1500            -- closures this close to the player exist (props of the loop are 2500)

-- The closure sites of an edition: a list of { id, leg, lat, lon, h, bearing, width } from the decoded files
-- (closuresByLeg: { [leg id] = decoded file }); legs without a file have none.
function M.closureSites(edition, closuresByLeg)
  local out = {}
  for _, leg in ipairs(edition.legs) do
    local f = closuresByLeg and closuresByLeg[leg.id]
    if leg.type == 'special' and f and f.closures then
      for i, c in ipairs(f.closures) do
        out[#out + 1] = { id = leg.id .. ':closure' .. i, leg = leg.id, lat = c.lat, lon = c.lon, h = c.h or 0, bearing = c.bearing,
          width = c.width or M.CLOSURE_TAPE_M }
      end
    end
  end
  return out
end

-- The props of the sites for one anchor, like place(): { id, leg, kind, shape, x, y, z, yaw, far, radius }.
-- toWorld(points): the world points ({x, y, z, ...}) of {lat, lon, h} points in the frame in force (legs.toWorld bound to the
-- anchor). The side road's direction in that frame comes from a second point 10 m down it, so a frame turned against
-- north (Earth's anchor turns it) is followed without knowing how.
function M.placeClosures(sites, toWorld)
  local out = {}
  if #sites == 0 then return out end
  local pts = {}
  for i, c in ipairs(sites) do
    local b = math.rad(c.bearing)
    pts[2 * i - 1] = { c.lat, c.lon, c.h }
    pts[2 * i] = { c.lat + math.cos(b) * 10 / 111320, c.lon + math.sin(b) * 10 / (111320 * math.cos(math.rad(c.lat))), c.h }
  end
  local w = toWorld(pts)
  for i, c in ipairs(sites) do
    local a, b = w[2 * i - 1], w[2 * i]
    local dx, dy = b[1] - a[1], b[2] - a[2]
    local l = sqrt(dx * dx + dy * dy)
    if l > 1e-9 then
      dx, dy = dx / l, dy / l                                    -- along the side road, away from the stage
      local ax, ay = dy, -dx                                      -- across it (to its right)
      -- the tape has its post at the origin and runs along local -y: that axis goes across the road, from the left post
      local yaw = deg(atan2(ay, ax)) + 90
      local n = math.max(1, math.floor(c.width / M.CLOSURE_TAPE_M + 0.5))
      for k = 0, n - 1 do
        local off = (k - n / 2) * M.CLOSURE_TAPE_M               -- along the row from its middle to this tape's post
        out[#out + 1] = { id = c.id .. ':tape' .. (k + 1), leg = c.leg, kind = 'closure_tape', shape = M.SHAPES.closure_tape,
          x = a[1] + off * ax, y = a[2] + off * ay, z = a[3], yaw = yaw, far = false, radius = M.CLOSURE_RADIUS_M }
      end
    end
  end
  return out
end

-- ---------------------------------------------------------------------------------------------------------------------
-- The service booth where the car starts (tools/booth.py makes it from the lot's map data and writes it into the
-- first leg's geometry file: servicePark.booth, a list of { kind, variant, lat, lon, h, bearing }, bearing the way the model's
-- front (+y) points in degrees clockwise from north). A gazebo is three shapes (its cover and two frames) standing together.

M.GAZEBO_HALF_M = 1.5               -- the frame and the frame add-on stand this far either side of the cover, along the model's +x

-- The booth sites of an edition: a list of { id, leg, kind, variant, lat, lon, h, bearing } (none without a lot).
function M.boothSites(edition, geometryByLeg)
  local out = {}
  local leg = edition and edition.legs and edition.legs[1]
  local g = leg and geometryByLeg and geometryByLeg[leg.id]
  local list = g and g.servicePark and g.servicePark.booth
  for i, it in ipairs(list or {}) do
    out[#out + 1] = { id = leg.id .. ':booth' .. i, leg = leg.id, kind = it.kind, variant = it.variant, lat = it.lat, lon = it.lon,
      h = it.h or 0, bearing = it.bearing }
  end
  return out
end

-- The props of the sites for one anchor, like placeClosures(): { id, leg, kind, shape, x, y, z, yaw, far }. The front's direction in
-- the frame in force comes from a second point 10 m on at the bearing, so a frame turned against north is followed.
function M.placeBooth(sites, toWorld)
  local out = {}
  if #sites == 0 then return out end
  local pts = {}
  for i, c in ipairs(sites) do
    local b = math.rad(c.bearing)
    pts[2 * i - 1] = { c.lat, c.lon, c.h }
    pts[2 * i] = { c.lat + math.cos(b) * 10 / 111320, c.lon + math.sin(b) * 10 / (111320 * math.cos(math.rad(c.lat))), c.h }
  end
  local w = toWorld(pts)
  for i, c in ipairs(sites) do
    local a, b = w[2 * i - 1], w[2 * i]
    local dx, dy = b[1] - a[1], b[2] - a[2]
    local l = sqrt(dx * dx + dy * dy)
    if l > 1e-9 then
      dx, dy = dx / l, dy / l                                    -- the front's direction
      local yaw = yawFor('along', dx, dy)
      local parts
      if c.kind == 'gazebo' then
        parts = { { 'gazebo_cover_' .. tostring(c.variant or 'blastr'), 0, ':cover' }, { 'gazebo_frame', -M.GAZEBO_HALF_M, ':frame' },
          { 'gazebo_addon', M.GAZEBO_HALF_M, ':addon' } }
      else
        parts = { { c.kind, 0, '' } }
      end
      for _, part in ipairs(parts) do
        local shape = M.SHAPES[part[1]]
        if shape then
          -- along the model's +x, to the right of its front: (dy, -dx)
          -- (a gazebo's three shapes are one thing: the road check drops all three or none, `group`; the lot's aisles the booth was made
          -- clear of offline are not roads of ours, `booth`, tools/booth.py)
          out[#out + 1] = { id = c.id .. part[3], leg = c.leg, kind = part[1], shape = shape, x = a[1] + part[2] * dy, y = a[2] - part[2] * dx,
            z = a[3], yaw = yaw, far = false, booth = true, group = c.kind == 'gazebo' and c.id or nil }
        end
      end
    end
  end
  return out
end

return M
