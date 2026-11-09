"""the bridges and tunnels of a stage's own road from OpenStreetMap's tags (tools/structures.py), on a synthetic line.

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

LAT, LON = 42.17, 13.82


def at(north_m, east_m):
    return (LAT + north_m / 111200.0, LON + east_m / (111200.0 * math.cos(math.radians(LAT))))


def way(wid, pts, **tags):
    tags.setdefault("highway", "secondary")
    return {"id": wid, "tags": tags, "geometry": [at(n, e) for n, e in pts]}


# the stage: 1 km straight north from the origin, a point every 20 m
LINE = [at(n, 0.0) for n in range(0, 1001, 20)]


def road_plain():
    return way(1, [(0, 0), (1000, 0)])


def test_a_bridge_way_the_line_runs_on_is_a_span_with_its_ends_where_the_way_has_them():
    ways = [way(1, [(0, 0), (300, 0)]), way(2, [(300, 0), (420, 0)], bridge="yes", layer="1"), way(3, [(420, 0), (1000, 0)])]
    got = structures.spans(LINE, ways)
    assert len(got) == 1
    s = got[0]
    assert s["kind"] == "bridge" and s["way"] == 2
    assert abs(s["from"] - 300) <= structures.SAMPLE_M and abs(s["to"] - 420) <= structures.SAMPLE_M


def test_a_tunnel_is_a_span_and_a_culvert_is_not():
    ways = [way(1, [(0, 0), (600, 0)]), way(2, [(600, 0), (700, 0)], tunnel="yes", layer="-1"), way(3, [(700, 0), (1000, 0)]),
            way(4, [(100, -4), (160, 4)], tunnel="culvert", layer="-1", highway="residential")]
    got = structures.spans(LINE, ways)
    assert [(s["kind"], round(s["from"] / 10), round(s["to"] / 10)) for s in got] == [("tunnel", 60, 70)]


def test_a_viaduct_over_the_road_is_not_the_road_it_crosses_perpendicularly_or_at_an_angle():
    # the A25 over the Svolte di Popoli's SR17: the road itself is the plain way the whole length, the motorway's bridge ways cross it at
    # 90 degrees (here) and at 22 degrees, and both are within a few metres of the line where they cross it
    cross90 = way(2, [(100, -200), (100, 200)], highway="motorway", bridge="yes", layer="1")
    a = math.radians(22)
    cross22 = way(3, [(500 - 200 * math.sin(a), -200 * math.cos(a)), (500 + 200 * math.sin(a), 200 * math.cos(a))],
                  highway="motorway", bridge="yes", layer="1")
    assert structures.spans(LINE, [road_plain(), cross90, cross22]) == []
    # (even with no plain way beyond the crossing's reach the angle keeps it out: the 90 degree one)
    assert structures.spans(LINE, [way(1, [(0, 0), (90, 0)]), way(4, [(110, 0), (1000, 0)]), cross90]) == []


def test_a_bridge_alongside_the_road_that_is_not_drivable_or_not_the_nearest_is_not_the_road():
    foot = way(2, [(300, 3), (420, 3)], highway="footway", bridge="yes")
    assert structures.spans(LINE, [road_plain(), foot]) == []
    # a plain road right on the line and a bridge way 8 m beside it (a parallel carriageway): the plain one is the nearest
    beside = way(3, [(300, 8), (420, 8)], bridge="yes")
    assert structures.spans(LINE, [road_plain(), beside]) == []


def test_short_gaps_are_merged_and_slivers_dropped():
    ways = [way(1, [(0, 0), (200, 0)]), way(2, [(200, 0), (300, 0)], bridge="yes"), way(3, [(300, 0), (306, 0)]),
            way(4, [(306, 0), (400, 0)], bridge="yes"), way(5, [(400, 0), (1000, 0)]),
            way(6, [(700, 0), (703, 0)], bridge="yes")]
    got = structures.spans(LINE, ways)
    assert len(got) == 1 and got[0]["from"] < 205 and got[0]["to"] > 395          # (one bridge across a 6 m gap; the 3 m piece is nothing)


def test_the_spans_are_metres_along_the_line_the_way_curate_lua_reads_them(tmp_path):
    ways = [way(1, [(0, 0), (300, 0)]), way(2, [(300, 0), (420, 0)], bridge="yes"), way(3, [(420, 0), (1000, 0)])]
    got = structures.spans(LINE, ways)
    text = json.dumps({"structures": got})
    back = json.loads(text)["structures"][0]
    assert set(back) == {"from", "to", "kind", "way"} and back["from"] < back["to"] and isinstance(back["kind"], str)


def test_kind_of_the_tags():
    assert structures.kind_of({"bridge": "yes"}) == "bridge"
    assert structures.kind_of({"bridge": "viaduct"}) == "bridge"
    assert structures.kind_of({"tunnel": "yes"}) == "tunnel"
    assert structures.kind_of({"tunnel": "building_passage"}) == "tunnel"
    assert structures.kind_of({"tunnel": "culvert"}) is None
    assert structures.kind_of({"bridge": "no"}) is None
    assert structures.kind_of({"highway": "secondary"}) is None


def test_the_editions_geometry_files_carry_structures_that_lie_on_their_line():
    base = os.path.join(ROOT, "editions")
    for ed in sorted(os.listdir(base)):
        gdir = os.path.join(base, ed, "geometry")
        if not os.path.isdir(gdir):
            continue
        for fn in sorted(os.listdir(gdir)):
            geo = json.load(open(os.path.join(gdir, fn), encoding="utf-8"))
            last = 0.0
            for s in geo.get("structures", []):
                assert s["kind"] in ("bridge", "tunnel") and 0 <= s["from"] < s["to"] <= geo["lengthM"] + 20, (ed, fn, s)
                assert s["from"] >= last - 1e-6                                   # (in order, apart)
                last = s["to"]
