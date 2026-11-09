"""The pace notes of the real stages (Svolte di Popoli and the Algarve testbed): the notebook legs.materialize writes from
the editions' own geometry.

What a driver needs of the co-driver, checked on real roads rather than on synthetic ones: that the corners are called
along the whole stage, that the game's route puts every note in order (a note out of order is never called, or is
called early), that a wide sweeping bend is not called a hairpin, and that a real hairpin still is one.
"""
import json
import math
import os

import pytest

import stock_rally as sr

lupa = pytest.importorskip("lupa")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EARTH = os.environ.get("BEAMNG_EARTH_DIR") or os.path.join(os.path.dirname(ROOT), "BeamNG.Earth")
EARTH_EXT = os.path.join(EARTH, "mod", "beamng_earth", "lua", "ge", "extensions").replace("\\", "/")
DAKAR_EXT = os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions").replace("\\", "/")

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


@pytest.fixture(scope="module")
def rt():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end")
    rt.execute("package.path = package.path .. ';%s/?.lua;%s/?.lua'" % (EARTH_EXT, DAKAR_EXT))
    return rt


def stage(rt, edition_name):
    """{ notes, length, files } of the edition's one special, written at an anchor by its service park."""
    root = os.path.join(ROOT, "editions", edition_name)
    edition = json.load(open(os.path.join(root, "legs.json"), encoding="utf-8"))
    geometry = {leg["id"]: json.load(open(os.path.join(root, "geometry", leg["id"] + ".json"), encoding="utf-8"))
                for leg in edition["legs"]}
    geodesy, legs = rt.eval("(require('beamngEarth/geodesy'))"), rt.eval("(require('beamngDakar/legs'))")
    park = geometry[edition["legs"][0]["id"]]["points"][0]
    res = legs.materialize(conv(rt, edition), conv(rt, geometry), geodesy.newAnchor(park[0], park[1]),
                           rt.eval("function(leg) return 'm/' .. leg.id end"))
    ss = next(leg["id"] for leg in edition["legs"] if leg["type"] == "special")
    nb = res["files"]["m/%s/rally/notebooks/structured.notebook.json" % ss]
    notes = []
    for i in range(1, len(nb["pacenotes"]) + 1):
        n = nb["pacenotes"][i]
        item = n["structured"]["items"]["4"]
        m = n["metadata"]["dakar"]
        notes.append({
            "at": m["atM"], "direction": m["direction"], "angle": m["angleDeg"], "radius": m["radiusM"],
            "severity": m["severity"], "length": m["lengthM"],
            "descriptor": item["descriptor"] if "descriptor" in item.keys() else None,
            "raw": n,
        })
    return {"notes": notes, "length": res["lengths"][ss], "files": res["files"], "ss": ss, "res": res}


@pytest.fixture(scope="module")
def popoli(rt):
    return stage(rt, "svolte-di-popoli")


@pytest.fixture(scope="module")
def algarve(rt):
    if not os.path.isfile(os.path.join(ROOT, "editions", "testbed-algarve", "geometry", "ss1.json")):
        pytest.skip("the testbed's geometry is not there")
    return stage(rt, "testbed-algarve")


def test_popoli_is_called_along_the_whole_stage(popoli):
    notes, length = popoli["notes"], popoli["length"]
    assert length == pytest.approx(7250, abs=100)
    assert len(notes) >= 40
    ats = [n["at"] for n in notes]
    assert ats == sorted(ats)
    # the first corner is the stage's first bend (a long right 415 m up the road) and the last is near the stop
    assert ats[0] < 600
    assert ats[-1] > length - 400
    # no long stretch is left to the driver alone: the longest wait between two calls is under half a kilometre
    gaps = [b - a for a, b in zip(ats, ats[1:])]
    assert max(gaps) < 500
    # ... and the corners are spread over every kilometre of the stage
    for km in range(int(length // 1000)):
        assert any(km * 1000 <= a < (km + 1) * 1000 for a in ats), "nothing called in kilometre %d" % (km + 1)


def test_the_algarve_testbed_is_called_along_the_whole_stage(algarve):
    notes, length = algarve["notes"], algarve["length"]
    ats = [n["at"] for n in notes]
    assert len(notes) > 50 and ats == sorted(ats)
    assert ats[0] < 1000 and ats[-1] > length - 2000
    assert max(b - a for a, b in zip(ats, ats[1:])) < 4000           # (the testbed has a straight of 2.5 km)


def severity_of(diameter):
    return 1 + sum(diameter >= b for b in (35, 65, 116, 190, 260))


def _in_order_on_the_games_route(stage_):
    """The game's own route over the stage's driveline, with the race nodes and the notes' waypoints put in as the game puts
    them (drivelineRoute.loadRouteFromRecordedDriveline), and where along it each note's corner start and end fall."""
    files, ss = stage_["files"], stage_["ss"]
    dl = files["m/%s/rally/drivelineSpline.json" % ss]
    nodes = [tuple(dl["nodes"][i][k] for k in (1, 2, 3)) for i in range(1, len(dl["nodes"]) + 1)]
    race = files["m/%s/race.race.json" % ss]
    sp = {race["startPositions"][i]["name"]: tuple(race["startPositions"][i]["pos"][k] for k in (1, 2, 3))
          for i in range(1, len(race["startPositions"]) + 1)}
    route = sr._Route(sr.points_free(nodes))
    route.insert("startPos", sp["SS_start_line"])
    route.insert("stopZone", sp["SS_stop_control"])
    for i in range(1, len(race["pathnodes"]) + 1):
        route.insert("node%d" % i, tuple(race["pathnodes"][i]["pos"][k] for k in (1, 2, 3)))
    route.rebuild()
    for note in stage_["notes"]:
        for k in (1, 2):
            wp = note["raw"]["pacenoteWaypoints"][k]
            route.insert("%s %s" % (note["raw"]["name"], wp["waypointType"]), tuple(wp["pos"][j] for j in (1, 2, 3)))
    keys = [k for k, _ in route.pts]
    a, b = keys.index("startPos"), keys.index("stopZone")
    acc, at = 0.0, {}
    for n, (k, p) in enumerate(route.pts[a:b + 1]):
        if n:
            acc += sr.dist(p, route.pts[a:b + 1][n - 1][1])
        at[k] = acc
    return at, acc


@pytest.mark.parametrize("edition_fixture", ["popoli", "algarve"])
def test_the_games_route_puts_every_note_in_order_between_the_start_and_the_stop(request, edition_fixture):
    # the game calls a note when the car nears its corner start and then looks to the next one: a note whose waypoints
    # fall before the one ahead of it (or off the route, past the stop) is lost, and with it the ones behind it
    stage_ = request.getfixturevalue(edition_fixture)
    at, total = _in_order_on_the_games_route(stage_)
    previous_end = 0.0
    for note in stage_["notes"]:
        name = note["raw"]["name"]
        cs, ce = at.get(name + " cornerStart"), at.get(name + " cornerEnd")
        assert cs is not None and ce is not None, name + " is not on the route"
        assert previous_end <= cs < ce <= total, name + " is out of order"
        previous_end = ce


def test_popoli_first_corner_is_a_wide_bend_not_a_hairpin(popoli):
    first = popoli["notes"][0]
    assert first["direction"] == "right"
    assert 140 <= first["angle"] <= 160                # it does turn about 150 degrees ...
    assert first["length"] > 100                       # ... but over some 150 m, on a circle some 60 m in radius
    assert 50 <= first["radius"] <= 80
    assert first["descriptor"] is None                 # so it is not named a hairpin: it is a long number
    assert first["severity"] in (3, 4)                 # the game's "three" or "four" (circle of 65-116 m or 116-190 m across)


def test_popoli_hairpins_are_still_hairpins(popoli):
    # the two U-turns of the SR17: a left 800 m up the road and a right 4.9 km up
    hairpins = [n for n in popoli["notes"] if n["descriptor"] in ("hairpin", "tightHairpin", "openHairpin")]
    assert len(hairpins) == 2
    left, right = hairpins
    assert (left["direction"], right["direction"]) == ("left", "right")
    assert 700 < left["at"] < 900 and 4800 < right["at"] < 4950
    for h in hairpins:
        assert h["angle"] >= 150 and h["radius"] < 18 and h["severity"] == 1


def test_the_algarve_testbed_keeps_its_hairpin(algarve):
    hairpins = [n for n in algarve["notes"] if n["descriptor"] in ("hairpin", "tightHairpin", "openHairpin")]
    assert hairpins and all(h["angle"] >= 130 and h["radius"] < 25 for h in hairpins)
    assert any(abs(h["at"] - 26838) < 100 for h in hairpins)


@pytest.mark.parametrize("edition_fixture", ["popoli", "algarve"])
def test_a_name_is_earned_by_the_shape_on_the_real_roads(request, edition_fixture):
    # no corner is named a hairpin for its angle alone; every wide U-turn keeps its number
    for n in request.getfixturevalue(edition_fixture)["notes"]:
        if n["descriptor"] in ("hairpin", "tightHairpin", "openHairpin"):
            assert n["angle"] >= 130 and n["radius"] < 25
        if n["angle"] >= 130 and n["radius"] >= 25:
            assert n["descriptor"] is None
        # the number is the stock one for the circle: 1 is a diameter under 35 m, 6 is 260 m and over (the radius is rounded)
        assert n["severity"] in {severity_of(2 * n["radius"] - 0.2), severity_of(2 * n["radius"] + 0.2)}


def test_popoli_severities_follow_the_geometry(popoli):
    # the numbers run from the tight to the flat as the circle widens, and the stage uses the whole scale
    notes = popoli["notes"]
    by_severity = {s: [n["radius"] for n in notes if n["severity"] == s] for s in range(1, 7)}
    assert all(by_severity[s] for s in range(1, 7))
    for s in range(1, 6):
        assert max(by_severity[s]) <= min(by_severity[s + 1]) + 1.0     # (the radius is rounded to a tenth)
    # a hairpin is the tightest thing on the road
    assert min(n["radius"] for n in notes if n["descriptor"]) <= min(n["radius"] for n in notes if not n["descriptor"])


def test_the_log_line_says_what_the_notebook_calls(popoli):
    lines = [popoli["res"]["noteSummaries"][i] for i in range(1, len(popoli["res"]["noteSummaries"]) + 1)]
    assert len(lines) == 1 and lines[0].startswith("ss1: %d corners called" % len(popoli["notes"]))
    assert "2 by name" in lines[0]
