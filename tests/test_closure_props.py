"""props.lua's closures across the side roads of a special stage: where the pieces of each stand, from the closures file
tools/stage_closures.py wrote for the testbed edition."""
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
EDITION = os.path.join(ROOT, "editions", "testbed-algarve")
CLOSURES = os.path.join(EDITION, "closures", "ss1.json")

if not os.path.isdir(EARTH_EXT):
    pytest.skip("BeamNG.Earth not found (set BEAMNG_EARTH_DIR)", allow_module_level=True)
if not (os.path.isfile(CLOSURES) and os.path.isfile(os.path.join(EDITION, "geometry", "ss1.json"))):
    pytest.skip("run tools/fetch_route.py and tools/stage_closures.py editions/testbed-algarve first", allow_module_level=True)


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
def world():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end")
    rt.execute("package.path = package.path .. ';%s/?.lua;%s/?.lua'" % (EARTH_EXT, DAKAR_EXT))
    geodesy, legs, props = (rt.eval("(require('beamngEarth/geodesy'))"), rt.eval("(require('beamngDakar/legs'))"),
                            rt.eval("(require('beamngDakar/props'))"))
    edition = conv(rt, json.load(open(os.path.join(EDITION, "legs.json"), encoding="utf-8")))
    anchor = geodesy.newAnchor(37.1900, -8.0300)
    to_world = rt.eval("function(legs, a) return function(pts) return legs.toWorld(pts, a) end end")(legs, anchor)
    sites = props.closureSites(edition, conv(rt, {"ss1": json.load(open(CLOSURES))}))
    placed = props.placeClosures(sites, to_world)
    w = legs.toWorld(conv(rt, json.load(open(os.path.join(EDITION, "geometry", "ss1.json")))["points"]), anchor)
    road = [(w[i][1], w[i][2]) for i in range(1, len(w) + 1)]
    return {"rt": rt, "props": props, "legs": legs, "anchor": anchor, "edition": edition, "road": road,
            "sites": [sites[i] for i in range(1, len(sites) + 1)], "placed": [placed[i] for i in range(1, len(placed) + 1)]}


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    f = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.dist(p, (a[0] + f * dx, a[1] + f * dy))


def dist_to_road(p, road):
    return min(seg_dist(p, a, b) for a, b in zip(road, road[1:]))


def rows(world):
    by = {}
    for p in world["placed"]:
        sid, tag = str(p["id"]).rsplit(":", 1)
        by.setdefault(sid, []).append(p)
    return by


def test_the_testbed_stage_has_closures_and_each_is_one_or_two_lengths_of_stock_tape(world):
    sites = world["sites"]
    assert len(sites) > 100                                  # 39 km of public roads: a few per kilometre
    assert all(c["width"] in (6, 12) for c in sites)
    assert any(c["width"] == 12 for c in sites) and any(c["width"] == 6 for c in sites)
    assert len(world["placed"]) == sum(round(c["width"] / 6) for c in sites)
    ids = [str(p["id"]) for p in world["placed"]]
    assert len(set(ids)) == len(ids)                         # unique: the manager keys objects by id
    assert all(str(p["shape"]) == "/art/shapes/race/rally/rally_assets/s_tape_road_closure_6m.dae" for p in world["placed"])
    assert all(not p["far"] and p["radius"] == world["props"].CLOSURE_RADIUS_M for p in world["placed"])


def test_each_closure_is_a_row_of_tape_across_the_side_road_centred_on_it_and_clear_of_the_stage_road(world):
    road = world["road"]
    by = rows(world)
    assert len(by) == len(world["sites"])
    for sid, tapes in by.items():
        for t in tapes:
            # the tape runs from its post (the origin) along local -y for 6 m: its far post and its middle
            tx, ty = -math.cos(math.radians(t["yaw"] + 90)), -math.sin(math.radians(t["yaw"] + 90))
            for f in (0.0, 0.5, 1.0):
                assert dist_to_road((t["x"] + 6 * f * tx, t["y"] + 6 * f * ty), road) > 4.0, (sid, f)
        if len(tapes) == 2:                                  # side by side, end to end
            a, b = tapes
            tx, ty = -math.cos(math.radians(a["yaw"] + 90)), -math.sin(math.radians(a["yaw"] + 90))
            assert b["x"] - a["x"] == pytest.approx(6 * tx, abs=0.01) and b["y"] - a["y"] == pytest.approx(6 * ty, abs=0.01)


def test_the_row_is_across_the_side_roads_direction_in_the_world_frame(world):
    rt, legs, anchor = world["rt"], world["legs"], world["anchor"]
    first = {}
    for p in world["placed"]:
        first.setdefault(str(p["id"]).rsplit(":", 1)[0], p)
    for c in world["sites"][:80]:
        b = math.radians(c["bearing"])
        pts = conv(rt, [[c["lat"], c["lon"], c["h"]],
                        [c["lat"] + math.cos(b) * 10 / 111320,
                         c["lon"] + math.sin(b) * 10 / (111320 * math.cos(math.radians(c["lat"]))), c["h"]]])
        w = legs.toWorld(pts, anchor)
        dx, dy = w[2][1] - w[1][1], w[2][2] - w[1][2]
        t = first[str(c["id"])]
        tx, ty = -math.cos(math.radians(t["yaw"] + 90)), -math.sin(math.radians(t["yaw"] + 90))
        assert abs((dx * tx + dy * ty) / math.hypot(dx, dy)) < 0.01       # the tape's length is across the road
        # and the row is centred on the site: the middle of the row is the site itself
        n = round(c["width"] / 6)
        mid = (t["x"] + n * 3 * tx, t["y"] + n * 3 * ty)
        sw = legs.toWorld(rt.eval("function(a, b, c) return { { a, b, c } } end")(c["lat"], c["lon"], c["h"]), anchor)
        assert math.dist(mid, (sw[1][1], sw[1][2])) < 0.01


def test_the_closures_sit_on_the_terrains_height_and_in_the_anchors_frame(world):
    c = world["sites"][5]
    p = next(q for q in world["placed"] if str(q["id"]).startswith(str(c["id"]) + ":"))
    w = world["legs"].toWorld(world["rt"].eval("function(a, b, c) return { { a, b, c } } end")(c["lat"], c["lon"], c["h"]),
                              world["anchor"])
    assert p["z"] == pytest.approx(w[1][3], abs=0.01)


def test_an_edition_without_closure_files_has_none(world):
    props, rt = world["props"], world["rt"]
    assert len(props.closureSites(world["edition"], rt.table())) == 0
    assert len(props.closureSites(world["edition"], None)) == 0
    assert len(props.placeClosures(props.closureSites(world["edition"], None), lambda pts: pts)) == 0
