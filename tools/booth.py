#!/usr/bin/env python3
"""The service booth where the car starts (GitHub #57): a gazebo beside the car in the parking lot, a table, chairs and tools
under it, cones and flags round it, and the gazebos of other teams in a row along the aisle.

    python tools/booth.py editions/svolte-di-popoli        # (re)make the booth of an edition's geometry, without routing again

The stock rally loops dress a service park with rows of 3 x 6 m gazebos (the BLASTR, NGRC, Rotopad and dark blue covers over
one frame, as the prefabs of the game's art/prefabs/p_rally_gazebo_mod_*_3x6 stand them), folding tables and chairs, tool
boxes, fire extinguishers, cones and flags. The player's screenshot of a stock service park (GitHub #57) shows the car at its
spot beside a gazebo with a table under it and the first time control a little way ahead. This makes that in the lot the loop
starts in, from what the lot's map data says:

  * the car stays on its aisle, facing the way out with TC0 ahead (GitHub #55), so the drive from the spawn to TC0 and on out of
    the lot is a plain drive along the aisles and nothing stands on it. Where along the aisles it starts is chosen for the booth:
    parkinglot.spawn_plan takes, among the spots that start toward the exit, one with room beside it for the booth (room() below
    counts the gazebos that fit), the furthest back of the best; the lot's entrance, where the car used to start, is often just
    outside the parking polygons;
  * the booth's own gazebo stands beside the car, along the aisle, on the side of it where it fits: all of it inside the lot (the
    parking polygons of the map, and the aisles), not in a building, and clear of every aisle and of the drive by CLEAR_M. The
    other side of the car is tried if that one does not fit, and the gazebo may be moved a little along the aisle;
  * gazebos of other teams follow it along the aisle behind the car (and one ahead), each only where it fits;
  * the table, chairs, tool boxes, extinguisher, cones and flags go in and round the gazebos, each only where it fits too;
  * nothing stands on a road other than the lot's aisles either (GitHub #59: the player found a table on the road leading out of the lot):
    the roads round the lot (Overture's road segments, written as servicePark.roads) are kept clear by their half width and ROAD_MARGIN_M,
    as the game's runtime check (mod/beamng_dakar/lua/ge/extensions/beamngDakar/roadcheck.lua) does for the roads it knows at play time.

The booth is written into the first leg's geometry file as servicePark.booth: a list of { kind, lat, lon, h, bearing } (and
`variant` for a gazebo), bearing the way the model's front (+y) points, in degrees clockwise from north. A gazebo is its cover,
frame and frame add-on in the game (props.lua puts them together), with its long side along the model's +x: the front points to the
left of the car's way, so the long side runs along it. The lot's parking polygons go with it as servicePark.area and the buildings
near it as servicePark.buildings (a list of polygons, a polygon a list of rings, a ring a list of [lat, lon]), so that what was
checked can be checked again.

Pure but for the command line (which reads the Overture map with DuckDB through tools/parkinglot.py and the heights through
tools/fetch_route.py).
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import joins  # noqa: E402
import parkinglot  # noqa: E402

R = 6371008.8
CLEAR_M = 2.6          # a prop stands this far from every aisle and from the drive: half the aisle's width and a little
AISLE_HALF_M = 3.5     # (the aisle's own ground, which is the lot where the map has no polygon there)
FALLBACK_M = 9.0       # without parking polygons the lot is this far round its aisles (a bay of 5 m and half an aisle)
GAZEBO_W, GAZEBO_L = 3.0, 6.0
PITCH_M = 6.3          # the gazebos of a row, end to end with a little between them
LATERAL_MIN_M = CLEAR_M + GAZEBO_W / 2
LATERAL_MAX_M = 8.0
LATERAL_STEP_M = 0.25
SHIFTS_M = (0.0, -1.5, 1.5, -3.0, 3.0)   # the own gazebo's centre beside the car, or this far along the aisle from it
BEHIND = (-1, -2, -3, -4)                  # the gazebos of other teams behind the car ...
AHEAD = (1,)                               # ... and ahead of it (TC0 is 17-20 m ahead: one only)
TC0_BEND_M = 12.0      # TC0 is at least this far before the end of the drive through the lot (see room)
TC0_CLEAR_M = 8.0     # nothing of the booth is nearer than this to TC0, whose own staging area stands there
FACING_M = 6.0         # the car's way is the chord of its first FACING_M metres (legs.loopPlacement faces it the same way)
DRIVE_M = 150.0        # the drive that is kept clear: the lead and this much of the leg after TC0
GRID_M = 1.0           # a footprint is checked every this many metres
ROAD_MARGIN_M = 0.75   # a prop stands this far outside the half width of a road that is not an aisle (GitHub #59; the runtime check's margin)

# what stands in a gazebo, as (kind, along the aisle u, across from the gazebo's middle w, the way its front points); w grows away
# from the aisle; the front: 'd' along the car's way, 'out' away from the aisle, 'in' toward it, 'b' against the car's way
OWN = [
    ("table", -0.65, 0.75, "d"), ("table", 0.65, 0.75, "d"),
    ("chair", -0.7, -0.1, "out"), ("chair", 0.7, -0.1, "out"),
    ("toolbox_big", -2.3, 1.0, "in"), ("toolbox_small", 2.3, 1.0, "in"),
    ("extinguisher", 2.7, -1.2, "in"),
    ("cone", -3.7, -1.2, "d"), ("cone", 3.7, -1.2, "d"), ("cone", -3.7, 1.2, "d"), ("cone", 3.7, 1.2, "d"),
    ("flag", 3.5, 1.9, "in"), ("flag_tear", -3.5, 1.9, "in"),
]
TEAM_A = [("table", -0.6, 0.8, "d"), ("table", 0.7, 0.8, "d"), ("chair", -0.6, -0.1, "out"), ("chair", 0.7, -0.1, "out"),
          ("flag_tear", 3.4, 1.9, "in")]
TEAM_B = [("table", -0.6, 0.8, "d"), ("tire_rack", 1.7, 1.0, "in"), ("toolbox_small", -2.1, 1.0, "in"), ("chair", -0.6, -0.1, "out"),
          ("flag", 3.4, 1.9, "in")]
VARIANTS = ("blastr", "dkblue", "ngrc", "rotopad")   # the covers: the own gazebo is BLASTR's, the rest of the row takes the others


def to_xy(p, o):
    return (math.radians(p[1] - o[1]) * R * math.cos(math.radians(o[0])), math.radians(p[0] - o[0]) * R)


def to_ll(x, y, o):
    return (o[0] + math.degrees(y / R), o[1] + math.degrees(x / (R * math.cos(math.radians(o[0])))))


def unit(x, y):
    n = math.hypot(x, y)
    return (x / n, y / n) if n > 1e-9 else (0.0, 1.0)


def bearing_of(v):
    return math.degrees(math.atan2(v[0], v[1])) % 360.0


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 < 1e-9 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def line_dist(p, line):
    return min((seg_dist(p, a, b) for a, b in zip(line, line[1:])), default=float("inf"))


def in_ring(p, ring):
    hit = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]:
            hit = not hit
    return hit


def in_polygon(p, poly):
    return in_ring(p, poly[0]) and not any(in_ring(p, h) for h in poly[1:])


class Lot:
    """The lot in metres round `origin` (lat, lon): its aisles, the parking polygons and the buildings, the drive to keep clear."""

    def __init__(self, origin, lines, area, buildings, drive, tc0, roads=None):
        self.o = origin
        # (the roads that are not aisles, as (half width, line in metres): the game draws them)
        self.roads = [(parkinglot.road_half(r.get("class")), [to_xy(p, origin) for p in r["line"]]) for r in (roads or [])
                      if len(r["line"]) >= 2]
        self.lines = [[to_xy(p, origin) for p in l] for l in lines if len(l) >= 2]
        self.area = [[[to_xy(p, origin) for p in ring] for ring in poly] for poly in (area or [])]
        self.buildings = [[[to_xy(p, origin) for p in ring] for ring in poly] for poly in (buildings or [])]
        self.drive = [to_xy(p, origin) for p in drive]
        self.tc0 = to_xy(tc0, origin)

    def aisle_dist(self, p):
        return min(line_dist(p, l) for l in self.lines + [self.drive])

    def road_gap(self, p):
        """How far p is outside the nearest road's half width plus the margin (negative: on a road); inf when there is none."""
        return min((line_dist(p, l) - half - ROAD_MARGIN_M for half, l in self.roads), default=float("inf"))

    def in_lot(self, p):
        if any(in_polygon(p, poly) for poly in self.area):
            return True
        return min(line_dist(p, l) for l in self.lines) <= (AISLE_HALF_M if self.area else FALLBACK_M)

    def ok_point(self, p):
        """Can something stand at p (metres): in the lot, not in a building, clear of the aisles and the drive, away from TC0?"""
        return (self.in_lot(p) and not any(in_polygon(p, b) for b in self.buildings) and self.aisle_dist(p) >= CLEAR_M
                and self.road_gap(p) >= 0.0 and math.hypot(p[0] - self.tc0[0], p[1] - self.tc0[1]) >= TC0_CLEAR_M)


def footprint(c, d, length=GAZEBO_L, width=GAZEBO_W, step=GRID_M):
    """Points over the rectangle centred at c, long side along the unit direction d (a grid and its corners and edge middles)."""
    r = (d[1], -d[0])
    nl, nw = int(math.ceil(length / step)), int(math.ceil(width / step))
    pts = []
    for i in range(nl + 1):
        for j in range(nw + 1):
            a, b = -length / 2 + length * i / nl, -width / 2 + width * j / nw
            pts.append((c[0] + a * d[0] + b * r[0], c[1] + a * d[1] + b * r[1]))
    return pts


def car_way(lead_xy):
    """The unit direction the car faces: the chord of the first FACING_M metres of the lead."""
    q = lead_xy[-1]
    for p in lead_xy[1:]:
        if math.hypot(p[0] - lead_xy[0][0], p[1] - lead_xy[0][1]) >= FACING_M:
            q = p
            break
    return unit(q[0] - lead_xy[0][0], q[1] - lead_xy[0][1])


FACES = {"d": (1, 0), "b": (-1, 0), "out": (0, 1), "in": (0, -1)}   # the front as (along, across); across grows away from the aisle


def plan(origin, lead, route, lines, area=None, buildings=None, roads=None):
    """The booth of a lot: a list of { kind, [variant], lat, lon, bearing } (the own gazebo first), or [] when no gazebo of it fits.
    origin: the car's spot (lat, lon); lead: from it to TC0; route: the leg after TC0 (only the first DRIVE_M of it is kept clear);
    lines: the lot's aisles; area: its parking polygons ([[ring, ...], ...], a ring a list of (lat, lon)); buildings likewise; roads: the
    other roads round it ([{ class, line }], a line a list of (lat, lon)), which nothing stands on."""
    c = joins.cumulative(route)
    k = next((i for i in range(len(route)) if c[i] > DRIVE_M), len(route))
    lot = Lot(origin, lines, area, buildings, [tuple(p[:2]) for p in lead] + [tuple(p[:2]) for p in route[:k]], lead[-1], roads)
    lead_xy = [to_xy(p, origin) for p in lead]
    d = car_way(lead_xy)
    r = (d[1], -d[0])
    best = None
    for side in (1, -1):                                     # (1: the right of the car's way)
        o = (r[0] * side, r[1] * side)                       # away from the aisle, toward the booth

        def at(u, w, off, o=o):
            return (u * d[0] + (off + w) * o[0], u * d[1] + (off + w) * o[1])

        def fits(u, off):
            ctr = at(u, 0.0, off)
            return all(lot.ok_point(p) for p in footprint(ctr, d))
        found = None
        for shift in SHIFTS_M:
            off = LATERAL_MIN_M
            while off <= LATERAL_MAX_M + 1e-9:
                if fits(shift, off):
                    found = (shift, off)
                    break
                off += LATERAL_STEP_M
            if found:
                break
        if not found:
            continue
        u0, off = found
        row = [(0, u0)]
        for ks in (BEHIND, AHEAD):
            for kk in ks:
                u = u0 + kk * PITCH_M
                if not fits(u, off):
                    break                                    # (a row: the first one that does not fit ends it on that side)
                row.append((kk, u))
        if best is None or len(row) > len(best[3]):
            best = (side, off, o, row, at)
    if best is None:
        return []
    side, off, o, row, at = best
    items = []

    def add(kind, p, front, variant=None):
        lat, lon = to_ll(p[0], p[1], origin)
        it = {"kind": kind}
        if variant:
            it["variant"] = variant
        it.update({"lat": lat, "lon": lon, "bearing": bearing_of(front)})
        items.append(it)

    def dir_of(face):
        a, b = FACES[face]
        return (a * d[0] + b * o[0], a * d[1] + b * o[1])

    for n, (kk, u) in enumerate(sorted(row, key=lambda t: (t[0] != 0, abs(t[0]), t[0]))):
        add("gazebo", at(u, 0.0, off), (-r[0], -r[1]), VARIANTS[0] if n == 0 else VARIANTS[1 + (n - 1) % (len(VARIANTS) - 1)])
        for kind, du, w, face in (OWN if n == 0 else (TEAM_A if n % 2 else TEAM_B)):
            p = at(u + du, w, off)
            if lot.ok_point(p):
                add(kind, p, dir_of(face))
    return items


def room(lines, area, buildings, roads=None):
    """A function for parkinglot.spawn_plan: the number of gazebos of the booth that fit beside a car that starts a path (the
    drive along the aisles to the exit, from the spot the car would start at), with TC0 TC0_AHEAD_M along it."""
    def count(path):
        # (a spot whose TC0 would stand within TC0_BEND_M of the end of the drive through the lot, where the route turns out of
        # it, is no spot: the plane of TC0 would face the turn and not the way the car arrives)
        total = joins.length(path)
        if total - min(parkinglot.TC0_AHEAD_M, parkinglot.TC0_SHARE * total) < TC0_BEND_M:
            return 0
        c = joins.cumulative(path)
        k = next((i for i in range(len(path)) if c[i] >= parkinglot.TC0_AHEAD_M), len(path) - 1)
        lead = [tuple(p) for p in path[:k]] + [joins.point_at(path, parkinglot.TC0_AHEAD_M, c)]
        if len(lead) < 2:
            return 0
        return sum(1 for it in plan(path[0], lead, path, lines, area, buildings, roads) if it["kind"] == "gazebo")
    return count


KEEP_BUILDINGS_M = 120.0   # the buildings written with the geometry: those this near the car's spot
KEEP_ROADS_M = 150.0       # likewise the roads (a road with a vertex this near the car's spot is kept whole)


def rounded(poly):
    return [[[round(a, 6), round(b, 6)] for a, b in ring] for ring in poly]


def record(origin, lead, route, lines, area, buildings, height, roads=None):
    """What goes into servicePark for the booth: { booth, area, buildings, roads } (see the module's text). height(lat, lon) is the
    ground's height there in metres."""
    items = plan(origin, lead, route, lines, area, buildings, roads)
    for it in items:
        h = round(float(height(it["lat"], it["lon"])), 2)
        it["lat"], it["lon"], it["bearing"] = round(it["lat"], 7), round(it["lon"], 7), round(it["bearing"], 1)
        it["h"] = h
    near = [b for b in (buildings or []) if any(joins.metres(origin, p) <= KEEP_BUILDINGS_M for p in b[0])]
    near_roads = [{"class": r["class"], "line": [[round(a, 6), round(b, 6)] for a, b in r["line"]]} for r in (roads or [])
                  if any(joins.metres(origin, p) <= KEEP_ROADS_M for p in r["line"])]
    return {"booth": items, "area": [rounded(p) for p in (area or [])], "buildings": [rounded(b) for b in near], "roads": near_roads}


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    import fetch_route
    ed = sys.argv[1]
    first = json.load(open(os.path.join(ed, "legs.json"), encoding="utf-8"))["legs"][0]["id"]
    path = os.path.join(ed, "geometry", first + ".json")
    g = json.load(open(path, encoding="utf-8"))
    lot = g["servicePark"]
    origin = (lot["spawn"]["lat"], lot["spawn"]["lon"])
    lines = [[tuple(p) for p in l] for l in lot["lines"]]
    parking, built = parkinglot.surroundings(origin[0], origin[1])
    around = parkinglot.roads(origin[0], origin[1])
    got = record(origin, [tuple(p) for p in lot["lead"]], [tuple(p) for p in g["points"]], lines,
                 parkinglot.lot_area(parking, lines), built, fetch_route.height, around)
    lot.update(got)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(g, f, separators=(",", ":"))
    kinds = {}
    for it in got["booth"]:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    print("%s: booth of %d props %s" % (first, len(got["booth"]), kinds))


if __name__ == "__main__":
    main()
