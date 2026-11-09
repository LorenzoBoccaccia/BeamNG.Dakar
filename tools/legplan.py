"""The legs of an edition as paths that fit together (GitHub #55): what tools/fetch_route.py writes, without the network.

Given each leg's via points and a router (vias -> a path of (lat, lon)), this makes

  * the start of the loop: when the first leg starts in a parking lot, the car's spawn on one of the lot's aisles, the drive
    along the aisles to the lot's exit and out on the route, and TC0 a few metres ahead of the car, inside the lot (the leg
    itself starts at TC0; the metres before it are the `lead`, from the spawn to TC0);
  * every join between a leg and the next, moved along the road until the leg is driven into it from behind and the next
    leg carries on the same way (tools/joins.py).

Pure: the router and the parking lot lookup are passed in (fetch_route.py passes OSRM and Overture).
"""
import math

import booth
import joins
import parkinglot

DEDUPE_M = 0.3


def dedupe(pts):
    out = [tuple(pts[0])]
    for p in pts[1:]:
        if joins.metres(out[-1], p) >= DEDUPE_M:
            out.append(tuple(p))
    if len(out) == 1:
        out.append(tuple(pts[-1]))
    return out


class Leg:
    """One leg while it is being planned: its via points, its path, and the lot plan when it starts in a lot."""

    def __init__(self, cfg, vias, plan=None, lot=None):
        self.cfg, self.vias, self.plan, self.lot, self.pts, self.joinIn = cfg, vias, plan, lot, None, None

    def route(self, route_fn, vias=None):
        vias = vias or self.vias
        if not self.plan:
            return dedupe(route_fn(vias))
        # the lot's own aisles to its exit, then the route from there (the first via point stands for the lot)
        path = [tuple(p) for p in self.plan["path"]]
        tail = route_fn([list(self.plan["exit"])] + list(vias[1:]))
        if joins.metres(path[-1], tail[0]) < 1.0:
            tail = tail[1:]
        return dedupe(path + [tuple(p) for p in tail])


def start_in_lot(leg, route_fn, find_lot, log):
    """(vias, plan, lot info) for a first leg with parkingNear: the lot found, the exit the router takes out of it and the
    car's spawn on the aisles behind it. (the leg's own vias, None, None) when there is no lot or no way out of it."""
    via = [list(v) for v in leg["via"]]
    try:
        found = find_lot(via[0][0], via[0][1], float(leg["parkingNear"]))
    except Exception as e:                           # (no duckdb, no network: the first via point stays the start)
        log("warning: no parking lot lookup for %s: %s: %s" % (leg["id"], type(e).__name__, e))
        found = None
    if not found:
        log("warning: no parking lot found near %s" % (via[0],))
        return via, None, None
    info, group = found
    log("%s: starts in a parking lot at %.5f, %.5f (%d aisles, %d m of aisle, %d m from the first point)" % (
        leg["id"], info["lat"], info["lon"], info["aisles"], info["lengthM"], info["distM"]))
    # the lot takes the first via point's place: routed through both, the leg went to that point and back past the lot,
    # ~1.8 km round the service park before it left (GitHub #29)
    vias = [[info["lat"], info["lon"]]] + via[1:]
    out = route_fn(vias)
    exit_at = parkinglot.exit_of(group, out)
    # (where the lot's map data is known the car starts where the service booth has room beside it, GitHub #57)
    room = booth.room(group, info["_area"], info.get("_buildings")) if "_area" in info else None
    plan = parkinglot.spawn_plan(group, exit_at, room) if exit_at else None
    if not plan:
        log("warning: the route does not leave the lot at %s: the leg starts at its middle" % (vias[0],))
        return vias, None, info
    info = dict(info)
    info["_lines"] = group
    return vias, plan, info


TC_ZONE_M = 10.0         # the loop's stopped-near-plane check (the flowgraph node): a car whose front is within this before a
                         # control's plane, and within this of its middle, is at the control
CAR_FRONT_M = 2.3        # the front of a rally car from its position
ZONE_MARGIN_M = 1.0


def at_control(pts, ahead):
    """Would a car standing at the start of `pts` facing along it be at a control placed `ahead` metres along it? (The control's
    plane faces the way the path goes there; the car's front is CAR_FRONT_M ahead of its position.) That is what timed the player
    at once when the stall was on TC0 (GitHub #27): TC0 is put where the car is not already at it."""
    c = joins.cumulative(pts)
    car = math.radians(joins.heading_between(pts, 0.0, min(6.0, c[-1]), c))
    tc = math.radians(joins.heading_between(pts, ahead, min(ahead + 10.0, c[-1]), c))
    p0, p1 = joins.point_at(pts, 0.0, c), joins.point_at(pts, ahead, c)
    k = math.cos(math.radians(p0[0]))
    to_x, to_y = math.radians(p1[1] - p0[1]) * joins.R * k, math.radians(p1[0] - p0[0]) * joins.R
    fx, fy = math.sin(car) * CAR_FRONT_M, math.cos(car) * CAR_FRONT_M
    nx, ny = math.sin(tc), math.cos(tc)
    rx, ry = fx - to_x, fy - to_y
    along = rx * nx + ry * ny
    signed, lateral = -along, math.hypot(rx - along * nx, ry - along * ny)
    return lateral <= TC_ZONE_M + ZONE_MARGIN_M and -ZONE_MARGIN_M <= signed <= TC_ZONE_M + ZONE_MARGIN_M


def split_lead(pts, plan):
    """(lead, pts): the path up to TC0 (spawn to TC0) and the path from TC0 on. TC0 stands TC0_AHEAD_M ahead of the car along
    the aisles, at most TC0_SHARE of the way to the lot's exit, and a little further (up to the exit) where the car would
    otherwise already be at it."""
    lot_len = joins.length(plan["path"])
    ahead = min(parkinglot.TC0_AHEAD_M, parkinglot.TC0_SHARE * lot_len)
    t = ahead
    while at_control(pts, t) and t < lot_len:
        t += 1.0
    if not at_control(pts, t):
        ahead = t
    c = joins.cumulative(pts)
    k = next((i for i in range(len(pts)) if c[i] >= ahead), len(pts) - 1)
    tc0 = joins.point_at(pts, ahead, c)
    lead = [tuple(p) for p in pts[:k]] + [tc0]
    rest = [tc0] + [tuple(p) for p in pts[k:] if joins.metres(tc0, p) > DEDUPE_M]
    if len(rest) < 2:
        rest = [tc0, tuple(pts[-1])]
    return lead, rest


def plan_edition(legs, route_fn, find_lot=None, log=print):
    """Every leg of an edition planned: a list of { leg, pts, lead, plan, lot, joinIn } in order (lead: the way from the car to
    TC0 for a first leg in a lot, else None; lot: the lot's record or None; joinIn: how the join with the leg before was
    made, or None). `legs`: the edition's legs.json legs."""
    items = []
    for i, cfg in enumerate(legs):
        via, plan, lot = [list(v) for v in cfg["via"]], None, None
        if i == 0 and cfg.get("parkingNear") and find_lot:
            via, plan, lot = start_in_lot(cfg, route_fn, find_lot, log)
        leg = Leg(cfg, via, plan, lot)
        leg.pts = leg.route(route_fn)
        items.append(leg)
    for i in range(len(items) - 1):
        a, b = items[i], items[i + 1]

        def reroute_a(end, a=a):
            return a.route(route_fn, a.vias[:-1] + [[end[0], end[1]]])

        def reroute_b(start, b=b):
            return dedupe(route_fn([[start[0], start[1]]] + b.vias[1:]))
        old_a, old_b = a.pts, b.pts
        pa, pb, info = joins.align(a.pts, b.pts, reroute_a, reroute_b)
        a.pts, b.pts = pa, pb
        if info["mode"] == "extend":
            t = info["shiftM"]
            a.vias = a.vias[:-1] + [[pa[-1][0], pa[-1][1]]]
            b.vias = joins.vias_after(b.vias, old_b, t, pa[-1])
        elif info["mode"] == "trim":
            end_s = joins.length(old_a) - info["shiftM"]
            keep = [v for v in a.vias[:-1] if joins.project_s(old_a, v) < end_s - 5.0]
            a.vias = keep + [[pb[0][0], pb[0][1]]]
            b.vias = [[pb[0][0], pb[0][1]]] + b.vias[1:]
        if info["mode"] == "none":
            log("warning: %s does not run into %s straight (%s); no better join within %d m" % (
                a.cfg["id"], b.cfg["id"], info["report"], joins.SHIFT_MAX_M))
        elif info["mode"] != "kept":
            log("%s -> %s: the join moved %d m (%s): %s" % (a.cfg["id"], b.cfg["id"], info["shiftM"], info["mode"], info["report"]))
        b.joinIn = info
    out = []
    for leg in items:
        lead = None
        pts = leg.pts
        if leg.plan:
            # (the way out: the heading of the route 10 m past the aisle vertex it leaves the lot from)
            s_exit = joins.length(leg.plan["path"])
            leg.plan["exitBearing"] = round(joins.bearing(joins.point_at(pts, s_exit), joins.point_at(pts, s_exit + 10.0)), 1)
            lead, pts = split_lead(pts, leg.plan)
        out.append({"leg": leg.cfg, "pts": pts, "lead": lead, "plan": leg.plan, "lot": leg.lot, "joinIn": leg.joinIn})
    return out
