"""Offline tests for shape.lua: the transient ground shaping on a stub TerrainBlock.

    python -m pytest tests

The stub block is the editor's API (getHeightGrid / setHeightGrid / updateGrid, grid heights above the block's base or
absolute) on a plane that slopes both ways, with a clock that advances a little at every look so the per-frame budget
is exercised. Pure Lua, needs only lupa.
"""
import math
import os
import re

import pytest

lupa = pytest.importorskip("lupa")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DAKAR_EXT = os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions").replace("\\", "/")

PRELUDE = r"""
math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end
package.path = package.path .. ';%(dakar)s/?.lua'
shape = require('beamngDakar/shape')

T, CLOCK_STEP = 0, 0.002        -- ms; every look at the clock costs CLOCK_STEP
function now() T = T + CLOCK_STEP; return T end
LOGS = {}
PLAYER = { x = 100, y = 0 }
-- the world's terrain: a plane that slopes both ways with 3 m spikes on the road at x = 100 and 180 (sigma 8 m: wide enough for the line, whose points are 10 m apart, to see; 80 m apart: a spike is found against the median of the points within 40 m either side, which two close ones would pull)
BUMPS, BUMP_H = { { 100, 0 }, { 180, 0 } }, 3
function GROUND(x, y)
  local z = 100 + 0.02 * x + 0.1 * y
  for _, b in ipairs(BUMPS) do z = z + BUMP_H * math.exp(-((x - b[1]) ^ 2 + (y - b[2]) ^ 2) / 128) end
  return z
end
DECK = nil

-- a stub TerrainBlock: n x n samples sq metres apart, the south-west corner at (px, py), base height pz. rel: the grid's
-- heights are above pz (else absolute). flip: the engine's grid rows run the other way than the vertex positions say.
function newBlock(o)
  o = o or {}
  local tb = { px = o.px or 0, py = o.py or -128, pz = o.pz or 40, sq = o.sq or 1, n = o.n or 256, rel = o.rel ~= false,
    flip = o.flip, h = {}, sets = 0, boxes = {}, ground = o.ground or GROUND }
  function tb:getPosition() return { x = self.px, y = self.py, z = self.pz } end
  function tb:getSquareSize() return self.sq end
  function tb:getWorldBox() local s = self; return { getExtents = function() return { x = s.n * s.sq, y = s.n * s.sq, z = 100 } end } end
  function tb:idx(gx, gy) return gx + (self.flip and (self.n - 1 - gy) or gy) * self.n end
  function tb:base() return self.rel and self.pz or 0 end
  function tb:getHeightGrid(gx, gy) return self.h[self:idx(gx, gy)] end
  function tb:setHeightGrid(gx, gy, v) self.h[self:idx(gx, gy)] = v; self.sets = self.sets + 1 end
  function tb:updateGrid(a, b) self.boxes[#self.boxes + 1] = { a.x, a.y, b.x, b.y } end
  for gy = 0, tb.n - 1 do
    for gx = 0, tb.n - 1 do
      tb.h[gx + gy * tb.n] = tb.ground(tb.px + gx * tb.sq, tb.py + gy * tb.sq) - tb:base()    -- (the file's own layout)
    end
  end
  return tb
end
function snapshot(tb) local c = {}; for k, v in pairs(tb.h) do c[k] = v end; return c end
function sameAs(tb, snap) for k, v in pairs(snap) do if tb.h[k] ~= v then return false end end; return true end
-- the world height at vertex (wx, wy) as the block has it now
function worldAt(tb, wx, wy)
  return tb:getHeightGrid(math.floor((wx - tb.px) / tb.sq + 0.5), math.floor((wy - tb.py) / tb.sq + 0.5)) + tb:base()
end
function changedCells(tb, snap)
  local out = {}
  for k, v in pairs(snap) do if tb.h[k] ~= v then out[#out + 1] = k end end
  return out
end
-- the world x and y range of the changed cells: xmin, xmax, ymin, ymax (nil: none)
function extent(tb, snap)
  local x0, x1, y0, y1
  for _, k in ipairs(changedCells(tb, snap)) do
    local gx, gy = k %% tb.n, math.floor(k / tb.n)
    if tb.flip then gy = tb.n - 1 - gy end
    local x, y = tb.px + gx * tb.sq, tb.py + gy * tb.sq
    x0, x1 = math.min(x0 or x, x), math.max(x1 or x, x)
    y0, y1 = math.min(y0 or y, y), math.max(y1 or y, y)
  end
  return x0, x1, y0, y1
end
-- every changed cell lies inside one of the boxes handed to updateGrid
function allInBoxes(tb, snap)
  for _, k in ipairs(changedCells(tb, snap)) do
    local gx, gy = k %% tb.n, math.floor(k / tb.n)
    if tb.flip then gy = tb.n - 1 - gy end
    local ok = false
    for _, b in ipairs(tb.boxes) do if gx >= b[1] and gy >= b[2] and gx <= b[3] and gy <= b[4] then ok = true; break end end
    if not ok then return false end
  end
  return true
end

-- the greatest difference of the world heights of two blocks over the vertices within r of (cx, cy), and how many there are
function maxDiff(a, b, cx, cy, r)
  local worst, n = 0, 0
  for wy = cy - r, cy + r do
    for wx = cx - r, cx + r do
      if (wx - cx) ^ 2 + (wy - cy) ^ 2 <= r * r then
        n = n + 1
        local d = math.abs(worldAt(a, wx, wy) - worldAt(b, wx, wy))
        if d > worst then worst = d end
      end
    end
  end
  return worst, n
end
-- every write to a block, with where the car was (and which frame): { gx, gy, car x, car y, frame }
function recordWrites(tb)
  WRITES, FRAME = {}, 0
  local orig = tb.setHeightGrid
  function tb:setHeightGrid(gx, gy, v) WRITES[#WRITES + 1] = { gx, gy, PLAYER.x, PLAYER.y, FRAME }; return orig(self, gx, gy, v) end
end
function logged(needle)
  for _, l in ipairs(LOGS) do if l:find(needle, 1, true) then return l end end
  return nil
end

CURRENT = nil
function newEngine(tb)
  CURRENT = tb
  return shape.new({
    block = function() return CURRENT end, now = now, clock = function() return T / 1000 end,
    player = function() return PLAYER end,
    ground = function(x, y) if STALE then return nil end return GROUND(x, y) end,
    deck = function(x, y, g) return DECK and DECK(x, y, g) or nil end,
    log = function(l, m) LOGS[#LOGS + 1] = l .. ' ' .. m end, vec3 = function(x, y, z) return { x = x, y = y, z = z } end,
  })
end
-- a stretch of road along y = 0 from x0 to x1 at height z every 10 m
function roadLine(x0, x1, z, y)
  local pts = {}
  for x = x0, x1, 10 do pts[#pts + 1] = { x, y or 0, z } end
  return { pts = pts, skip = {} }
end
-- one frame at a time until nothing is left to do; returns the frames and the longest frame (ms of the fake clock)
function runIdle(E)
  local frames, worst = 0, 0
  while E:busy() and frames < 5000 do
    local t0 = T
    E:update()
    if T - t0 > worst then worst = T - t0 end
    frames = frames + 1
  end
  return frames, worst
end
"""


@pytest.fixture()
def lua():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute(PRELUDE % {"dakar": DAKAR_EXT})
    return rt


BUMPS = [(100, 0), (180, 0)]
BUMP_H = 3.0


def plane(x, y):
    return 100 + 0.02 * x + 0.1 * y


def ground(x, y, bumps=BUMPS):
    """The stub world's terrain: a plane that slopes both ways with 3 m spikes on the road (sigma 3 m)."""
    return plane(x, y) + sum(BUMP_H * math.exp(-((x - bx) ** 2 + (y - by) ** 2) / 128.0) for bx, by in bumps)


def road(x, y=0):
    """The height the shaping gives the road on a line along y: the game's ground under it with the spikes out."""
    return plane(x, y)


def world(lua, x, y, tb="TB"):
    return lua.eval("worldAt(%s, %r, %r)" % (tb, float(x), float(y)))


def setup(lua, line="roadLine(40, 216, 110)", **block):
    opts = ", ".join("%s = %s" % (k, str(v).lower() if isinstance(v, bool) else v) for k, v in block.items())
    lua.execute("TB = newBlock({%s}); SNAP = snapshot(TB); E = newEngine(TB)" % opts)
    lua.execute("E:setLines({ %s })" % line)


def shape_it(lua):
    """Frames until idle, and the longest frame (ms of the fake clock)."""
    return lua.eval("runIdle(E)")


CORE, BLEND = 5.5, 6.0           # (shape.CORE_M and BLEND_M: the road's half width and half a metre, and the taper beside it)


def weight(d, core=CORE, blend=BLEND):
    """The falloff, as the module defines it: 1 in the core, a smoothstep down to 0 at core + blend."""
    if d <= core:
        return 1.0
    if d >= core + blend:
        return 0.0
    t = (d - core) / blend
    return 1 - t * t * (3 - 2 * t)


TOL = 0.3           # (at a point of the line a fixed spike is on the line between the points beside its feet, which carry what is left of the spikes: 0.13 m each)


def spike_delta(bx=100):
    """The line's delta at a spike (the stub's 3 m, sigma 8 m, on a line with points 10 m apart): the line is fixed to the straight line
    between the points 20 m either side of it (10 m beside the spike are its feet, fixed with it: the taper), minus the ground there."""
    only = [(bx, 0)]
    return (ground(bx - 20, 0, only) + ground(bx + 20, 0, only)) / 2 - ground(bx, 0, only)


def extent(lua):
    """x and y (world) of the cells that differ from the snapshot: xmin, xmax, ymin, ymax."""
    return lua.eval("extent(TB, SNAP)")


@pytest.mark.parametrize("rel", [True, False])
def test_a_spike_on_the_line_is_fixed_as_a_delta_that_keeps_the_cross_slope_and_nothing_else_is_written(lua, rel):
    lua.execute("BUMPS = { { 100, 0 } }")
    bumps = [(100, 0)]
    setup(lua, rel=rel, pz=40)
    shape_it(lua)
    d = spike_delta()
    assert d < -2.8                                                     # (a 3 m spike: the line comes down by it)
    # (grid conversion: the cell at world (x, y) is where the line says; heights are world heights whichever the convention)
    # the cells across the road, within the core, move by the same delta as the line: the cross slope and the spike's own shape across stay
    for dy in (0, 1, 3, 5):
        for sign in (1, -1):
            assert world(lua, 100, sign * dy) - ground(100, sign * dy, bumps) == pytest.approx(d, abs=1e-6), (dy, sign)
    # through the blend: the delta moved by the weight of the distance (a change under MIN_CHANGE_M is not written)
    for dy in range(6, 12):
        for sign in (1, -1):
            want = d * weight(dy) if abs(d * weight(dy)) >= lua.eval("shape.MIN_CHANGE_M") else 0.0
            assert world(lua, 100, sign * dy) - ground(100, sign * dy, bumps) == pytest.approx(want, abs=1e-6), (dy, sign)
    # beyond the blend: untouched
    for dy in (12, 25, 40, -30):
        assert world(lua, 100, dy) == pytest.approx(ground(100, dy, bumps), abs=1e-9)
    # the spike's top is on the line between its feet; the feet are fixed with it and the delta falls to 0 at the points 20 m beside it
    for x in (80, 120, 60, 140, 40, 200):
        assert world(lua, x, 0) == pytest.approx(ground(x, 0, bumps), abs=1e-9), x
    top = world(lua, 100, 0)
    assert top == pytest.approx(plane(100, 0), abs=0.15)
    deltas = [abs(world(lua, x, 0) - ground(x, 0, bumps)) for x in range(80, 101)]
    assert all(a <= b + 1e-9 for a, b in zip(deltas, deltas[1:]))        # (tapered: it grows to the spike, never a step)
    assert max(abs(b - a) for a, b in zip(deltas, deltas[1:])) < 0.35    # (no step: 0.3 m a metre of the line at most)
    # nothing is written beyond the stretch fixed, its taper and the falloff: the deltas of the points from 80 to 120 m, 11.5 m round
    x0, x1, y0, y1 = extent(lua)
    reach = CORE + BLEND
    assert 80 - reach - 1 <= x0 and x1 <= 120 + reach + 1 and -reach - 1 <= y0 and y1 <= reach + 1
    st = lua.eval("E:status()")
    assert st.state == "shaped" and st.cells > 500
    assert st.maxChange == pytest.approx(-d, abs=1e-6)                   # the largest change: the delta on the line
    assert st.stretches == 1 and st.fixLargest == pytest.approx(-d, abs=1e-6) and st.at is not None
    assert lua.eval("TB.sets") == st.cells                               # (every cell written once, and only those)
    said = lua.eval("logged('1 stretch of the line fixed (0 under an overpass, 1 spike, 0 deck ends)')")
    assert said and "largest change of the line %.1f m" % -d in said and "m of it with the tapers" in said
    assert lua.eval("logged('road lines stranded')")                     # (the cells of the road core that moved: the lines stay)


def test_a_cambered_sloping_ground_without_defects_is_left_exactly_as_it_is(lua):
    # a cross slope, a crown, a 20 percent climb and a crest: relief, not defects of the data. Not one cell is written.
    lua.execute("BUMPS = {}; GROUND = function(x, y) return 100 + 0.2 * x + 0.3 * y - 0.03 * math.abs(y) - 0.0003 * (x - 130) ^ 2 end")
    setup(lua)
    shape_it(lua)
    assert lua.eval("TB.sets") == 0 and lua.eval("sameAs(TB, SNAP)") is True
    assert lua.eval("#TB.boxes") == 0                                    # (no grid update either)
    st = lua.eval("E:status()")
    assert st.cells == 0 and st.stretches == 0 and st.state == "nothing to shape" and st.linesStranded == 0
    assert lua.eval("logged('0 stretches of the line fixed')")
    assert not lua.eval("E:busy()")


def test_a_steep_but_smooth_climb_is_untouched(lua):
    # 45 percent over the whole stretch (a Popoli hairpin's grade), straight and with a constant bend: no spike to take out
    lua.execute("BUMPS = {}; GROUND = function(x, y) return 100 + 0.45 * x + 0.1 * y end")
    setup(lua)
    shape_it(lua)
    assert lua.eval("TB.sets") == 0
    lua.execute("GROUND = function(x, y) return 100 + 0.25 * x + 0.0008 * (x - 40) ^ 2 + 0.1 * y end")      # (and a curving climb: 8 percent a 10 m)
    setup(lua)
    shape_it(lua)
    assert lua.eval("TB.sets") == 0
    assert lua.eval("E:status().stretches") == 0


def test_the_ground_that_is_not_the_roads_own_height_is_not_a_defect_either(lua):
    # (the line's heights are the offline terrain data; at the Popoli stage's start they stood 10 m above the game's
    # ground. The reference is the game's own ground, and nothing of the line's heights is written into it)
    lua.execute("BUMPS = {}")
    setup(lua, line="(function() local l = roadLine(40, 216, 0); for i, p in ipairs(l.pts) do p[3] = 110 + 10 * (i % 2) end; return l end)()")
    shape_it(lua)
    assert lua.eval("TB.sets") == 0


def test_a_spike_beside_a_steep_climb_is_found_against_the_climb(lua):
    # (the spike is off the line's own trend, which here is steep: the delta is against the trend, not against the level)
    lua.execute("BUMPS = {}; GROUND = function(x, y) return 100 + 0.3 * x + 0.1 * y + 3 * math.exp(-((x - 100) ^ 2 + y ^ 2) / 128) end")
    setup(lua)
    shape_it(lua)
    only = [(100, 0)]
    g = lambda x, y=0: 100 + 0.3 * x + 0.1 * y + 3 * math.exp(-((x - 100) ** 2 + y ** 2) / 128.0)
    assert world(lua, 100, 0) == pytest.approx(100 + 0.3 * 100, abs=0.15)      # the climb's own height at 100 (0.13 of the spike's feet)
    assert world(lua, 60, 0) == pytest.approx(g(60), abs=1e-9)                 # the climb itself is not touched
    assert world(lua, 140, 0) == pytest.approx(g(140), abs=1e-9)


def test_the_road_is_never_moved_further_than_MAX_CHANGE_M(lua):
    # a ridge of the ground 10 m high and 60 m long on the road: a defect of the data bigger than the cap, so the cap holds. It is held
    # by spreading the change, not by cutting every cell off at the cap, which leaves a plateau with the ridge's steep sides
    lua.execute("BUMPS = {}; GROUND_PLANE = GROUND; "
                "GROUND = function(x, y) return GROUND_PLANE(x, y) + 10 * math.exp(-((x - 120) ^ 2) / 450) end")
    setup(lua)
    shape_it(lua)
    cap = lua.eval("shape.MAX_CHANGE_M")
    st = lua.eval("E:status()")
    assert st.stretches >= 1 and st.maxChange == pytest.approx(cap, abs=1e-9) and st.capped == 0
    for x in range(40, 211, 10):                                      # (at the line's points; between them the road is straight)
        g = lua.eval("GROUND(%r, 0)" % float(x))
        assert abs(world(lua, x, 0) - g) <= cap + 1e-6, x
    assert lua.eval("logged('asked for more than the 4 m cap')") and lua.eval("logged('spread over the stretch')")
    assert not lua.eval("logged('road core was asked to move')")        # (no cell is cut off at the cap)
    # the remainder is the ridge's own shape, smaller: the road follows the plane's own height plus 60 percent of the ridge, with no step
    for x in range(110, 140, 10):
        ridge = 10 * math.exp(-((x - 120) ** 2) / 450.0)
        assert world(lua, x, 0) == pytest.approx(plane(x, 0) + ridge * 0.6, abs=0.35), x


def test_the_box_handed_to_updateGrid_covers_every_changed_cell(lua):
    setup(lua)
    shape_it(lua)
    assert lua.eval("allInBoxes(TB, SNAP)") is True
    assert lua.eval("#TB.boxes") >= 1
    # (the box is grid coordinates, as the stock editor's updateGrid takes them: x east and y north from the block's corner)
    b = lua.eval("TB.boxes[1]")
    assert 0 <= b[1] <= b[3] < 256 and 0 <= b[2] <= b[4] < 256


def test_the_work_is_spread_over_frames_within_the_budget(lua):
    lua.execute("CLOCK_STEP = 0.3")                                 # a look at the clock costs 300 us: a frame does a few columns
    setup(lua)
    frames, worst = shape_it(lua)
    assert frames > 10
    assert worst <= lua.eval("shape.URGENT_BUDGET_MS") + 3.0          # no frame much past the biggest budget it is given (a column and the plan over)
    # the frames after the urgent slice (the stretch within URGENT_M of the player) are held to the normal budget
    setup(lua)
    lua.execute("PLAYER = { x = 5000, y = 5000 }")                    # (nobody near the stretch: no urgent slice at all)
    lua.execute("E:update(); E:update()")
    lua.execute("local t0 = T; E:update(); LAST = T - t0")
    assert lua.eval("LAST") <= lua.eval("shape.BUDGET_MS") + 1.5        # (the fake clock charges every look: a few of them over)
    assert lua.eval("E:status().working") is True


def test_the_stretch_nearest_the_player_is_shaped_first(lua):
    lua.execute("CLOCK_STEP = 0.05")
    setup(lua)
    lua.execute("PLAYER = { x = 180, y = 0 }")
    lua.execute("E:update()")                                         # the first frame after a commit: the urgent slice
    assert world(lua, 180, 0) == pytest.approx(road(180), abs=TOL)    # at the car
    assert world(lua, 100, 0) == pytest.approx(ground(100, 0), abs=1e-6)  # the other spike, 80 m away: not yet
    shape_it(lua)
    assert world(lua, 100, 0) == pytest.approx(road(100), abs=TOL)


def test_the_original_heights_are_kept_and_put_back_when_the_loop_stops(lua):
    setup(lua)
    shape_it(lua)
    n = lua.eval("#changedCells(TB, SNAP)")
    assert n > 1000
    sets = lua.eval("TB.sets")
    lua.execute("E:stop()")
    shape_it(lua)
    assert lua.eval("sameAs(TB, SNAP)") is True                       # every cell as it was
    assert lua.eval("TB.sets") - sets == n                            # and only the cells we had changed were written
    assert lua.eval("E:status().state") == "restored"
    assert lua.eval("allInBoxes(TB, SNAP)") is True                    # (nothing to flag: all back)
    assert not lua.eval("E:busy()")


def test_the_switch_puts_the_ground_back_and_shapes_again(lua):
    setup(lua)
    shape_it(lua)
    lua.execute("E:setEnabled(false)")
    shape_it(lua)
    assert lua.eval("sameAs(TB, SNAP)") is True
    assert lua.eval("E:status().enabled") is False
    lua.execute("E:setEnabled(true)")
    shape_it(lua)
    assert world(lua, 100, 0) == pytest.approx(road(100), abs=TOL)


def test_a_new_block_is_shaped_again_and_the_old_ones_originals_are_not_written_into_it(lua):
    setup(lua, rel=True, pz=40)
    shape_it(lua)
    # Earth swaps in a new .ter: other heights (the ground 2 m up), another base height and convention; its commit
    # is tapped: blockChanged
    lua.execute("GROUND_OLD = GROUND; GROUND = function(x, y) return GROUND_OLD(x, y) + 2 end")      # (the world as the game now answers)
    lua.execute("TB2 = newBlock({ rel = false, pz = 55 }); SNAP2 = snapshot(TB2); CURRENT = TB2; E:blockChanged()")
    old_sets = lua.eval("TB.sets")
    shape_it(lua)
    assert lua.eval("TB.sets") == old_sets                             # the old data is not touched again
    assert world(lua, 100, 0, "TB2") == pytest.approx(road(100) + 2, abs=TOL)     # (the new ground's height, not the old one's)
    d = spike_delta()
    o = ground(100, 12) + 2
    assert world(lua, 100, 8, "TB2") == pytest.approx(ground(100, 8) + 2 + d * weight(8), abs=1e-6)
    sets2 = lua.eval("TB2.sets")
    lua.execute("E:stop()")
    shape_it(lua)
    assert lua.eval("sameAs(TB2, SNAP2)") is True                      # the NEW block's own heights are back
    assert lua.eval("TB.sets") == old_sets                             # still nothing written into the old one
    assert lua.eval("TB2.sets") > sets2


def test_a_commit_between_shaping_and_stopping_leaves_the_old_ground_alone_and_the_new_ends_as_it_was(lua):
    setup(lua)
    shape_it(lua)
    old_sets = lua.eval("TB.sets")
    # (the new block is shaped at the car at once, and the stop that follows puts its own heights back)
    lua.execute("TB2 = newBlock({}); SNAP2 = snapshot(TB2); CURRENT = TB2; E:blockChanged(); E:stop()")
    shape_it(lua)
    assert lua.eval("TB.sets") == old_sets                                           # nothing written into the old data
    assert lua.eval("sameAs(TB2, SNAP2)") is True                                    # the new block as Earth made it


def test_shaping_a_second_time_on_the_same_block_goes_back_first(lua):
    lua.execute("BUMPS[#BUMPS + 1] = { 100, 40 }")
    bumps = BUMPS + [(100, 40)]
    setup(lua, line="roadLine(40, 216, 110, 0)")
    shape_it(lua)
    lua.execute("E:setLines({ roadLine(40, 216, 108, 40) })")          # another line, 40 m north
    shape_it(lua)
    assert world(lua, 100, 40) == pytest.approx(road(100, 40), abs=TOL)
    assert world(lua, 100, 0) == pytest.approx(ground(100, 0, bumps), abs=1e-6)    # the first one gone, the ground as it was
    lua.execute("E:stop()")
    shape_it(lua)
    assert lua.eval("sameAs(TB, SNAP)") is True


def test_a_bridge_or_tunnel_span_is_left_alone(lua):
    lua.execute("BUMPS[#BUMPS + 1] = { 60, 0 }")                      # (a spike before the span, one in it, one after it)
    bumps = BUMPS + [(60, 0)]
    setup(lua)
    lua.execute("for i = 5, 12 do E.lines[1].skip[i] = true end")     # the segments from x = 80 to 160
    shape_it(lua)
    assert world(lua, 100, 0) == pytest.approx(ground(100, 0, bumps), abs=1e-6)    # under the span: as Earth made it
    assert world(lua, 100, 3) == pytest.approx(ground(100, 3, bumps), abs=1e-6)
    assert world(lua, 60, 0) == pytest.approx(road(60), abs=TOL)            # either side: fixed
    assert world(lua, 180, 0) == pytest.approx(road(180), abs=TOL)
    st = lua.eval("E:status()")
    assert st.skipped >= 8


# A bridge the road is on: the ground falls away under it into a valley (12 m at the middle, from x = 95 to 145) and the
# deck's top is the road's own height plus a metre, from x = 91 to 149. Where it begins the top continues from the ground before it.
VALLEY = ("function GROUND(x, y) local z = 100 + 0.02 * x + 0.1 * y; if x > 95 and x < 145 then "
          "local t = math.min((x - 95) / 8, (145 - x) / 8, 1); z = z - 12 * t * t * (3 - 2 * t) end; return z end")
DECK_ROAD = "DECK = function(x, y, g) if x > 91 and x < 149 then return 100 + 0.02 * x + 0.1 * y + 1.0 end end"


def test_a_deck_the_road_is_on_is_left_alone_and_its_ends_are_met_without_a_tag(lua):
    lua.execute(VALLEY)
    lua.execute(DECK_ROAD)
    setup(lua)
    shape_it(lua)
    # (the points 100 to 140 stand under a wide collider that continues from the ground at x = 90: the road is on a deck, left alone)
    assert world(lua, 120, 0) == pytest.approx(lua.eval("GROUND(120, 0)"), abs=1e-6)
    assert world(lua, 125, 0) == pytest.approx(lua.eval("GROUND(125, 0)"), abs=1e-6)
    assert lua.eval("E:status().skipped") >= 4
    # ... and the ground follows the road we drive: at the deck's two ends it is at the deck's top, tagged or not
    top = lambda x: plane(x, 0) + 1.0
    assert world(lua, 92, 0) == pytest.approx(top(92), abs=0.05)         # (the deck begins at x = 91: the first ray that finds it is at 92)
    assert world(lua, 148, 0) == pytest.approx(top(148), abs=0.05)
    assert lua.eval("logged('2 deck ends met')")
    assert lua.eval("logged('is a deck the road is on, left alone (the ground meets its top at both ends)')")
    # (a pavement's 15 cm is no deck)
    lua.execute("DECK = function(x, y, g) return g + 0.15 end")
    lua.execute("GROUND = nil; BUMPS = { { 100, 0 } }")
    lua.execute(PRELUDE_GROUND)
    setup(lua)
    shape_it(lua)
    assert world(lua, 100, 0) == pytest.approx(plane(100, 0), abs=TOL)


PRELUDE_GROUND = ("function GROUND(x, y) local z = 100 + 0.02 * x + 0.1 * y; "
                  "for _, b in ipairs(BUMPS) do z = z + BUMP_H * math.exp(-((x - b[1]) ^ 2 + (y - b[2]) ^ 2) / 128) end; return z end")


def test_a_tagged_span_has_the_ground_meet_the_deck_at_both_ends_too(lua):
    lua.execute(VALLEY)
    lua.execute(DECK_ROAD)
    setup(lua)
    lua.execute("for i = 6, 11 do E.lines[1].skip[i] = 'tagged' end")     # the segments from x = 90 to 150 (OpenStreetMap's bridge)
    shape_it(lua)
    assert world(lua, 120, 0) == pytest.approx(lua.eval("GROUND(120, 0)"), abs=1e-6)
    top = lambda x: plane(x, 0) + 1.0
    assert world(lua, 90, 0) == pytest.approx(top(92.5), abs=0.05)
    assert world(lua, 150, 0) == pytest.approx(top(147.5), abs=0.05)
    assert lua.eval("E:status().skipped") >= 6
    assert lua.eval("logged('2 deck ends met')")
    assert lua.eval("logged('2 deck ends')")                     # (the stretches fixed: two, one at each end)
    assert lua.eval("logged(', deck end: ')")                    # ... each with its place along the line


def test_a_span_the_profile_alone_found_is_left_alone_without_its_ends_met(lua):
    # (a bridge or tunnel the height profile suggests, not OpenStreetMap: skip is true, not 'tagged')
    lua.execute(VALLEY)
    lua.execute(DECK_ROAD)
    setup(lua)
    lua.execute("for i = 6, 11 do E.lines[1].skip[i] = true end")
    shape_it(lua)
    assert world(lua, 90, 0) == pytest.approx(lua.eval("GROUND(90, 0)"), abs=1e-6)
    assert world(lua, 150, 0) == pytest.approx(lua.eval("GROUND(150, 0)"), abs=1e-6)
    assert lua.eval("TB.sets") == 0


# the two 'deck ends' a stage with no bridge had were a collider at road level beside or over the road taken for a deck (a
# retaining wall, a barrier, a rail: Earth builds them all as static colliders), and the ground was pinned to its top, raised or lowered
# with a 15 m taper, which stranded Earth's road lines.
NARROW_OVER = "DECK = function(x, y, g) if x > 91 and x < 149 and math.abs(y) < 0.6 then return g + 3 end end"
WALL_BESIDE = "DECK = function(x, y, g) if x > 91 and x < 149 and math.abs(y) >= 1 and math.abs(y) <= 3 then return g + 3 end end"


def test_a_narrow_collider_over_the_line_is_not_a_deck(lua):
    lua.execute("BUMPS = {}")
    lua.execute(NARROW_OVER)                    # (a wall, 1.2 m wide, 3 m over the ground, standing on the line)
    setup(lua)
    shape_it(lua)
    assert lua.eval("E:status().skipped") == 0 and lua.eval("TB.sets") == 0
    assert lua.eval("logged('0 deck ends met')")
    assert lua.eval("logged('not a deck: narrow')")
    # (nor an overpass: the ground is the ground, not a ridge to take out)
    assert lua.eval("logged('0 under an overpass')") and lua.eval("E:status().stretches") == 0


def test_a_barrier_or_wall_beside_the_line_is_not_a_deck_end(lua):
    lua.execute("BUMPS = {}")
    lua.execute(WALL_BESIDE)                    # (the ray along the line finds the ground: nothing stands on it)
    setup(lua)
    shape_it(lua)
    assert lua.eval("E:status().skipped") == 0 and lua.eval("TB.sets") == 0
    # ... also at the end of a tagged span: the ground is not pinned to a wall beside the road
    lua.execute(VALLEY)
    lua.execute(WALL_BESIDE)
    setup(lua)
    lua.execute("for i = 6, 11 do E.lines[1].skip[i] = 'tagged' end")
    shape_it(lua)
    assert world(lua, 90, 0) == pytest.approx(lua.eval("GROUND(90, 0)"), abs=1e-6)
    assert world(lua, 150, 0) == pytest.approx(lua.eval("GROUND(150, 0)"), abs=1e-6)
    assert lua.eval("logged('0 deck ends met')")


def test_a_narrow_collider_at_a_tagged_spans_end_is_not_a_deck_to_pin_to(lua):
    lua.execute(VALLEY)
    lua.execute("DECK = function(x, y, g) if x > 91 and x < 149 and math.abs(y) < 0.6 then return 100 + 0.02 * x + 0.1 * y + 1.0 end end")
    setup(lua)
    lua.execute("for i = 6, 11 do E.lines[1].skip[i] = 'tagged' end")
    shape_it(lua)
    assert world(lua, 90, 0) == pytest.approx(lua.eval("GROUND(90, 0)"), abs=1e-6)


# A ditch under the road, 3.5 m deep from x = 98 to 122, and (when a deck is over it) the deck 0.5 m over the road: the points 100 to 120.
DITCH = ("function GROUND(x, y) local z = 100 + 0.02 * x + 0.1 * y; if x > 95 and x < 125 then "
         "local t = math.min((x - 95) / 3, (125 - x) / 3, 1); z = z - 3.5 * t * t * (3 - 2 * t) end; return z end")


def test_a_wide_collider_that_is_too_short_or_jumps_is_not_a_deck(lua):
    lua.execute("BUMPS = {}")
    lua.execute(DITCH)
    # (one point of the line only: a few metres of something wide over a ditch)
    lua.execute("DECK = function(x, y, g) if x > 95 and x < 105 then return 100 + 0.02 * x + 0.1 * y + 0.5 end end")
    setup(lua)
    shape_it(lua)
    assert lua.eval("E:status().skipped") == 0 and lua.eval("logged('0 deck ends met')")      # (the ditch is a dip of the data, filled as one)
    assert lua.eval("logged('not a deck: shorter than')")
    # (a top that jumps from one point to the next, a taller structure on the road: buildings, not a road's deck)
    lua.execute("DECK = function(x, y, g) if x > 95 and x < 125 then return 100 + 0.02 * x + 0.1 * y + 0.5 + 3.5 * (math.floor(x / 10) % 2) end end")
    setup(lua)
    shape_it(lua)
    assert lua.eval("E:status().skipped") == 0 and lua.eval("logged('0 deck ends met')")
    assert lua.eval("logged('not a deck: its top steps')")


def test_the_log_says_where_each_stretch_fixed_is(lua):
    setup(lua)                                  # (3 m spikes at x = 100 and 180 on the line from x = 40: 60 and 140 m along it)
    shape_it(lua)
    mine = [str(lua.eval("LOGS[%d]" % i)) for i in range(1, int(lua.eval("#LOGS")) + 1)]
    stretches = [l for l in mine if "shaping: stretch " in l]
    assert len(stretches) == 2
    for l, along in zip(stretches, (60, 140)):
        m = re.search(r"stretch (\d+), spike: (\d+) to (\d+) m along line 1, largest change ([+-][\d.]+) m at x (\d+), y (-?\d+)", l)
        assert m, l
        assert int(m.group(2)) < along < int(m.group(3)) and float(m.group(4)) < -2
        assert int(m.group(5)) in (along + 30, along + 40, along + 50) and int(m.group(6)) == 0
    # and the colliders over the line are told with where they are
    lua.execute("BUMPS = {}")
    lua.execute(NARROW_OVER)
    setup(lua)
    shape_it(lua)
    assert lua.eval("logged('a collider 3.0 m over the ground at ')") and lua.eval("logged('m along line 1 (x ')")


def test_a_bump_under_the_spike_limit_is_left_and_an_edition_may_set_its_own_limit(lua):
    lua.execute("BUMP_H = 1.8")                 # (a 1.8 m bump over a 10 m step: its departure from the trend is 1.7 m, under SPIKE_M)
    setup(lua)
    shape_it(lua)
    assert lua.eval("TB.sets") == 0 and lua.eval("shape.SPIKE_M") >= 2
    setup(lua, line="(function() local l = roadLine(40, 216, 110); l.spikeM = 1.2; return l end)()")      # (the edition's limit, legs.json "shaping")
    shape_it(lua)
    assert lua.eval("TB.sets") > 0
    lua.execute("BUMP_H = 3")
    setup(lua, line="(function() local l = roadLine(40, 216, 110); l.spikeM = 5; return l end)()")
    shape_it(lua)
    assert lua.eval("TB.sets") == 0                                                                   # (a higher one leaves the 3 m bumps)


# An overpass above the road: the A25 viaduct over the Svolte di Popoli's SR17. The elevation data has its deck as a 6 m ridge
# across the road, wider than the deck, and the game's collider of the deck stands 10 m above the road.
RIDGE = ("function GROUND(x, y) return 100 + 0.02 * x + 0.1 * y + 6 * math.exp(-((x - 120) ^ 2) / 200) * math.exp(-(y ^ 2) / 450) end")
OVERPASS = "DECK = function(x, y, g) if x >= 100 and x <= 140 then return 100 + 0.02 * x + 0.1 * y + 10 end end"


def test_an_overpass_above_the_line_does_not_stop_the_shaping_and_the_ridge_under_it_is_smoothed(lua):
    lua.execute(RIDGE)
    lua.execute(OVERPASS)
    setup(lua, line="roadLine(40, 216, 110)")
    shape_it(lua)
    st = lua.eval("E:status()")
    assert st.skipped == 0                                           # (nothing is a deck: the road runs under it)
    # the ridge is gone from the road: the straight line between the ground beyond the overpass's pad (the ridge is 6 m: more than the
    # cap of a road on the ground, 4 m, the overpass's own is more)
    assert lua.eval("shape.OVERPASS_CHANGE_M") >= 6
    for x in (100, 110, 120, 130, 140):
        assert world(lua, x, 0) == pytest.approx(plane(x, 0), abs=0.15), x
    # as a delta: the cells across the road move by what the line moves, the cross slope and the ridge's fall across are Earth's
    d = world(lua, 120, 0) - lua.eval("GROUND(120, 0)")
    assert d == pytest.approx(-6, abs=0.15)
    for dy in (-5, -3, 3, 5):
        assert world(lua, 120, dy) - lua.eval("GROUND(120, %d)" % dy) == pytest.approx(d, abs=1e-6)
    assert lua.eval("logged('1 stretch of the line fixed (1 under an overpass, 0 spikes, 0 deck ends)')")
    assert lua.eval("logged('segments under an overpass')")
    assert lua.eval("logged(', overpass: ')")                                          # (told with its place along the line and the world)
    assert lua.eval("logged('over the ground at ')") and lua.eval("logged('is an overpass: the road runs under it')")
    # the pad (20 m past the overpass's 100 to 140) is the stretch, and nothing is written past it and the falloff
    x0, x1, y0, y1 = extent(lua)
    assert 70 - CORE - BLEND - 1 <= x0 and x1 <= 170 + CORE + BLEND + 1
    assert world(lua, 190, 0) == pytest.approx(lua.eval("GROUND(190, 0)"), abs=1e-9)
    assert st.stretches == 1 and 3 < st.fixLength < 130
    # beyond the pad the ordinary 4 m cap holds
    assert lua.eval("shape.MAX_CHANGE_M") < lua.eval("shape.OVERPASS_CHANGE_M")


def test_an_overpass_ridge_bigger_than_the_overpass_cap_is_held_at_the_cap(lua):
    lua.execute("function GROUND(x, y) return 100 + 0.02 * x + 0.1 * y + 12 * math.exp(-((x - 120) ^ 2) / 200) * math.exp(-(y ^ 2) / 450) end")
    lua.execute("DECK = function(x, y, g) if x >= 100 and x <= 140 then return 100 + 0.02 * x + 0.1 * y + 16 end end")
    setup(lua, line="roadLine(40, 216, 110)")
    shape_it(lua)
    cap = lua.eval("shape.OVERPASS_CHANGE_M")
    st = lua.eval("E:status()")
    assert st.maxChange == pytest.approx(cap, abs=1e-9) and st.capped > 0
    assert world(lua, 120, 0) - plane(120, 0) == pytest.approx(12 - cap, abs=0.15)      # (the ridge, less the cap)
    assert lua.eval("logged('road core was asked to move 12')")


def test_the_same_ridge_with_no_collider_over_it_is_the_ground_and_taken_out_as_a_spike_only_as_far_as_the_cap_allows(lua):
    lua.execute(RIDGE)
    lua.execute("DECK = nil")
    setup(lua, line="roadLine(40, 216, 110)")
    shape_it(lua)
    cap = lua.eval("shape.MAX_CHANGE_M")
    assert abs(world(lua, 120, 0) - lua.eval("GROUND(120, 0)")) <= cap + 1e-6
    assert lua.eval("E:status().skipped") == 0


def test_a_deck_that_does_not_continue_from_the_ground_at_either_end_is_an_overpass_not_a_deck(lua):
    # (a collider 5 m over the road is one a car passes under: the deck's top is 5 m over the ground where it begins)
    lua.execute("BUMPS = {}")
    lua.execute("DECK = function(x, y, g) if x >= 100 and x <= 140 then return g + 5 end end")
    setup(lua)
    shape_it(lua)
    assert lua.eval("E:status().skipped") == 0
    # ... and where no end can be told (the stretch begins at the line's start and ends at its end) it stays a deck, as it was
    lua.execute("DECK = function(x, y, g) return g + 5 end")
    setup(lua)
    shape_it(lua)
    assert lua.eval("E:status().skipped") >= 15


def test_a_run_ending_at_the_deck_whose_height_cannot_be_measured_keeps_the_ground(lua):
    # (the colliders of Earth's decks exist near the car only: the ray finds the terrain, the deck's height is not known, no pin)
    lua.execute(VALLEY)
    lua.execute("DECK = function(x, y, g) if x > 91 and x < 149 and g < 97 then return 100 + 0.02 * x + 1.0 end end")
    setup(lua)
    shape_it(lua)
    assert world(lua, 90, 0) == pytest.approx(plane(90, 0), abs=0.05)


# a short bridge over a gully 3.5 m deep that OpenStreetMap does not tag (the culvert of the SR17), on a 6 percent grade with a slight
# hump, 0.5 m over the road where it lands. The road runs on to it and off it: the ground meets the deck's top at both ends with a ramp that keeps the
# curated road's grade change (curate.MAX_GRADE_CHANGE), and the gully under the deck is left as it is.
GRADE6 = ("function GROUND(x, y) local z = 100 + 0.06 * x + 0.1 * y; if x > 95 and x < 125 then "
          "local t = math.min((x - 95) / 8, (125 - x) / 8, 1); z = z - 3.5 * t * t * (3 - 2 * t) end; return z end")
HUMP_DECK = ("DECK = function(x, y, g) if x > 96 and x < 124 then return 100 + 0.06 * x + 0.1 * y + 0.5 + 0.3 * math.sin(math.pi * (x - 96) / 28) end end")


def bridge_top(x):
    return 100 + 0.06 * x + 0.5 + 0.3 * math.sin(math.pi * (x - 96) / 28)


def grade_change(lua, centres, step=10):
    """The largest change of grade per metre of the shaped road along y = 0 at these points, from the heights `step` metres either side."""
    worst = 0.0
    for x in centres:
        a, b, c = (world(lua, x + k * step, 0) for k in (-1, 0, 1))
        worst = max(worst, abs(a - 2 * b + c) / (step * step))
    return worst


def test_an_untagged_short_bridge_over_a_gully_is_a_deck_and_both_its_ends_are_met_smoothly(lua):
    lua.execute("BUMPS = {}")
    lua.execute(GRADE6)
    lua.execute(HUMP_DECK)
    setup(lua, line="roadLine(40, 220, 110)")
    shape_it(lua)
    limit = lua.eval("require('beamngDakar/curate').MAX_GRADE_CHANGE")
    # it is a deck our road is on (not an overpass, not a spike to fill): the gully under it is as Earth made it
    assert lua.eval("logged('is a deck the road is on, left alone (the ground meets its top at both ends)')")
    assert not lua.eval("logged('is an overpass')")
    assert lua.eval("E:status().skipped") >= 3
    for x in (105, 110, 115, 120):
        assert world(lua, x, 0) == pytest.approx(lua.eval("GROUND(%d, 0)" % x), abs=1e-6), x
    # both ends met: the ground is at the deck's top where the deck begins (the first ray finds it at x = 97 and ends at 123) ...
    assert world(lua, 97, 0) == pytest.approx(bridge_top(97), abs=0.1)
    assert world(lua, 123, 0) == pytest.approx(bridge_top(123), abs=0.1)
    assert lua.eval("logged('2 deck ends met')") and lua.eval("logged(', deck end: ')")
    # ... with no step, and the road's own grade change kept on the approach and on the exit (the ramps are a smoothstep, as long as it asks)
    assert grade_change(lua, range(40, 86, 5)) <= limit * 1.05
    assert grade_change(lua, range(135, 196, 5)) <= limit * 1.05
    # the ground is moved only as far as the deck stands over it where it begins (a ramp over 40 m or more, not a cliff at the end)
    for x0, x1 in ((60, 94), (126, 160)):
        zs = [world(lua, x0 + k, 0) - lua.eval("GROUND(%d, 0)" % (x0 + k)) for k in range(0, x1 - x0 + 1)]
        assert max(abs(b - a) for a, b in zip(zs, zs[1:])) < 0.1                    # (the delta changes by centimetres a metre)
    # nothing is moved far from the deck's ends: the road beyond the ramps is as Earth made it, and the cap is not reached
    assert world(lua, 20, 0) == pytest.approx(lua.eval("GROUND(20, 0)"), abs=1e-9)
    assert world(lua, 200, 0) == pytest.approx(lua.eval("GROUND(200, 0)"), abs=1e-9)
    assert lua.eval("E:status().capped") == 0 and not lua.eval("logged('held at the cap')")


def test_the_ramp_to_a_deck_is_as_long_as_the_grade_change_limit_asks(lua):
    rl = lua.eval("require('beamngDakar/curate').MAX_GRADE_CHANGE")
    # (a smoothstep of height d over L has a grade change of 6 d / L^2: a deck a metre over the road, on a ground that falls away at its end, needs 40 m of ramp or more, not 15)
    lua.execute("BUMPS = {}")
    lua.execute(GRADE6)
    lua.execute("DECK = function(x, y, g) if x > 96 and x < 124 then return 100 + 0.06 * x + 0.1 * y + 1.0 end end")
    setup(lua, line="roadLine(40, 220, 110)")
    shape_it(lua)
    assert lua.eval("logged('2 deck ends met')")
    assert grade_change(lua, range(30, 86, 5)) <= rl * 1.05
    assert grade_change(lua, range(135, 196, 5)) <= rl * 1.05
    assert abs(world(lua, 60, 0) - lua.eval("GROUND(60, 0)")) > 0.01      # (the ramp is still going on 37 m before the deck)
    assert world(lua, 20, 0) == pytest.approx(lua.eval("GROUND(20, 0)"), abs=1e-9)


def test_an_overpass_over_a_gully_road_is_still_an_overpass_not_a_deck(lua):
    # (a collider 6 m over the road, over the same gully: its top does not continue from the road at either end)
    lua.execute("BUMPS = {}")
    lua.execute(GRADE6)
    lua.execute("DECK = function(x, y, g) if x > 96 and x < 124 then return 100 + 0.06 * x + 0.1 * y + 6 end end")
    setup(lua, line="roadLine(40, 220, 110)")
    shape_it(lua)
    assert lua.eval("logged('is an overpass: the road runs under it')")
    assert lua.eval("logged('0 deck ends met')")
    assert lua.eval("E:status().skipped") == 0


def test_a_wall_beside_the_road_over_a_gully_does_not_make_a_deck_or_an_end(lua):
    lua.execute("BUMPS = {}")
    lua.execute(GRADE6)
    # a wall 3 m over the ground beside the road (not on the line, 1 to 3 m off it) along a gully: neither a deck nor an end to meet
    lua.execute("DECK = function(x, y, g) if x > 96 and x < 124 and math.abs(y) >= 1 and math.abs(y) <= 3 then return g + 3 end end")
    setup(lua, line="roadLine(40, 220, 110)")
    shape_it(lua)
    assert lua.eval("E:status().skipped") == 0


def test_the_rays_are_only_cast_near_the_car(lua):
    # (a ray a point of the line was most of the plan's cost, and the colliders of Earth's decks exist near the car only)
    lua.execute("NRAYS = 0; DECK = function(x, y, g) NRAYS = NRAYS + 1 end; PLAYER = { x = -580, y = 0 }")
    setup(lua, line="roadLine(-580, 580, 110)", px=-600, py=-600, n=1200)
    shape_it(lua)
    n = lua.eval("NRAYS")
    assert 40 < n < 60                                                 # (the points within 450 m of the car, of the 117 of the line)
    lua.execute("NRAYS = 0; PLAYER = { x = 5000, y = 5000 }")
    setup(lua, line="roadLine(-580, 580, 110)", px=-600, py=-600, n=1200)
    shape_it(lua)
    assert lua.eval("NRAYS") == 0                                      # (a car far away: no ray)


def test_the_blocks_rim_is_left_alone(lua):
    lua.execute("BUMPS = { { 200, 0 }, { 252, 0 } }")
    setup(lua, line="roadLine(150, 255, 110)")                         # to the block's own edge (256 samples)
    shape_it(lua)
    bumps = [(200, 0), (252, 0)]
    rim = lua.eval("shape.EDGE_CELLS")
    assert 255 - rim < 252
    assert world(lua, 252, 0) == pytest.approx(ground(252, 0, bumps), abs=1e-6)
    assert world(lua, 200, 0) == pytest.approx(road(200), abs=TOL)


def test_a_grid_that_does_not_match_the_ground_is_not_written(lua):
    setup(lua, flip=True, py=-60)                                      # the rows run the other way than we think
    shape_it(lua)
    assert lua.eval("TB.sets") == 0
    st = lua.eval("E:status()")
    assert str(st.state).startswith("unverified")
    logs = lua.eval("LOGS")
    assert any("nothing written" in str(logs[i]) for i in range(1, len(logs) + 1))


def test_a_stretch_outside_the_block_does_nothing(lua):
    setup(lua, line="roadLine(2000, 2200, 110)")
    shape_it(lua)
    assert lua.eval("TB.sets") == 0
    assert lua.eval("E:status().state") == "no stage in this block"


def test_without_a_block_there_is_nothing_to_do_and_nothing_fails(lua):
    setup(lua)
    lua.execute("CURRENT = nil; E:update()")
    assert lua.eval("TB.sets") == 0


def test_the_falloff_weight(lua):
    w = lua.eval("shape.weight")
    assert w(0, 6.5, 12) == 1 and w(6.5, 6.5, 12) == 1 and w(18.5, 6.5, 12) == 0 and w(30, 6.5, 12) == 0
    assert w(12.5, 6.5, 12) == pytest.approx(0.5)
    assert all(w(6.5 + k * 0.5, 6.5, 12) >= w(6.5 + (k + 1) * 0.5, 6.5, 12) for k in range(24))


def test_two_close_passes_of_a_road_blend_instead_of_stepping(lua):
    # a hairpin: two passes of the road 12 m apart; the ground between is a ramp, not a cliff
    lua.execute("CLOCK_STEP = 0.0005; BUMPS = { { 130, 0 }, { 130, 12 } }")
    line = "{ pts = (function() local p = {}; for x = 60, 200, 10 do p[#p+1] = { x, 0, 110 } end; " \
           "for x = 200, 60, -10 do p[#p+1] = { x, 12, 114 } end; return p end)(), skip = {} }"
    setup(lua, line=line)
    shape_it(lua)
    zs = [world(lua, 130, y) for y in range(-6, 19)]
    assert zs[6] == pytest.approx(road(130, 0), abs=0.5)              # the lower pass is where it should be (the nearer pass counts most)
    assert zs[18] == pytest.approx(road(130, 12), abs=0.5)            # and the upper
    steps = [abs(b - a) for a, b in zip(zs, zs[1:])]
    assert max(steps) < 1.0                                           # no cliff between them


def test_an_engine_that_refuses_the_edit_switches_the_shaping_off_instead_of_failing_every_frame(lua):
    setup(lua)
    lua.execute("function TB:setHeightGrid() error('no such method') end")
    shape_it(lua)
    st = lua.eval("E:status()")
    assert str(st.state).startswith("failed") and st.enabled is False
    n = len(lua.eval("LOGS"))
    lua.execute("for i = 1, 20 do E:update() end")
    assert len(lua.eval("LOGS")) == n                                  # (said once)
    assert lua.eval("TB.sets") == 0


# The Svolte di Popoli SS1's sag at 740 to 890 m: the street dips to 283.7 m at 796 m and climbs 9 m in 50 m (20 percent), a dip and a climb
# of the street itself (the geometry's raw heights, metres along -> z). Taken for a spike, the ground there was raised by 4 m (the stretch asked for
# 4.7 m) over 780 to 859 m, under Earth's road lines. A sag is no defect of the elevation data: a road crosses it, and a gully under a
# deck (whose collider is not in the game yet, or out of the rays' reach) looks the same.
SS1_SAG = [(656, 286.42), (672, 286.46), (689, 286.22), (706, 285.85), (723, 285.32), (740, 284.94), (756, 284.4), (772, 283.97),
           (788, 283.69), (796, 283.69), (801, 283.84), (806, 284.2), (810, 284.92), (814, 285.77), (819, 286.85), (823, 288.06),
           (827, 289.25), (832, 290.42), (837, 291.58), (842, 292.41), (847, 292.95), (857, 293.69), (870, 294.62), (884, 295.57),
           (894, 296.4), (905, 297.32), (924, 299.14), (943, 300.84)]


def sag_ground(extra_dip=0.0, bump=0.0):
    """Lua for GROUND along y = 0 (x is the metres along the stage): the raw heights, on the game's ground 3.8 m above them (the log's
    readout at 739 m), a cross slope, and a gully `extra_dip` deeper at 800 m (the game's ground was 4.7 m under the stretch's chord, the raw line
    2.7 m), or a `bump` on the climb."""
    pts = ", ".join("{ %d, %.2f }" % p for p in SS1_SAG)
    return ("SAG = { %s }; function GROUND(x, y) local z; for i = 1, #SAG - 1 do local a, b = SAG[i], SAG[i + 1]; "
            "if x >= a[1] and x <= b[1] then z = a[2] + (b[2] - a[2]) * (x - a[1]) / (b[1] - a[1]) end end; "
            "z = (z or SAG[#SAG][2]) + 3.8 + 0.05 * y; z = z - %r * math.exp(-((x - 800) ^ 2) / 200); "
            "return z + %r * math.exp(-((x - 840) ^ 2 + y ^ 2) / 50) end" % (pts, extra_dip, bump))


@pytest.mark.parametrize("extra_dip", [0.0, 2.0])
def test_a_sag_the_road_dips_into_and_climbs_out_of_is_not_a_spike_and_not_one_cell_is_moved(lua, extra_dip):
    lua.execute("BUMPS = {}; DECK = nil")                                  # (no collider seen over the line)
    lua.execute(sag_ground(extra_dip))
    setup(lua, line="roadLine(660, 940, 110)", px=630, py=-64, n=340)
    lua.execute("PLAYER = { x = 600, y = 0, speed = 0 }")
    lua.execute("E.lines[1].spikeM = 3")                                   # (the Popoli edition's own limit)
    shape_it(lua)
    assert lua.eval("TB.sets") == 0 and lua.eval("sameAs(TB, SNAP)") is True
    st = lua.eval("E:status()")
    assert st.stretches == 0 and st.cells == 0
    assert lua.eval("logged('0 stretches of the line fixed')")


def test_a_bump_on_that_climb_is_still_fixed_and_the_sag_beside_it_is_not_touched(lua):
    lua.execute("BUMPS = {}; DECK = nil")
    lua.execute(sag_ground(extra_dip=2.0, bump=5.0))
    setup(lua, line="roadLine(660, 940, 110)", px=630, py=-64, n=340)
    lua.execute("PLAYER = { x = 600, y = 0, speed = 0 }")
    lua.execute("E.lines[1].spikeM = 3")
    shape_it(lua)
    assert lua.eval("E:status().stretches") == 1 and lua.eval("logged('1 spike')")
    # the bump (at 840 m) comes down; the sag at 800 m is as it was, and so is the road before it
    assert world(lua, 840, 0) < lua.eval("GROUND(840, 0)") - 3
    for x in (680, 720, 760, 790, 800, 810):
        assert world(lua, x, 0) == pytest.approx(lua.eval("GROUND(%d, 0)" % x), abs=1e-9), x


# --- the ground beside a tunnel is Earth's (it builds the tube and portals and shapes the terrain for them) --------------------------

def spiked_world(lua):
    lua.execute("BUMPS = { { 120, 0 }, { 220, 0 } }")        # (3 m spikes on the line at 120 m, beside a tunnel, and at 220 m, away from it)


def untouched_between(lua, x0, x1):
    return all(world(lua, x, y) == pytest.approx(lua.eval("GROUND(%r, %r)" % (float(x), float(y))), abs=1e-9)
               for x in range(x0, x1 + 1, 2) for y in range(-12, 13, 3))


def test_a_stretch_the_geometry_says_is_beside_a_tunnel_is_left_as_earth_made_it_and_the_spike_beyond_it_is_fixed(lua):
    spiked_world(lua)
    setup(lua, line="(function() local l = roadLine(40, 260, 110); l.keep = { [8] = true, [9] = true, [10] = true }; return l end)()")
    shape_it(lua)
    st = lua.eval("E:status()")
    assert st.stretches == 1 and st.tunnelSegments >= 2                      # (only the spike at 220 m is fixed)
    assert untouched_between(lua, 95, 145)                                     # the spike at 120 m, the ground round it, the segments either side
    assert world(lua, 220, 0) == pytest.approx(plane(220, 0), abs=0.3)         # ... and the other one is taken out as ever
    assert lua.eval("logged('segments beside a tunnel left as Earth made them')")


def test_the_tubes_earth_has_built_in_the_game_are_left_alone_too(lua):
    spiked_world(lua)
    lua.execute("TUBES = { { 110, 0, 100 }, { 130, 0, 100 } }")
    setup(lua, line="roadLine(40, 260, 110)")
    lua.execute("E.env.tunnels = function() return TUBES end; E:setLines({ roadLine(40, 260, 110) })")
    shape_it(lua)
    assert untouched_between(lua, 85, 155)
    assert world(lua, 220, 0) == pytest.approx(plane(220, 0), abs=0.3)
    assert lua.eval("E:status().tunnelSegments") >= 2
    # no tunnel in the game: the same stretch is fixed
    lua.execute("TUBES = {}")
    setup(lua, line="roadLine(40, 260, 110)")
    lua.execute("E.env.tunnels = function() return TUBES end; E:setLines({ roadLine(40, 260, 110) })")
    shape_it(lua)
    assert world(lua, 120, 0) == pytest.approx(plane(120, 0), abs=0.3)


def test_the_same_stages_set_again_on_a_shaped_block_are_not_a_new_job(lua):
    lua.execute("BUMPS = { { 100, 0 } }")
    setup(lua)
    shape_it(lua)
    sets, jobs = lua.eval("TB.sets"), lua.eval("E:status().jobs")
    lua.execute("E:setLines({ roadLine(40, 216, 110) })")                        # (a new table, the same stretches)
    assert not lua.eval("E:busy()")
    shape_it(lua)
    assert lua.eval("TB.sets") == sets and lua.eval("E:status().jobs") == jobs
    lua.execute("E:setLines({ roadLine(40, 216, 111) })")                        # (the heights of the line are not what the ground is shaped to)
    assert not lua.eval("E:busy()")
    lua.execute("E:setLines({ roadLine(40, 226, 110) })")                        # (another line: shaped again)
    assert lua.eval("E:busy()")
    shape_it(lua)
    assert lua.eval("E:status().jobs") == jobs + 1 and lua.eval("TB.sets") >= sets


def test_the_shaping_reaches_the_road_and_a_short_taper_and_nothing_wider(lua):
    # (the user's rule: only the road the player races on, and the ground under it: a 10 m road, half a metre of margin, and a taper to blend in)
    assert lua.eval("shape.CORE_M") == CORE and lua.eval("shape.BLEND_M") == BLEND
    assert CORE == pytest.approx(lua.eval("require('beamngDakar/legs').ROAD_WIDTH") / 2 + 0.5)
    assert CORE + BLEND < 12
