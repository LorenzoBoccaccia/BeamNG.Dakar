"""Another road's ground is not the stage road's to move (shape.lua, the geometry's `otherRoads`): a road beside the stage road, one that
crosses it, one that leaves it. The stage's own footprint (the core) is worked on as ever.

    python -m pytest tests

The stub block of test_shape: a plane with a 3 m spike on the stage's line (y = 0) at x = 100, which the shaping takes out with a taper of
15 m along the line and 6 m beside it past the core of 5.5 m.
"""
import pytest

lupa = pytest.importorskip("lupa")

from test_shape import lua, setup, shape_it, world  # noqa: E402,F401  (the stub block and the engine)


def other_road(pts, hw=6.5):
    """A piece of another road in the lines' Lua form: pts [(x, y), ...] along it, half width hw."""
    return "{ hw = %r, pts = { %s } }" % (hw, ", ".join("{ %r, %r }" % (float(x), float(y)) for x, y in pts))


def with_others(*roads):
    """The stage's line (40 to 216 m along y = 0) with these pieces of other roads."""
    return "(function() local l = roadLine(40, 216, 110); l.others = { %s }; return l end)()" % ", ".join(roads)


def change_at(lua, x, y):
    """How far the block's world height at (x, y) is from the game's ground there."""
    return world(lua, x, y) - lua.eval("GROUND(%r, %r)" % (float(x), float(y)))


def spike_at_100(lua):
    lua.execute("BUMPS = { { 100, 0 } }")


def test_without_other_roads_the_taper_of_a_fix_reaches_the_ground_beside_the_road(lua):
    # (the control for the tests below: the spike is taken out and its taper moves ground up to 11.5 m from the line)
    spike_at_100(lua)
    setup(lua)
    shape_it(lua)
    assert change_at(lua, 100, 0) < -2.5
    assert abs(change_at(lua, 100, 8)) > 0.1 and abs(change_at(lua, 100, -8)) > 0.1


def test_a_road_beside_the_stage_road_keeps_the_ground_it_stands_on(lua):
    # a motorway 9 m off the line (the A25 beside the Popoli stage): its carriageway, shoulders and margin reach 0.5 m from the line. Nothing
    # beyond the stage road's own footprint (5.5 m) is moved on it; the stage road's own ground is fixed as ever
    spike_at_100(lua)
    setup(lua, line=with_others(other_road([(0, 9), (260, 9)])))
    shape_it(lua)
    assert change_at(lua, 100, 0) < -2.5                                      # the stage road: the spike is out
    assert abs(change_at(lua, 100, 4)) > 0.5                                  # ... and its footprint, motorway or not
    for x in range(70, 131, 3):
        for y in range(6, 19):                                                # (the motorway's ground and its margin, up to 17.5 m)
            assert change_at(lua, x, y) == pytest.approx(0, abs=1e-9), (x, y)
    assert abs(change_at(lua, 100, -8)) > 0.1                                 # (the other side, with no road there, is moved as before)
    assert lua.eval("logged('cells beside the road kept clear of other roads')")
    assert int(lua.eval("tonumber(string.match(logged('cells beside the road kept clear') or '', '(%d+) cells beside the road'))")) > 0


def test_the_taper_eases_out_toward_another_roads_ground_and_does_not_end_in_a_step(lua):
    spike_at_100(lua)
    setup(lua)
    shape_it(lua)
    free = {y: abs(change_at(lua, 100, y)) for y in (-6, -7, -8, -9, -10, -11)}
    # a minor road 14 m off the line on the other side: its ground is 4 m either side (2 m half width and the 2 m margin), then 3 m of ease
    setup(lua, line=with_others(other_road([(0, -14), (260, -14)], hw=2)))
    shape_it(lua)
    kept = {y: abs(change_at(lua, 100, y)) for y in free}
    assert kept[-6] == pytest.approx(free[-6], abs=1e-9)                      # (further than the ease reaches)
    assert 0 < kept[-9] < free[-9]                                            # eased
    assert kept[-10] == pytest.approx(0, abs=1e-9) and kept[-11] == pytest.approx(0, abs=1e-9)    # (on the road's ground: nothing)
    assert kept[-7] >= kept[-8] >= kept[-9] >= kept[-10]                      # (monotone toward the road: no step)


def test_a_road_crossing_the_stage_road_is_shaped_with_the_stage_road_only_not_beyond_its_footprint(lua):
    # a road crosses at x = 100 where the spike is. The recording leaves out what is within 5 m of the line (the stage road's own ground
    # there), so its pieces start 5 m either side
    spike_at_100(lua)
    setup(lua, line=with_others(other_road([(100, 5), (100, 60)], hw=3), other_road([(100, -60), (100, -5)], hw=3)))
    shape_it(lua)
    assert change_at(lua, 100, 0) < -2.5
    assert abs(change_at(lua, 100, 3)) > 0.5 and abs(change_at(lua, 103, 3)) > 0.5       # at the crossing, within the stage road's footprint: fixed
    for y in range(7, 40, 2):                                                             # along the crossing road, beyond it: not moved
        for x in (97, 99, 100, 101, 103):
            assert change_at(lua, x, y) == pytest.approx(0, abs=1e-9), (x, y)
            assert change_at(lua, x, -y) == pytest.approx(0, abs=1e-9), (x, -y)
    assert abs(change_at(lua, 90, 4)) > 0.1 and abs(change_at(lua, 110, 4)) > 0.1       # (the stage road on either side of the crossing is shaped)


def test_other_roads_far_from_the_work_change_nothing(lua):
    spike_at_100(lua)
    setup(lua)
    shape_it(lua)
    lua.execute("REF = snapshot(TB)")
    setup(lua, line=with_others(other_road([(0, 60), (260, 60)]), other_road([(100, 30), (100, 90)], hw=3)))
    shape_it(lua)
    assert lua.eval("(function() for k, v in pairs(REF) do if TB.h[k] ~= v then return false end end; return true end)()") is True
    assert lua.eval("E:status().stretches") == 1


def test_another_roads_ground_is_kept_clear_of_an_overpass_fix_too(lua):
    # (the pad of an overpass fix reaches 20 m past the deck and moves ground by 8 m: another road's ground within it is still not moved)
    lua.execute("BUMPS = {}")
    lua.execute("DECK = function(x, y, g) if x >= 100 and x <= 130 and math.abs(y) < 8 then return g + 6 end end")
    lua.execute("function GROUND(x, y) local z = 100 + 0.02 * x + 0.1 * y; if x >= 100 and x <= 130 and math.abs(y) < 8 then z = z + 5 end; return z end")
    setup(lua, line=with_others(other_road([(60, 10), (180, 10)], hw=3)))
    shape_it(lua)
    assert lua.eval("E:status().stretches") >= 1
    for x in range(70, 171, 4):
        for y in range(7, 14):
            assert change_at(lua, x, y) == pytest.approx(0, abs=1e-9), (x, y)


def test_the_same_stages_with_the_same_other_roads_are_not_a_new_job_and_other_roads_are_part_of_what_is_the_same(lua):
    spike_at_100(lua)
    setup(lua, line=with_others(other_road([(0, 9), (260, 9)])))
    shape_it(lua)
    jobs = lua.eval("E:status().jobs")
    lua.execute("E:setLines({ %s })" % with_others(other_road([(0, 9), (260, 9)])))
    assert not lua.eval("E:busy()")
    lua.execute("E:setLines({ %s })" % with_others(other_road([(0, -9), (260, -9)])))     # (another road beside it: shaped again)
    assert lua.eval("E:busy()")
    shape_it(lua)
    assert lua.eval("E:status().jobs") == jobs + 1
