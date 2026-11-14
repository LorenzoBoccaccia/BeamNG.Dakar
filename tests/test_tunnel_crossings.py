"""A road that crosses over a tunnel (crossing.lua, shape.lua): BeamNG.Earth leaves the raw ground over a covered tunnel's strip, which also undoes the grading of the
road laid across it; the ground there is laid to the road, from what the game has at runtime (the tunnels Earth built, the terrain block), whatever the
option for the general shaping says. Portals and a road that runs into a tunnel are left as Earth made them.

    python -m pytest tests

Two worlds: a synthetic one (a flat road across a hill's strip over a tunnel) and the Popoli stage's A25 crossings, on Earth 1.9.0's built terrain block of the
session where the road could not be driven (tests/data/popoli_a25_crossings.json: the 1 m terrain heights in cm, the stage line, the bores' axes every 20 m
like the tunnel points the game has, and the road's plan: the grading without the keep, which is what Earth draws the lane lines on).
"""
import json
import math
import os

import pytest

lupa = pytest.importorskip("lupa")

from test_shape import lua, shape_it  # noqa: E402,F401  (the stub block and the engine)

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "data", "popoli_a25_crossings.json")


# --- a synthetic crossing ----------------------------------------------------------------------------------------------------------------------------------------------
# The road runs along y = 0 and rises 2 cm a metre; the grading is the plan everywhere (level across the carriageway, 100 + 0.02 x) but over the tunnel's strip, the
# raw hill: 18.4 m wide (x 140.8 to 159.2 for the tunnel's axis along y at x = 150), 40 cm over the plan at the road and sloping 0.25 across it.
STRIP_WORLD = r"""
AXIS_X, STRIP_HALF = 150, 9.2
BUMPS = {}
function bumps(x, y)
  local z = 0
  for _, b in ipairs(BUMPS) do z = z + BUMP_H * math.exp(-((x - b[1]) ^ 2 + (y - b[2]) ^ 2) / 128) end
  return z
end
function GROUND(x, y)
  local z = 100 + 0.02 * x + bumps(x, y)
  if math.abs(x - AXIS_X) <= STRIP_HALF then z = z + 0.4 + 0.25 * y end
  return z
end
function PLAN(x, y) return 100 + 0.02 * x end
function lights(x0, y0, x1, y1)        -- (a tunnel's points every 20 m from 10 m inside its ends, as the game's tunnel lights)
  local out, L = {}, math.sqrt((x1 - x0) ^ 2 + (y1 - y0) ^ 2)
  local t = 10
  while t <= L - 10 + 1e-6 do out[#out + 1] = { x0 + (x1 - x0) * t / L, y0 + (y1 - y0) * t / L, 95 }; t = t + 20 end
  return out
end
function crossSetup(tubes, enabled, line)
  TUBES = tubes
  TB = newBlock({}); SNAP = snapshot(TB); E = newEngine(TB)
  E.env.tunnels = function() return TUBES end
  E:setEnabled(enabled)
  E:setLines({ line or roadLine(40, 216, 110) })
end
function levelErr(x0, x1)              -- the largest distance of the block from the road's plan over the carriageway, x0..x1
  local worst = 0
  for x = x0, x1 do for y = -4, 4 do worst = math.max(worst, math.abs(worldAt(TB, x, y) - PLAN(x, y))) end end
  return worst
end
"""


@pytest.fixture()
def hill(lua):
    lua.execute(STRIP_WORLD)
    return lua


def crossing_tunnel(lua, y0=-200, y1=200, x=150):
    return "lights(%d, %d, %d, %d)" % (x, y0, x, y1)


def test_the_raw_ground_of_a_crossing_over_a_tunnel_is_laid_to_the_road(hill):
    lua = hill
    assert lua.eval("(function() crossSetup({}, true); return levelErr(142, 158) end)()") > 1.0     # (the control: the hill runs through the carriageway)
    lua.execute("crossSetup(%s, true)" % crossing_tunnel(lua))
    shape_it(lua)
    assert lua.eval("E:status().crossings") == 1 and lua.eval("logged('a road across a tunnel at 90 degrees')")
    assert lua.eval("levelErr(142, 158)") < 0.08                                   # level across, at the road's own height
    # beyond the strip nothing moved, and not beside the road past its footprint and taper (11.5 m)
    assert lua.eval("(function() for x = 100, 139 do for y = -13, 13 do if worldAt(TB, x, y) ~= GROUND(x, y) then return false end end end return true end)()")
    assert lua.eval("(function() for x = 142, 158 do for y = 12, 20 do if worldAt(TB, x, y) ~= GROUND(x, y) then return false end end end return true end)()")
    assert lua.eval("E:status().maxChange") <= 6.0


def test_the_crossing_is_repaired_with_the_shaping_option_off_and_nothing_else_is(hill):
    lua = hill
    lua.execute("BUMPS = { { 70, 0 } }")                                          # (a 3 m spike away from the tunnel: the option's own work)
    lua.execute("crossSetup(%s, false)" % crossing_tunnel(lua))
    spike_before = lua.eval("worldAt(TB, 70, 0)")
    shape_it(lua)
    assert lua.eval("levelErr(142, 158)") < 0.08                                   # the crossing: laid to the road
    assert lua.eval("worldAt(TB, 70, 0)") == pytest.approx(spike_before, abs=1e-9)  # the spike: left, the option is off
    assert lua.eval("E:status().stretches") == 1 and lua.eval("E:status().crossings") == 1
    assert lua.eval("logged('the option is off; the ground where the stage crosses over a tunnel is still laid')")
    # the same with the option on: the spike goes too
    lua.execute("crossSetup(%s, true)" % crossing_tunnel(lua))
    shape_it(lua)
    assert lua.eval("worldAt(TB, 70, 0)") < spike_before - 2.5 and lua.eval("levelErr(142, 158)") < 0.08


def test_switching_the_option_off_puts_the_shaping_back_but_keeps_the_crossing(hill):
    lua = hill
    lua.execute("BUMPS = { { 70, 0 } }")
    lua.execute("crossSetup(%s, true)" % crossing_tunnel(lua))
    shape_it(lua)
    shaped = lua.eval("worldAt(TB, 70, 0)")
    lua.execute("E:setEnabled(false)")
    shape_it(lua)
    assert lua.eval("worldAt(TB, 70, 0)") > shaped + 2.5                           # the spike is back
    assert lua.eval("levelErr(142, 158)") < 0.08                                   # the road across the tunnel is still laid
    lua.execute("E:setEnabled(true)")
    shape_it(lua)
    assert lua.eval("worldAt(TB, 70, 0)") == pytest.approx(shaped, abs=1e-6)


def test_the_option_off_with_no_tunnel_near_writes_nothing_and_ends_idle(hill):
    lua = hill
    lua.execute("BUMPS = { { 70, 0 } }")
    lua.execute("crossSetup({}, false)")
    assert not lua.eval("E:busy()")
    shape_it(lua)
    assert lua.eval("TB.sets") == 0


def test_a_tunnel_that_shows_up_later_is_planned_when_it_does(hill):
    # (the road tile with the tunnel comes after the terrain: the lines were set with none in the game)
    lua = hill
    lua.execute("crossSetup({}, false)")
    shape_it(lua)
    assert lua.eval("TB.sets") == 0
    lua.execute("TUBES = %s" % crossing_tunnel(lua))
    lua.execute("T = T + 5000")
    lua.execute("E:update()")
    shape_it(lua)
    assert lua.eval("levelErr(142, 158)") < 0.08


def test_without_a_tunnel_in_the_game_a_tilted_road_is_not_touched(hill):
    # the same terrain, no tunnel: nothing says it is a crossing, so the ground is not laid (a plan that is tilted for another reason stays)
    lua = hill
    lua.execute("crossSetup({}, true)")
    shape_it(lua)
    assert lua.eval("TB.sets") == 0


@pytest.mark.parametrize("name, tube", [
    ("a road running into the tunnel's portal: the tunnel ends at the road", "lights(150, 0, 150, 200)"),
    ("a road along the tunnel", "lights(60, 3, 240, 3)"),
    ("a crossing 25 m from a portal", "lights(150, -25, 150, 200)"),
])
def test_a_road_that_enters_a_tunnel_or_crosses_at_its_portal_is_left_as_earth_made_it(hill, name, tube):
    lua = hill
    lua.execute("crossSetup(%s, true)" % tube)
    shape_it(lua)
    assert lua.eval("E:status().crossings") == 0, name
    assert lua.eval("TB.sets") == 0, name
    assert lua.eval("levelErr(142, 158)") > 1.0                                    # (the hill is still through the carriageway: Earth's)


def test_a_crossing_well_inside_the_tunnel_is_one_and_a_slanting_one_too(hill):
    lua = hill
    lua.execute("crossSetup(%s, true)" % "lights(150, -70, 150, 200)")           # (the portal 70 m from the road)
    shape_it(lua)
    assert lua.eval("E:status().crossings") == 1
    # a tunnel at 60 degrees to the road: its strip is as wide, the stretch of the road in it longer
    lua.execute("""
AXIS_X = 150
GROUND = function(x, y)
  local z = 100 + 0.02 * x
  local d = math.abs((x - 150) * math.sin(math.rad(60)) - y * math.cos(math.rad(60)))     -- the distance from the axis through (150, 0), direction (cos 60, sin 60)
  if d <= 9.2 then z = z + 0.4 + 0.25 * y end
  return z
end
local c, s = math.cos(math.rad(60)), math.sin(math.rad(60))
crossSetup(lights(150 - 200 * c, -200 * s, 150 + 200 * c, 200 * s), true)
""")
    shape_it(lua)
    assert lua.eval("E:status().crossings") == 1
    assert lua.eval("E:status().crossAt[1].angle") == pytest.approx(60, abs=1.5)
    assert lua.eval("levelErr(140, 160)") < 0.1


def test_a_bridge_deck_the_road_is_on_over_a_tunnel_is_not_laid(hill):
    lua = hill
    lua.execute("""
crossSetup(%s, true, (function() local l = roadLine(40, 216, 110); for i = 1, #l.pts - 1 do l.skip[i] = true end; return l end)())
""" % crossing_tunnel(lua))
    shape_it(lua)
    assert lua.eval("E:status().crossings") == 0 and lua.eval("TB.sets") == 0


def test_two_bores_side_by_side_are_two_tunnels_and_each_is_crossed(hill):
    lua = hill
    lua.execute("""
GROUND = function(x, y)
  local z = 100 + 0.02 * x
  if math.abs(x - 150) <= 9.2 or math.abs(x - 178) <= 9.2 then z = z + 0.4 + 0.25 * y end
  return z
end
local t = lights(150, -200, 150, 200)
for _, p in ipairs(lights(178, -200, 178, 200)) do t[#t + 1] = p end
crossSetup(t, true)
""")
    shape_it(lua)
    assert lua.eval("E:status().crossings") == 2
    assert lua.eval("levelErr(142, 158)") < 0.1 and lua.eval("levelErr(170, 186)") < 0.1


# --- the Popoli stage's crossings of the A25 ---------------------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def popoli(lua):
    fx = json.load(open(FIXTURE, encoding="utf-8"))
    n, x0, y0 = fx["n"], fx["x0"], fx["y0"]
    lua.execute("""
function setupPopoli(cm, n, x0, y0, line, tubes)
  POP = { cm = cm, n = n, x0 = x0, y0 = y0 }
  function GROUND(x, y)
    local fx, fy = x - x0, y - y0
    if fx < 0 then fx = 0 elseif fx > n - 1.001 then fx = n - 1.001 end
    if fy < 0 then fy = 0 elseif fy > n - 1.001 then fy = n - 1.001 end
    local i, j = math.floor(fx), math.floor(fy)
    local tx, ty = fx - i, fy - j
    local function h(a, b) return cm[b * n + a + 1] / 100 end
    return (h(i, j) * (1 - tx) + h(i + 1, j) * tx) * (1 - ty) + (h(i, j + 1) * (1 - tx) + h(i + 1, j + 1) * tx) * ty
  end
  TUBES = tubes
  TB = newBlock({ px = x0, py = y0, n = n, pz = 200 }); SNAP = snapshot(TB)
  PLAYER = { x = line[1][1], y = line[1][2] }
  E = newEngine(TB)
  E.env.tunnels = function() return TUBES end
  local pts = {}
  for i, p in ipairs(line) do pts[i] = { p[1], p[2], 0 } end
  LINE = { pts = pts, skip = {} }
end
""")
    cm = lua.table_from(fx["cm"])
    line = lua.table_from([lua.table_from(p) for p in fx["line"]])
    tubes = []
    for pts in fx["tunnels"].values():
        tubes.extend(pts)
    tubes_lua = lua.table_from([lua.table_from([p[0], p[1], 90.0]) for p in tubes])
    lua.globals().setupPopoli(cm, n, x0, y0, line, tubes_lua)
    return lua, fx


def plan_errors(lua, fx, core=4):
    """The block's height minus the road's plan at the plan samples within `core` m of the line: (largest |error|, the number of samples over 0.3 m)."""
    worst, over = 0.0, 0
    for x, y, z, s, o in fx["plan"]:
        if abs(o) <= core:
            e = abs(lua.eval("worldAt(TB, %r, %r)" % (float(round(x)), float(round(y)))) - z)
            # (vertices at whole metres: the plan sample's neighbour; the plan changes a few cm a metre)
            worst, over = max(worst, e), over + (e > 0.3)
    return worst, over


def test_the_four_a25_crossings_of_the_popoli_stage_are_found_and_laid_to_the_road(popoli):
    lua, fx = popoli
    lua.execute("E:setLines({ LINE })")
    before = plan_errors(lua, fx)
    assert before[0] > 1.0 and before[1] > 100                                     # (the hill through the road: lane edges 1 to 2 m off)
    shape_it(lua)
    st = lua.eval("E:status()")
    assert st.crossings == 4
    found = sorted((st.crossAt[i].s0, st.crossAt[i].s1) for i in range(1, 5))
    # the angle and the place along the tunnel
    for i in range(1, 5):
        assert 65 <= st.crossAt[i].angle <= 90 and st.crossAt[i].arcM >= 30, i
        assert 8 <= st.crossAt[i].strip <= 12, i                                   # (the tunnel's half width and 5 m: 9.2 m)
    assert len(found) == 4
    after = plan_errors(lua, fx)
    assert after[0] < 0.25 and after[1] == 0, after                                 # (the plan's own few cm; was 1 to 2 m)
    assert st.maxChange <= 6.0 and st.maxChange > 3.0                              # (up to 4.3 m on the data: more than the shaping's own 4 m cap)
    assert lua.eval("logged('a road across a tunnel at')")


def test_the_four_crossings_are_laid_with_the_shaping_option_off(popoli):
    lua, fx = popoli
    lua.execute("E:setEnabled(false); E:setLines({ LINE })")
    shape_it(lua)
    assert lua.eval("E:status().crossings") == 4
    after = plan_errors(lua, fx)
    assert after[0] < 0.25 and after[1] == 0, after
    assert lua.eval("logged('the option is off; the ground where the stage crosses over a tunnel is still laid')")


def test_nothing_but_the_strips_over_the_bores_moves_on_the_popoli_block(popoli):
    lua, fx = popoli
    lua.execute("E:setLines({ LINE })")
    shape_it(lua)
    moved = lua.eval("(function() local c = changedCells(TB, SNAP); local out = {}; for _, k in ipairs(c) do out[#out + 1] = k end; return #out end)()")
    assert 1500 < moved < 4000                                                      # (about 1100 and 1200 cells at each of the two places: both bores' crossings)
    # every moved cell is within 11.5 m of the line and within the strip (9.7 m) and its fade (3 m) of a bore
    tubes = [p for pts in fx["tunnels"].values() for p in pts]
    bad = lua.eval("""(function()
      local n, bad = TB.n, 0
      for k, v in pairs(SNAP) do
        if TB.h[k] ~= v then
          local gx, gy = k %% n, math.floor(k / n)
          local x, y = TB.px + gx * TB.sq, TB.py + gy * TB.sq
          local d = math.huge
          for i = 1, #LINE.pts - 1 do
            local a, b = LINE.pts[i], LINE.pts[i + 1]
            local dx, dy = b[1] - a[1], b[2] - a[2]
            local l2 = dx * dx + dy * dy
            local t = math.max(0, math.min(1, ((x - a[1]) * dx + (y - a[2]) * dy) / l2))
            d = math.min(d, math.sqrt((x - a[1] - dx * t) ^ 2 + (y - a[2] - dy * t) ^ 2))
          end
          if d > 11.6 then bad = bad + 1 end
        end
      end
      return bad
    end)()""".replace("%%", "%"))
    assert bad == 0 and tubes


def test_a_portal_and_a_road_entering_a_tunnel_on_the_real_data_are_not_crossings(popoli):
    lua, fx = popoli
    lua.execute("E:setLines({ LINE })")
    shape_it(lua)
    st = lua.eval("E:status()")
    where = [(st.crossAt[i].x, st.crossAt[i].y) for i in range(1, 5)]
    # the westbound bore's west end is 36 m from the first crossing: shorten the bore to the road, so that the road meets its end (a road that enters at a portal)
    west = fx["tunnels"]["29382955"]
    first = min(where, key=lambda p: min(math.hypot(p[0] - q[0], p[1] - q[1]) for q in west))
    far = max(west, key=lambda q: math.hypot(q[0] - first[0], q[1] - first[1]))
    reach = math.hypot(first[0] - far[0], first[1] - far[1])
    kept = [q for q in west if math.hypot(q[0] - far[0], q[1] - far[1]) < reach - 12]
    assert 0 < len(kept) < len(west)
    lua.globals().TUBES = lua.table_from([lua.table_from([p[0], p[1], 90.0]) for p in fx["tunnels"]["29382785"] + kept])
    lua.execute("TB = newBlock({ px = POP.x0, py = POP.y0, n = POP.n, pz = 200 }); SNAP = snapshot(TB); E = newEngine(TB); "
                "E.env.tunnels = function() return TUBES end; E:setLines({ LINE })")
    shape_it(lua)
    st = lua.eval("E:status()")
    left = [(st.crossAt[i].x, st.crossAt[i].y) for i in range(1, int(st.crossings) + 1)]
    assert st.crossings == 3, left                                                  # (the road that now ends at a portal is not one)
    assert not any(math.hypot(p[0] - first[0], p[1] - first[1]) < 20 for p in left)
    # the ground there is Earth's: no cell within 5.5 m of the road at that place was written
    assert lua.eval("(function() local x0, y0 = %r, %r; for _, k in ipairs(changedCells(TB, SNAP)) do local gx, gy = k %% TB.n, math.floor(k / TB.n); "
                    "if (TB.px + gx - x0) ^ 2 + (TB.py + gy - y0) ^ 2 < 36 then return false end end return true end)()" % (first[0], first[1]))
