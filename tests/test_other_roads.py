"""The other roads near a stage's line, from OpenStreetMap (tools/structures.py other_roads): ground the shaping does not move.

    python -m pytest tests
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import structures  # noqa: E402

from test_structures import LINE, LAT, LON, at, way, road_plain  # noqa: E402

M_LAT = 111200.0
M_LON = 111200.0 * math.cos(math.radians(LAT))


def east_north(p):
    """A recorded point's metres east and north of the stage's origin."""
    return (p[1] - LON) * M_LON, (p[0] - LAT) * M_LAT


def test_other_roads_are_the_pieces_of_the_other_drivable_ways_near_the_line_without_the_stage_road_itself():
    own = road_plain()
    beside = way(2, [(0, 12), (1000, 12)], highway="motorway", lanes="2", oneway="yes")           # (the A25 beside the stage road)
    far = way(3, [(0, 60), (1000, 60)])
    foot = way(4, [(100, 10), (300, 10)], highway="footway")
    got = structures.other_roads(LINE, [own, beside, far, foot])
    assert [o["way"] for o in got] == [2]
    assert got[0]["hw"] == 6.5 and len(got[0]["pts"]) > 100                                       # (a motorway: carriageway and shoulders)
    assert all(abs(east_north(p)[0] - 12) < 0.2 for p in got[0]["pts"])                          # (12 m east of the line)


def test_a_crossing_road_is_recorded_without_its_crossing_and_a_junction_without_its_mouth():
    cross = way(2, [(500, -80), (500, 80)], highway="residential")
    side = way(3, [(300, 0), (300, 90)], highway="service")                                       # (leaves the stage road at a junction)
    got = structures.other_roads(LINE, [road_plain(), cross, side])
    assert sorted(o["way"] for o in got) == [2, 2, 3]                                             # (the crossing road on either side of the line)
    for o in got:
        # each piece is between the stage road's own ground (OWN_M) and OTHER_NEAR_M from the line
        assert all(structures.OWN_M - 0.3 <= abs(east_north(p)[0]) <= structures.OTHER_NEAR_M + 0.3 for p in o["pts"]), o["way"]


def test_bridges_tunnels_are_not_other_roads_ground_and_a_culvert_is_no_tunnel():
    deck = way(2, [(0, 12), (1000, 12)], highway="motorway", bridge="yes", layer="1")             # (the A25's viaduct: a deck, not ground)
    tube = way(3, [(0, 14), (1000, 14)], highway="motorway", tunnel="yes", layer="-1")
    culvert = way(4, [(0, 16), (1000, 16)], tunnel="culvert")                                    # (a road on the ground)
    got = structures.other_roads(LINE, [road_plain(), deck, tube, culvert])
    assert [o["way"] for o in got] == [4]


def test_a_road_that_runs_beside_the_line_for_a_stretch_only_is_recorded_where_it_does():
    leaves = way(2, [(0, 6), (400, 6), (700, 400)], highway="residential")                       # (beside the line for 400 m, then away)
    got = structures.other_roads(LINE, [leaves])
    ends = [east_north(p)[1] for o in got for p in o["pts"]]
    assert 390 < max(ends) < 480 and min(ends) < 5


def test_the_half_width_of_a_way_by_its_tags():
    assert structures.half_width({"highway": "motorway", "lanes": "2"}) == 6.5
    assert structures.half_width({"highway": "residential"}) == 3.0
    assert structures.half_width({"highway": "residential", "lanes": "2"}) == 4.0
    assert structures.half_width({"highway": "track"}) == 1.5
    assert structures.half_width({"highway": "service", "width": "4 m"}) == 2.0
    assert structures.half_width({"highway": "primary", "lanes": "four"}) == 4.5


def test_record_all_gives_the_structures_the_tunnels_beside_and_the_other_roads_from_one_fetch(monkeypatch):
    ways = [road_plain(), way(2, [(0, 12), (1000, 12)], highway="motorway"), way(3, [(500, -200), (500, 200)], highway="motorway", tunnel="yes")]
    monkeypatch.setattr(structures, "fetch_ways", lambda points: ways)
    found, beside, others = structures.record_all(LINE)
    assert found == [] and [b["way"] for b in beside] == [3] and {o["way"] for o in others} == {2}
    assert structures.other_roads([(LAT, LON)], []) == []


def geometries():
    base = os.path.join(ROOT, "editions")
    for ed in sorted(os.listdir(base)):
        gdir = os.path.join(base, ed, "geometry")
        if not os.path.isdir(gdir):
            continue
        for fn in sorted(os.listdir(gdir)):
            yield ed, fn, json.load(open(os.path.join(gdir, fn), encoding="utf-8"))


def test_the_editions_geometry_files_carry_other_roads_that_are_well_formed():
    seen = 0
    for ed, fn, geo in geometries():
        for o in geo.get("otherRoads", []):
            assert o["way"] and 0.5 <= o["hw"] <= 12 and len(o["pts"]) >= 2, (ed, fn, o["way"])
            assert all(len(p) == 2 and -90 < p[0] < 90 and -180 < p[1] < 180 for p in o["pts"])
            seen += 1
    assert seen > 0


def test_the_popoli_stage_records_no_a25_deck_or_tube_and_not_its_own_road_as_other_roads():
    # the A25 at the stage's 690 m: its tunnel under the line (the ground over it is Earth's: `tunnelsBeside`), its plain ways 35 m off and its
    # viaduct 55 m: out of the shaping's 11.5 m. The SR17 the line runs on is the stage's own road
    geo = json.load(open(os.path.join(ROOT, "editions", "svolte-di-popoli", "geometry", "ss1.json"), encoding="utf-8"))
    ways = {o["way"] for o in geo["otherRoads"]}
    assert not ways & {29382771, 29382821, 29382785, 29382955}
    assert not ways & {554988563, 554988564}
