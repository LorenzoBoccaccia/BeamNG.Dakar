"""The Svolte di Popoli edition: the SR17 hillclimb as a loop with one special stage. Its legs and the files
tools/fetch_route.py and tools/stage_closures.py wrote, then what legs.materialize makes of them."""
import json
import math
import os

import pytest

lupa = pytest.importorskip("lupa")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EARTH = os.environ.get("BEAMNG_EARTH_DIR") or os.path.join(os.path.dirname(ROOT), "BeamNG.Earth")
EARTH_EXT = os.path.join(EARTH, "mod", "beamng_earth", "lua", "ge", "extensions").replace("\\", "/")
DAKAR_EXT = os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions").replace("\\", "/")
EDITION = os.path.join(ROOT, "editions", "svolte-di-popoli")

if not os.path.isdir(EARTH_EXT):
    pytest.skip("BeamNG.Earth not found (set BEAMNG_EARTH_DIR)", allow_module_level=True)


def conv(rt, o):
    if isinstance(o, dict):
        t = rt.table()
        for k, v in o.items():
            t[k] = conv(rt, v)
        return t
    if isinstance(o, list):
        t = rt.table()
        for i, v in enumerate(o, 1):
            t[i] = conv(rt, v)
        return t
    return o


def load_json(*parts):
    return json.load(open(os.path.join(EDITION, *parts), encoding="utf-8"))


def metres(a, b):
    dn = math.radians(b[0] - a[0]) * 6371008.8
    de = math.radians(b[1] - a[1]) * 6371008.8 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dn, de)


@pytest.fixture(scope="module")
def edition():
    return load_json("legs.json")


@pytest.fixture(scope="module")
def geometry(edition):
    return {leg["id"]: load_json("geometry", leg["id"] + ".json") for leg in edition["legs"]}


@pytest.fixture(scope="module")
def made(edition, geometry):
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end")
    rt.execute("package.path = package.path .. ';%s/?.lua;%s/?.lua'" % (EARTH_EXT, DAKAR_EXT))
    geodesy, legs = rt.eval("(require('beamngEarth/geodesy'))"), rt.eval("(require('beamngDakar/legs'))")
    # an anchor near the service park, as Earth's own would be
    park = geometry["road1"]["points"][0]
    res = legs.materialize(conv(rt, edition), conv(rt, geometry), geodesy.newAnchor(park[0], park[1]),
                           rt.eval("function(leg) return 'm/' .. leg.id end"))
    return res


def test_the_edition_is_one_transfer_and_one_special(edition):
    assert edition["edition"] == "svolte-di-popoli" and edition["missionPrefix"] == "dakar-popoli"
    assert [(l["id"], l["type"]) for l in edition["legs"]] == [("road1", "transfer"), ("ss1", "special")]
    assert edition["legs"][0]["parkingNear"]                      # (the service park is a parking lot near Popoli)


def test_the_special_is_the_published_hillclimb(geometry):
    # it.wikipedia: 7.53 km from 250 m (contrada Canapine, Popoli Terme) to 725 m (San Benedetto in Perillis)
    ss = geometry["ss1"]
    hs = [p[2] for p in ss["points"]]
    # (the stage starts past the junction of the side road, 40 m up the SR17 from it and well north of the railway underpass,
    # which is blocked in the game: )
    assert ss["lengthM"] == pytest.approx(7250, abs=100)
    assert hs[0] == pytest.approx(250, abs=10) and hs[-1] == pytest.approx(725, abs=10)
    assert hs[-1] - hs[0] == pytest.approx(475, abs=20)
    assert max(hs) - hs[-1] < 5 and hs[0] - min(hs) < 5             # a climb from the first point to the last
    assert ss["points"][0][:2] == pytest.approx([42.1748, 13.8243], abs=0.001)
    assert ss["points"][-1][:2] == pytest.approx([42.1969, 13.7864], abs=0.001)


def test_the_special_follows_the_sr17_hairpins(edition, geometry):
    # the via points lie on the SR17 line (chained from OpenStreetMap): the route passes each, in order, within a few
    # metres, so OSRM did not shortcut a hairpin; and it is far longer than the straight line across them
    pts = geometry["ss1"]["points"]
    assert geometry["ss1"]["lengthM"] > 1.5 * metres(pts[0], pts[-1])
    assert all(metres(a, b) <= 21 for a, b in zip(pts, pts[1:]))
    via = edition["legs"][1]["via"]
    assert len(via) >= 25
    last = 0
    for v in via:
        d, i = min((metres(v, p[:2]), i) for i, p in enumerate(pts))
        assert d < 5 and i >= last
        last = i


def test_the_transfer_is_short_and_leaves_a_parking_lot_for_the_stage_start(geometry):
    road, ss = geometry["road1"], geometry["ss1"]
    assert 100 < road["lengthM"] < 2000
    lot = road["servicePark"]
    assert lot["aisles"] > 1 and lot["lengthM"] >= 80
    assert metres((lot["lat"], lot["lon"]), (lot["spawn"]["lat"], lot["spawn"]["lon"])) < 1       # (the car starts on an aisle)
    assert metres(road["points"][0], lot["lead"][-1][:2]) < 1                                    # and TC0, where the lead ends, starts the leg
    assert metres(road["points"][-1], ss["points"][0]) < 1          # the stage starts where the transfer ends
    assert metres(road["points"][0], (42.1749, 13.8231)) < 50       # the car park north of the railway


def test_the_special_has_closures_on_its_side_roads(geometry):
    cl = load_json("closures", "ss1.json")["closures"]
    assert len(cl) >= 5
    ss = [p[:2] for p in geometry["ss1"]["points"]]
    for c in cl:
        d = min(metres((c["lat"], c["lon"]), p) for p in ss)
        assert 5 < d < 40                                          # near the stage line, not on it
        assert 0 <= c["s"] <= geometry["ss1"]["lengthM"]


def test_materialize_writes_the_race_path_driveline_and_notebook_of_each_leg(made):
    files = made["files"]
    for leg in ("road1", "ss1"):
        assert files["m/%s/race.race.json" % leg] and files["m/%s/rally/drivelineSpline.json" % leg]
    stage = files["m/ss1/race.race.json"]
    assert [stage["startPositions"][i]["name"] for i in (1, 2)] == ["SS_start_line", "SS_stop_control"]
    assert files["m/road1/rally/notebooks/structured.notebook.json"] is None
    assert made["lengths"]["ss1"] == pytest.approx(7250, abs=100)


def test_the_special_gets_split_checkpoints(made):
    # a split every 3 km, none in the last kilometre: two on 7.5 km, between the start node and the node before the stop
    pn = made["files"]["m/ss1/race.race.json"]["pathnodes"]
    nodes = [pn[i] for i in range(1, len(pn) + 1)]
    assert len(nodes) == 4
    assert [bool(n["useAsSplit"]) for n in nodes] == [False, True, True, False]


def test_the_special_gets_a_pace_note_notebook(made):
    nb = made["files"]["m/ss1/rally/notebooks/structured.notebook.json"]
    assert nb["name"] == "structured"
    notes = [nb["pacenotes"][i] for i in range(1, len(nb["pacenotes"]) + 1)]
    assert len(notes) > 30                                        # a hairpin road: a corner every couple of hundred metres
    assert {n["metadata"]["dakar"]["direction"] for n in notes} == {"left", "right"}
