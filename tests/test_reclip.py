"""GitHub #61: the road surface after the ground shaping.

BeamNG.Earth (the user's server: near visual 'detail', geotiles, roads in the ground) paints a paved road's asphalt into the ground and
drapes its centre and edge lines as a mesh per geotile on the ground as the server had it; roads that are DecalRoads are cut to the terrain
when made and not told when it changes. reclip.lua regenerates the DecalRoads over the boxes a shaping job changed; the shaping's log says
how much of the road core moved far enough to strand the lines. Pure Lua, needs only lupa.

    python -m pytest tests
"""
import pytest

lupa = pytest.importorskip("lupa")

from test_shape import DAKAR_EXT, PRELUDE, setup, shape_it  # noqa: E402


@pytest.fixture()
def shape_lua():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute(PRELUDE % {"dakar": DAKAR_EXT})
    return rt


RECLIP = r"""
package.path = package.path .. ';%(dakar)s/?.lua'
reclip = require('beamngDakar/reclip')
CLOCK = 0
LOG = {}
REGEN = {}
-- a stub road group: objects { class, box = {x0, y0, x1, y1}, id }
GROUP = {}
function addRoad(id, x0, y0, x1, y1, class)
  GROUP[#GROUP + 1] = { id = id, class = class or 'DecalRoad', box = { x0, y0, x1, y1 } }
end
function newReclip(idle)
  return reclip.new({
    clock = function() return CLOCK end,
    idle = idle,
    group = function()
      if GROUP.none then return nil end
      return {
        count = function() return #GROUP end,
        at = function(i)
          local o = GROUP[i]
          return { class = function() return o.class end, box = function() return table.unpack(o.box) end,
                   regenerate = function() REGEN[#REGEN + 1] = o.id end }
        end,
      }
    end,
    log = function(l, m) LOG[#LOG + 1] = l .. ' ' .. m end,
  })
end
"""


@pytest.fixture()
def rc():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end")
    rt.execute(RECLIP % {"dakar": DAKAR_EXT})
    return rt


def regen(rc):
    t = rc.eval("REGEN")
    return [str(t[i]) for i in range(1, len(t) + 1)]


def test_the_decalroads_over_a_changed_box_are_regenerated_and_the_others_and_other_classes_are_not(rc):
    rc.execute("addRoad('over', 100, -5, 160, 5); addRoad('far', 900, 900, 960, 910); addRoad('edge', 168, -5, 200, 5); "
               "addRoad('mesh', 100, -5, 160, 5, 'TSStatic'); addRoad('across', 120, -80, 130, 80)")
    rc.execute("R = newReclip(nil); R:note(110, -3, 150, 3); CLOCK = 5; R:update()")
    got = regen(rc)
    assert "over" in got and "across" in got                       # (inside the box)
    assert "far" not in got and "mesh" not in got                  # (outside it; not a DecalRoad)
    assert "edge" not in got                                       # (18 m from the box: beyond the 2 m margin)
    log = rc.eval("LOG")
    assert any("2 DecalRoads over the shaped ground cut again" in str(log[i]) for i in range(1, len(log) + 1))


def test_a_sweep_is_throttled_waits_for_the_shaping_and_regenerates_a_few_a_frame(rc):
    rc.execute("for i = 1, 10 do addRoad('r' .. i, 100, -5, 160, 5) end")
    rc.execute("BUSY = true; R = newReclip(function() return not BUSY end); R:note(110, -3, 150, 3); CLOCK = 5")
    rc.execute("R:update()")
    assert regen(rc) == []                                         # (the shaping is still at it)
    rc.execute("BUSY = false; R:update()")
    assert len(regen(rc)) == rc.eval("reclip.MAX_REGEN_FRAME")      # (a few a frame)
    for _ in range(10):
        rc.execute("R:update()")
    assert len(regen(rc)) == 10 and sorted(set(regen(rc))) == sorted(regen(rc))     # (each once)
    # a next job's box: not before SWEEP_EVERY_S after the last sweep ended
    rc.execute("R:note(110, -3, 150, 3); R:update()")
    assert len(regen(rc)) == 10
    rc.execute("CLOCK = CLOCK + reclip.SWEEP_EVERY_S + 0.1; for _ = 1, 10 do R:update() end")
    assert len(regen(rc)) == 20


def test_a_long_group_is_looked_at_in_slices(rc):
    rc.execute("for i = 1, 400 do addRoad('o' .. i, 5000 + i, 0, 5001 + i, 1) end; addRoad('hit', 100, 0, 110, 1)")
    rc.execute("R = newReclip(nil); R:note(100, 0, 110, 1); CLOCK = 5; R:update()")
    assert regen(rc) == []                                         # (the first 150 objects: the road is the 401st)
    rc.execute("R:update(); R:update()")
    assert regen(rc) == ["hit"]
    assert rc.eval("R:status().sweeps") == 1


def test_many_boxes_are_merged_and_no_group_and_a_failing_regenerate_do_not_break_anything(rc):
    rc.execute("for i = 1, 100 do R = R or newReclip(nil); R:note(i * 10, 0, i * 10 + 5, 5) end; CLOCK = 5")
    assert rc.eval("#R.boxes") <= rc.eval("reclip.MAX_BOXES")
    rc.execute("GROUP.none = true; R:update()")
    assert rc.eval("#R.boxes") == 0 and rc.eval("R:status().working") is False
    rc.execute("GROUP.none = nil; addRoad('bad', 0, 0, 5, 5); R:note(0, 0, 5, 5); "
               "GROUP[1].regenerate = true; local o = GROUP[1]; "
               "R.env.group = function() return { count = function() return 1 end, at = function() return "
               "{ class = function() return 'DecalRoad' end, box = function() return 0, 0, 5, 5 end, regenerate = function() error('no') end } end } end")
    rc.execute("CLOCK = 20; R:update()")
    assert rc.eval("R:status().sweeps") == 1                       # (the sweep ends; said once)


# --- the shaping tells what it did to the ground the lines are on and which boxes -------------------------------------------------

def test_the_shaping_says_how_much_of_the_road_core_moved_far_enough_to_strand_earths_lines(shape_lua):
    lua = shape_lua
    setup(lua)
    shape_it(lua)
    logs = [str(lua.eval("LOGS")[i]) for i in range(1, len(lua.eval("LOGS")) + 1)]
    said = [l for l in logs if "road lines" in l]
    assert said and "mesh on the ground as it was" in said[0]
    st = lua.eval("E:status()")
    assert st is not None
    # (the 3 m spikes of the stub's road are cut: cells lowered; the road runs along the plane: little raised)
    assert "lowered" in said[0]


def test_a_flat_road_with_nothing_to_smooth_says_nothing_about_lines(shape_lua):
    lua = shape_lua
    lua.execute("BUMPS = {}; GROUND = function(x, y) return 100 end")      # (level ground: nothing to fix)
    setup(lua)
    shape_it(lua)
    logs = [str(lua.eval("LOGS")[i]) for i in range(1, len(lua.eval("LOGS")) + 1)]
    assert not [l for l in logs if "road lines" in l]


def test_the_boxes_handed_to_updategrid_are_told_to_the_env_in_world_metres(shape_lua):
    lua = shape_lua
    lua.execute("BOXES = {}")
    setup(lua)
    lua.execute("E.env.changed = function(x0, y0, x1, y1) BOXES[#BOXES + 1] = { x0, y0, x1, y1 } end")
    shape_it(lua)
    boxes = lua.eval("BOXES")
    n = len(boxes)
    assert n >= 1
    # (the stub block's south-west corner is at (0, -128), 1 m cells: the spikes of the road along y = 0 are at x = 100 and 180, each fixed
    # from 20 m before to 20 m after it, with the falloff 18.5 m round that)
    xs0 = min(boxes[i][1] for i in range(1, n + 1))
    xs1 = max(boxes[i][3] for i in range(1, n + 1))
    ys0 = min(boxes[i][2] for i in range(1, n + 1))
    ys1 = max(boxes[i][4] for i in range(1, n + 1))
    assert 60 < xs0 < 95 and 190 < xs1 < 230 and -40 < ys0 < -5 and 5 < ys1 < 40
