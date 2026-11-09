"""Offline tests for props.lua: where the service park and the start/finish furniture stand."""
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

if not os.path.isdir(EARTH_EXT):
    pytest.skip("BeamNG.Earth not found (set BEAMNG_EARTH_DIR)", allow_module_level=True)
if not os.path.isfile(os.path.join(EDITION, "geometry", "ss1.json")):
    pytest.skip("run tools/fetch_route.py editions/testbed-algarve first", allow_module_level=True)


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


SPLIT_NODES = []      # the stage race path's split checkpoints (filled by the fixture)


@pytest.fixture(scope="module")
def world():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end")
    rt.execute("package.path = package.path .. ';%s/?.lua;%s/?.lua'" % (EARTH_EXT, DAKAR_EXT))
    geodesy, legs, props = (rt.eval("(require('beamngEarth/geodesy'))"), rt.eval("(require('beamngDakar/legs'))"),
                            rt.eval("(require('beamngDakar/props'))"))
    edition = json.load(open(os.path.join(EDITION, "legs.json"), encoding="utf-8"))
    anchor = geodesy.newAnchor(37.0900, -7.9800)
    by_leg, pts = rt.table(), {}
    for leg in edition["legs"]:
        g = json.load(open(os.path.join(EDITION, "geometry", leg["id"] + ".json")))
        by_leg[leg["id"]] = legs.toWorld(conv(rt, g["points"]), anchor)
    # the split checkpoints' distances, as core.lua takes them from legs.raceNodes (GitHub #37)
    ed = conv(rt, edition)
    splits = rt.table()
    for i in range(1, len(ed.legs) + 1):
        leg = ed.legs[i]
        nodes, _radius = legs.raceNodes(leg, by_leg[leg.id])
        splits[leg.id] = rt.table(*[nodes[k].at for k in range(1, len(nodes) + 1) if nodes[k].split])
    defs = props.layout(ed, splits)
    placed = props.place(defs, by_leg)
    items = [placed[i] for i in range(1, len(placed) + 1)]
    race = legs.race(ed.legs[2], by_leg["ss1"])
    SPLIT_NODES[:] = [race.pathnodes[i] for i in range(1, len(race.pathnodes) + 1) if race.pathnodes[i].useAsSplit]
    return rt, by_leg, items


def road_points(by_leg, leg):
    t = by_leg[leg]
    return [(t[i][1], t[i][2]) for i in range(1, len(t) + 1)]


def test_every_prop_has_a_real_shape_and_a_place(world):
    _, by_leg, items = world
    assert len(items) > 30
    assert all(str(p["shape"]).startswith("/art/shapes/") for p in items)
    ids = [p["id"] for p in items]
    assert len(set(ids)) == len(ids)                   # unique: the manager keys objects by id


def test_the_start_boards_stand_5_and_a_half_metres_either_side_of_the_road(world):
    _, by_leg, items = world
    road = road_points(by_leg, "ss1")
    for pid in ("ss1:start_l", "ss1:start_r"):
        p = next(i for i in items if i["id"] == pid)
        d = min(math.dist((p["x"], p["y"]), r) for r in road)
        assert 5.0 < d < 6.2, (pid, d)
    l = next(i for i in items if i["id"] == "ss1:start_l")
    r = next(i for i in items if i["id"] == "ss1:start_r")
    first, second = road[0], road[1]
    hx, hy = second[0] - first[0], second[1] - first[1]
    # right of the direction of travel is (hy, -hx): the right board is on that side of the left one
    assert (r["x"] - l["x"]) * hy - (r["y"] - l["y"]) * hx > 0


def test_a_driver_facing_board_has_its_front_against_the_direction_of_travel(world):
    _, by_leg, items = world
    p = next(i for i in items if i["id"] == "ss1:start_r")
    road = road_points(by_leg, "ss1")
    hx, hy = road[1][0] - road[0][0], road[1][1] - road[0][1]
    n = math.hypot(hx, hy)
    # the model's front is its local +y: yaw is the turn of local +x, counter-clockwise from +x
    fx, fy = math.cos(math.radians(p["yaw"] + 90)), math.sin(math.radians(p["yaw"] + 90))
    assert (fx * hx + fy * hy) / n < -0.95


def test_the_finish_props_stand_at_the_end_of_the_stage(world):
    _, by_leg, items = world
    road = road_points(by_leg, "ss1")
    end = road[-1]
    for pid in ("ss1:fin_l", "ss1:fin_r"):
        p = next(i for i in items if i["id"] == pid)
        assert math.dist((p["x"], p["y"]), end) < 8
    entry = next(i for i in items if i["id"] == "ss1:fin_entry")
    # 250 m along the road: less in a straight line where the road bends
    assert 150 < math.dist((entry["x"], entry["y"]), end) < 260


def test_the_service_park_is_at_the_start_of_the_transfer(world):
    _, by_leg, items = world
    start = road_points(by_leg, "road1")[0]
    for pid in ("road1:table", "road1:chair1", "road1:flag1", "road1:tc_in"):
        p = next(i for i in items if i["id"] == pid)
        assert math.dist((p["x"], p["y"]), start) < 20, pid


def test_the_landmarks_are_tall_far_and_at_the_service_park_the_end_of_the_transfer_and_the_finish(world):
    # (the stage start's own landmark is the staging area's of the transfer before it, which ends there: GitHub #49)
    _, by_leg, items = world
    far = [p for p in items if p["far"]]
    towers = [p for p in far if "s_tower" in str(p["shape"])]
    flags = [p for p in far if "feather" in str(p["shape"])]
    assert len(towers) == 3 and len(flags) == 12            # a tower and four feather flags at each of the three places
    assert {str(p["id"]).split(":")[1].split("_")[0] for p in towers} == {"park", "stagein", "finish"}
    near = [p for p in items if not p["far"]]
    assert len(near) > 30 and not any("feather" in str(p["shape"]) or "s_tower" in str(p["shape"]) for p in near)


def test_the_landmarks_stand_beside_the_road_not_on_it(world):
    _, by_leg, items = world
    for leg in ("road1", "ss1"):
        road = road_points(by_leg, leg)
        for p in items:
            if p["far"] and str(p["id"]).startswith(leg + ":"):
                d = min(math.dist((p["x"], p["y"]), r) for r in road)
                assert d > 7.0, (p["id"], d)


def test_each_split_checkpoint_has_its_radio_and_medical_boards_and_their_warnings(world):
    # GitHub #37: the stock stages stand the radio point and medical point boards at a split, warnings before them
    _, by_leg, items = world
    nodes = SPLIT_NODES
    kinds = {}
    for p in items:
        if ":split" in str(p["id"]):
            kinds.setdefault(str(p["kind"]), []).append(p)
    assert set(kinds) == {"radio_point", "medical_point", "radio_warning", "medical_warning"}
    assert len(kinds["radio_point"]) == len(nodes) == len(kinds["medical_warning"]) >= 10
    for n, radio in zip(nodes, kinds["radio_point"]):
        d = math.dist((radio["x"], radio["y"]), (n.pos[1], n.pos[2]))
        assert d < 30                                           # beside the split checkpoint (the node is <= 20 m on)
    for b, w in zip(kinds["radio_point"], kinds["radio_warning"]):
        assert 80 < math.dist((b["x"], b["y"]), (w["x"], w["y"])) < 105   # the warning ~100 m before


# ---------------------------------------------------------------------------------------------------------------------
# The staging area at the end of a transfer (GitHub #49): the time control before the special, dressed like the service
# park, so the player can see where it is from the approach

def by_id(items, pid):
    return next(i for i in items if i["id"] == pid)


def test_the_staging_area_stands_at_the_end_of_the_transfer(world):
    _, by_leg, items = world
    road = road_points(by_leg, "road1")
    end = road[-1]
    tc = by_id(items, "road1:stagein_tc")
    assert str(tc["shape"]).endswith("s_rally_signboard_time_control.dae")
    assert math.dist((tc["x"], tc["y"]), end) < 8                      # the board where the control is, beside the road
    entry = by_id(items, "road1:stagein_tc_entry")
    assert str(entry["shape"]).endswith("s_rally_signboard_time_control_entry.dae")
    assert 35 < math.dist((entry["x"], entry["y"]), end) < 52          # its entry board 50 m before (less on a bend)
    for pid in ("stagein_table", "stagein_chair1", "stagein_chair2", "stagein_flag1", "stagein_flag2"):
        p = by_id(items, "road1:" + pid)
        assert math.dist((p["x"], p["y"]), end) < 50, pid
    kinds = {str(p["kind"]) for p in items if str(p["id"]).startswith("road1:stagein_")}
    assert {"tc_board", "tc_entry", "table", "chair", "flag", "flag_tear", "cone", "tower", "feather"} <= kinds
    # cones line the approach on both sides, behind the control
    cones = [p for p in items if str(p["id"]).startswith("road1:stagein_cone_")]
    assert len(cones) >= 20 and all(math.dist((p["x"], p["y"]), end) < 95 for p in cones)
    assert max(math.dist((p["x"], p["y"]), end) for p in cones) > 60


def test_the_staging_landmark_is_far_and_beside_the_road_near_the_control(world):
    _, by_leg, items = world
    road = road_points(by_leg, "road1")
    end = road[-1]
    mine = [p for p in items if str(p["id"]).startswith("road1:stagein_tower") or str(p["id"]).startswith("road1:stagein_feather")]
    assert len(mine) == 5 and all(p["far"] for p in mine)
    tower = by_id(items, "road1:stagein_tower")
    d = min(math.dist((tower["x"], tower["y"]), r) for r in road)
    assert 12 < d < 20                                                  # beside the road, not on it
    assert math.dist((tower["x"], tower["y"]), end) < 35


def test_a_transfer_that_ends_where_the_special_starts_is_dressed_once(world):
    # the stage start's landmark and table are the staging area's: nothing stands twice at the shared place
    rt, by_leg, items = world
    ids = {str(p["id"]) for p in items}
    assert not any(i.startswith("ss1:stagestart") or i.startswith("ss1:start_tower") or i.startswith("ss1:start_feather") for i in ids)
    assert {"ss1:start_l", "ss1:start_r", "ss1:sensor_l", "ss1:sensor_r"} <= ids        # (the start's own boards stay)
    near = [p for p in items if math.dist((p["x"], p["y"]), road_points(by_leg, "ss1")[0]) < 100]
    for kind in ("table", "tower", "tc_board"):          # (the two chairs stand together by design)
        mine = [p for p in near if str(p["kind"]) == kind]
        for i, a in enumerate(mine):
            for b in mine[i + 1:]:
                assert math.dist((a["x"], a["y"]), (b["x"], b["y"])) > 3.0, (a["id"], b["id"])


SHIFT_STAGE = """
function(world, dx, dy)
  local by = {}
  for id, pts in pairs(world) do by[id] = pts end
  local moved = {}
  for i, p in ipairs(world.ss1) do moved[i] = { p[1] + dx, p[2] + dy, p[3] } end
  by.ss1 = moved
  return by
end
"""


def test_a_special_that_starts_away_from_the_transfers_end_is_dressed_on_its_own(world):
    rt, by_leg, items = world
    far = rt.eval(SHIFT_STAGE)(by_leg, 800, 0)                          # the stage 800 m off the end of the transfer
    ed = conv(rt, json.load(open(os.path.join(EDITION, "legs.json"), encoding="utf-8")))
    props = rt.eval("(require('beamngDakar/props'))")
    placed = props.place(props.layout(ed, rt.table()), far)
    ids = {str(placed[i]["id"]) for i in range(1, len(placed) + 1)}
    assert "ss1:start_tower" in ids and "ss1:stagestart_table" in ids and "ss1:stagestart_flag1" in ids
    assert "road1:stagein_tower" in ids                                 # and the transfer's end keeps its own


def build(rt, points, staging=None):
    """The layout of a one-transfer edition over `points` ((x, y) each), placed: { id: prop }."""
    props = rt.eval("(require('beamngDakar/props'))")
    leg = {"id": "t1", "type": "transfer"}
    if staging:
        leg["staging"] = staging
    ed = conv(rt, {"legs": [leg]})
    w, pts = rt.table(), rt.table()
    for i, (x, y) in enumerate(points, 1):
        pts[i] = rt.table(x, y, 0)
    w["t1"] = pts
    placed = props.place(props.layout(ed, rt.table()), w)
    return {str(placed[i]["id"]): placed[i] for i in range(1, len(placed) + 1)}


def side_of(tower, end, heading):
    """+1 when the tower is right of a road ending at `end` with the given heading (radians), -1 when left."""
    hx, hy = math.cos(heading), math.sin(heading)
    return 1 if (tower["x"] - end[0]) * hy - (tower["y"] - end[1]) * hx > 0 else -1


def arc(turn_deg, n=40):
    """A road: 150 m straight east, then 150 m on an arc turning `turn_deg` in all (+ left). Returns its points and the final heading."""
    pts = [(float(x), 0.0) for x in range(-150, 1, 10)]
    th, x, y = 0.0, 0.0, 0.0
    step = 150.0 / n
    for _ in range(n):
        th += math.radians(turn_deg) / n
        x, y = x + step * math.cos(th), y + step * math.sin(th)
        pts.append((x, y))
    return pts, th


@pytest.mark.parametrize("turn,expected", [(60, 1), (-60, -1), (3, -1), (-3, -1)])
def test_the_landmark_stands_on_the_outer_side_of_the_last_bend(world, turn, expected):
    # a left bend (+): the outside is the right (+1); a right bend: the left; straight: the left, like the service park's
    rt = world[0]
    pts, th = arc(turn)
    got = build(rt, pts)
    assert side_of(got["t1:stagein_tower"], pts[-1], th) == expected


def test_legs_json_picks_the_landmark_side_and_its_reach(world):
    rt = world[0]
    pts, th = arc(60)                                                   # a left bend: the outside is the right
    assert side_of(build(rt, pts, {"side": "left"})["t1:stagein_tower"], pts[-1], th) == -1
    assert side_of(build(rt, pts, {"side": "right"})["t1:stagein_tower"], pts[-1], th) == 1
    near = build(rt, pts, {"side": "right", "reach": 25, "landmarkS": 30})["t1:stagein_tower"]
    d = min(math.dist((near["x"], near["y"]), r) for r in pts)
    assert 22 < d < 27 and math.dist((near["x"], near["y"]), pts[-1]) > 28


def test_the_popoli_landmark_stands_on_the_outer_side_of_its_last_bend_by_tc1():
    # the Svolte di Popoli's TC1, up the SR17 past the left turn the transfer makes onto it (GitHub #55; at the time of #49 it
    # was after a short underpass and a bend to the right)
    popoli = os.path.join(ROOT, "editions", "svolte-di-popoli")
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end")
    rt.execute("package.path = package.path .. ';%s/?.lua;%s/?.lua'" % (EARTH_EXT, DAKAR_EXT))
    geodesy, legs, props = (rt.eval("(require('beamngEarth/geodesy'))"), rt.eval("(require('beamngDakar/legs'))"),
                            rt.eval("(require('beamngDakar/props'))"))
    edition = json.load(open(os.path.join(popoli, "legs.json"), encoding="utf-8"))
    anchor = geodesy.newAnchor(42.1700, 13.8300)
    by_leg = rt.table()
    for leg in edition["legs"]:
        g = json.load(open(os.path.join(popoli, "geometry", leg["id"] + ".json")))
        by_leg[leg["id"]] = legs.toWorld(conv(rt, g["points"]), anchor)
    placed = props.place(props.layout(conv(rt, edition), rt.table()), by_leg)
    items = [placed[i] for i in range(1, len(placed) + 1)]
    road = road_points(by_leg, "road1")
    end = road[-1]
    tower = by_id(items, "road1:stagein_tower")
    hx, hy = end[0] - road[-4][0], end[1] - road[-4][1]
    assert side_of(tower, end, math.atan2(hy, hx)) == 1                # the road bends left onto the SR17: the tower is on the right
    assert 12 < min(math.dist((tower["x"], tower["y"]), r) for r in road) < 20
    assert math.dist((tower["x"], tower["y"]), end) < 35
    # and TC1 and the stage start (a few metres apart) are dressed once
    assert not any(str(p["id"]).startswith("ss1:start_tower") for p in items)


def known_shapes():
    path = os.path.join(HERE, "data", "game_shapes.txt")
    return {l.strip() for l in open(path, encoding="utf-8") if l.strip() and not l.startswith("#")}


def test_every_shape_the_props_use_is_one_the_game_has(world):
    rt, _, items = world
    shapes = rt.eval("(require('beamngDakar/props')).SHAPES")
    used = {str(shapes[k]) for k in shapes}
    known = known_shapes()
    assert used and used <= known, sorted(used - known)
    assert {str(p["shape"]) for p in items} <= known
    # the staging area's own: the time-control boards and the teardrop flag
    assert any(u.endswith("s_rally_signboard_time_control.dae") for u in used)
    assert any(u.endswith("s_rally_signboard_time_control_entry.dae") for u in used)


GAME_ART = os.path.join(os.environ.get("BEAMNG_DRIVE_DIR", "D:/SteamLibrary/steamapps/common/BeamNG.drive"), "content", "art_shapes.zip")


@pytest.mark.skipif(not os.path.isfile(GAME_ART), reason="BeamNG.drive not found (set BEAMNG_DRIVE_DIR)")
def test_the_known_shapes_are_in_the_games_own_content():
    import zipfile
    names = set(zipfile.ZipFile(GAME_ART).namelist())
    missing = [s for s in known_shapes() if s.lstrip("/") not in names]
    assert not missing, missing
