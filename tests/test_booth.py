"""The service booth where the car starts: tools/booth.py on synthetic lots, the booths of both editions' geometry, and
props.lua standing them."""
import json
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import booth  # noqa: E402
import joins  # noqa: E402
import parkinglot as pl  # noqa: E402

EDITIONS = ["testbed-algarve", "svolte-di-popoli"]
LAT, LON = 37.0, -8.0


def at(north_m, east_m):
    return (LAT + north_m / 111320.0, LON + east_m / (111320.0 * math.cos(math.radians(LAT))))


def rect(x0, y0, x1, y1):
    """A ring (lat, lon) of the rectangle x0..x1 east, y0..y1 north of the origin."""
    return [at(y0, x0), at(y0, x1), at(y1, x1), at(y1, x0), at(y0, x0)]


def lot(x_min=-45.0, x_max=5.0, rows=(0.0, -17.5, -35.0)):
    """A lot of N-S aisles `rows` (east offsets) from y -60 to 120, area x_min..x_max, the car at the origin of the first aisle
    heading north, TC0 20 m ahead, the exit at the north end. Returns the arguments of booth.plan."""
    lines = [[at(y, x) for y in (-60.0, 0.0, 60.0, 120.0)] for x in rows]
    area = [[rect(x_min, -65.0, x_max, 125.0)]]
    lead = [at(0.0, 0.0), at(10.0, 0.0), at(20.0, 0.0)]
    route = [at(y, 0.0) for y in (20.0, 60.0, 120.0)]
    return at(0.0, 0.0), lead, route, lines, area


def xy(p):
    return booth.to_xy(p, (LAT, LON))


def east_of_car(item):
    return xy((item["lat"], item["lon"]))[0]


def gazebos(items):
    return [i for i in items if i["kind"] == "gazebo"]


def test_the_gazebo_stands_on_the_side_of_the_car_the_lot_has_room_on_and_along_the_aisle():
    origin, lead, route, lines, area = lot()
    items = booth.plan(origin, lead, route, lines, area)
    gz = gazebos(items)
    assert len(gz) >= 3 and gz[0]["variant"] == "blastr"
    # the area ends 5 m east of the aisle: not room for a gazebo (3 m wide) 2.6 m clear of the drive, so it is the west
    assert all(east_of_car(g) < 0 for g in gz)
    own = xy((gz[0]["lat"], gz[0]["lon"]))
    assert 4.0 <= abs(own[0]) <= 5.0 and abs(own[1]) <= 3.1          # beside the car, a gazebo's width and a clearance from its way
    # the front points across the aisle, so that the long side runs along it (a gazebo's long side is its +x, right of its front)
    b = math.radians(gz[0]["bearing"])
    assert math.hypot(math.sin(b) - 1.0, math.cos(b)) < 0.05 or math.hypot(math.sin(b) + 1.0, math.cos(b)) < 0.05
    # the row follows along the aisle: a gazebo's length from the next
    ys = sorted(xy((g["lat"], g["lon"]))[1] for g in gz)
    assert all(abs((b - a) - booth.PITCH_M) < 0.01 for a, b in zip(ys, ys[1:]))


def test_the_other_side_is_taken_when_this_one_has_no_room():
    origin, lead, route, lines, area = lot(x_min=-5.0, x_max=45.0)           # only the east is the lot
    items = booth.plan(origin, lead, route, lines, area)
    assert gazebos(items) and all(east_of_car(g) > 0 for g in gazebos(items))


def test_a_lot_with_no_room_beside_the_aisle_has_no_booth():
    origin, lead, route, lines, area = lot(x_min=-5.0, x_max=5.0)             # a strip too narrow for a gazebo and its clearance
    assert booth.plan(origin, lead, route, lines, area) == []


def test_a_building_in_the_way_ends_the_row_and_nothing_stands_in_it():
    origin, lead, route, lines, area = lot()
    full = gazebos(booth.plan(origin, lead, route, lines, area))
    building = [rect(-8.0, -14.0, -2.0, -9.0)]                                 # across the second gazebo behind the car
    items = booth.plan(origin, lead, route, lines, area, [building])
    ring = [xy(p) for p in building[0]]
    assert len(gazebos(items)) < len(full)
    for it in items:
        assert not booth.in_ring(xy((it["lat"], it["lon"])), ring), it["kind"]


def test_everything_in_the_booth_is_in_the_lot_and_clear_of_the_aisles_the_drive_and_tc0():
    origin, lead, route, lines, area = lot()
    items = booth.plan(origin, lead, route, lines, area)
    kinds = {i["kind"] for i in items}
    assert {"gazebo", "table", "chair", "cone", "flag"} <= kinds
    drive = [xy(p) for p in lead + route]
    aisles = [[xy(p) for p in l] for l in lines]
    poly = [xy(p) for p in area[0][0]]
    for it in items:
        p = xy((it["lat"], it["lon"]))
        assert booth.in_ring(p, poly), it
        assert min(booth.line_dist(p, l) for l in aisles + [drive]) >= booth.CLEAR_M - 1e-6, it
        assert math.dist(p, xy(lead[-1])) >= booth.TC0_CLEAR_M, it


def street(north_m_from, north_m_to, east_m, cls="service"):
    """A road (not an aisle) along a N-S line `east_m` east of the origin, for the roads argument of booth.plan."""
    return {"class": cls, "line": [at(y, east_m) for y in (north_m_from, north_m_to)]}


def test_nothing_of_the_booth_stands_on_a_road_that_is_not_an_aisle():
    # (the player found a table on the road leading out of the lot)
    origin, lead, route, lines, area = lot()
    free = booth.plan(origin, lead, route, lines, area)
    # a road across the lot's west part (a service road along x = -9.5), where the first gazebo behind the car stands
    road = {"class": "service", "line": [at(-60.0, -9.5), at(120.0, -9.5)]}
    items = booth.plan(origin, lead, route, lines, area, None, [road])
    line = [xy(p) for p in road["line"]]
    half = pl.road_half("service")
    assert items != free
    for it in items:
        assert booth.line_dist(xy((it["lat"], it["lon"])), line) >= half + booth.ROAD_MARGIN_M - 1e-6, it
    for g in gazebos(items):
        c = xy((g["lat"], g["lon"]))
        b = math.radians(g["bearing"])
        d = (math.cos(b), -math.sin(b))
        assert min(booth.line_dist(p, line) for p in booth.footprint(c, d)) >= half + booth.ROAD_MARGIN_M - 1e-6


def test_a_wide_road_is_given_a_wider_berth_than_a_narrow_one():
    origin, lead, route, lines, area = lot()
    narrow = booth.plan(origin, lead, route, lines, area, None, [street(-60, 120, -14.0, "track")])
    wide = booth.plan(origin, lead, route, lines, area, None, [street(-60, 120, -14.0, "primary")])
    line = [xy(p) for p in street(-60, 120, -14.0)["line"]]
    assert min(booth.line_dist(xy((i["lat"], i["lon"])), line) for i in narrow) >= pl.road_half("track") + booth.ROAD_MARGIN_M - 1e-6
    assert min(booth.line_dist(xy((i["lat"], i["lon"])), line) for i in wide) >= pl.road_half("primary") + booth.ROAD_MARGIN_M - 1e-6


def test_the_roads_kept_are_the_drivable_ones_the_game_draws_and_the_footways_and_aisles_are_not_roads():
    for cls in ("motorway", "primary", "residential", "service", "track"):
        assert cls in pl.ROAD_HALF_M and pl.road_half(cls) >= 2.0
    assert not {"footway", "path", "steps", "cycleway", "pedestrian"} & set(pl.ROAD_HALF_M)
    assert {"parking_aisle", "driveway"} <= pl.ROAD_SKIP_SUBCLASSES


def test_without_parking_polygons_the_lot_is_the_ground_round_its_aisles():
    origin, lead, route, lines, _ = lot()
    items = booth.plan(origin, lead, route, lines, [])
    assert gazebos(items)
    for it in items:
        p = xy((it["lat"], it["lon"]))
        assert min(booth.line_dist(p, [xy(q) for q in l]) for l in lines) <= booth.FALLBACK_M


def test_the_car_starts_where_the_booth_has_room_when_the_lot_is_known():
    # one aisle to the exit at its north end, the lot's bays only along its last 55 m: the furthest-back spot, 90 m from the exit,
    # has no lot beside it (as at the testbed, where the entrance throat is outside the parking polygons)
    lines = [[at(y, 0.0) for y in range(-60, 121, 10)]]
    exit_at = at(120.0, 0.0)
    area = [[rect(-45.0, 70.0, 5.0, 125.0)]]
    plain = pl.spawn_plan(lines, exit_at)
    with_room = pl.spawn_plan(lines, exit_at, booth.room(lines, area, []))
    assert xy(plain["spawn"])[1] < 40
    assert 70 <= xy(with_room["spawn"])[1] <= 95                      # (25 m from the exit at the least, and the lot begins at 70)
    assert pl.metres(plain["exit"], with_room["exit"]) < 1.0
    # where no spot has room the choice is as it was
    assert pl.spawn_plan(lines, exit_at, lambda path: 0)["spawn"] == plain["spawn"]


def test_a_spot_with_room_beats_the_furthest_back_one_without():
    lines = [[at(y, 0.0) for y in range(-90, 101, 10)]]
    exit_at = at(100.0, 0.0)
    plain = pl.spawn_plan(lines, exit_at)
    far = xy(plain["spawn"])
    # room only at spots beyond 30 m from the furthest-back one
    plan = pl.spawn_plan(lines, exit_at, lambda path: 3 if math.dist(xy(path[0]), far) > 30 else 0)
    assert math.dist(xy(plan["spawn"]), far) > 30
    # the furthest back of those that have it (the most room first: 4 gazebos are as good as 9)
    again = pl.spawn_plan(lines, exit_at, lambda path: 9 if math.dist(xy(path[0]), far) > 50 else 3)
    assert math.dist(xy(again["spawn"]), far) > 50


# ---------------------------------------------------------------------------------------------------------------------
# the booths of the two editions' geometry (tools/fetch_route.py wrote them from the real lots)

def load(ed):
    base = os.path.join(ROOT, "editions", ed)
    edition = json.load(open(os.path.join(base, "legs.json"), encoding="utf-8"))
    geo = {l["id"]: json.load(open(os.path.join(base, "geometry", l["id"] + ".json"))) for l in edition["legs"]}
    return edition, geo


def lot_of(ed):
    """(servicePark record, the geometry's first leg, a booth.Lot over its lot, the car's spot)."""
    edition, geo = load(ed)
    g = geo[edition["legs"][0]["id"]]
    sp = g["servicePark"]
    origin = (sp["spawn"]["lat"], sp["spawn"]["lon"])
    lead = [tuple(p[:2]) for p in sp["lead"]]
    route = [tuple(p[:2]) for p in g["points"]]
    c = joins.cumulative(route)
    drive = lead + [p for p, s in zip(route, c) if s <= booth.DRIVE_M]
    area = [[[tuple(p) for p in ring] for ring in poly] for poly in sp["area"]]
    built = [[[tuple(p) for p in ring] for ring in poly] for poly in sp["buildings"]]
    lines = [[tuple(p) for p in l] for l in sp["lines"]]
    roads = [{"class": r["class"], "line": [tuple(p) for p in r["line"]]} for r in sp.get("roads", [])]
    return sp, g, booth.Lot(origin, lines, area, built, drive, lead[-1], roads), origin


def footprints(sp, origin):
    """{ index: footprint points (metres) } of the booth's gazebos: 3 x 6 m, the long side along the model's +x (right of its front)."""
    out = {}
    for i, it in enumerate(sp["booth"]):
        if it["kind"] == "gazebo":
            b = math.radians(it["bearing"])
            front = (math.sin(b), math.cos(b))
            out[i] = booth.footprint(booth.to_xy((it["lat"], it["lon"]), origin), (front[1], -front[0]))
    return out


@pytest.mark.parametrize("ed", EDITIONS)
def test_the_lot_has_a_booth_with_a_gazebo_a_table_chairs_tools_and_cones(ed):
    sp, _, _, _ = lot_of(ed)
    items = sp["booth"]
    kinds = {i["kind"] for i in items}
    assert {"gazebo", "table", "chair", "cone"} <= kinds
    assert {"toolbox_big", "toolbox_small", "tire_rack", "extinguisher"} & kinds
    gz = [i for i in items if i["kind"] == "gazebo"]
    assert items[0]["kind"] == "gazebo" and items[0]["variant"] == "blastr"        # the car's own booth first, in BLASTR's cover
    assert {g["variant"] for g in gz} <= set(booth.VARIANTS)
    assert all(set(i) >= {"kind", "lat", "lon", "h", "bearing"} for i in items)


@pytest.mark.parametrize("ed", EDITIONS)
def test_the_booth_is_inside_the_lot_and_not_in_a_building(ed):
    sp, _, lot, origin = lot_of(ed)
    assert sp["area"], "the lot's parking polygons are in the geometry"
    for it in sp["booth"]:
        p = booth.to_xy((it["lat"], it["lon"]), origin)
        assert lot.in_lot(p), it
        assert not any(booth.in_polygon(p, b) for b in lot.buildings), it
    for i, pts in footprints(sp, origin).items():
        assert all(lot.in_lot(p) for p in pts), sp["booth"][i]
        assert not any(booth.in_polygon(p, b) for p in pts for b in lot.buildings)


@pytest.mark.parametrize("ed", EDITIONS)
def test_the_booth_is_off_the_drive_from_the_spawn_to_tc0_and_out_of_the_lot_and_off_the_aisles(ed):
    sp, _, lot, origin = lot_of(ed)
    for it in sp["booth"]:
        p = booth.to_xy((it["lat"], it["lon"]), origin)
        assert lot.aisle_dist(p) >= booth.CLEAR_M - 0.05, it                         # (the coordinates are stored to 1 cm)
    for pts in footprints(sp, origin).values():
        assert min(lot.aisle_dist(p) for p in pts) >= booth.CLEAR_M - 0.05          # the whole of a gazebo, not just its middle
    # the exit is not built over either
    ex = booth.to_xy((sp["exit"]["lat"], sp["exit"]["lon"]), origin)
    assert all(math.dist(booth.to_xy((it["lat"], it["lon"]), origin), ex) > 3.0 for it in sp["booth"])


@pytest.mark.parametrize("ed", EDITIONS)
def test_the_booth_is_off_the_roads_round_the_lot(ed):
    # (the lot's drivable roads other than its aisles, which the geometry keeps, as it keeps the buildings)
    sp, _, lot, origin = lot_of(ed)
    assert sp["roads"] and all(r["class"] in pl.ROAD_HALF_M and len(r["line"]) >= 2 for r in sp["roads"])
    assert lot.roads
    for it in sp["booth"]:
        p = booth.to_xy((it["lat"], it["lon"]), origin)
        assert lot.road_gap(p) >= -0.05, it                                                     # (the coordinates are stored to 1 cm)
    for pts in footprints(sp, origin).values():
        assert min(lot.road_gap(p) for p in pts) >= -0.05                                      # the whole of a gazebo
    # (and it is the roads round the lot that were looked at: some are near)
    assert min(booth.line_dist((0.0, 0.0), [booth.to_xy(tuple(p), origin) for p in r["line"]]) for r in sp["roads"]) < 150


@pytest.mark.parametrize("ed", EDITIONS)
def test_the_car_stands_beside_the_booths_gazebo_with_tc0_further_on(ed):
    sp, g, lot, origin = lot_of(ed)
    fp = footprints(sp, origin)
    own = fp[0]
    assert min(math.hypot(p[0], p[1]) for p in own) <= 3.6                          # the car (at the origin) by the gazebo's near edge
    assert math.hypot(sum(p[0] for p in own) / len(own), sum(p[1] for p in own) / len(own)) <= 6.6     # by it, not a row away
    # TC0 and its own staging area stand further on: the booth is clear of them
    assert min(math.dist(p, lot.tc0) for pts in fp.values() for p in pts) >= booth.TC0_CLEAR_M - 0.05
    assert 12 <= joins.metres(origin, g["points"][0][:2]) <= 21                     # TC0 17-20 m ahead of the car


@pytest.mark.parametrize("ed", EDITIONS)
def test_the_gazebos_lie_along_the_way_the_car_faces(ed):
    sp, _, _, origin = lot_of(ed)
    way = booth.car_way([booth.to_xy(tuple(p[:2]), origin) for p in sp["lead"]])
    for i in footprints(sp, origin):
        b = math.radians(sp["booth"][i]["bearing"])
        along = (math.cos(b), -math.sin(b))
        assert abs(along[0] * way[0] + along[1] * way[1]) > 0.999, sp["booth"][i]


# ---------------------------------------------------------------------------------------------------------------------
# props.lua: the booth stood in the game's frame

EARTH = os.environ.get("BEAMNG_EARTH_DIR") or os.path.join(os.path.dirname(ROOT), "BeamNG.Earth")
EARTH_EXT = os.path.join(EARTH, "mod", "beamng_earth", "lua", "ge", "extensions").replace("\\", "/")
DAKAR_EXT = os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions").replace("\\", "/")


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


def known_shapes():
    return {l.strip() for l in open(os.path.join(HERE, "data", "game_shapes.txt"), encoding="utf-8") if l.strip() and not l.startswith("#")}


@pytest.fixture(scope="module", params=EDITIONS)
def stood(request):
    lupa = pytest.importorskip("lupa")
    if not os.path.isdir(EARTH_EXT):
        pytest.skip("BeamNG.Earth not found (set BEAMNG_EARTH_DIR)")
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end")
    rt.execute("package.path = package.path .. ';%s/?.lua;%s/?.lua'" % (EARTH_EXT, DAKAR_EXT))
    geodesy, legs, props = (rt.eval("(require('beamngEarth/geodesy'))"), rt.eval("(require('beamngDakar/legs'))"),
                            rt.eval("(require('beamngDakar/props'))"))
    edition, geo = load(request.param)
    spawn = geo[edition["legs"][0]["id"]]["servicePark"]["spawn"]
    anchor = geodesy.newAnchor(spawn["lat"], spawn["lon"])
    sites = props.boothSites(conv(rt, edition), conv(rt, geo))
    placed = props.placeBooth(sites, rt.eval("function(legs, anchor) return function(pts) return legs.toWorld(pts, anchor) end end")(legs, anchor))
    items = [{k: placed[i][k] for k in ("id", "kind", "shape", "x", "y", "z", "yaw")} for i in range(1, len(placed) + 1)]
    lead = legs.toWorld(conv(rt, geo[edition["legs"][0]["id"]]["servicePark"]["lead"]), anchor)
    return geo[edition["legs"][0]["id"]]["servicePark"], items, [(lead[i][1], lead[i][2]) for i in range(1, len(lead) + 1)], len(sites)


def test_the_booth_is_stood_from_the_geometry_with_every_shape_one_the_game_has(stood):
    sp, items, _, n = stood
    gazebos = sum(1 for i in sp["booth"] if i["kind"] == "gazebo")
    assert n == len(sp["booth"]) and len(items) == len(sp["booth"]) + 2 * gazebos       # a gazebo: its cover and its two frames
    ids = [i["id"] for i in items]
    assert len(set(ids)) == len(ids)
    assert {i["shape"] for i in items} <= known_shapes()
    assert {"gazebo_cover_blastr", "gazebo_frame", "gazebo_addon", "table", "chair", "cone"} <= {i["kind"] for i in items}


def test_a_gazebo_is_its_cover_with_a_frame_each_side_along_its_length_which_runs_along_the_cars_way(stood):
    sp, items, lead, _ = stood
    by_id = {i["id"]: i for i in items}
    cover, frame, addon = (by_id["road1:booth1" + s] for s in (":cover", ":frame", ":addon"))
    assert cover["shape"].endswith("s_rally_gazebo_mod_cover_blastr_3x6.dae")
    assert frame["shape"].endswith("s_rally_gazebo_mod_frame_3x3.dae") and addon["shape"].endswith("s_rally_gazebo_mod_frame_addon_3x3.dae")
    assert cover["yaw"] == frame["yaw"] == addon["yaw"]
    # the model's +x is its length: the frames stand 1.5 m either side of the cover along it, the add-on forward (+x)
    ux, uy = math.cos(math.radians(cover["yaw"])), math.sin(math.radians(cover["yaw"]))
    for part, sign in ((addon, 1), (frame, -1)):
        dx, dy = part["x"] - cover["x"], part["y"] - cover["y"]
        assert abs(dx * ux + dy * uy - sign * 1.5) < 1e-6 and abs(-dx * uy + dy * ux) < 1e-6
    # and that is the way the car faces: the long side is along the aisle
    q = next(p for p in lead if math.dist(p, lead[0]) >= 6.0)
    wx, wy = q[0] - lead[0][0], q[1] - lead[0][1]
    assert abs(ux * wx + uy * wy) / math.hypot(wx, wy) > 0.999


def test_the_booth_stands_where_the_geometry_has_it_beside_the_car(stood):
    sp, items, lead, _ = stood
    car = lead[0]
    cover = next(i for i in items if i["id"] == "road1:booth1:cover")
    assert 2.0 < math.dist((cover["x"], cover["y"]), car) < 6.6                      # beside the car
    near = [i for i in items if math.dist((i["x"], i["y"]), car) < 40]
    assert len(near) >= len(items) / 2
    # the table is under the car's own gazebo and its chairs by it, the cones round it
    table = next(i for i in items if i["kind"] == "table")
    assert math.dist((table["x"], table["y"]), (cover["x"], cover["y"])) < 3.2
    assert min(math.dist((i["x"], i["y"]), (cover["x"], cover["y"])) for i in items if i["kind"] == "cone") < 5.0
