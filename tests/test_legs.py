"""Offline tests for mod/beamng_dakar/.../legs.lua with BeamNG.Earth's real geodesy.lua, run in LuaJIT (lupa).

    python -m pytest tests

BeamNG.Earth is found at $BEAMNG_EARTH_DIR, else the sibling folder ../BeamNG.Earth.
"""
import json
import math
import os

import pytest

lupa = pytest.importorskip("lupa")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EARTH = os.environ.get("BEAMNG_EARTH_DIR") or os.path.join(os.path.dirname(ROOT), "BeamNG.Earth")
EARTH_EXT = os.path.join(EARTH, "mod", "beamng_earth", "lua", "ge", "extensions")
DAKAR_EXT = os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions")
EDITION = os.path.join(ROOT, "editions", "testbed-algarve")

if not os.path.isdir(EARTH_EXT):
    pytest.skip("BeamNG.Earth not found (set BEAMNG_EARTH_DIR)", allow_module_level=True)
if not os.path.isfile(os.path.join(EDITION, "geometry", "ss1.json")):
    pytest.skip("run tools/fetch_route.py editions/testbed-algarve first", allow_module_level=True)


@pytest.fixture(scope="module")
def lua():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    # (the game runs LuaJIT; lupa may bundle Lua 5.4+, where BeamNG.Earth's math.atan2 is gone)
    rt.execute("math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end")
    rt.execute("package.path = package.path .. ';%s/?.lua;%s/?.lua'" % (
        EARTH_EXT.replace("\\", "/"), DAKAR_EXT.replace("\\", "/")))
    return rt


@pytest.fixture(scope="module")
def mods(lua):
    # (require returns the module first; Lua 5.4+ adds the loader data after it)
    return (lua.eval("(require('beamngEarth/geodesy'))"), lua.eval("(require('beamngDakar/legs'))"))


def load():
    edition = json.load(open(os.path.join(EDITION, "legs.json"), encoding="utf-8"))
    geo = {leg["id"]: json.load(open(os.path.join(EDITION, "geometry", leg["id"] + ".json"))) for leg in edition["legs"]}
    return edition, geo


def _conv(lua, o):
    if isinstance(o, dict):
        t = lua.table()
        for k, v in o.items():
            t[k] = _conv(lua, v)
        return t
    if isinstance(o, list):
        t = lua.table()
        for i, v in enumerate(o, 1):
            t[i] = _conv(lua, v)
        return t
    return o


def materialize(lua, mods, anchor_lat, anchor_lon):
    geodesy, legs = mods
    edition, geo = load()
    anchor = geodesy.newAnchor(anchor_lat, anchor_lon)
    res = legs.materialize(_conv(lua, edition), _conv(lua, geo), anchor, lambda leg: "m/" + leg["id"])
    return anchor, res


def pyl(t):
    return [pyl(t[i]) if hasattr(t[i], "keys") else t[i] for i in range(1, len(t) + 1)]


def test_files_and_names(lua, mods):
    _, res = materialize(lua, mods, 37.0884, -7.9755)
    files = res[0]["files"] if isinstance(res, tuple) else res["files"]
    assert files["m/road1/race.race.json"] and files["m/ss1/race.race.json"]
    road = files["m/road1/race.race.json"]
    names = [road["startPositions"][i]["name"] for i in (1, 2)]
    assert names == ["TC_in", "TC_out"]
    stage = files["m/ss1/race.race.json"]
    assert [stage["startPositions"][i]["name"] for i in (1, 2)] == ["SS_start_line", "SS_stop_control"]
    assert len(stage["pathnodes"]) >= 3 and len(stage["segments"]) == len(stage["pathnodes"]) - 1
    dl = files["m/ss1/rally/drivelineSpline.json"]
    assert len(dl["nodes"]) == len(dl["nmls"]) == len(dl["widths"])


def _along(pts, pos):
    """Arc length of the driveline at the node nearest `pos`."""
    acc, best, best_d = 0.0, 0.0, 1e18
    for i, p in enumerate(pts):
        if i:
            acc += math.dist(p, pts[i - 1])
        d = math.dist(p, pos)
        if d < best_d:
            best, best_d = acc, d
    return best


@pytest.mark.parametrize("leg", ["road1", "ss1"])
def test_route_builder_margins(lua, mods, leg):
    """Like the stock loops: the driveline starts before the start position and runs past the stop position, and the
    last race node is well before the stop position (otherwise 'finishLineDistOffset is nil', no schedule)."""
    _, res = materialize(lua, mods, 37.0884, -7.9755)
    files = res["files"]
    race = files["m/%s/race.race.json" % leg]
    dl = files["m/%s/rally/drivelineSpline.json" % leg]
    nodes = [tuple(dl["nodes"][i][k] for k in (1, 2, 3)) for i in range(1, len(dl["nodes"]) + 1)]
    sp = race["startPositions"]
    a, b = tuple(sp[1]["pos"][i] for i in (1, 2, 3)), tuple(sp[2]["pos"][i] for i in (1, 2, 3))
    total = sum(math.dist(nodes[i], nodes[i - 1]) for i in range(1, len(nodes)))
    start_at, stop_at = _along(nodes, a), _along(nodes, b)
    assert start_at >= 15
    assert total - stop_at >= 5
    pn = race["pathnodes"]
    last = tuple(pn[len(pn)]["pos"][i] for i in (1, 2, 3))
    assert math.dist(last, b) >= 20
    assert race["endNode"] == len(pn)
    assert math.dist(tuple(pn[1]["pos"][i] for i in (1, 2, 3)), a) < 1.0


def test_stage_is_longer_than_a_rebase(lua, mods):
    _, res = materialize(lua, mods, 37.0884, -7.9755)
    lengths = res["lengths"]
    assert lengths["ss1"] > 30000 and lengths["road1"] > 15000   # several 10 km rebases inside each leg


def test_earth_rebase_transform_reproduces_the_lat_lon_truth(lua, mods):
    """The rebase transform applied to frame A's world positions must land on frame B's positions made from lat/lon."""
    geodesy, _ = mods
    a, ra = materialize(lua, mods, 37.0884, -7.9755)
    b, rb = materialize(lua, mods, 37.1530, -7.8870)      # a rebase 10 km on
    worst = 0.0
    nodes_a = ra["files"]["m/road1/race.race.json"]["pathnodes"]
    nodes_b = rb["files"]["m/road1/race.race.json"]["pathnodes"]
    n = len(nodes_a)
    for i in range(1, n + 1):
        pa, pb = nodes_a[i]["pos"], nodes_b[i]["pos"]
        xf = a.worldTransformTo(a, b, pa[1], pa[2], pa[3])
        x, y, z = xf.apply(xf, pa[1], pa[2], pa[3])
        worst = max(worst, math.dist((x, y, z), (pb[1], pb[2], pb[3])))
    # nodes up to ~19 km from the new anchor: the transform is exact at the point it is made for (worldTransformTo)
    assert worst < 0.05, worst


def test_one_transform_for_the_whole_leg_is_good_enough(lua, mods):
    """One transform, made at the car as Earth makes it, applied to a point 39 km away: the shift is rigid (ENU frames
    are rigid maps of ECEF), so the adapter can move every rally position with the rebase's own transform."""
    a, ra = materialize(lua, mods, 37.0884, -7.9755)
    b, rb = materialize(lua, mods, 37.1530, -7.8870)
    nodes_a = ra["files"]["m/ss1/race.race.json"]["pathnodes"]
    nodes_b = rb["files"]["m/ss1/race.race.json"]["pathnodes"]
    p0 = nodes_a[1]["pos"]
    xf = a.worldTransformTo(a, b, p0[1], p0[2], p0[3])
    far = len(nodes_a)
    pa, pb = nodes_a[far]["pos"], nodes_b[far]["pos"]
    x, y, z = xf.apply(xf, pa[1], pa[2], pa[3])
    err = math.dist((x, y, z), (pb[1], pb[2], pb[3]))
    print("one-transform error at the far end of a 39 km stage: %.3f m" % err)
    # (what is left: geodeticToWorld puts x, y at height 0 and only z at the height)
    assert err < 0.25, err


@pytest.mark.parametrize("spacing_m", [10000.0, 2000.0])
def test_error_over_a_chain_of_rebases(lua, mods, spacing_m):
    """Rebase every spacing_m along the 39 km stage, moving the stage's world positions with each rebase's own
    transform (made at the car, as Earth makes it) and never re-deriving them: how far from the lat/lon truth after
    the last? (Earth's default is 10 km; 2 km is 20 rebases on this stage.)"""
    geodesy, _ = mods
    edition, geo = load()
    pts = geo["ss1"]["points"]
    idx, acc = [0], 0.0
    for i in range(1, len(pts)):
        acc += _hav(pts[i - 1], pts[i])
        if acc >= spacing_m:
            idx.append(i)
            acc = 0.0
    assert len(idx) >= 3
    anchors = [geodesy.newAnchor(pts[i][0], pts[i][1]) for i in idx]
    cur = [anchors[0].geodeticToWorld(anchors[0], p[0], p[1], p[2]) for p in pts[::50]]
    for k in range(1, len(anchors)):
        q = pts[idx[k]]
        car = anchors[k - 1].geodeticToWorld(anchors[k - 1], q[0], q[1], q[2])
        xf = anchors[k - 1].worldTransformTo(anchors[k - 1], anchors[k], car[0], car[1], car[2])
        cur = [xf.apply(xf, c[0], c[1], c[2]) for c in cur]
    truth = [anchors[-1].geodeticToWorld(anchors[-1], p[0], p[1], p[2]) for p in pts[::50]]
    worst = max(math.dist(a, b) for a, b in zip(cur, truth))
    print("%d rebases (every %.0f km), worst error vs the lat/lon truth: %.3f m" % (len(anchors) - 1, spacing_m / 1000, worst))
    assert worst < 1.0, worst


def _hav(a, b):
    r = 6371008.8
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def test_the_ground_bias_moves_every_height_of_the_route(lua, mods):
    geodesy, legs = mods
    edition, geo = load()
    anchor = geodesy.newAnchor(37.0884, -7.9755)
    plain = legs.materialize(_conv(lua, edition), _conv(lua, geo), anchor, lambda leg: "m/" + leg["id"])
    moved = legs.materialize(_conv(lua, edition), _conv(lua, geo), anchor, lambda leg: "m/" + leg["id"], -3.3)
    for name in ("m/road1/race.race.json", "m/ss1/race.race.json"):
        a, b = plain["files"][name], moved["files"][name]
        assert abs((b["pathnodes"][2]["pos"][3] - a["pathnodes"][2]["pos"][3]) + 3.3) < 1e-6
        for k in (1, 2):
            assert abs((b["startPositions"][k]["pos"][3] - a["startPositions"][k]["pos"][3]) + 3.3) < 1e-6
    a, b = plain["files"]["m/ss1/rally/drivelineSpline.json"], moved["files"]["m/ss1/rally/drivelineSpline.json"]
    assert abs((b["nodes"][5][3] - a["nodes"][5][3]) + 3.3) < 1e-6
    assert abs((moved["placement"]["pos"][3] - plain["placement"]["pos"][3]) + 3.3) < 1e-6


def test_the_service_stall_stands_70_m_before_tc0_facing_it_when_there_is_no_lot(lua, mods):
    # GitHub #27: the stall on TC0 itself timed the player at once; the stock loops park 65-73 m away. (With a parking lot
    # the car starts on one of its aisles instead, GitHub #55: tests/test_alignment.py.)
    geodesy, legs = mods
    edition, geo = load()
    for g in geo.values():
        g.pop("servicePark", None)
    anchor = geodesy.newAnchor(37.0884, -7.9755)
    res = legs.materialize(_conv(lua, edition), _conv(lua, geo), anchor, lambda leg: "m/" + leg["id"])
    tc0 = res["files"]["m/road1/race.race.json"]["startPositions"][1]["pos"]
    sp, q = res["placement"]["pos"], res["placement"]["rot"]
    dx, dy = tc0[1] - sp[1], tc0[2] - sp[2]
    assert abs(math.hypot(dx, dy) - 70) < 1e-6
    yaw = 2 * math.atan2(q[3], q[4])                    # the car's nose (+y turned by yaw) points at TC0
    fwd = (-math.sin(yaw), math.cos(yaw))
    assert (dx * fwd[0] + dy * fwd[1]) / 70 > 0.999
    edition["serviceStallBackM"] = 40                   # an edition can set its own
    res = legs.materialize(_conv(lua, edition), _conv(lua, geo), anchor, lambda leg: "m/" + leg["id"])
    sp = res["placement"]["pos"]
    assert abs(math.hypot(tc0[1] - sp[1], tc0[2] - sp[2]) - 40) < 1e-6


def test_with_a_lot_the_stall_is_the_spawn_on_an_aisle_and_tc0_is_a_few_metres_ahead(lua, mods):
    geodesy, legs = mods
    edition, geo = load()
    anchor = geodesy.newAnchor(37.0884, -7.9755)
    res = legs.materialize(_conv(lua, edition), _conv(lua, geo), anchor, lambda leg: "m/" + leg["id"])
    tc0 = res["files"]["m/road1/race.race.json"]["startPositions"][1]["pos"]
    sp = res["placement"]["pos"]
    assert res["placement"]["inLot"] is True
    assert 12 < math.hypot(tc0[1] - sp[1], tc0[2] - sp[2]) < 21           # TC0 a few metres ahead of the car, not 70 m behind it
    lead = geo["road1"]["servicePark"]["lead"]
    w = legs.toWorld(_conv(lua, lead), anchor)
    assert math.hypot(sp[1] - w[1][1], sp[2] - w[1][2]) < 1e-6


def test_a_liaison_has_one_node_on_its_start_and_one_before_its_tc(lua, mods):
    # GitHub #31: every race node is a fixed waypoint; on a liaison one missed meant "do a U-turn" whichever way
    _, res = materialize(lua, mods, 37.0884, -7.9755)
    road, stage = res["files"]["m/road1/race.race.json"], res["files"]["m/ss1/race.race.json"]
    pn = road["pathnodes"]
    assert len(pn) == 2 and road["endNode"] == 2 and len(road["segments"]) == 1
    assert pn[1]["radius"] == 20 and pn[2]["radius"] == 20
    start, tc = road["startPositions"][1]["pos"], road["startPositions"][2]["pos"]
    p1 = tuple(pn[1]["pos"][i] for i in (1, 2, 3))
    p2 = tuple(pn[2]["pos"][i] for i in (1, 2, 3))
    assert math.dist(p1, tuple(start[i] for i in (1, 2, 3))) < 1.0           # on the start
    assert 20 <= math.dist(p2, tuple(tc[i] for i in (1, 2, 3))) <= 60        # just before the TC
    dl = res["files"]["m/road1/rally/drivelineSpline.json"]
    assert len(dl["nodes"]) > 100                                            # the distance and the arrows stay


def _pos(p):
    return tuple(p[i] for i in (1, 2, 3))


def _seg_dist(p, a, b):
    """Distance from p to the segment a-b."""
    ab = [b[i] - a[i] for i in range(3)]
    l2 = sum(c * c for c in ab)
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, sum((p[i] - a[i]) * ab[i] for i in range(3)) / l2))
    return math.dist(p, [a[i] + ab[i] * t for i in range(3)])


def test_a_special_gets_a_stock_notebook_and_a_liaison_none(lua, mods):
    # GitHub #34: the corners of the stage, read from its geometry, in the notebook the rally manager looks for
    _, res = materialize(lua, mods, 37.0884, -7.9755)
    files = res["files"]
    assert "m/road1/rally/notebooks/structured.notebook.json" not in files
    nb = files["m/ss1/rally/notebooks/structured.notebook.json"]
    assert nb["name"] == "structured" and nb["audioMode"] == 4 and nb["version"] == "4"
    notes = [nb["pacenotes"][i] for i in range(1, len(nb["pacenotes"]) + 1)]
    assert len(notes) > 50                                   # 39 km of the Algarve hills
    dl = files["m/ss1/rally/drivelineSpline.json"]
    nodes = [_pos(dl["nodes"][i]) for i in range(1, len(dl["nodes"]) + 1)]
    sp = files["m/ss1/race.race.json"]["startPositions"]
    start_at, stop_at = _along(nodes, _pos(sp[1]["pos"])), _along(nodes, _pos(sp[2]["pos"]))
    last_end, last_pos = -1.0, None
    seen = set()
    for note in notes:
        wps = {note["pacenoteWaypoints"][k]["waypointType"]: note["pacenoteWaypoints"][k] for k in (1, 2)}
        cs, ce = _along(nodes, _pos(wps["cornerStart"]["pos"])), _along(nodes, _pos(wps["cornerEnd"]["pos"]))
        # in order of travel, between the start line and the stop, never overlapping the note before
        assert start_at <= cs <= ce <= stop_at
        assert math.dist(_pos(wps["cornerStart"]["pos"]), _pos(wps["cornerEnd"]["pos"])) >= 5.0
        assert cs >= last_end                      # (the nodes are coarse; the metres apart are checked below)
        if last_pos:
            assert math.dist(last_pos, _pos(wps["cornerStart"]["pos"])) >= 2.0
        last_end, last_pos = ce, _pos(wps["cornerEnd"]["pos"])
        # on the road: the driveline thins the route to a node every 30 m, which cuts a tight corner by some metres
        assert min(_seg_dist(_pos(wps["cornerStart"]["pos"]), nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1)) < 20
        assert note["structured"]["items"]["4"]["type"] == "corner"
        seen.add(note["metadata"]["dakar"]["direction"])
    assert seen == {"left", "right"}


def test_the_notebook_follows_the_anchor_and_the_ground_bias(lua, mods):
    # like the race path: world positions of the current anchor, on the game's ground
    geodesy, legs = mods
    edition, geo = load()
    name = "m/ss1/rally/notebooks/structured.notebook.json"

    def run(lat, lon, bias=None):
        return legs.materialize(_conv(lua, edition), _conv(lua, geo), geodesy.newAnchor(lat, lon),
                                lambda leg: "m/" + leg["id"], bias)["files"][name]
    na, nb_, nc = run(37.0884, -7.9755), run(37.0884, -7.9755, -3.3), run(37.15, -8.05)
    assert len(na["pacenotes"]) == len(nb_["pacenotes"]) == len(nc["pacenotes"])      # the same corners, wherever the origin is
    wa, wb, wc = (n["pacenotes"][7]["pacenoteWaypoints"][1]["pos"] for n in (na, nb_, nc))
    assert abs((wb[3] - wa[3]) + 3.3) < 1e-6 and abs(wb[1] - wa[1]) < 1e-6
    assert math.hypot(wc[1] - wa[1], wc[2] - wa[2]) > 1000                        # another origin, another frame


def test_a_special_has_wide_split_checkpoints_only(lua, mods):
    # GitHub #37: a dense node on a wonky stretch or under a building was a U-turn trap; the closures at the exits and
    # the pace notes keep the player on the road, the race path only times them
    _, res = materialize(lua, mods, 37.0884, -7.9755)
    stage = res["files"]["m/ss1/race.race.json"]
    pn = [stage["pathnodes"][i] for i in range(1, len(stage["pathnodes"]) + 1)]
    total = res["lengths"]["ss1"]
    splits = [p for p in pn if p["useAsSplit"]]
    assert len(splits) == int((total - 1000) // 3000)                 # every 3 km, none in the last kilometre
    assert not pn[0]["useAsSplit"] and not pn[-1]["useAsSplit"]       # the start and the node before the stop
    assert len(pn) == len(splits) + 2 and all(p["radius"] == 25 for p in pn)
    gaps = [math.dist(tuple(a["pos"][i] for i in (1, 2)), tuple(b["pos"][i] for i in (1, 2))) for a, b in zip(splits, splits[1:])]
    assert all(500 < g <= 3020 for g in gaps)                         # (straight-line, so at most the 3 km along)
    for p in pn:                                                      # facing along the road
        n = p["normal"]
        assert abs(math.hypot(n[1], n[2]) - 1) < 0.2
