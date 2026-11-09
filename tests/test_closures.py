"""tools/stage_closures.py without the network: the junctions of a small synthetic road network along a straight stage
(GitHub #33). The stage runs 1 km east; a closure is where a side road is shut, a few metres from the stage."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import stage_closures as sc  # noqa: E402

LAT, LON = 37.2, -8.0


def ll(x, y):
    """(lat, lon) of a point x metres east and y metres north of the origin."""
    return (LAT + y / 111320.0, LON + x / (111320.0 * math.cos(math.radians(LAT))))


def road(*pts, cls="residential", sub=None, elevated=False):
    return {"line": [ll(*p) for p in pts], "class": cls, "subclass": sub, "elevated": elevated}


STAGE = [ll(x, 0) for x in range(0, 1001, 100)]
# the stage's own road, in two segments that meet at the T junction at x = 300
OWN = [road((0, 0), (300, 0), cls="tertiary"), road((300, 0), (1000, 0), cls="tertiary")]


def found(*roads, extra=True):
    return sc.closures(STAGE, (OWN if extra else []) + list(roads))


def metres_from(c, x, y):
    plat, plon = ll(x, y)
    return math.hypot((c["lon"] - plon) * 111320.0 * math.cos(math.radians(LAT)), (c["lat"] - plat) * 111320.0)


def test_the_stages_own_road_gets_no_closure():
    assert found() == []


def test_a_side_road_that_starts_on_the_stage_is_closed_a_few_metres_in():
    cl = found(road((300, 0), (300, 200)))
    assert len(cl) == 1
    c = cl[0]
    assert metres_from(c, 300, 10) < 3.5                    # OFFSET_M along the side road
    assert abs(c["bearing"]) < 1 or abs(c["bearing"] - 360) < 1     # pointing north, away from the stage
    assert 290 < c["s"] < 310                               # where along the stage it meets it


def test_a_side_road_to_the_south_points_south():
    cl = found(road((300, 0), (300, -200)))
    assert len(cl) == 1 and abs(cl[0]["bearing"] - 180) < 1


def test_a_road_that_crosses_the_stage_is_closed_on_both_sides():
    cl = found(road((600, -200), (600, 200)))
    assert sorted(round(c["bearing"]) for c in cl) == [0, 180]
    assert all(metres_from(c, 600, 10 if c["bearing"] < 90 else -10) < 3.5 for c in cl)


def test_a_bridge_over_the_stage_is_left_alone_but_a_junction_on_a_road_of_another_level_is_not():
    assert found(road((600, -200), (600, 200), elevated=True)) == []
    assert len(found(road((300, 0), (300, 200), elevated=True))) == 1      # it starts on the stage: a real junction


def test_footways_paths_cycleways_and_parking_aisles_are_not_ways_off_a_stage():
    for cls, sub in (("footway", None), ("path", None), ("steps", None), ("cycleway", None), ("pedestrian", None),
                     ("service", "parking_aisle")):
        assert found(road((300, 0), (300, 200), cls=cls, sub=sub)) == [], cls


def test_tracks_and_service_roads_are_closed_but_a_short_driveway_is_not():
    assert len(found(road((450, 0), (450, 200), cls="track"))) == 1
    assert len(found(road((450, 0), (450, 200), cls="service"))) == 1
    assert found(road((450, 0), (450, 30), cls="service", sub="driveway")) == []
    assert len(found(road((450, 0), (450, 150), cls="service", sub="driveway"))) == 1


def test_a_stub_that_ends_before_the_closure_would_stand_gets_none():
    assert found(road((450, 0), (450, 7))) == []


def test_a_short_connector_leads_on_to_a_closure_in_each_road_beyond_it():
    cl = found(road((150, 0), (150, 6)), road((150, 6), (120, 60)), road((150, 6), (180, 60)))
    assert len(cl) == 2
    assert {c["bearing"] > 0 and c["bearing"] < 90 for c in cl} == {True, False}      # one leans east, one west
    assert all(sc_dist(c) >= sc.CLEAR_CENTRE_M for c in cl)


def sc_dist(c):
    """The distance of a closure from the stage line (the x axis)."""
    return abs((c["lat"] - LAT) * 111320.0)


def test_an_acute_fork_is_closed_where_the_whole_row_is_off_the_road():
    cl = found(road((700, 0), (900, 70)))                   # leaves the stage at about 19 degrees
    assert len(cl) == 1
    c = cl[0]
    r = math.radians(c["bearing"] + 90)
    y = (c["lat"] - LAT) * 111320.0
    for sgn in (-1, 0, 1):
        assert abs(y + sgn * sc.TAPE_M / 2 * math.cos(r)) >= sc.CLEAR_ENDS_M - 0.2
    assert metres_from(c, 700, 0) > sc.OFFSET_M


def test_the_roads_that_go_on_behind_the_start_and_beyond_the_finish_are_not_closed():
    assert found(road((0, 0), (-200, 0)), road((1000, 0), (1200, 0))) == []


def test_a_side_road_close_to_the_start_that_leaves_sideways_is_still_closed():
    cl = found(road((20, 0), (20, 150)))
    assert len(cl) == 1


def test_a_road_that_only_runs_beside_the_stage_is_not_a_junction():
    assert found(road((100, 40), (900, 40))) == []          # 40 m away all along
    assert found(road((100, 6), (700, 6))) == []            # alongside it for its whole length: it is the stage's road


def test_two_segments_of_one_arm_make_one_closure():
    cl = found(road((300, 0), (300, 200)), road((300, 0), (300, 200)))
    assert len(cl) == 1


def test_closures_come_in_order_along_the_stage():
    cl = found(road((800, 0), (800, 100)), road((300, 0), (300, 100)), road((500, 0), (500, 100)))
    assert [c["s"] for c in cl] == sorted(c["s"] for c in cl) and len(cl) == 3


def test_a_stage_with_no_other_roads_has_no_closures():
    assert found(extra=False) == []


def test_a_narrow_side_road_gets_one_length_of_tape_and_a_wide_one_two():
    narrow = found(road((300, 0), (300, 200), cls="residential"))[0]
    wide = found(road((300, 0), (300, 200), cls="tertiary"))[0]
    assert narrow["width"] == sc.TAPE_M and wide["width"] == 2 * sc.TAPE_M


def test_the_wide_closure_still_keeps_its_ends_off_the_stage_road():
    c = found(road((700, 0), (900, 70), cls="secondary"))[0]
    r = math.radians(c["bearing"] + 90)
    y = (c["lat"] - LAT) * 111320.0
    for sgn in (-1, 1):
        assert abs(y + sgn * c["width"] / 2 * math.cos(r)) >= sc.CLEAR_ENDS_M - 0.2
