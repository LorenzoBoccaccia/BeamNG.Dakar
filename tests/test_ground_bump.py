"""GitHub #59: the bump that smashed the car. The ground under a moving car never changes between two physics frames.

At a terrain swap Earth's new block has its own, unshaped ground; our shaping used to land some frames later (the urgent slice
of 6 ms, then 1.5 ms a frame), so the ground under the moving car went from the shaped road to Earth's and, some frames on,
back to the shaped road: a step of the shaping's change (up to several metres) under the wheels, twice. These tests use the
stub TerrainBlock of test_shape.py.

    python -m pytest tests
"""
import pytest

pytest.importorskip("lupa")

from test_shape import TOL, ground, lua, road, setup, shape_it, world  # noqa: E402,F401  (the stub block and its fixture)


def md(lua, args):
    """The greatest difference of the world heights of two blocks (maxDiff of the prelude, its first result)."""
    return lua.eval("(maxDiff(%s))" % args)


def logs_of(lua):
    t = lua.eval("LOGS")
    return [str(t[i]) for i in range(1, len(t) + 1)]


SPIKES = [(60, 0), (140, 0), (220, 0), (300, 0), (380, 0)]


def swap_world(lua, speed=60.0, new_block="{ px = 20, py = -256, n = 512 }"):
    """The stage along y = 0 from x = 0 to 400 shaped on a block of 512 x 1 m (x from -50), the car at x = 250 driving at `speed` m/s,
    and Earth's next block (its own, unshaped ground of the same world, 70 m east) ready to be swapped in with SWAP()."""
    lua.execute("BUMPS = { { 60, 0 }, { 140, 0 }, { 220, 0 }, { 300, 0 }, { 380, 0 } }")
    lua.execute("TB = newBlock({ px = -50, py = -256, n = 512 }); SNAP = snapshot(TB); E = newEngine(TB)")
    lua.execute("E:setLines({ roadLine(0, 400, 110) })")
    lua.execute("PLAYER = { x = 5000, y = 5000, speed = 0 }")        # (shaped while the car was elsewhere)
    shape_it(lua)
    lua.execute("PLAYER = { x = 250, y = 0, speed = %r }" % speed)
    lua.execute("TB2 = newBlock(%s); SNAP2 = snapshot(TB2)" % new_block)
    lua.execute("function SWAP() CURRENT = TB2; E:blockChanged() end")


def test_the_diagnosis_a_new_blocks_own_ground_is_a_step_from_the_shaped_road_under_the_car(lua):
    swap_world(lua)
    worst, n = lua.eval("maxDiff(TB, TB2, 250, 0, 100)")
    assert n > 30000 and worst > 2.0                       # (the 3 m spikes the road was shaped flat of)


def test_the_old_behaviour_left_those_frames_to_the_job(lua):
    # the frames' job on its own (no stretch shaped at once, as before): after the swap and one frame the ground within 100 m is
    # still mostly Earth's own
    swap_world(lua)
    lua.execute("CURRENT = TB2; E:blockChanged(true); E:update()")
    assert md(lua, "TB, TB2, 250, 0, 100") > 2.0


@pytest.mark.parametrize("speed", [60.0, 25.0, 0.0])
def test_a_swap_while_the_car_drives_is_shaped_at_the_car_before_it_returns_to_the_old_blocks_heights(lua, speed):
    swap_world(lua, speed)
    lua.execute("SWAP()")                                  # (no frame has run: the next physics step sees this)
    r = min(lua.eval("shape.SYNC_MAX_M"), lua.eval("shape.SYNC_MARGIN_M") + lua.eval("shape.SYNC_S") * speed)
    worst, n = lua.eval("maxDiff(TB, TB2, 250, 0, %d)" % int(r))
    assert n > 1000 and worst < 0.005                      # the same ground, the same profile: the old block's heights
    st = lua.eval("E:status()")
    assert st.syncCells > 200 and st.working is True       # (and the rest of the stage is still to do)
    assert lua.eval("logged('shaped at once')")
    shape_it(lua)
    assert md(lua, "TB, TB2, 250, 0, 150") < 0.005
    assert world(lua, 380, 0, "TB2") == pytest.approx(road(380), abs=TOL)


@pytest.mark.parametrize("speed", [60.0, 0.0])
def test_the_swap_writes_only_the_cells_of_the_stretches_fixed(lua, speed):
    # (GitHub #63: the block that arrives is Earth's own ground but for the spikes of the line: at a swap, at once and over the frames after
    # it, the cells written are the fixed stretches' (a spike's points 20 m either side, and the falloff round the line), nothing else)
    swap_world(lua, speed)
    lua.execute("SWAP()")
    shape_it(lua)
    x0, x1, y0, y1 = lua.eval("extent(TB2, SNAP2)")
    spikes = [bx for bx, _ in SPIKES]
    for x in (x0, x1):
        assert any(bx - 20 - 18.5 - 1 <= x <= bx + 20 + 18.5 + 1 for bx in spikes), x
    assert -19.5 <= y0 and y1 <= 19.5
    # ... and every cell written is within reach of one of them (none between: the road between the spikes is Earth's, as made)
    assert lua.eval("(function() local n = 0; for _, k in ipairs(changedCells(TB2, SNAP2)) do "
                    "local gx = k % TB2.n; local x = TB2.px + gx; local ok = false; "
                    "for _, b in ipairs({ 60, 140, 220, 300, 380 }) do if math.abs(x - b) <= 39.5 then ok = true end end; "
                    "if not ok then n = n + 1 end end; return n end)()") == 0
    assert lua.eval("TB2.sets") == lua.eval("#changedCells(TB2, SNAP2)") > 1000      # (each cell written once)


def test_the_speed_comes_from_the_players_position_when_the_env_has_no_velocity(lua):
    swap_world(lua, 0.0)
    lua.execute("PLAYER = { x = 250, y = 0 }; CLOCK = 0; E.env.clock = function() return CLOCK end; E:car()")
    lua.execute("CLOCK = 1; PLAYER = { x = 280, y = 0 }")
    assert lua.eval("select(3, E:car())") == pytest.approx(30.0)


def test_what_a_swap_leaves_for_the_frames_is_far_from_the_car_and_done_before_the_car_is_there(lua):
    swap_world(lua, 30.0)
    lua.execute("SWAP()")
    r = 37.0                                               # (22 m and what the car covers in half a second)
    assert world(lua, 380, 0, "TB2") == pytest.approx(ground(380, 0, SPIKES), abs=1e-9)       # Earth's own, far ahead
    assert world(lua, 300, 0, "TB2") == pytest.approx(ground(300, 0, SPIKES), abs=1e-9)       # (the spike at 300: beyond the 287 of the stretch)
    assert 250 + r < 300 - 10
    lua.execute("E:update()")                              # the first frame: the urgent slice, the stretch near the car first
    assert world(lua, 300, 0, "TB2") == pytest.approx(road(300), abs=TOL)


def test_the_stretch_shaped_at_once_costs_a_few_milliseconds_on_the_stub(lua):
    swap_world(lua, 30.0)
    lua.execute("local t0 = os.clock(); SWAP(); COST = (os.clock() - t0) * 1000")
    cost = lua.eval("COST")
    st = lua.eval("E:status()")
    print("sync: %d cells in %.1f ms (the plan included)" % (st.syncCells, cost))
    assert st.syncCells > 800                              # (GitHub #61: 9358 cells and 24.6 ms at 35 m/s before; GitHub #63: only the spike's cells)
    assert cost < 250                                      # (generous for a loaded machine on plain Lua; LuaJIT measured 7 ms)


def test_the_stretch_at_the_car_is_cut_to_the_budget_but_never_inside_what_the_frames_leave_alone(lua):
    # (GitHub #61: the call costs 1 ms a column on this clock: the freeze radius is done whatever it costs, the rest of the stretch
    # only inside SYNC_BUDGET_MS, which the freeze radius alone already spends)
    swap_world(lua, 30.0)
    lua.execute("CLOCK_STEP = 1.0")
    lua.execute("SWAP()")
    freeze = lua.eval("shape.FREEZE_M") + 30 * lua.eval("shape.FREEZE_S")
    assert md(lua, "TB, TB2, 250, 0, %d" % int(freeze)) < 0.005          # the ground under and round the car: the old block's heights
    cut = lua.eval("E:status().syncCells")
    lua.execute("CLOCK_STEP = 0.002")
    swap_world(lua, 30.0)
    lua.execute("SWAP()")
    assert cut < lua.eval("E:status().syncCells")                         # (the whole stretch, when the clock is cheap)


def test_a_swap_with_the_ground_not_answering_yet_is_shaped_on_the_offset_of_the_block_before_and_checked_next_frame(lua):
    swap_world(lua, 60.0, "{ px = 20, py = -256, n = 512, pz = 55 }")      # (another base height: the grid is above it)
    lua.execute("STALE = true; SWAP()")                    # (the game's ground has not caught up with the new block)
    assert md(lua, "TB, TB2, 250, 0, 45") < 0.005
    assert lua.eval("logged('offset of the block before')")
    lua.execute("STALE = false")
    shape_it(lua)
    assert not lua.eval("logged('shaped again')")          # (measured: the offset was right)
    assert md(lua, "TB, TB2, 250, 0, 150") < 0.005


def test_a_grid_offset_that_was_wrong_is_put_back_and_done_again_on_the_measured_one(lua):
    # the new block's grid is absolute where the old one's was above its base: the assumption is wrong and the next frame says so
    swap_world(lua, 60.0, "{ px = 20, py = -256, n = 512, pz = 55, rel = false }")
    lua.execute("STALE = true; SWAP(); STALE = false; PLAYER.speed = 0")     # (the car stops: nothing is held back from the redo)
    shape_it(lua)
    assert any("grid offset" in l and "shaped again" in l for l in logs_of(lua))
    assert md(lua, "TB, TB2, 250, 0, 150") < 0.005
    lua.execute("E:stop()")
    shape_it(lua)
    assert lua.eval("sameAs(TB2, SNAP2)") is True


def test_a_rebase_in_progress_has_its_stages_set_after_the_swap_and_then_shapes_at_the_car(lua):
    # (core.lua tells blockChanged the lines are stale during a rebase or a teleport: setLines follows with the new frame's)
    swap_world(lua, 30.0)
    lua.execute("CURRENT = TB2; E:blockChanged(true)")
    assert lua.eval("TB2.sets") == 0
    lua.execute("E:setLines({ roadLine(0, 400, 110) })")
    assert lua.eval("TB2.sets") > 1000                    # shaped at the car in the setLines call
    assert md(lua, "TB, TB2, 250, 0, 35") < 0.005


# --- later frames and restores leave the ground under a moving car ----------------------------------------------------------------

def run_until_idle_or(lua, frames, step):
    n = 0
    while lua.eval("E:busy()") and n < frames:
        n += 1
        lua.execute(step)
    return n


def test_a_later_frame_never_writes_a_cell_within_reach_of_a_moving_car(lua):
    lua.execute("CLOCK_STEP = 0.5")                        # (a frame does a few columns: the job takes many frames)
    setup(lua, line="roadLine(40, 216, 110)")
    lua.execute("PLAYER = { x = 100, y = 0, speed = 20 }; recordWrites(TB)")
    freeze = lua.eval("shape.FREEZE_M") + 20 * lua.eval("shape.FREEZE_S")
    run_until_idle_or(lua, 60, "FRAME = FRAME + 1; PLAYER.x = PLAYER.x + 0.33; E:update()")
    assert lua.eval("E:busy()")                            # (still at it: the cells under the car wait for it to pass)
    writes = lua.eval("WRITES")
    assert len(writes) > 1000
    for i in range(1, len(writes) + 1):
        gx, gy, cx, cy = (writes[i][k] for k in range(1, 5))
        # (the block's south-west corner is at x = 0, y = -128)
        assert (gx - cx) ** 2 + ((gy - 128) - cy) ** 2 >= (freeze - 1.0) ** 2, (gx, gy, cx, cy)
    assert lua.eval("E:status().deferred") > 0
    # the car stops: the cells it waited on are done, and the whole stretch is as a car that never was there would have had it
    lua.execute("PLAYER.speed = 0")
    shape_it(lua)
    assert lua.eval("E:status().deferred") == 0 and lua.eval("E:status().state") == "shaped"
    lua.execute("REF = newBlock({}); E2 = shape.new({ block = function() return REF end, now = now, clock = function() return T / 1000 end, "
                "player = function() return { x = 5000, y = 5000 } end, ground = GROUND, log = function() end, "
                "vec3 = function(x, y, z) return { x = x, y = y, z = z } end }); "
                "E2:setLines({ roadLine(40, 216, 110) }); while E2:busy() do E2:update() end")
    assert md(lua, "TB, REF, 128, 0, 100") < 1e-9


def test_a_car_that_drives_on_is_left_the_ground_it_is_on_and_gets_the_rest_behind_it(lua):
    lua.execute("CLOCK_STEP = 0.5")
    setup(lua, line="roadLine(40, 216, 110)")
    lua.execute("PLAYER = { x = 60, y = 0, speed = 30 }; recordWrites(TB)")
    n = run_until_idle_or(lua, 800, "FRAME = FRAME + 1; PLAYER.x = math.min(PLAYER.x + 0.5, 300); E:update()")
    assert not lua.eval("E:busy()") and n > 20
    writes = lua.eval("WRITES")
    freeze = lua.eval("shape.FREEZE_M") + 30 * lua.eval("shape.FREEZE_S")
    for i in range(1, len(writes) + 1):
        gx, gy, cx, cy = (writes[i][k] for k in range(1, 5))
        assert (gx - cx) ** 2 + ((gy - 128) - cy) ** 2 >= (freeze - 1.0) ** 2 or cx >= 299.0, (gx, gy, cx, cy)
    assert lua.eval("E:status().state") == "shaped"
    assert world(lua, 100, 0) == pytest.approx(road(100), abs=TOL)         # (the stretch the car left behind: done after it)


def test_a_restore_while_the_car_moves_leaves_the_cells_near_it_and_does_them_when_it_has_gone(lua):
    lua.execute("CLOCK_STEP = 0.5")
    setup(lua, line="roadLine(40, 216, 110)")
    lua.execute("PLAYER = { x = 5000, y = 5000, speed = 0 }")
    shape_it(lua)
    assert lua.eval("sameAs(TB, SNAP)") is False
    lua.execute("E:stop(); PLAYER = { x = 100, y = 0, speed = 20 }; recordWrites(TB)")
    freeze = lua.eval("shape.FREEZE_M") + 20 * lua.eval("shape.FREEZE_S")
    for _ in range(40):
        lua.execute("FRAME = FRAME + 1; E:update()")
    writes = lua.eval("WRITES")
    assert len(writes) > 500                               # (the rest of the stretch is going back)
    for i in range(1, len(writes) + 1):
        gx, gy = writes[i][1], writes[i][2]
        assert (gx - 100) ** 2 + ((gy - 128) - 0) ** 2 >= (freeze - 1.0) ** 2, (gx, gy)
    # the ground under the car is still the shaped road there (not dropped to Earth's own: a step under it)
    assert world(lua, 100, 0) == pytest.approx(road(100), abs=TOL)
    assert lua.eval("E:busy()")
    lua.execute("PLAYER = { x = 100, y = 0, speed = 0 }")      # it has stopped (or is gone): the rest is put back
    shape_it(lua)
    assert lua.eval("sameAs(TB, SNAP)") is True and lua.eval("E:status().state") == "restored"


def test_the_switch_going_off_while_driving_leaves_the_ground_under_the_car(lua):
    lua.execute("CLOCK_STEP = 0.5")
    setup(lua, line="roadLine(40, 216, 110)")
    lua.execute("PLAYER = { x = 5000, y = 5000, speed = 0 }")
    shape_it(lua)
    lua.execute("PLAYER = { x = 100, y = 0, speed = 25 }; E:setEnabled(false)")
    for _ in range(300):
        lua.execute("E:update()")
    assert world(lua, 100, 0) == pytest.approx(road(100), abs=TOL)
    assert world(lua, 190, 0) == pytest.approx(ground(190, 0), abs=1e-9)      # far from it: Earth's own again


def test_a_session_that_ends_puts_every_height_back_whatever_the_car_does(lua):
    setup(lua)
    lua.execute("PLAYER = { x = 5000, y = 5000, speed = 0 }")
    shape_it(lua)
    lua.execute("E:stop(); PLAYER = { x = 100, y = 0, speed = 40 }")
    lua.execute("for _ = 1, 400 do if not E:busy() then break end E:update(50, true) end")
    assert lua.eval("sameAs(TB, SNAP)") is True


# --- the size of the change: the 8.6 m of #59 -------------------------------------------------------------------------------------

def hillside(lua, slope):
    lua.execute("BUMPS = {}; GROUND = function(x, y) return 100 + 0.02 * x + %r * y end" % slope)
    setup(lua)
    lua.execute("PLAYER = { x = 5000, y = 5000, speed = 0 }")


def test_a_hillside_is_relief_and_not_written_even_at_90_percent(lua):
    # (GitHub #59: a flat core on a 90 percent hillside moved the core's edge 8.6 m; GitHub #63: the road keeps its cross slope, nothing is a defect)
    hillside(lua, 0.9)
    shape_it(lua)
    assert lua.eval("TB.sets") == 0
    st = lua.eval("E:status()")
    assert st.wantedCore == 0 and st.wantedMax == 0 and st.capped == 0


def test_the_largest_change_is_logged_on_the_road_core_and_anywhere_and_the_core_never_moves_more_than_the_cap(lua):
    # a 6 m spike (a defect bigger than the cap) on a hillside of 90 percent across the road: the delta is the spike, the cross slope is not in it.
    # GitHub #69: the change is spread over the stretch (scaled to the cap) and not cut off at it, so no cell is held at the cap
    lua.execute("BUMPS = {}; GROUND = function(x, y) return 100 + 0.02 * x + 0.9 * y + 6 * math.exp(-((x - 100) ^ 2 + y ^ 2) / 128) end")
    setup(lua)
    lua.execute("PLAYER = { x = 5000, y = 5000, speed = 0 }")
    shape_it(lua)
    cap = lua.eval("shape.MAX_CHANGE_M")
    st = lua.eval("E:status()")
    assert st.wantedCore == pytest.approx(cap, abs=1e-6)     # what the core is given: the cap (the spike is 6 m, less what its feet keep: it asked for more)
    assert st.wantedMax >= st.wantedCore
    assert st.maxCore <= cap + 1e-9 and st.maxChange <= cap + 1e-9     # never more than the cap
    assert st.capped == 0                                              # ... and no cell is cut off at it
    done = [l for l in logs_of(lua) if "cells written along" in l]
    assert done and "on the road core" in done[0] and "asked for up to" in done[0] and "held at" in done[0]
    assert "(%.1f m on the road core" % st.maxCore in done[0]
    assert "asked for up to %.1f m (%.1f m on the core)" % (st.wantedMax, st.wantedCore) in done[0]
    spread = [l for l in logs_of(lua) if "asked for more than the 4 m cap" in l]
    assert spread and "largest 5." in spread[0] and "spread over the stretch" in spread[0]
    for x, y in ((100, 6), (100, -6), (100, 5.5), (100, 0), (100, 10)):
        assert abs(world(lua, x, y) - lua.eval("GROUND(%r, %r)" % (float(x), float(y)))) <= cap + 1e-6
    # (and the change across the road is continuous: no step where the cap starts to hold)
    ch = [world(lua, 100, y) - lua.eval("GROUND(100, %d)" % y) for y in range(-14, 15)]
    assert max(abs(b - a) for a, b in zip(ch, ch[1:])) < 0.8      # (the falloff of 6 m over 12 m: 0.75 m a metre at its steepest)


def test_a_gentle_road_is_not_held_by_the_cap_and_logs_no_warning(lua):
    hillside(lua, 0.1)
    shape_it(lua)
    st = lua.eval("E:status()")
    assert st.capped == 0 and st.wantedCore == pytest.approx(st.maxCore, abs=1e-9) and st.maxCore < 1.0
    assert not any("road core was asked" in l for l in logs_of(lua))


def test_the_spikes_the_shaping_exists_for_are_still_taken_out_in_full(lua):
    # (a spike under the cap goes whole, less its feet: the delta of the line)
    setup(lua)
    lua.execute("PLAYER = { x = 5000, y = 5000, speed = 0 }")
    shape_it(lua)
    st = lua.eval("E:status()")
    assert st.maxCore == pytest.approx(3.0, abs=0.15) and st.capped == 0


# --- the profile's pinned ends are far from the car -------------------------------------------------------------------------------

def test_a_run_of_the_line_ending_at_the_blocks_edge_near_the_car_is_said(lua):
    # the line runs past the block's edge: its profile is pinned to the ground there, and the car is near it
    setup(lua, line="roadLine(-100, 400, 110)", n=256, px=0)
    lua.execute("PLAYER = { x = 100, y = 0, speed = 0 }; E:blockChanged()")
    assert lua.eval("logged(\"ends at the block's edge\")")


def test_a_car_in_the_middle_of_a_big_block_is_far_from_the_ends_of_its_runs(lua):
    setup(lua, line="roadLine(-800, 800, 110)", n=1024, px=-512, py=-512)
    lua.execute("PLAYER = { x = 0, y = 0, speed = 30 }; E:blockChanged()")
    assert lua.eval("logged('shaped at once')")
    assert not lua.eval("logged(\"ends at the block's edge\")")


def test_a_run_end_of_a_big_block_does_not_move_the_profile_at_the_car(lua):
    # the measured reach of a pinned end: well inside what the car is kept away from
    assert lua.eval("shape.RUN_END_REACH_M") <= 100
    lua.execute("BUMPS = { { -300, 0 }, { -100, 0 }, { 100, 0 }, { 300, 0 } }")
    lua.execute("TB = newBlock({ px = -512, py = -512, n = 1024 }); E = newEngine(TB); "
                "E:setLines({ roadLine(-800, 800, 110) }); PLAYER = { x = 0, y = 0, speed = 30 }; E:blockChanged()")
    shape_it(lua)
    lua.execute("TB2 = newBlock({ px = -400, py = -512, n = 1024 }); CURRENT = TB2; E:blockChanged()")
    assert md(lua, "TB, TB2, 0, 0, 35") < 0.005                 # (the block moved 112 m: its run ends moved with it)
