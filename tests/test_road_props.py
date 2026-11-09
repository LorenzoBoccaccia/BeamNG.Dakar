"""GitHub #59: props are not placed on roads.

The player found a table on the road leading out of the service park lot. The props (TSStatic shapes without collision) are
checked against the roads the game knows (its navgraph: map.getMap().nodes, which BeamNG.Earth builds from the roads it streams,
a few tiles at a time) and against our own stage and transfer road (roadcheck.lua), when they are made and again, near the
player, whenever the roads changed.

    python -m pytest tests
"""
import json
import math
import os

import pytest

lupa = pytest.importorskip("lupa")

from test_core import DAKAR_EXT, EARTH_EXT, EDITION, POPOLI, STUBS, conv, launched, logs, lua  # noqa: E402,F401

CROSS = {"legs": [{"id": "s1", "type": "special"}]}


@pytest.fixture(scope="module")
def pure():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end")
    rt.execute("package.path = package.path .. ';%s/?.lua'" % DAKAR_EXT)
    return {"rt": rt, "rc": rt.eval("(require('beamngDakar/roadcheck'))"), "props": rt.eval("(require('beamngDakar/props'))")}


def prop(x, y, yaw=0.0, kind="table", **extra):
    d = {"x": x, "y": y, "yaw": yaw, "kind": kind}
    d.update(extra)
    return d


def tbl(pure, d):
    return pure["rt"].table_from(d)


def index(pure, *roads, z=0.0):
    """An Index of roads (ax, ay, bx, by, half width a, half width b)."""
    idx = pure["rc"].new()
    for ax, ay, bx, by, ha, hb in roads:
        idx.add(idx, ax, ay, z, bx, by, z, ha, hb)
    return idx


def hit(pure, p, idx, own=None, z=None):
    got = pure["rc"].hit(tbl(pure, p), pure["props"].footprint(p["kind"]), idx, own, z)
    return got


# --- the geometry ---------------------------------------------------------------------------------------------------------------

def test_a_table_on_a_road_is_a_hit_and_one_beyond_the_half_width_and_the_margin_is_not(pure):
    road = index(pure, (-50, 0, 50, 0, 3.0, 3.0))                     # 6 m wide
    d, half = hit(pure, prop(0, 0.5), road)
    assert d < 1.0 and half == 3.0                                   # on the road
    assert hit(pure, prop(0, 3.0 + 0.75 + 0.45 - 0.1), road)         # the table's near edge (half its width, 0.45 m) is inside the margin
    assert hit(pure, prop(0, 3.0 + 0.75 + 0.45 + 0.1), road) is None  # outside the half width and the margin
    assert hit(pure, prop(0, -9.0), road) is None
    assert hit(pure, prop(80, 0.0), road) is None                    # (past the road's end)


def test_the_footprint_counts_not_only_the_middle(pure):
    road = index(pure, (-50, 0, 50, 0, 2.0, 2.0))
    # a gazebo's cover is 3 m by 1.5 m half sizes, its long side along the model's x: with its middle 4.9 m off the road it is clear
    # lying along it (its near edge is 3.4 m off, beyond the half width and the margin, 2.75 m) and on it turned across (1.9 m)
    assert hit(pure, prop(0, 4.9, yaw=0.0, kind="gazebo_cover_blastr"), road) is None
    assert hit(pure, prop(0, 4.9, yaw=90.0, kind="gazebo_cover_blastr"), road)


def test_the_half_width_is_the_nodes_radius_interpolated_along_the_link(pure):
    road = index(pure, (0, 0, 100, 0, 2.0, 8.0))                     # a road that widens from 4 m to 16 m
    assert hit(pure, prop(10, 5.0), road) is None                    # near its narrow end 5 m off is clear ...
    assert hit(pure, prop(90, 5.0), road)                            # ... near its wide end it is on the road
    d, half = hit(pure, prop(50, 1.0), road)
    assert half == pytest.approx(5.0, abs=0.1)


def test_a_road_far_above_or_below_a_prop_whose_ground_is_known_is_a_bridge_or_a_tunnel(pure):
    road = index(pure, (-50, 0, 50, 0, 3.0, 3.0), z=50.0)
    assert hit(pure, prop(0, 0.0), road)                             # (the prop's ground unknown: the road counts)
    assert hit(pure, prop(0, 0.0), road, None, 49.0)                 # a prop on the ground of the road
    assert hit(pure, prop(0, 0.0), road, None, 30.0) is None         # 20 m under a deck


def test_the_navgraph_of_the_game_is_read_as_the_game_lays_it_out(pure):
    rt = pure["rt"]
    idx = rt.eval("""(function()
      local rc = require('beamngDakar/roadcheck')
      local function vec(x, y, z) return { x = x, y = y, z = z } end
      local nodes = {
        a = { pos = vec(0, 0, 0), radius = 3, links = {} },
        b = { pos = vec(100, 0, 0), radius = 3, links = {} },
        c = { pos = vec(100, 60, 0), radius = 2, links = {} },       -- (a one-sided link b -> c)
        d = { pos = vec(300, 300, 0), radius = 2, links = {} },     -- (a node with no road)
      }
      -- the edge data carries the radii at each end by which node is the `in` one
      local e = { inNode = 'a', outNode = 'b', inRadius = 1.5, outRadius = 4.5 }
      nodes.a.links.b, nodes.b.links.a = e, e
      nodes.b.links.c = { inNode = 'b', outNode = 'c', inRadius = 2, outRadius = 2 }
      local idx = rc.new()
      idx:addGraph(nodes)
      return idx
    end)()""")
    assert idx.n == 2                                                # each link once
    assert hit(pure, prop(0, 3.2), idx) is None                      # 1.5 m half width at a
    assert hit(pure, prop(100, 3.2), idx)                            # 4.5 m at b
    assert hit(pure, prop(100, 30.0), idx)                           # the one-sided link b - c
    assert hit(pure, prop(300, 300), idx) is None


def test_no_navgraph_means_no_roads_and_everything_stays(pure):
    assert hit(pure, prop(0, 0), pure["rc"].new()) is None
    assert pure["rc"].hit(tbl(pure, prop(0, 0)), pure["props"].footprint("table"), None, None, None) is None


def test_the_closure_tape_has_no_footprint_it_stands_across_a_side_road_on_purpose(pure):
    props = pure["props"]
    assert props.footprint("closure_tape") is None
    for kind in ("table", "chair", "cone", "start_board", "tc_board", "gazebo_cover_blastr", "gazebo_frame", "gazebo_addon", "tower"):
        assert props.footprint(kind) is not None
    # every shape of the layout, the booth and the closures is either footprinted or the tape
    assert {k for k in props.SHAPES.keys() if props.footprint(k) is None} == {"closure_tape"}


def straight_world(pure):
    """A special's layout on a straight leg along x (its start at 0, 1 km long), in the world frame: the placed props by id."""
    rt, props = pure["rt"], pure["props"]
    pts = rt.table()
    for i in range(101):
        pts[i + 1] = rt.table_from({1: 10.0 * i, 2: 0.0, 3: 100.0})
    by_leg = rt.table_from({"s1": pts})
    placed = props.place(props.layout(conv(rt, CROSS), rt.table()), by_leg)
    return {str(placed[i].id): placed[i] for i in range(1, len(placed) + 1)}, pts


def test_the_boards_beside_our_own_road_stand_and_the_ones_a_wide_road_covers_go(pure):
    placed, pts = straight_world(pure)
    own = pure["rc"].new()
    own.addLine(own, pts, pure["rc"].OWN_HALF_M)
    fp = pure["props"].footprint
    for pid in ("s1:start_l", "s1:start_r", "s1:sensor_l", "s1:sensor_r", "s1:fin_l", "s1:fin_r", "s1:start_cone_r1", "s1:start_cone_l1"):
        p = placed[pid]
        assert pure["rc"].hit(p, fp(str(p.kind)), None, own, None) is None, pid       # (the boards at 5.5 m, sensors at 4.2, cones at 4.8)
    # the navgraph's road of a 7 m carriageway (half 3.5): the boards still stand; of 10 m (half 5): they are on it
    for half, stands in ((3.5, True), (5.0, False)):
        road = index(pure, (-10, 0, 1010, 0, half, half))
        for pid in ("s1:start_r", "s1:fin_l"):
            p = placed[pid]
            got = pure["rc"].hit(p, fp(str(p.kind)), road, None, None)
            assert (got is None) == stands, (half, pid)


def test_a_prop_on_our_own_road_is_a_hit_even_where_the_navgraph_has_not_got_it_yet(pure):
    placed, pts = straight_world(pure)
    own = pure["rc"].new()
    own.addLine(own, pts, pure["rc"].OWN_HALF_M)
    on_it = prop(200, 1.0)
    assert pure["rc"].hit(tbl(pure, on_it), pure["props"].footprint("table"), None, own, None)
    assert pure["rc"].hit(tbl(pure, on_it), pure["props"].footprint("table"), None, None, None) is None


# --- core.lua: the props in the game --------------------------------------------------------------------------------------------

NAVGRAPH = r"""
NAV = { nodes = {} }
map = { getMap = function() return NAV end }
-- a road from (x0, y0) to (x1, y1) of half width r, as the game's navgraph has it (one link both ways, one edge table)
function addRoad(name, x0, y0, x1, y1, r)
  local a = { pos = vec3(x0, y0, 0), radius = r, links = {} }
  local b = { pos = vec3(x1, y1, 0), radius = r, links = {} }
  local edge = { inNode = name .. 'a', outNode = name .. 'b', inRadius = r, outRadius = r }
  a.links[name .. 'b'], b.links[name .. 'a'] = edge, edge
  NAV.nodes[name .. 'a'], NAV.nodes[name .. 'b'] = a, b
  core.onNavgraphReloaded()                    -- (the game's hook, raised by map.reset)
end
-- which objects were deleted, by id
DEL = {}
local base = scenetree.findObjectById
scenetree.findObjectById = function(id)
  local o = base(id)
  local d = o.delete
  o.delete = function() DEL[id] = true; d() end
  return o
end
"""


def standing(lua, needle):
    """[(x, y, id)] of the props of a shape (a part of its file name) that have been made and not deleted."""
    created, dead = lua.eval("CREATED"), lua.eval("DEL")
    out = []
    for i in range(1, len(created) + 1):
        o = created[i]
        if needle in str(o.fields["shapeName"]) and not dead[i]:
            out.append((o.pos[1], o.pos[2], i))
    return out


def start_at(lua, car=(0, 0, 0)):
    lua.execute(NAVGRAPH)
    launched(lua)
    lua.execute("core.onAnyMissionChanged('started', MISSION)")
    lua.execute("CAR = { %r, %r, %r }" % car)
    lua.execute("core.onUpdate(1)")


@pytest.fixture()
def world():
    """Two worlds of the same loop: a function that makes the second."""
    def make():
        rt = lupa.LuaRuntime(unpack_returned_tuples=True)
        rt.execute(STUBS % {"earth": EARTH_EXT, "dakar": DAKAR_EXT})
        put = rt.eval("function(k, v) JSON[k] = v end")
        base = "/dakar/editions/testbed-algarve/"
        edition = json.load(open(os.path.join(EDITION, "legs.json"), encoding="utf-8"))
        put(base + "legs.json", conv(rt, edition))
        for leg in edition["legs"]:
            g = json.load(open(os.path.join(EDITION, "geometry", leg["id"] + ".json")))
            put(base + "geometry/%s.json" % leg["id"], conv(rt, g))
            closures = os.path.join(EDITION, "closures", leg["id"] + ".json")
            if os.path.isfile(closures):
                put(base + "closures/%s.json" % leg["id"], conv(rt, json.load(open(closures))))
            if leg is edition["legs"][0]:
                rt.execute("PLAYER_LL = { %r, %r }; SERVICE_LL = PLAYER_LL" % (g["points"][0][0], g["points"][0][1]))
                rt.execute("local w = require('beamngDakar/legs').toWorld({ { %r, %r, %r } }, geodesy.newAnchor(ANCHOR_LL[1], ANCHOR_LL[2]));"
                           " PLAYER_XY = { w[1][1], w[1][2] }" % tuple(g["points"][0]))
        return rt
    return make


def test_without_a_navgraph_the_props_stand_as_before(lua):
    start_at(lua)
    assert len(standing(lua, "fold_table")) >= 3 and len(standing(lua, "gazebo_mod_cover")) >= 1
    assert not logs(lua, "road check")


def test_a_table_on_a_road_the_game_knows_is_not_placed(world):
    a = world()
    start_at(a)
    tables = standing(a, "fold_table")
    x, y, _ = tables[0]
    b = world()
    b.execute(NAVGRAPH)
    b.execute("addRoad('exit', %r, %r, %r, %r, 3.0)" % (x - 40, y, x + 40, y))          # the road under the table
    launched(b)
    b.execute("core.onAnyMissionChanged('started', MISSION)")
    b.execute("CAR = { 0, 0, 0 }; core.onUpdate(1)")
    assert not [t for t in standing(b, "fold_table") if math.hypot(t[0] - x, t[1] - y) < 0.5]
    assert len(standing(b, "fold_table")) < len(tables)
    assert len(standing(b, "cone.dae")) >= 4                                          # (the rest of the dressing stands)
    said = logs(b, "road check")
    assert said and any("not placed" in l and "road1:" in l for l in said)
    assert all("half width" in l for l in said)


def test_a_placed_prop_is_deleted_in_the_next_pass_when_its_road_arrives_and_not_made_again(lua):
    start_at(lua)
    tables = standing(lua, "fold_table")
    x, y, ident = tables[0]
    assert not lua.eval("DEL[%d]" % ident)
    lua.execute("addRoad('late', %r, %r, %r, %r, 3.0)" % (x - 40, y, x + 40, y))      # the roads of the tile arrive
    lua.execute("core.onUpdate(1)")                                                   # the next re-snap pass
    assert lua.eval("DEL[%d]" % ident) is True
    deleted = [l for l in logs(lua, "road check") if "deleted" in l]
    assert deleted and "road1:" in deleted[0] and "m from the centre line of a road of half width 3.0 m" in deleted[0]
    # a pass again, or the props made again after a move: not placed again, and said once
    lua.execute("core.onAnyMissionChanged('started', MISSION); core.onUpdate(1); core.onUpdate(1)")
    assert not [t for t in standing(lua, "fold_table") if math.hypot(t[0] - x, t[1] - y) < 0.5]
    ids = [l.split("road check: ")[1].split(" ")[0] for l in logs(lua, "road check")]
    assert len(ids) == len(set(ids))                                                   # (once for each prop)


def test_a_road_that_arrives_near_the_player_only_checks_the_props_in_reach(lua):
    start_at(lua)
    n = len(standing(lua, "cone.dae"))
    lua.execute("addRoad('far', 20000, 20000, 20100, 20000, 3.0); core.onUpdate(1)")      # (nowhere near any prop)
    assert len(standing(lua, "cone.dae")) == n
    assert not logs(lua, "road check")


def test_a_gazebo_is_dropped_whole_when_any_part_is_on_a_road(lua):
    start_at(lua)
    covers = standing(lua, "gazebo_mod_cover")
    frames = standing(lua, "gazebo_mod_frame_3x3")
    addons = standing(lua, "gazebo_mod_frame_addon")
    assert len(covers) >= 2 and len(addons) == len(covers) == len(frames)
    cx, cy, _ = covers[0]
    ax, ay, _ = min(addons, key=lambda t: math.hypot(t[0] - cx, t[1] - cy))         # its add-on, 1.5 m along its length
    lua.execute("addRoad('side', %r, %r, %r, %r, 0.1)" % (ax - 0.5, ay, ax + 0.5, ay))     # (a gazebo's neighbour in the row is 0.3 m away: a point of road)
    lua.execute("core.onUpdate(1)")
    left = standing(lua, "gazebo_mod_cover")
    assert len(left) == len(covers) - 1
    assert not [t for t in left if math.hypot(t[0] - cx, t[1] - cy) < 0.5]
    assert len(standing(lua, "gazebo_mod_frame_3x3")) == len(covers) - 1 and len(standing(lua, "gazebo_mod_frame_addon")) == len(covers) - 1
    said = [l for l in logs(lua, "road check") if any(part in l for part in (":cover", ":frame", ":addon"))]
    assert len(said) == 3 and all("deleted" in l for l in said)                      # (said once for each of its three shapes)


def test_the_closure_tapes_stand_across_roads_on_purpose(lua):
    start_at(lua, (8260, 6996, 50))                       # the stage start: the closures of the stage's side roads are near
    tapes = standing(lua, "tape_road_closure")
    boards = standing(lua, "signboard_start")
    assert tapes and boards
    for k, (x, y, _) in enumerate(tapes):
        lua.execute("addRoad('t%d', %r, %r, %r, %r, 4.0)" % (k, x - 8, y - 8, x + 8, y + 8))
    bx, by, bid = boards[0]
    lua.execute("addRoad('b', %r, %r, %r, %r, 3.0)" % (bx - 8, by + 2.0, bx + 8, by + 2.0))
    lua.execute("core.onUpdate(1)")
    left = standing(lua, "tape_road_closure")
    assert len(left) == len(tapes)                         # (the tape closes the side road: it is meant to be on it)
    # a board on a road is not dropped (GitHub #61: a start without its boards is wrong): it is pushed out of the road
    assert not lua.eval("DEL[%d]" % bid)
    now = [t for t in standing(lua, "signboard_start") if t[2] == bid]
    assert now and 0.2 < math.hypot(now[0][0] - bx, now[0][1] - by) <= 4.0
    said = [l for l in logs(lua, "road check") if "moved" in l and "out of the road" in l]
    assert said


def test_the_stage_boards_sensors_and_cones_a_wide_navgraph_road_covers_are_pushed_out_not_deleted(lua):
    start_at(lua, (8260, 6996, 50))
    before = {k: standing(lua, k) for k in ("signboard_start", "start_sensor", "cone.dae")}
    assert before["signboard_start"] and before["start_sensor"]
    # the navgraph's road along the stage's start at 10 m wide (Earth's half width 5 m for the SR17): the boards at 5.5 m, the sensors at
    # 4.2 m and the cones at 4.8 m from the line stand on it by its margin
    (x1, y1, _), (x2, y2, _) = before["signboard_start"][:2]                        # (the two start boards, one each side of the line)
    mx, my, w = (x1 + x2) / 2, (y1 + y2) / 2, math.hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / w, (y2 - y1) / w                                           # (across the road: the road runs along (-uy, ux))
    lua.execute("addRoad('sr17', %r, %r, %r, %r, 5.0)" % (mx + 30 * uy, my - 30 * ux, mx - 30 * uy, my + 30 * ux))
    lua.execute("core.onUpdate(1)")
    after = {k: standing(lua, k) for k in before}
    assert len(after["signboard_start"]) == len(before["signboard_start"])         # (none deleted)
    assert len(after["start_sensor"]) == len(before["start_sensor"])
    moved = [l for l in logs(lua, "road check") if "moved" in l and "out of the road" in l]
    assert moved


def test_a_table_or_a_gazebo_on_a_road_is_still_dropped_and_only_the_prop_kinds_that_do_a_job_are_pushed(pure):
    pushable = pure["props"].pushable
    for kind in ("start_board", "finish_board", "finish_entry", "radio_point", "radio_warning", "medical_point", "medical_warning",
                 "tc_board", "tc_entry", "start_sensor", "tower", "cone"):
        assert pushable(kind), kind
    for kind in ("table", "chair", "flag", "flag_tear", "feather", "gazebo_cover_blastr", "gazebo_frame", "toolbox_big", "tire_rack", "extinguisher"):
        assert not pushable(kind), kind


def test_a_stage_board_a_wide_road_covers_is_moved_along_its_normal_until_it_clears_the_road_and_the_margin(pure):
    placed, pts = straight_world(pure)
    road = index(pure, (-10, 0, 1010, 0, 5.0, 5.0))                   # Earth's SR17: half width 5 m
    fp = pure["props"].footprint
    rc = pure["rc"]
    for pid in ("s1:start_l", "s1:start_r", "s1:sensor_l", "s1:sensor_r", "s1:fin_l", "s1:fin_r", "s1:start_cone_r1", "s1:start_cone_l1"):
        p = placed[pid]
        assert rc.hit(p, fp(str(p.kind)), road, None, None), pid        # (on the road by the margin: what the first run deleted)
        nx, ny, moved = rc.relocate(p, fp(str(p.kind)), road, None, None, pure["props"].PUSH_MAX_M)
        assert 0 < moved <= pure["props"].PUSH_MAX_M, pid
        assert rc.hit(tbl(pure, {"x": nx, "y": ny, "yaw": p.yaw, "kind": str(p.kind)}), fp(str(p.kind)), road, None, None) is None
        assert math.copysign(1, ny) == math.copysign(1, p.y) and abs(ny) > abs(p.y)        # (outward, on the side it stood: the board of the left stays left)
        assert abs(nx - p.x) < 1e-6                                     # (along the normal of the road, which runs along x)


def test_nothing_that_cannot_be_cleared_within_the_push_is_moved_and_a_prop_off_every_road_is_not_touched(pure):
    road = index(pure, (-50, 0, 50, 0, 5.0, 5.0))
    fp = pure["props"].footprint("start_board")
    rc = pure["rc"]
    on_it = prop(0, 0.5, kind="start_board", side=1)
    assert rc.relocate(tbl(pure, on_it), fp, road, None, None, 4.0) is None          # (6.05 m out would be needed: more than 4)
    nx, ny, moved = rc.relocate(tbl(pure, on_it), fp, road, None, None, 8.0)
    assert ny > 0 and moved == pytest.approx(6.0, abs=0.3) and rc.hit(tbl(pure, prop(nx, ny, kind="start_board")), fp, road, None, None) is None
    assert rc.relocate(tbl(pure, prop(0, -40.0, kind="start_board")), fp, road, None, None, 4.0) is None           # (not on a road)
    # (right on the centre line of a road running along +x: the side it belongs to, 1 the right of the direction of travel, -y)
    nx, ny, moved = rc.relocate(tbl(pure, prop(0, 0.0, kind="start_board", side=1)), fp, road, None, None, 8.0)
    assert ny < 0
    nx, ny, moved = rc.relocate(tbl(pure, prop(0, 0.0, kind="start_board", side=-1)), fp, road, None, None, 8.0)
    assert ny > 0


def test_our_own_stage_road_counts_without_the_navgraph(lua):
    start_at(lua, (8260, 6996, 50))
    # the testbed's staging area at the end of the transfer has cones that stand where the stage's own line bends back
    said = logs(lua, "road check")
    assert any("not placed" in l and "cone" in l.lower() or "stagein_cone" in l for l in said), said
    assert all("half width 2.5 m" in l for l in said)


def test_the_booths_gazebo_beside_the_lots_aisles_is_not_a_road_of_ours(lua):
    start_at(lua)
    assert len(standing(lua, "gazebo_mod_cover")) >= 2    # the booth stands beside the drive through the lot (the own line is not checked for it)


def test_a_rebuilt_navgraph_counts_even_when_the_hook_was_missed(lua):
    start_at(lua)
    tables = standing(lua, "fold_table")
    x, y, ident = tables[0]
    lua.execute("""
      local a = { pos = vec3(%r, %r, 0), radius = 3, links = {} }
      local b = { pos = vec3(%r, %r, 0), radius = 3, links = {} }
      local e = { inNode = 'za', outNode = 'zb', inRadius = 3, outRadius = 3 }
      a.links.zb, b.links.za = e, e
      NAV.nodes.za, NAV.nodes.zb = a, b                -- (no onNavgraphReloaded: the node count is looked at every 2 s)
    """ % (x - 40, y, x + 40, y))
    lua.execute("core.onUpdate(3); core.onUpdate(1)")
    assert lua.eval("DEL[%d]" % ident) is True


# --- the offline check of the booth against the roads round the lot (tools/booth.py) ----------------------------------------------

def test_the_popoli_tc0_table_stands_on_the_road_out_of_the_lot_and_is_not_placed(pure):
    """The table the player found: the table of the first transfer's start stands 9 m beside the leg's line at s = 8 m, which in the
    lot is a loop of the drive; the leg comes back past it."""
    rt, props, rc = pure["rt"], pure["props"], pure["rc"]
    rt.execute("package.path = package.path .. ';%s/?.lua'" % EARTH_EXT)
    geodesy, legs = rt.eval("(require('beamngEarth/geodesy'))"), rt.eval("(require('beamngDakar/legs'))")
    edition = json.load(open(os.path.join(POPOLI, "legs.json"), encoding="utf-8"))
    geo = {l["id"]: json.load(open(os.path.join(POPOLI, "geometry", l["id"] + ".json"))) for l in edition["legs"]}
    spawn = geo[edition["legs"][0]["id"]]["servicePark"]["spawn"]
    anchor = geodesy.newAnchor(spawn["lat"], spawn["lon"])
    by_leg = rt.table()
    for leg in edition["legs"]:
        by_leg[leg["id"]] = legs.toWorld(conv(rt, geo[leg["id"]]["points"]), anchor)
    placed = props.place(props.layout(conv(rt, edition), rt.table()), by_leg)
    own = rc.new()
    for leg in edition["legs"]:
        own.addLine(own, by_leg[leg["id"]], rc.OWN_HALF_M)
    got = {}
    for i in range(1, len(placed) + 1):
        p = placed[i]
        fp = props.footprint(str(p.kind))
        if fp and rc.hit(p, fp, None, own, None):
            got[str(p.id)] = str(p.kind)
    assert got.get("road1:table") == "table", got
    # and what stands beside the road, as designed, is not dropped: the start boards, sensors and finish boards
    assert not [k for k in got.values() if k in ("start_board", "finish_board", "start_sensor", "radio_point", "tc_board")]
