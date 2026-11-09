"""The mission start, TC0, TC1, SS1 ... aligned (GitHub #55): the car starts in the parking lot facing its exit with TC0 ahead
of it, every leg is driven into the next one from behind and the controls, the start line and the staging props all face
along the road there. On both editions' real geometry and on a synthetic loop with several liaisons and specials."""
import json
import math
import os
import sys

import pytest

lupa = pytest.importorskip("lupa")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import joins  # noqa: E402
import legplan  # noqa: E402
import parkinglot as pl  # noqa: E402
from test_joins import LOOP_STREETS, Net, ll, loop_legs  # noqa: E402

EARTH = os.environ.get("BEAMNG_EARTH_DIR") or os.path.join(os.path.dirname(ROOT), "BeamNG.Earth")
EARTH_EXT = os.path.join(EARTH, "mod", "beamng_earth", "lua", "ge", "extensions").replace("\\", "/")
DAKAR_EXT = os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions").replace("\\", "/")
EDITIONS = ["testbed-algarve", "svolte-di-popoli"]

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


def load(ed):
    base = os.path.join(ROOT, "editions", ed)
    edition = json.load(open(os.path.join(base, "legs.json"), encoding="utf-8"))
    geo = {l["id"]: json.load(open(os.path.join(base, "geometry", l["id"] + ".json"))) for l in edition["legs"]}
    return edition, geo


@pytest.fixture(scope="module")
def rt():
    r = lupa.LuaRuntime(unpack_returned_tuples=True)
    r.execute("math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end")
    r.execute("package.path = package.path .. ';%s/?.lua;%s/?.lua'" % (EARTH_EXT, DAKAR_EXT))
    return r


class World:
    """An edition made for one anchor: the Lua result, the legs' world points and the props, read into Python."""

    def __init__(self, rt, edition, geo, anchor_ll):
        self.rt, self.edition, self.geo = rt, edition, geo
        geodesy, self.legs, self.props = (rt.eval("(require('beamngEarth/geodesy'))"), rt.eval("(require('beamngDakar/legs'))"),
                                          rt.eval("(require('beamngDakar/props'))"))
        self.anchor = geodesy.newAnchor(anchor_ll[0], anchor_ll[1])
        self.res = self.legs.materialize(conv(rt, edition), conv(rt, geo), self.anchor, rt.eval("function(leg) return 'm/' .. leg.id end"))
        self.ids = [l["id"] for l in edition["legs"]]
        self.types = {l["id"]: l["type"] for l in edition["legs"]}
        self.world = {}
        for i in self.ids:
            w = self.legs.toWorld(conv(rt, geo[i]["points"]), self.anchor)
            self.world[i] = [(w[k][1], w[k][2], w[k][3]) for k in range(1, len(w) + 1)]

    def start(self, leg, k):
        """The start position k (1 = TC_in / SS_start_line, 2 = TC_out / SS_stop_control): name, (x, y, z), facing in degrees."""
        sp = self.res["files"]["m/%s/race.race.json" % leg]["startPositions"][k]
        q = sp["rot"]
        return sp["name"], (sp["pos"][1], sp["pos"][2], sp["pos"][3]), math.degrees(2 * math.atan2(q[3], q[4])) + 90, (q[1], q[2], q[3], q[4])

    def road(self, leg, at_end, window=10.0):
        """The direction (degrees counter-clockwise from +x) the leg goes in over its first / last `window` metres."""
        w = self.world[leg][::-1] if at_end else self.world[leg]
        a = w[0]
        for b in w[1:]:
            if math.dist(a[:2], b[:2]) >= window:
                break
        d = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        return (d + 180) % 360 if at_end else d


def off(a, b):
    return abs((a - b + 180) % 360 - 180)


@pytest.fixture(scope="module", params=EDITIONS)
def real(request, rt):
    edition, geo = load(request.param)
    first = geo[edition["legs"][0]["id"]]["points"][0]
    return World(rt, edition, geo, first[:2])


def synthetic_world(rt):
    net = Net(LOOP_STREETS)
    items = legplan.plan_edition(loop_legs(), net.route)
    edition = {"edition": "synthetic", "title": "Synthetic", "missionPrefix": "dakar-synth",
               "legs": [{"id": it["leg"]["id"], "type": it["leg"]["type"]} for it in items]}
    geo = {it["leg"]["id"]: {"id": it["leg"]["id"], "type": it["leg"]["type"], "points": [[p[0], p[1], 0.0] for p in it["pts"]]}
           for it in items}
    return World(rt, edition, geo, ll(0, 0)), items


@pytest.fixture(scope="module")
def synth(rt):
    return synthetic_world(rt)[0]


# ---------------------------------------------------------------------------------------------------------------------
# the geometry the tools wrote

@pytest.mark.parametrize("ed", EDITIONS)
def test_every_join_of_the_geometry_is_driven_into_from_behind_and_straight(ed):
    edition, geo = load(ed)
    ids = [l["id"] for l in edition["legs"]]
    for a, b in zip(ids, ids[1:]):
        pa, pb = [tuple(p[:2]) for p in geo[a]["points"]], [tuple(p[:2]) for p in geo[b]["points"]]
        assert joins.metres(pa[-1], pb[0]) < 1.0                              # the end of one is the start of the next
        # the transfer's last segment heading is the stage's first segment heading, within a few degrees
        last = joins.bearing(pa[-2], pa[-1])
        first = joins.bearing(pb[0], pb[1])
        assert joins.angle_diff(joins.arriving(pa), joins.leaving(pb)) <= 10, (ed, a, b)
        assert joins.angle_diff(last, first) <= 25, (ed, a, b)
        # and no turn of more than about 20 degrees in the last 30 m before the control
        assert joins.tail_turn(pa, 30.0) <= 20.0, (ed, a, b)
        assert joins.check(pa, pb)["ok"]


def test_popoli_ends_its_transfer_up_the_sr17_past_the_turn_and_not_near_the_underpass():
    edition, geo = load("svolte-di-popoli")
    road, ss = geo["road1"]["points"], geo["ss1"]["points"]
    # the stage starts past the junction the transfer turns onto the SR17 at, which is south of it, heading north
    assert ss[0][0] > 42.1745
    assert joins.bearing(road[-2], road[-1]) < 20 or joins.bearing(road[-2], road[-1]) > 340      # arriving heading north
    # the blocked railway underpass (42.1710, 13.8247) is nowhere near either leg
    for p in road + ss:
        assert joins.metres(p[:2], (42.1710, 13.8247)) > 300


@pytest.mark.parametrize("ed", EDITIONS)
def test_the_car_starts_on_an_aisle_inside_the_lot_facing_its_exit(ed):
    edition, geo = load(ed)
    lot = geo[edition["legs"][0]["id"]]["servicePark"]
    lines = [[tuple(p) for p in l] for l in lot["lines"]]
    spawn, lead, exit_at = lot["spawn"], [tuple(p[:2]) for p in lot["lead"]], (lot["exit"]["lat"], lot["exit"]["lon"])
    assert (lot["lat"], lot["lon"]) == (spawn["lat"], spawn["lon"])
    assert pl.to_lines((spawn["lat"], spawn["lon"]), lines) < 3.0              # on an aisle (the lines are stored to 0.1 m)
    assert all(pl.to_lines(p, lines) < 6.0 for p in lead)                      # inside the lot from the car to TC0
    assert pl.to_lines(exit_at, lines) < 3.0
    # facing the exit: the car's heading is within 90 degrees of the way to the exit, and TC0 is nearer the exit than the car
    heading = joins.leaving(lead, joins.SAMPLE_M + 1)
    assert joins.angle_diff(heading, joins.bearing(lead[0], exit_at)) <= 90
    assert joins.angle_diff(spawn["bearing"], heading) <= 10
    assert joins.metres(lead[-1], exit_at) < joins.metres(lead[0], exit_at)
    # the route out of the lot leaves the way the exit says
    pts = [tuple(p[:2]) for p in geo[edition["legs"][0]["id"]]["points"]]
    beyond = pts[min(range(len(pts)), key=lambda i: joins.metres(pts[i], exit_at)) + 1]
    assert joins.angle_diff(lot["exit"]["bearing"], joins.bearing(exit_at, beyond)) < 90


@pytest.mark.parametrize("ed", EDITIONS)
def test_tc0_is_a_few_metres_ahead_inside_the_lot_and_the_car_is_not_timed_at_once(ed):
    edition, geo = load(ed)
    g = geo[edition["legs"][0]["id"]]
    lines = [[tuple(p) for p in l] for l in g["servicePark"]["lines"]]
    lead = [tuple(p[:2]) for p in g["servicePark"]["lead"]]
    tc0 = tuple(g["points"][0][:2])
    assert joins.metres(lead[-1], tc0) < 0.5                                    # the leg starts where the lead ends
    assert pl.to_lines(tc0, lines) < 6.0                                        # TC0 is in the lot
    assert 12 <= joins.length(lead) <= 21                                       # a few metres, not the 70 m of #27
    # (GitHub #27) the loop times the player at once when its front is within 10 m before the TC plane: not so here
    assert not legplan.at_control(lead + [tuple(p[:2]) for p in g["points"][1:6]], joins.length(lead))


# ---------------------------------------------------------------------------------------------------------------------
# what the mod makes of it

def test_the_stall_is_the_spawn_facing_along_the_lot_with_tc0_ahead(real):
    res, lot = real.res, real.geo[real.ids[0]]["servicePark"]
    pl_ = res["placement"]
    w = real.legs.toWorld(conv(real.rt, lot["lead"]), real.anchor)
    assert pl_["inLot"] is True
    assert math.dist((pl_["pos"][1], pl_["pos"][2]), (w[1][1], w[1][2])) < 1e-6          # the car is on the aisle, at the spawn
    q = pl_["rot"]
    facing = math.degrees(2 * math.atan2(q[3], q[4])) + 90
    lead_dir = math.degrees(math.atan2(w[len(w)][2] - w[1][2], w[len(w)][1] - w[1][1]))
    assert off(facing, math.degrees(math.atan2(w[2][2] - w[1][2], w[2][1] - w[1][1]))) < 35
    assert off(facing, pl_["road"]) < 35
    assert off(facing, lead_dir) < 90                                                     # TC0 is ahead, not behind
    # TC0 itself: the first control, at the end of the lead
    name, pos, _, _ = real.start(real.ids[0], 1)
    assert name == "TC_in" and math.dist(pos[:2], (w[len(w)][1], w[len(w)][2])) < 1e-6
    assert 12 <= pl_["backM"] <= 21


@pytest.mark.parametrize("ed", EDITIONS)
def test_without_a_lot_the_stall_stands_back_from_tc0_as_before(rt, ed):
    edition, geo = load(ed)
    for g in geo.values():
        g.pop("servicePark", None)
    w = World(rt, edition, geo, geo[edition["legs"][0]["id"]]["points"][0][:2])
    pl_ = w.res["placement"]
    assert not pl_["inLot"] and pl_["backM"] == 70
    name, tc0, facing, _ = w.start(w.ids[0], 1)
    assert math.dist(tc0[:2], (pl_["pos"][1], pl_["pos"][2])) == pytest.approx(70, abs=1e-6)
    q = pl_["rot"]
    assert off(math.degrees(2 * math.atan2(q[3], q[4])) + 90, facing) < 5


def check_markers_and_joins(w):
    ids = w.ids
    for leg in ids:
        for k, at_end in ((1, False), (2, True)):
            name, pos, facing, _ = w.start(leg, k)
            assert off(facing, w.road(leg, at_end)) <= 15, (leg, name)                  # faces along the road, not against it
            end = w.world[leg][-1 if at_end else 0]
            assert math.dist(pos[:2], end[:2]) < 1e-6
    for a, b in zip(ids, ids[1:]):
        out_name, out_pos, out_facing, out_q = w.start(a, 2)
        in_name, in_pos, in_facing, in_q = w.start(b, 1)
        assert out_name == ("TC_out" if w.types[a] == "transfer" else "SS_stop_control")
        assert in_name == ("TC_in" if w.types[b] == "transfer" else "SS_start_line")
        assert math.dist(out_pos[:2], in_pos[:2]) < 1.0                                  # one place
        assert out_q == pytest.approx(in_q, abs=1e-9)                                    # facing the same way exactly
        # driven into from behind: the leg's road 30 m before the control is behind its plane, the next leg's 30 m after it is ahead
        fx, fy = math.cos(math.radians(out_facing)), math.sin(math.radians(out_facing))
        before = point_along(w.world[a], -30.0)
        after = point_along(w.world[b], 30.0)
        assert (before[0] - out_pos[0]) * fx + (before[1] - out_pos[1]) * fy < -25
        assert (after[0] - in_pos[0]) * fx + (after[1] - in_pos[1]) * fy > 25


def point_along(pts, s):
    """The point `s` metres along the path from its start (s >= 0) or before its end (s < 0), in x, y."""
    p = pts if s >= 0 else pts[::-1]
    left = abs(s)
    for a, b in zip(p, p[1:]):
        d = math.dist(a[:2], b[:2])
        if d >= left:
            f = left / d
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        left -= d
    return p[-1][:2]


def test_every_control_faces_along_the_road_and_both_sides_of_a_join_agree(real):
    check_markers_and_joins(real)


def test_the_probe_lists_every_control_join_and_the_spawn(real):
    lines = [real.legs.probeLines(real.res)[i] for i in range(1, len(real.legs.probeLines(real.res)) + 1)]
    n = len(real.ids)
    assert len(lines) == 2 * n + (n - 1) + 1
    assert all(l.startswith("DAKAR-PROBE ") for l in lines)
    assert sum(" marker " in l for l in lines) == 2 * n and sum(" join " in l for l in lines) == n - 1
    assert any(" spawn: " in l and "inside the lot" in l for l in lines)
    for l in lines:
        if " marker " in l:
            assert float(l.rsplit("off by ", 1)[1]) <= 15, l


# ---------------------------------------------------------------------------------------------------------------------
# a loop of liaisons and specials: TC0 -> L1 -> TC1 -> SS1 -> TC2 -> L2 -> TC3 -> L3 -> TC4 -> SS2 -> TC5 -> L4

def test_a_long_synthetic_loop_has_every_kind_of_join_and_every_one_is_aligned(synth):
    kinds = [(synth.types[a], synth.types[b]) for a, b in zip(synth.ids, synth.ids[1:])]
    assert sum(t == "transfer" for t in synth.types.values()) >= 3 and sum(t == "special" for t in synth.types.values()) >= 2
    assert {("transfer", "special"), ("special", "transfer"), ("transfer", "transfer")} <= set(kinds)
    check_markers_and_joins(synth)
    assert len(synth.res["joins"]) == len(synth.ids) - 1
    for k in range(1, len(synth.ids)):
        j = synth.res["joins"][k]
        assert j["dir"] is not None and j["diff"] <= 15 and j["gap"] < 1.0


def props_by_id(w):
    defs = w.props.layout(conv(w.rt, w.edition), w.rt.table())
    by_leg = w.rt.table()
    for i in w.ids:
        by_leg[i] = w.legs.toWorld(conv(w.rt, w.geo[i]["points"]), w.anchor)
    placed = w.props.place(defs, by_leg)
    return {placed[k]["id"]: placed[k] for k in range(1, len(placed) + 1)}, {defs[k]["id"]: defs[k] for k in range(1, len(defs) + 1)}


def along(w, leg, s):
    """The point `s` metres along the leg (x, y) and the unit direction of travel there (worked out here, not by the mod)."""
    pts = w.world[leg]
    left = max(0.0, s)
    for a, b in zip(pts, pts[1:]):
        d = math.dist(a, b)                                          # (along the 3D path, as the mod measures it)
        if d >= left or b is pts[-1]:
            f = min(1.0, left / d)
            h = math.hypot(b[0] - a[0], b[1] - a[1])
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f), ((b[0] - a[0]) / h, (b[1] - a[1]) / h)
        left -= d


def check_props_face_the_road(w):
    """Nothing is mirrored: a prop facing the driver has its front against the direction of travel, 'along' along it, 'out' to
    the right of the road, and a prop 'lateral' metres to the right of the road stands there, wherever the road goes (the
    model's front is local +y, yaw counter-clockwise from +x)."""
    placed, defs = props_by_id(w)
    assert placed
    seen = set()
    for pid, p in placed.items():
        d = defs[pid]
        total = sum(math.dist(a, b) for a, b in zip(w.world[p["leg"]], w.world[p["leg"]][1:]))
        s = total - d["s"] if d["fromEnd"] else d["s"]
        (x, y), (dx, dy) = along(w, p["leg"], s)
        fx, fy = math.cos(math.radians(p["yaw"] + 90)), math.sin(math.radians(p["yaw"] + 90))
        dot, right = fx * dx + fy * dy, fx * dy - fy * dx          # (dy, -dx) is to the right of the direction of travel
        if not d["autoSide"]:
            assert math.dist((p["x"], p["y"]), (x + d["lateral"] * dy, y - d["lateral"] * dx)) < 0.02, pid
        else:                                                      # (the side of the last bend's outside is the mod's to pick)
            assert abs(math.hypot(p["x"] - x, p["y"] - y) - abs(d["lateral"])) < 0.02, pid
        want = d["facing"]
        if want == "driver":
            assert dot < -0.999, (pid, dot)
        elif want == "along":
            assert dot > 0.999, (pid, dot)
        else:
            assert right > 0.999, (pid, right)
        seen.add(want)
    assert seen == {"driver", "along", "out"}
    return placed


def test_every_prop_of_a_synthetic_loop_faces_the_road_and_a_join_is_dressed_once(synth):
    placed = check_props_face_the_road(synth)
    # transfer -> special (ss1, ss2) and transfer -> transfer (road3): the end of the transfer dresses the join
    assert "road1:stagein_tc" in placed and "road3:stagein_tc" in placed
    assert not any(p.startswith("ss1:stagestart_") or p.startswith("ss2:stagestart_") for p in placed)
    assert not any(p.startswith("road3:") and ":tc_in" in p or p == "road3:table" for p in placed)       # (road 3 starts where road 2 ends)
    # special -> transfer: the finish boards of the special, the service-park style start of the transfer after it
    assert "ss1:fin_l" in placed and "road2:tc_in" in placed and "road4:tc_in" in placed


def test_every_prop_of_both_real_editions_faces_the_road(real):
    check_props_face_the_road(real)
