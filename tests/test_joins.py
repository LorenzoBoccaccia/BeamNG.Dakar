"""tools/joins.py, tools/legplan.py and the lot start of tools/parkinglot.py without the network: a fake road
network stands in for the router, so the joins between legs and the car's start in a parking lot are made and checked
offline, for any loop (TC0 -> liaison -> TC1 -> special -> TC2 -> liaison -> TC3 -> liaison ...)."""
import heapq
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import joins  # noqa: E402
import legplan  # noqa: E402
import parkinglot as pl  # noqa: E402

LAT0, LON0 = 42.0, 13.8
M_LAT = 111320.0
M_LON = 111320.0 * math.cos(math.radians(LAT0))


def ll(x, y):
    """(lat, lon) of a place x metres east and y metres north of the origin."""
    return (LAT0 + y / M_LAT, LON0 + x / M_LON)


def xy(p):
    return ((p[1] - LON0) * M_LON, (p[0] - LAT0) * M_LAT)


class Net:
    """A road network of straight streets (polylines in metres, corners and crossings on multiples of 10 m) with a router
    that snaps each via point to the nearest 10 m node and takes the shortest way between them: what OSRM does, small."""

    STEP = 10

    def __init__(self, streets):
        self.adj = {}
        for line in streets:
            keys = []
            for (x0, y0), (x1, y1) in zip(line, line[1:]):
                n = max(1, int(round(math.hypot(x1 - x0, y1 - y0) / self.STEP)))
                for k in range(n):
                    keys.append((round((x0 + (x1 - x0) * k / n) / self.STEP), round((y0 + (y1 - y0) * k / n) / self.STEP)))
            keys.append((round(line[-1][0] / self.STEP), round(line[-1][1] / self.STEP)))
            for a, b in zip(keys, keys[1:]):
                self.adj.setdefault(a, set()).add(b)
                self.adj.setdefault(b, set()).add(a)

    def snap(self, p):
        x, y = xy(p)
        return min(self.adj, key=lambda k: math.hypot(k[0] * self.STEP - x, k[1] * self.STEP - y))

    def path(self, a, b):
        dist, prev, heap = {a: 0.0}, {}, [(0.0, a)]
        while heap:
            d, k = heapq.heappop(heap)
            if k == b:
                break
            if d > dist.get(k, 1e18):
                continue
            for n in self.adj[k]:
                nd = d + math.hypot(n[0] - k[0], n[1] - k[1])
                if nd < dist.get(n, 1e18):
                    dist[n], prev[n] = nd, k
                    heapq.heappush(heap, (nd, n))
        out = [b]
        while out[-1] != a:
            out.append(prev[out[-1]])
        return out[::-1]

    def route(self, vias):
        nodes = [self.snap(v) for v in vias]
        keys = [nodes[0]]
        for a, b in zip(nodes, nodes[1:]):
            keys += self.path(a, b)[1:]
        return [ll(k[0] * self.STEP, k[1] * self.STEP) for k in keys]


def leg(id_, type_, *points):
    return {"id": id_, "type": type_, "via": [list(ll(*p)) for p in points]}


# ---------------------------------------------------------------------------------------------------------------------
# the checks

def test_a_straight_run_is_ok_and_a_turn_just_before_the_end_is_not():
    straight = [ll(0, y) for y in range(0, 201, 10)]
    onwards = [ll(0, y) for y in range(200, 401, 10)]
    assert joins.check(straight, onwards)["ok"]
    bent = [ll(0, y) for y in range(0, 101, 10)] + [ll(x, 100) for x in range(10, 101, 10)]          # a right angle 100 m on
    assert joins.check(bent, [ll(100, 100)] + [ll(x, 100) for x in range(110, 300, 10)])["ok"]       # (80 m of straight after it)
    late = [ll(0, y) for y in range(0, 101, 10)] + [ll(x, 100) for x in range(10, 21, 10)]           # the corner 10 m before the end
    rep = joins.check(late, [ll(20, 100)] + [ll(x, 100) for x in range(30, 300, 10)])
    assert not rep["ok"] and rep["tailTurn"] > joins.MAX_TURN_DEG


def test_legs_that_run_back_on_each_other_do_not_join():
    a = [ll(0, y) for y in range(0, 201, 10)]
    back = [ll(0, y) for y in range(200, -1, -10)]
    rep = joins.check(a, back)
    assert not rep["ok"] and rep["diff"] > 170


# ---------------------------------------------------------------------------------------------------------------------
# the join moved past the turn

POPOLI_LIKE = [
    [(-300, 10), (0, 10)],                    # the side road the transfer comes down, to the junction ...
    [(0, -300), (0, 700)],                    # ... with the main road, which runs north-south
]


def test_a_transfer_that_turns_onto_the_stage_road_ends_past_the_turn():
    # Popoli: road 1 came down the side road, turned onto the main road and went south to the foot of the hill; the
    # stage started there and went back north. TC1 and the start have to be north of the junction, the transfer driving
    # into them heading north.
    net = Net(POPOLI_LIKE)
    legs = [leg("road1", "transfer", (-300, 10), (0, -100)), leg("ss1", "special", (0, -100), (0, 500))]
    a, b = legplan.plan_edition(legs, net.route)
    rep = joins.check(a["pts"], b["pts"])
    assert rep["ok"] and rep["diff"] < 5, rep
    assert joins.metres(a["pts"][-1], b["pts"][0]) < 0.5                        # one place
    x, y = xy(a["pts"][-1])
    assert abs(x) < 1 and y >= 10 + joins.RUN_M                                 # on the main road, a straight run past the junction
    assert a["joinIn"] is None and b["joinIn"]["mode"] == "extend"
    assert 40 <= b["joinIn"]["shiftM"] <= 160
    # the transfer's last 30 m and the stage's first are the one straight run
    assert joins.tail_turn(a["pts"]) < 10 and joins.head_turn(b["pts"]) < 10


def test_a_join_that_is_right_stays_where_it_is():
    net = Net([[(0, 0), (0, 1000)]])
    a, b = legplan.plan_edition([leg("l1", "transfer", (0, 0), (0, 300)), leg("ss1", "special", (0, 300), (0, 900))], net.route)
    assert b["joinIn"]["mode"] == "kept"
    assert xy(a["pts"][-1]) == pytest.approx((0, 300), abs=1)


# ---------------------------------------------------------------------------------------------------------------------
# a loop of any length: TC0 -> L1 -> TC1 -> SS1 -> TC2 -> L2 -> TC3 -> L3 -> TC4 -> SS2 -> TC5 -> L4

LOOP_STREETS = [
    [(0, 0), (500, 0), (500, 300)],            # L1 comes up to (500, 300) heading north ...
    [(500, 300), (1500, 300)],                 # ... SS1 leaves east
    [(1500, 300), (1500, 600)],                # L2 leaves north from where SS1 stopped (it arrives heading east)
    [(1500, 600), (300, 600)],                 # L3 turns west, SS2 carries on west (a join that is right)
    [(300, 600), (300, 900)],                  # L4 leaves north from where SS2 stopped
]


def loop_legs():
    return [leg("road1", "transfer", (0, 0), (500, 300)),
            leg("ss1", "special", (500, 300), (1500, 300)),
            leg("road2", "transfer", (1500, 300), (1500, 600)),
            leg("road3", "transfer", (1500, 600), (900, 600)),
            leg("ss2", "special", (900, 600), (300, 600)),
            leg("road4", "transfer", (300, 600), (300, 900))]


def test_every_join_of_a_long_loop_is_one_straight_run():
    net = Net(LOOP_STREETS)
    items = legplan.plan_edition(loop_legs(), net.route)
    assert [i["leg"]["id"] for i in items] == ["road1", "ss1", "road2", "road3", "ss2", "road4"]
    modes = []
    for a, b in zip(items, items[1:]):
        rep = joins.check(a["pts"], b["pts"])
        assert rep["ok"], (a["leg"]["id"], b["leg"]["id"], rep)
        assert rep["diff"] <= joins.MAX_DIFF_DEG and rep["tailTurn"] <= joins.MAX_TURN_DEG
        assert joins.metres(a["pts"][-1], b["pts"][0]) < 0.5                   # one place
        modes.append(b["joinIn"]["mode"])
    # transfer -> special, special -> transfer, transfer -> transfer: moved; transfer -> special on a straight: left
    assert modes == ["extend", "extend", "extend", "kept", "extend"]
    for it in items:                                                           # (no leg was eaten by the moves)
        assert joins.length(it["pts"]) > 150
    # the roads are still the network's: every point on a street
    for it in items:
        for p in it["pts"]:
            x, y = xy(p)
            assert any(abs(x - sx) < 1 or abs(y - sy) < 1 for sx, sy in ((0, 0), (500, 0), (500, 300), (1500, 300), (1500, 600),
                                                                          (300, 600), (300, 900)))


def test_a_join_nothing_can_fix_is_reported_and_left():
    # a dead end: the stage starts where the transfer ends and goes back the way it came, nothing to move along
    net = Net([[(0, 0), (0, 400)]])
    said = []
    a, b = legplan.plan_edition([leg("l1", "transfer", (0, 0), (0, 400)), leg("ss1", "special", (0, 400), (0, 0))], net.route,
                                log=said.append)
    assert b["joinIn"]["mode"] == "none" and any("does not run into ss1 straight" in s for s in said)


# ---------------------------------------------------------------------------------------------------------------------
# the car's start in a lot

def lot_lines(rows=4, width=120, gap=24):
    """A lot east of the origin: `rows` aisles `gap` m apart, `width` m long, joined by a cross aisle at the west end; the road
    leaves from its east end at the middle row."""
    out = [[ll(0, gap * k), ll(width, gap * k)] for k in range(rows)]
    out.append([ll(0, gap * k) for k in range(rows)])
    return out


def lot_world():
    # the aisles, and the road the lot's exit joins: from the middle row's east end on east, then north
    return lot_lines(), Net([[(0, 0), (120, 0)], [(0, 0), (0, 72)], [(0, 24), (120, 24)], [(0, 48), (120, 48)], [(0, 72), (120, 72)],
                             [(120, 24), (400, 24), (400, 600)]])


def test_the_car_starts_on_an_aisle_facing_the_exit_with_tc0_ahead_inside_the_lot():
    lines, net = lot_world()
    info = {"lat": ll(0, 24)[0], "lon": ll(0, 24)[1], "aisles": 5, "lengthM": 800, "distM": 0}
    legs = [dict(leg("road1", "transfer", (0, 24), (400, 600)), parkingNear=300)]
    a, = legplan.plan_edition(legs, net.route, lambda lat, lon, r: (info, lines))
    plan, lead = a["plan"], a["lead"]
    spawn, tc0 = lead[0], lead[-1]
    assert pl.to_lines(spawn, lines) < 1.0                              # on an aisle
    assert all(pl.to_lines(p, lines) < 2.0 for p in lead)               # inside the lot all the way to TC0
    assert joins.metres(tc0, a["pts"][0]) < 0.5                         # the leg starts at TC0
    assert joins.length(lead) == pytest.approx(pl.TC0_AHEAD_M, abs=1.5)
    exit_at = plan["exit"]
    assert pl.to_lines(exit_at, lines) < 1.0
    # facing the exit: the first metres go toward it, and the whole way from the car to the exit never turns round
    want = joins.bearing(spawn, exit_at)
    assert joins.angle_diff(joins.leaving(lead, 8.0), want) <= pl.SPAWN_FACING_DEG
    assert joins.angle_diff(joins.leaving(lead, 8.0), joins.bearing(spawn, tc0)) < 30     # the pin is ahead of the car, not behind
    assert joins.metres(tc0, exit_at) < joins.metres(spawn, exit_at)                       # (and nearer the exit than the car)
    assert joins.length(plan["path"]) >= pl.SPAWN_MIN_M


def test_the_exit_is_where_the_route_leaves_the_aisles():
    lines, net = lot_world()
    route = net.route([ll(0, 24), ll(400, 600)])
    ex = pl.exit_of(lines, route)
    x, y = xy(ex)
    assert 100 <= x <= 140 and abs(y - 24) < 8                            # the east end of the middle row
    assert pl.exit_of(lines, [ll(0, 24), ll(60, 24)]) is None             # a route that stays in the lot has no exit


def test_a_small_lot_gives_the_furthest_spawn_it_has_and_tc0_stays_inside():
    lines = [[ll(0, 0), ll(40, 0)], [ll(40, 0), ll(40, 30), ll(0, 30)], [ll(0, 30), ll(0, 0)]]        # a ring, ~140 m round
    plan = pl.spawn_plan(lines, ll(40, 0))
    assert plan and pl.to_lines(plan["spawn"], lines) < 1.0
    assert 20 < joins.length(plan["path"]) <= pl.SPAWN_MAX_M
    lead, rest = legplan.split_lead([tuple(p) for p in plan["path"]] + [ll(80, 0), ll(200, 0)], plan)
    assert all(pl.to_lines(p, lines) < 2.0 for p in lead)
    assert joins.length(lead) <= pl.TC0_SHARE * joins.length(plan["path"]) + 0.5
