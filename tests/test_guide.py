"""Liaison guidance: the ground arrows lead to a point of the planned line the local navgraph reaches, not to a time control
kilometres outside it (guide.lua, and the hook in core.lua). Pure functions on their own, then the extension with a stub of
the game's core_groundMarkers (its planner keeps a path of nodes with distToTarget, and calcDistance forgets what lies
beyond the target) and a navgraph that covers only part of the liaison, as Earth's does.
"""
import pytest

lupa = pytest.importorskip("lupa")

from test_core import DAKAR_EXT, lua, logs, launched   # noqa: E402,F401  (the extension's stubs; skips without Earth)


# --- guide.lua on its own ----------------------------------------------------------------------------------------------

@pytest.fixture()
def g():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("package.path = package.path .. ';%s/?.lua'" % DAKAR_EXT.replace("\\", "/"))
    rt.execute("guide = require('beamngDakar/guide')")
    # a line east for 1000 m, north for 500, west back for 1000: a hairpin, the two runs 500 m apart
    rt.execute("""
      LINE = guide.line({ {0, 0, 10}, {1000, 0, 20}, {1000, 500, 30}, {0, 500, 40} })
      function onGraphTo(limit) return function(x, y) return x <= limit end end
      function all() return true end
      function none() return false end
    """)
    return rt


def test_line_gives_the_length_and_points_along_it(g):
    assert g.eval("LINE.total") == pytest.approx(2500)
    x, y, z = g.eval("guide.pointAt(LINE, 1250)")
    assert (x, y, z) == pytest.approx((1000, 250, 25), abs=0.01)         # (250 m up the north leg: 20 -> 30 m high)
    assert g.eval("guide.line({ {0, 0} })") is None
    assert tuple(g.eval("guide.pointAt(LINE, 99999)")[:2]) == pytest.approx((0, 500))


def test_along_is_the_nearest_point_of_the_asked_stretch(g):
    s, off = g.eval("guide.along(LINE, 400, 30, 0, 2500)")
    assert s == pytest.approx(400) and off == pytest.approx(30)
    s, off = g.eval("guide.along(LINE, 400, 30, 1500, 2500)")        # (only the way back: 470 m off)
    assert s == pytest.approx(2100) and off == pytest.approx(470)


def test_progress_keeps_to_the_last_place_while_on_the_line_and_rejoins_at_the_nearest_when_off_it(g):
    # (the hairpin's two runs are 500 m apart; a car on the first run is on the first whatever is nearer after)
    assert g.eval("(guide.progress(LINE, 400, 20, nil))") == pytest.approx(400)
    s, off = g.eval("guide.progress(LINE, 420, 10, 400)")
    assert s == pytest.approx(420) and off == pytest.approx(10)
    # 250 m off the line: not on it, the nearest point of the whole line (the way back is 250 m off too: the first wins the tie)
    s, off = g.eval("guide.progress(LINE, 600, 250, 580)")
    assert off == pytest.approx(250) and s == pytest.approx(600)
    # a car that left the line near its end and is nearest the way back is led from there
    s, off = g.eval("guide.progress(LINE, 300, 530, 100)")
    assert s == pytest.approx(2200) and off == pytest.approx(30)


def test_the_target_is_the_farthest_point_the_graph_covers_within_the_lookahead(g):
    t = g.eval("guide.target(LINE, 0, onGraphTo(800))")
    assert t.s == pytest.approx(800) and t.final is False and t.x == pytest.approx(800)
    # ... never beyond the lookahead
    t = g.eval("guide.target(LINE, 0, all)")
    assert t.s == pytest.approx(1500) and t.final is False
    # ... and from further along
    t = g.eval("guide.target(LINE, 1200, all)")
    assert t.s == pytest.approx(2500) and t.final is True


def test_a_short_gap_in_the_graph_is_crossed_and_a_long_one_ends_the_search(g):
    g.execute("function gap(a, b) return function(x, y) return not (y < 1 and x > a and x < b) end end")
    assert g.eval("guide.target(LINE, 0, gap(300, 400)).s") == pytest.approx(1500)        # (two samples off: crossed)
    assert g.eval("guide.target(LINE, 0, gap(300, 600)).s") == pytest.approx(300)         # (more than three in a row)


def test_the_end_of_the_line_is_the_target_when_it_is_in_reach_with_the_graph_under_it_give_or_take_a_gap(g):
    # the last 100 m off the graph (two samples): the end itself, the planner goes on to it
    g.execute("function nearEnd(x, y) return not (y > 499 and x < 100) end")
    t = g.eval("guide.target(LINE, 1500, nearEnd)")
    assert t.final is True and t.s == pytest.approx(2500)


def test_nothing_on_the_graph_leaves_the_target_where_the_car_is_along_the_line(g):
    t = g.eval("guide.target(LINE, 300, none)")
    assert t.s == pytest.approx(300) and t.x == pytest.approx(300) and t.final is False


def test_the_target_is_moved_on_in_steps_and_the_rest_is_what_is_left_of_the_line(g):
    g.execute("A = { s = 800, final = false }")
    assert g.eval("guide.moved(nil, A)") is True
    assert g.eval("guide.moved(A, { s = 900, final = false })") is False
    assert g.eval("guide.moved(A, { s = 950, final = false })") is True
    assert g.eval("guide.moved(A, { s = 600, final = false })") is True          # (back: the car rejoined elsewhere)
    assert g.eval("guide.moved(A, { s = 810, final = true })") is True
    assert g.eval("guide.rest(LINE, A)") == pytest.approx(1700)
    assert g.eval("guide.rest(LINE, { s = 2500, final = true })") == 0


def test_the_rest_is_added_once_and_again_after_the_planner_forgot_it(g):
    g.execute("""
      RP = { distance = 300, path = { { distToTarget = 300 }, { distToTarget = 100 }, { distToTarget = 0 } } }
    """)
    assert g.eval("guide.addRest(RP, 1700)") == pytest.approx(1700)
    assert g.eval("RP.path[1].distToTarget") == pytest.approx(2000) and g.eval("RP.path[3].distToTarget") == pytest.approx(1700)
    assert g.eval("RP.distance") == pytest.approx(2000)
    assert g.eval("guide.addRest(RP, 1700)") == 0                            # (nothing to add the second time)
    g.execute("RP.path[1].distToTarget, RP.path[2].distToTarget, RP.path[3].distToTarget, RP.distance = 300, 100, 0, 300")
    assert g.eval("guide.addRest(RP, 1700)") == pytest.approx(1700)           # (a re-plan of the planner's own)
    assert g.eval("RP.path[1].distToTarget") == pytest.approx(2000)
    assert g.eval("guide.addRest({ path = {} }, 100)") == 0 and g.eval("guide.addRest(nil, 100)") == 0


def test_the_planner_is_told_to_keep_off_the_minor_ways(g):
    # (the navgraph's drivability: main roads 1, residential 0.8, living street 0.5, service 0.4, track and busway 0.2)
    assert g.eval("guide.PLANNER.cutOffDrivability") == pytest.approx(0.5)
    assert g.eval("guide.PLANNER.penaltyBelowCutoff") > g.eval("guide.PLANNER.penaltyAboveCutoff") >= 1


# --- the extension --------------------------------------------------------------------------------------------------------

STUBS = r"""
legsmod = require('beamngDakar/legs')
guide = require('beamngDakar/guide')
GEO = JSON['/dakar/editions/testbed-algarve/geometry/road1.json']
ANCH = geodesy.newAnchor(37.0900, -7.9800)
WORLD = legsmod.toWorld(GEO.points, ANCH, 0)
ROAD = guide.line(WORLD)
function at(s) local x, y, z = guide.pointAt(ROAD, s); return { x, y, z } end

-- the navgraph covers the line from s = A to s = B only (Earth's covers about a kilometre round the player)
NODES = {}
function coverLine(a, b)
  NODES = {}
  for s = a, b, 40 do local p = at(s); NODES['n' .. s] = { pos = { x = p[1], y = p[2], z = p[3] } } end
end
map = { getMap = function() return { nodes = NODES } end }

-- the game's planner as far as the guidance reads it: a path of nodes (the car, the target), each with its distance to the
-- target, and calcDistance, which forgets whatever lies beyond the target
GM_CALLS = {}
local function straight(a, b) return math.sqrt((a.x - b.x) ^ 2 + (a.y - b.y) ^ 2) end
GM = { endWP = nil, routePlanner = { path = {}, distance = 0 } }
function GM.routePlanner:calcDistance()
  local d = 0
  for i = #self.path, 2, -1 do self.path[i].distToTarget = d; d = d + straight(self.path[i].pos, self.path[i - 1].pos) end
  if self.path[1] then self.path[1].distToTarget = d end
  self.distance = d
end
function GM.currentlyHasTarget() return GM.endWP ~= nil end
function GM.getPathLength() return GM.routePlanner.path[1] and GM.routePlanner.path[1].distToTarget or 0 end
function GM.setPath(wp, options)
  GM_CALLS[#GM_CALLS + 1] = { wp = wp, options = options }
  GM.endWP = wp
  local rp = GM.routePlanner
  if not wp then rp.path = {}; rp.distance = 0; return end
  rp.path = { { pos = { x = CAR[1], y = CAR[2], z = CAR[3] } }, { pos = wp[#wp], fixed = true } }
  rp:calcDistance()
end
core_groundMarkers = GM

-- the game's route node: the planner from the car through the race nodes to the stop's offset position
function gameRoutes(carS)
  CAR = at(carS)
  local first, last, final = at(0), at(ROAD.total - 30), at(ROAD.total - 10)
  GM.setPath({ { x = first[1], y = first[2], z = first[3] }, { x = last[1], y = last[2], z = last[3] },
               { x = final[1] + 3, y = final[2] + 2, z = final[3] } })
  GM_CALLS = {}
  core.onCreatedRallyGroundMarkerRoute()
end
function driveTo(s) CAR = at(s); core.onUpdate(1.1) end
function lastCall() return GM_CALLS[#GM_CALLS] end
"""


@pytest.fixture()
def run(lua):
    launched(lua)
    lua.execute(STUBS)
    return lua


def target_s(rt):
    """Metres along the liaison line of the single target of the arrows' path in force."""
    return rt.eval("(guide.along(ROAD, GM.endWP[1].x, GM.endWP[1].y, 0, ROAD.total))")


def test_the_game_plans_toward_a_far_control_and_we_take_over_with_a_target_the_graph_reaches(run):
    run.execute("coverLine(0, 1400)")                       # (the control is 18 km away, far outside the graph)
    run.execute("gameRoutes(0)")
    assert run.eval("#GM_CALLS") == 1
    call = run.eval("lastCall()")
    assert run.eval("#lastCall().wp") == 1                  # one target, not the race nodes and the control
    assert 1300 < target_s(run) <= 1400
    assert run.eval("lastCall().options.cutOffDrivability") == pytest.approx(0.5)
    assert run.eval("lastCall().options.penaltyBelowCutoff") > 1
    assert logs(run, "liaison road1 guidance: on the line")


def test_the_panel_distance_is_the_planners_plus_the_line_beyond_its_target(run):
    run.execute("coverLine(0, 1400)")
    run.execute("gameRoutes(0)")
    total = run.eval("ROAD.total")
    s_t = target_s(run)
    chord = run.eval("math.sqrt((GM.endWP[1].x - CAR[1])^2 + (GM.endWP[1].y - CAR[2])^2)")
    assert run.eval("GM.getPathLength()") == pytest.approx(chord + (total - s_t), abs=1)
    assert run.eval("GM.getPathLength()") > total * 0.9        # (not the 1.4 km the planner alone would say)


def test_the_distance_stays_right_when_the_planner_replans_on_its_own(run):
    run.execute("coverLine(0, 1400)")
    run.execute("gameRoutes(0)")
    want = run.eval("GM.getPathLength()")
    run.execute("GM.routePlanner:calcDistance()")           # (the car was off the path: the planner's re-plan forgets the rest)
    assert run.eval("GM.getPathLength()") < 1500
    run.execute("core.onRecalculatedRoute()")
    assert run.eval("GM.getPathLength()") == pytest.approx(want, abs=0.5)


def test_the_target_moves_on_with_the_car_in_steps_and_the_distance_goes_down_as_it_drives(run):
    run.execute("coverLine(0, 6000)")
    run.execute("gameRoutes(0)")
    first = target_s(run)
    d0 = run.eval("GM.getPathLength()")
    run.execute("GM_CALLS = {}")
    run.execute("driveTo(60)")                              # (a short way: the same target)
    assert run.eval("#GM_CALLS") == 0
    run.execute("driveTo(400)")
    assert run.eval("#GM_CALLS") == 1 and target_s(run) > first + 150
    # (the line left to the control, 400 m on: the planner's own part is the chord to the target, never longer than the line)
    left = run.eval("ROAD.total") - 400
    assert left - 1500 <= run.eval("GM.getPathLength()") <= left + 0.5 and run.eval("GM.getPathLength()") < d0 + 400


def test_a_car_that_left_the_line_is_led_to_the_line_ahead_from_where_it_is(run):
    run.execute("coverLine(0, 6000)")
    run.execute("gameRoutes(0)")
    # the car took another road: 400 m off the line, abreast of its 1000 m point
    run.execute("local p = at(1000); CAR = { p[1] + 400, p[2], p[3] }")
    run.execute("GM_CALLS = {}; core.onUpdate(1.1)")
    assert run.eval("#GM_CALLS") == 1
    s_t = target_s(run)
    assert s_t > 1000                                        # (ahead of where it rejoins the line)
    assert logs(run, "off the line")
    total = run.eval("ROAD.total")
    assert run.eval("GM.getPathLength()") >= (total - s_t) - 1


def test_a_missed_point_of_the_line_does_not_matter(run):
    # the arrows are not a chain of points to be passed: the target is one point ahead of the car and moves with it
    run.execute("coverLine(0, 6000)")
    run.execute("gameRoutes(0)")
    for s in (300, 900, 1500, 2100):
        run.execute("driveTo(%d)" % s)
        assert run.eval("#GM_CALLS[#GM_CALLS].wp") == 1
    run.execute("driveTo(3300)")                             # (a long stretch with no check in between: no stale waypoint)
    assert target_s(run) > 3300


def test_the_arrows_end_before_the_stop_when_it_is_in_reach(run):
    total = run.eval("ROAD.total")
    run.execute("coverLine(%f, %f)" % (total - 3000, total))
    run.execute("gameRoutes(%f)" % (total - 800))
    assert run.eval("lastCall().wp[1].x") == pytest.approx(run.eval("at(ROAD.total - 10)[1]"), abs=0.01)
    assert run.eval("GM.getPathLength()") == pytest.approx(
        run.eval("math.sqrt((GM.endWP[1].x - CAR[1])^2 + (GM.endWP[1].y - CAR[2])^2)"), abs=0.5)


def test_with_no_graph_under_the_line_the_target_is_the_nearest_point_of_it(run):
    run.execute("NODES = {}")
    run.execute("gameRoutes(500)")
    assert run.eval("#GM_CALLS") == 1 and run.eval("#lastCall().wp") == 1
    assert abs(target_s(run) - 500) < 5


def test_a_new_navgraph_has_the_arrows_planned_again_at_the_next_check(run):
    run.execute("coverLine(0, 1400)")
    run.execute("gameRoutes(0)")
    run.execute("GM_CALLS = {}; core.onUpdate(1.1)")
    assert run.eval("#GM_CALLS") == 0
    run.execute("core.onNavgraphReloaded(); core.onUpdate(1.1)")
    assert run.eval("#GM_CALLS") == 1


def test_a_rebase_changes_the_frame_and_the_line_with_it(run):
    run.execute("coverLine(0, 1400)")
    run.execute("gameRoutes(0)")
    run.execute("GM_CALLS = {}")
    run.execute("earthRebase(37.0900, -7.9800, 37.0950, -7.9700)")
    run.execute("ANCH = geodesy.newAnchor(37.0950, -7.9700); WORLD = legsmod.toWorld(GEO.points, ANCH, 0); ROAD = guide.line(WORLD)")
    run.execute("coverLine(0, 1400); CAR = at(0); core.onUpdate(1.1)")
    assert run.eval("#GM_CALLS") == 1
    assert 1300 < target_s(run) <= 1400                      # (on the line in the new frame)


def test_when_the_game_ends_the_guidance_ours_goes_too(run):
    run.execute("coverLine(0, 6000)")
    run.execute("gameRoutes(0)")
    run.execute("GM.setPath(nil); GM_CALLS = {}")            # (the control was reached: the game clears the arrows)
    run.execute("driveTo(500); driveTo(1500)")
    assert run.eval("#GM_CALLS") == 0 and run.eval("GM.endWP") is None


def test_the_game_setting_the_next_liaison_starts_over_and_a_route_that_is_not_ours_is_left_alone(run):
    run.execute("coverLine(0, 1400)")
    run.execute("CAR = at(0); GM.setPath({ { x = 5000000, y = 5000000, z = 0 } }); GM_CALLS = {}; core.onCreatedRallyGroundMarkerRoute()")
    assert run.eval("#GM_CALLS") == 0                        # (a control of no liaison of ours)
    run.execute("driveTo(100)")
    assert run.eval("#GM_CALLS") == 0


def test_a_gps_that_replaced_the_arrows_is_cleared_and_ours_come_back(run):
    run.execute("coverLine(0, 6000)")
    run.execute("gameRoutes(0)")
    run.execute("GM.setPath({ { x = 4000, y = 4000, z = 0 } }); GM_CALLS = {}")      # (Earth's GPS, not ours)
    run.execute("core.onUpdate(1.1)")
    assert run.eval("#GM_CALLS") == 0                        # (left alone while it is there)
    run.execute("GM.setPath(nil); GM_CALLS = {}")            # (claimGroundMarkers cleared it)
    run.execute("core.onUpdate(1.1)")
    assert run.eval("#GM_CALLS") == 1 and run.eval("#lastCall().wp") == 1
    assert target_s(run) > 1000


def test_the_guidance_is_over_with_the_loop(run):
    run.execute("coverLine(0, 6000)")
    run.execute("gameRoutes(0)")
    run.execute("core.onAnyMissionChanged('stopped', MISSION); GM_CALLS = {}")
    run.execute("GM.setPath({ { x = 1, y = 2, z = 3 } }); GM_CALLS = {}")
    run.execute("driveTo(500)")
    assert run.eval("#GM_CALLS") == 0


def test_a_failure_in_the_guidance_leaves_the_games_own_route(run):
    run.execute("coverLine(0, 1400)")
    run.execute("map = { getMap = function() error('no map') end }")
    run.execute("gameRoutes(0)")
    # (the map is asked through pcall: no graph, the target is the car's own place along the line)
    assert run.eval("GM.endWP ~= nil") is True
    run.execute("legsmod_toWorld = legsmod.toWorld; legsmod.toWorld = function() error('boom') end")
    run.execute("CAR = at(0); GM.setPath({ { x = 1, y = 2, z = 3 } }); core.onCreatedRallyGroundMarkerRoute()")
    assert logs(run, "liaison guidance failed")
    run.execute("legsmod.toWorld = legsmod_toWorld")


def test_the_position_readout_names_the_liaison_line_too(run):
    run.execute("CAR = at(1000); core.onUpdate(1.1)")
    assert any("line " in l and "road1" in l and "m along" in l for l in logs(run, "where:"))
