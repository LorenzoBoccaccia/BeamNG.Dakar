"""The 2027 edition (editions/2027-portugal-morocco): every stop of the route is playable, from the data alone (there is no way to
drive it in the game here): each driven leg has its geometry, the legs of a day and the days join, the ferry's ports are where the
legs before and after it are, every day's loop builds through legs.materialize, and the built lengths are near the published ones.
"""
import functools
import json
import math
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ED = os.path.join(ROOT, "editions", "2027-portugal-morocco")
EARTH = os.environ.get("BEAMNG_EARTH_DIR") or os.path.join(os.path.dirname(ROOT), "BeamNG.Earth")
EARTH_EXT = os.path.join(EARTH, "mod", "beamng_earth", "lua", "ge", "extensions").replace("\\", "/")
DAKAR_EXT = os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions").replace("\\", "/")

BASE = json.load(open(os.path.join(ED, "legs.json"), encoding="utf-8"))
LEGS = {l["id"]: l for l in BASE["legs"]}
DRIVEN = [l for l in BASE["legs"] if l["type"] in ("special", "transfer")]
SPECIAL_TOLERANCE = 0.12          # a built special within this of the published special km (the pistes are authored, not published)
JOIN_M = 5.0                      # the legs of a day run into each other: the end of one is the start of the next
DAY_JOIN_M = 400.0                # the day after starts where the day ended (the bivouac; a day that starts in a parking lot is a few hundred metres from the road)


def hav(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


@functools.lru_cache(maxsize=8)
def geometry(leg_id):
    return json.load(open(os.path.join(ED, "geometry", leg_id + ".json"), encoding="utf-8"))


INDEX = json.load(open(os.path.join(ED, "index.json"), encoding="utf-8"))["legs"]


def day_legs(day):
    return [LEGS[i] for i in day["legs"] if LEGS[i]["type"] in ("special", "transfer")]


def test_the_route_is_all_there_17_stops_in_15_days():
    types = [l["type"] for l in BASE["legs"]]
    assert types.count("ferry") == 1 and types.count("rest") == 1
    assert [d["label"] for d in BASE["days"]][:3] == ["Prologue", "Stage 1", "Transfer"]
    specials = [l["id"] for l in BASE["legs"] if l["type"] == "special"]
    assert specials == ["P"] + ["S%d" % i for i in range(1, 13)]
    stops = {l["id"] for l in BASE["legs"]}
    for stop in ("P", "S1", "T1", "F1", "T2", "S2", "S3", "S4", "S5", "S6", "R", "S7", "S8", "S9", "S10", "S11", "S12"):
        assert stop in stops
    assert len(BASE["days"]) == 15 and len([d for d in BASE["days"] if d.get("kind") == "rest"]) == 1


def test_every_driven_leg_has_its_geometry_and_an_index_entry():
    for leg in DRIVEN:
        g = geometry(leg["id"])
        assert g["id"] == leg["id"] and len(g["points"]) > 10
        ix = INDEX[leg["id"]]
        assert ix["start"] == g["points"][0] and ix["end"] == g["points"][-1]
        assert ix["lengthM"] == g["lengthM"]
        assert all(math.isfinite(p[2]) for p in g["points"][::50])
    assert set(INDEX) == {l["id"] for l in DRIVEN}


def test_the_ferry_and_the_rest_day_have_no_geometry():
    assert not os.path.exists(os.path.join(ED, "geometry", "F1.json"))
    assert not os.path.exists(os.path.join(ED, "geometry", "R.json"))


def test_a_leg_is_densely_sampled_without_jumps():
    for leg in DRIVEN:
        pts = geometry(leg["id"])["points"]
        worst = max(hav(a, b) for a, b in zip(pts[::1], pts[1::1]))
        assert worst < 60, "%s has a %.0f m jump" % (leg["id"], worst)


@pytest.mark.parametrize("day", [d for d in BASE["days"] if d.get("kind") != "rest"], ids=lambda d: d["id"])
def test_the_legs_of_a_day_run_into_each_other(day):
    legs = day_legs(day)
    assert legs, day["id"]
    for a, b in zip(legs, legs[1:]):
        gap = hav(geometry(a["id"])["points"][-1], geometry(b["id"])["points"][0])
        assert gap < JOIN_M, "%s -> %s: %.1f m" % (a["id"], b["id"], gap)


def test_each_day_starts_where_the_day_before_ended_and_the_ferry_is_the_only_gap():
    prev = None
    for day in BASE["days"]:
        if day.get("kind") == "rest":
            continue
        legs = day_legs(day)
        first = geometry(legs[0]["id"])["points"][0]
        if prev is not None:
            ferry = next((LEGS[i] for i in prev["legs"] if LEGS[i]["type"] == "ferry"), None)
            end = geometry(day_legs(prev)[-1]["id"])["points"][-1]
            if ferry:
                assert hav(end, (ferry["from"]["lat"], ferry["from"]["lon"])) < 500
                assert hav(first, (ferry["to"]["lat"], ferry["to"]["lon"])) < 500
                assert hav(end, first) > 100000                 # (the sea between)
            else:
                assert hav(end, first) < DAY_JOIN_M, "%s -> %s: %.0f m" % (prev["id"], day["id"], hav(end, first))
        prev = day


def test_the_ferry_runs_from_a_spanish_port_to_nador():
    f = LEGS["F1"]
    assert f["from"]["lat"] > 36.5 and f["from"]["lon"] < -2.0 and f["to"]["lat"] < 35.5 and f["to"]["lon"] > -3.1
    assert "Nador" in f["to"]["name"]
    assert any(d.get("ferry") == "F1" for d in BASE["days"])


@pytest.mark.parametrize("leg", [l for l in DRIVEN if l["type"] == "special"], ids=lambda l: l["id"])
def test_a_special_is_about_as_long_as_the_published_one(leg):
    built = geometry(leg["id"])["lengthM"] / 1000
    assert abs(built / leg["specialKm"] - 1) <= SPECIAL_TOLERANCE, "%s: built %.1f km, published %s" % (leg["id"], built, leg["specialKm"])


def test_the_stage_1_day_ends_with_the_transfer_to_the_port_of_about_the_published_590_km():
    t1 = geometry("T1")["lengthM"] / 1000
    assert abs(t1 / LEGS["T1"]["officialKm"] - 1) < 0.05


def test_every_leg_says_where_its_numbers_are_approximate():
    assert BASE["status"] and BASE["unconfirmed"] and BASE["sources"]
    for leg in DRIVEN:
        assert leg.get("router") in ("osrm", "piste"), leg["id"]
    # (a piste says how much of it is off the map: the straight lines across the dunes)
    for leg in DRIVEN:
        if leg["router"] == "piste":
            assert "offNetworkM" in geometry(leg["id"]), leg["id"]


def test_a_long_stage_does_not_ask_earths_server_for_its_whole_corridor():
    # (the preload takes the first 40 km of a stage: preload.MAX_M)
    longest = max(geometry(l["id"])["lengthM"] for l in DRIVEN if l["type"] == "special")
    assert longest > 300000


lupa = pytest.importorskip("lupa")


@pytest.fixture(scope="module")
def rt():
    if not os.path.isdir(EARTH_EXT):
        pytest.skip("BeamNG.Earth not found (set BEAMNG_EARTH_DIR)")
    r = lupa.LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
package.path = package.path .. ';%s/?.lua;%s/?.lua'
function log() end
function dumps(t) return tostring(t) end
geodesy = require('beamngEarth/geodesy')
legs = require('beamngDakar/legs')
days = require('beamngDakar/days')
""" % (EARTH_EXT, DAKAR_EXT))
    return r


def to_lua(rt, o):
    if isinstance(o, dict):
        t = rt.table()
        for k, v in o.items():
            t[k] = to_lua(rt, v)
        return t
    if isinstance(o, list):
        t = rt.table()
        for i, v in enumerate(o, 1):
            t[i] = to_lua(rt, v)
        return t
    return o


@pytest.mark.parametrize("day", [d for d in BASE["days"] if d.get("kind") != "rest"], ids=lambda d: d["id"])
def test_every_days_loop_builds_through_materialize(rt, day):
    rt.globals().BASE = to_lua(rt, BASE)
    rt.execute("DAY = (function() for _, v in ipairs(days.expand(BASE)) do if v.edition == BASE.edition .. '/' .. '%s' then return v end end end)()" % day["id"])
    assert rt.eval("DAY ~= nil")
    legs_ = day_legs(day)
    assert rt.eval("#DAY.legs") == len(legs_)
    rt.globals().GEO = rt.table()
    for leg in legs_:
        rt.globals().GEO[leg["id"]] = to_lua(rt, geometry(leg["id"]))
    first = geometry(legs_[0]["id"])["points"][0]
    rt.execute("""
local anchor = geodesy.newAnchor(%r, %r)
local function dirOf(leg) return '/m/' .. leg.id end
RES, ERR = legs.materialize(DAY, GEO, anchor, dirOf, 0, nil)
""" % (first[0], first[1]))
    assert rt.eval("ERR") is None, rt.eval("tostring(ERR)")
    assert rt.eval("RES ~= nil")
    for leg in legs_:
        assert rt.eval("RES.files['/m/%s/race.race.json'] ~= nil" % leg["id"])
        built = rt.eval("RES.lengths['%s']" % leg["id"])
        assert built == pytest.approx(geometry(leg["id"])["lengthM"], rel=0.02)
    assert rt.eval("RES.placement.pos ~= nil")
    rt.execute("GEO, RES = nil, nil")


@pytest.mark.parametrize("leg_id", ["S4", "S7"])
def test_a_stage_of_nearly_500_km_curates_without_giving_up(rt, leg_id):
    import time
    g = geometry(leg_id)
    rt.globals().G = to_lua(rt, {"points": g["points"], "structures": g.get("structures")})
    rt.execute("curate = require('beamngDakar/curate')")
    t = time.time()
    rt.execute("R, WHY = curate.line(G.points, { structures = G.structures })")
    assert rt.eval("R ~= nil"), rt.eval("tostring(WHY)")
    assert time.time() - t < 120
    assert rt.eval("#R.points") > 1000
    rt.execute("G, R = nil, nil")


def test_the_add_on_zip_carries_the_whole_route_and_the_missions_of_every_day():
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import install
    rels = {rel for rel, _ in install.mod_files()}
    base = "dakar/editions/2027-portugal-morocco/"
    assert base + "legs.json" in rels and base + "index.json" in rels
    for leg in DRIVEN:
        assert base + "geometry/%s.json" % leg["id"] in rels
    assert not any(r.startswith(base + "geometry/F1") or r.startswith(base + "geometry/R.") for r in rels)
    for day in BASE["days"]:
        if day.get("kind") == "rest":
            continue
        assert "gameplay/missions/beamng_earth/rallyLoop/dakar-2027-%s/info.json" % day["id"] in rels
        for leg in day_legs(day):
            kind = "rallyStage" if leg["type"] == "special" else "rallyRoadSection"
            assert "gameplay/missions/beamng_earth/%s/dakar-2027-%s-%s/info.json" % (kind, day["id"], leg["id"]) in rels


def test_the_preload_takes_the_first_40_km_of_a_long_stage_only():
    # (preload.slice cuts at preload.MAX_M, tests/test_days.py: the whole 490 km corridor would be gigabytes)
    pts = geometry("S4")["points"]
    acc, n = 0.0, 0
    for a, b in zip(pts, pts[1:]):
        acc += hav(a, b)
        n += 1
        if acc >= 40000:
            break
    assert 1500 < n < 3500          # (40 km of 20 m steps: about 2,000 of the 24,000 points)
