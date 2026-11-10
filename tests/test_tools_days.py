"""The tools of an edition of days: the road network router, joins across a ferry, OSRM in pieces, the long stage's structures and closures,
the mission files of the days. No network: the readers of Overture and OSRM are replaced."""
import json
import math
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("PIL")

import fetch_route  # noqa: E402
import legplan  # noqa: E402
import roadnet  # noqa: E402
import stage_closures  # noqa: E402
import structures  # noqa: E402


def seg(id, line, cls="track", conns=(), flags=()):
    return {"id": id, "cls": cls, "sub": None, "surface": None, "line": line, "flags": list(flags), "elevated": False, "conns": list(conns)}


def two_tracks():
    a = seg("a", [(0.0, 0.0), (0.0, 0.01)], conns=[("c1", 0.0), ("c2", 1.0)])
    b = seg("b", [(0.0, 0.01), (0.0, 0.02)], conns=[("c2", 0.0), ("c3", 1.0)])
    return roadnet.Network([a, b])


def test_the_piste_router_follows_connected_tracks_and_says_nothing_is_off_the_network():
    net = two_tracks()
    pts, rep = net.route_report([(0.0, 0.0), (0.0, 0.02)], "piste")
    assert rep["crossingM"] == 0 and rep["networkM"] > 2000
    assert pts[0] == (0.0, 0.0) and pts[-1] == (0.0, 0.02)


def test_where_the_network_has_nothing_two_via_points_are_joined_straight_and_counted():
    net = two_tracks()
    pts, rep = net.route_report([(0.0, 0.0), (0.0, 0.02), (0.0, 0.3)], "piste")
    assert rep["crossingM"] > 25000                    # (0.28 degrees of longitude on the equator)
    assert rep["crossings"] and rep["crossings"][0][1] - rep["crossings"][0][0] > 25000
    assert pts[-1] == (0.0, 0.3)


def test_the_road_profile_prefers_the_big_road_and_the_piste_profile_the_track():
    a = seg("a", [(0.0, 0.0), (0.0, 0.02)], cls="track", conns=[("c1", 0.0), ("c2", 1.0)])
    b = seg("b", [(0.0, 0.0), (0.004, 0.01), (0.0, 0.02)], cls="primary", conns=[("c1", 0.0), ("c2", 1.0)])
    net = roadnet.Network([a, b])
    piste, _ = net.route_report([(0.0, 0.0), (0.0, 0.02)], "piste")
    road, _ = net.route_report([(0.0, 0.0), (0.0, 0.02)], "road")
    assert max(abs(p[0]) for p in piste) < 1e-9        # straight along the track
    assert max(abs(p[0]) for p in road) > 0.003        # the detour by the primary road


def test_roads_along_keeps_the_segments_near_the_line_only(monkeypatch):
    near = seg("near", [(37.0, -8.0), (37.0, -7.99)])
    far = seg("far", [(37.5, -8.0), (37.5, -7.99)])
    crossing = seg("cross", [(36.99, -7.995), (37.01, -7.995)])           # (no vertex near: only the sampled middle)
    monkeypatch.setattr(roadnet, "roads_near", lambda pts, margin, log=None: [near, far, crossing])
    line = [(37.0, -8.0 + i * 0.0002) for i in range(51)]
    got = [s["id"] for s in roadnet.roads_along(line, 60.0)]
    assert "near" in got and "cross" in got and "far" not in got


def test_osrm_asks_for_a_long_route_in_pieces_that_share_their_end(monkeypatch):
    calls = []

    def piece(via):
        calls.append(list(via))
        return [tuple(v) for v in via]
    monkeypatch.setattr(fetch_route, "osrm_piece", piece)
    via = [[37.0 + i * 0.01, -8.0] for i in range(45)]
    out = fetch_route.osrm_route(via)
    assert len(calls) == 3 and all(len(c) <= fetch_route.OSRM_VIAS for c in calls)
    assert calls[0][-1] == calls[1][0] and calls[1][-1] == calls[2][0]
    assert out == [tuple(v) for v in via]               # (each shared point once)


def test_a_legs_router_says_how_its_via_points_are_joined(monkeypatch):
    via = [[1.0, 2.0], [3.0, 4.0]]
    assert fetch_route.route(via, {"id": "x", "router": "straight"}) == [(1.0, 2.0), (3.0, 4.0)]
    monkeypatch.setattr(fetch_route, "osrm_route", lambda v: ["osrm"])
    assert fetch_route.route(via, {"id": "x"}) == ["osrm"]
    assert fetch_route.route(via) == ["osrm"]


def test_the_plan_leaves_out_the_ferry_and_does_not_join_across_it():
    def router(via, leg=None):
        return [tuple(v) for v in via]
    legs = [
        {"id": "a", "type": "transfer", "via": [[37.0, -8.0], [37.0, -7.9]]},
        {"id": "f", "type": "ferry", "from": {"lat": 37.0, "lon": -7.9}, "to": {"lat": 35.0, "lon": -3.0}},
        {"id": "r", "type": "rest"},
        {"id": "b", "type": "transfer", "via": [[35.0, -3.0], [35.0, -2.9]]},
    ]
    items = legplan.plan_edition(legs, router)
    assert [it["leg"]["id"] for it in items] == ["a", "b"]
    assert items[1]["joinIn"]["mode"] == "kept"
    assert items[0]["pts"][-1] == (37.0, -7.9) and items[1]["pts"][0] == (35.0, -3.0)


def test_a_router_that_knows_the_legs_is_given_the_leg():
    seen = []

    def router(via, leg=None):
        seen.append(leg["id"])
        return [tuple(v) for v in via]
    legplan.plan_edition([{"id": "a", "type": "special", "via": [[1.0, 1.0], [1.0, 1.1]]}], router, join=False)
    assert seen == ["a"]


def test_a_long_stages_structures_come_from_the_overture_flags(monkeypatch):
    bridge = seg("br", [(37.0, -7.995), (37.0, -7.99)], cls="track", flags=["is_bridge"])
    plain = seg("pl", [(37.0, -8.0), (37.0, -7.995)], cls="track")
    monkeypatch.setattr(roadnet, "roads_along", lambda pts, margin, log=None: [plain, bridge])
    line = [(37.0, -8.0 + i * 0.0002) for i in range(101)]
    found, beside, others = structures.record_all(line, long_leg=True)
    assert [f["kind"] for f in found] == ["bridge"]
    assert 400 < found[0]["from"] < 480 and found[0]["to"] > found[0]["from"] + 300 and found[0]["way"] == "br"
    assert others == []


def test_a_long_stages_closures_are_worked_in_pieces_and_keep_their_place_along_the_stage(monkeypatch):
    lat0 = 37.0
    line = [(lat0, -8.0 + i * 0.0002) for i in range(1501)]           # ~ 26.6 km due east
    side = {"id": "s", "cls": "unclassified", "sub": None, "line": [(lat0 - 0.003, -7.85), (lat0 + 0.003, -7.85)], "elevated": False}
    monkeypatch.setattr(roadnet, "roads_along", lambda pts, margin, log=None: [side])
    monkeypatch.setattr(stage_closures, "PIECE_M", 9000.0)
    cl, n = stage_closures.closures_long(line)
    assert n == 1 and len(cl) >= 1
    at = (-7.85 - -8.0) * 111320 * math.cos(math.radians(lat0))
    assert all(abs(c["s"] - at) < 40 for c in cl)
    assert sorted(c["bearing"] for c in cl) == [0.0, 180.0]          # (one closure a side: the piece overlap does not make a second)


def test_the_missions_of_the_days_loop_liaison_special_return_and_a_transfer_day_with_no_stage(tmp_path, monkeypatch):
    import make_missions
    missions = tmp_path / "missions"
    for kind, name in make_missions.TEMPLATE.items():
        src = os.path.join(make_missions.MISSIONS, kind, name)
        shutil.copytree(src, str(missions / kind / name))
    monkeypatch.setattr(make_missions, "MISSIONS", str(missions))
    ed = tmp_path / "ed"
    ed.mkdir()
    legs = [{"id": "S2-in", "type": "transfer", "title": "in"}, {"id": "S2", "type": "special", "title": "Stage", "specialKm": 100},
            {"id": "S2-out", "type": "transfer", "title": "out"}, {"id": "T2", "type": "transfer", "title": "road"},
            {"id": "R", "type": "rest"}]
    base = {"edition": "e", "title": "E", "missionPrefix": "dk", "legs": legs, "days": [
        {"id": "d01", "label": "Stage 2", "title": "Stage 2", "date": "d", "legs": ["S2-in", "S2", "S2-out"]},
        {"id": "d02", "label": "Transfer", "title": "T", "date": "d", "legs": ["T2"]},
        {"id": "d03", "label": "Rest", "title": "R", "kind": "rest", "date": "d", "legs": ["R"]}]}
    (ed / "legs.json").write_text(json.dumps(base))
    (ed / "index.json").write_text(json.dumps({"legs": {l: {"lengthM": 36000.0} for l in ("S2-in", "S2", "S2-out", "T2")}}))
    monkeypatch.setattr(sys, "argv", ["make_missions.py", str(ed)])
    make_missions.main()
    loop = json.load(open(missions / "rallyLoop" / "dk-d01" / "info.json"))
    td = loop["missionTypeData"]
    assert td["dakarEdition"] == "e/d01" and td["startingSSNumber"] == 2
    assert td["stage1_rallyRoadSection"].endswith("(beamng_earth/rallyRoadSection/dk-d01-S2-in)")
    assert td["stage1_rallyStage"].endswith("(beamng_earth/rallyStage/dk-d01-S2)")
    assert td["return_rallyRoadSection"].endswith("(beamng_earth/rallyRoadSection/dk-d01-S2-out)")
    assert td["stage2_rallyStage"] == "<none>" and loop["devMission"] is False
    stage = json.load(open(missions / "rallyStage" / "dk-d01-S2" / "info.json"))
    assert stage["devMission"] is True and stage["missionTypeData"]["goldTime"] == 2000.0       # 36 km at 18 m/s
    assert stage["missionTypeData"]["bronzeTime"] == 3000.0
    assert stage["layers"][0]["dir"].endswith("/rallyStage/dk-d01-S2/")
    transfer_day = json.load(open(missions / "rallyLoop" / "dk-d02" / "info.json"))["missionTypeData"]
    assert transfer_day["stage1_rallyStage"] == "<none>" and transfer_day["return_rallyRoadSection"].endswith("dk-d02-T2)")
    assert not (missions / "rallyLoop" / "dk-d03").exists()                                 # (no mission for a rest day)
