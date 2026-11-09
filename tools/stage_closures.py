#!/usr/bin/env python3
"""Where the side roads meet a special stage, for closing them off.

    python tools/stage_closures.py editions/testbed-algarve

A special stage runs on public roads: every other drivable road that meets it is a way off the stage, and a real closed
stage has a barrier across each of them. For every leg of type "special" in <edition>/legs.json this reads the road
network near the stage's geometry (geometry/<leg>.json, from tools/fetch_route.py) from Overture Maps' transportation
segments (the public S3 bucket, with DuckDB, like tools/parkinglot.py) and writes <edition>/closures/<leg>.json:

    {"id": ..., "closures": [{"lat": ..., "lon": ..., "h": ..., "bearing": ..., "s": ..., "class": ..., "width": ...}, ...]}

lat/lon/h is where the closure stands, a few metres (OFFSET_M) into the side road; bearing is the side road's direction
there, in degrees clockwise from north, pointing away from the stage (the closure goes across it); s is the metres along
the stage where the side road meets it; width is how many metres across the closure goes (WIDE_M, in 6 m tape lengths:
the stock stages close a side road with one 6 m length of road-closure tape, two side by side for a wide road). h is the ground height from the same terrain source as fetch_route.py.

How a junction is found: each road is sampled every STEP_M and the samples within NEAR_M of the stage line form runs.
A road with all of it near the stage is the stage's own road. A long run (LONG_RUN_M) is a road that runs along the
stage (a parallel lane, the road the stage starts or ends in). A short run is a junction: the road leaves the stage
there, and the part of it that leaves is an arm; a road that crosses the stage without a shared node gives two arms.
The arm is followed OFFSET_M from the stage (through the next segments, a branch at a node giving one closure each)
and the closure stands where its whole width is clear of the stage road. Footways, steps, paths, cycleways, bridleways
and pedestrian streets are not ways off a stage for a car and are left out; so are parking aisles, and driveways that
are only a stub (DRIVEWAY_STUB_M). Everything else is closed, tracks and unpaved lanes included: a missed junction is a
way off the stage, a closure on a doubtful one costs nothing. Roads on another level (bridges, tunnels) are left alone
where they only cross over.

Needs duckdb, Pillow and numpy; network: overturemaps-us-west-2.s3.amazonaws.com and s3.amazonaws.com (read only).
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parkinglot

R = 6371008.8
NOT_FOR_CARS = {"footway", "steps", "path", "cycleway", "bridleway", "pedestrian"}
NOT_WAYS_OFF = {"parking_aisle"}     # (subclass) the aisles of a car park
STEP_M = 3.0                          # roads are looked at every this many metres
NEAR_M = 10.0                         # a road sample this close to the stage line touches it
OWN_M = 4.0                           # a road that is near the stage all along and never further than this is the stage's own
LONG_RUN_M = 60.0                    # a road that stays near the stage longer than this runs along it
OFFSET_M = 10.0                       # the closure stands this far from the stage line, in the side road
TAPE_M = 6.0                          # the stock road-closure tape's length: a narrow side road gets one, a wide one two
WIDE = {"motorway", "trunk", "primary", "secondary", "tertiary"}      # classes of road wide enough for two lanes and a verge
CLEAR_CENTRE_M = 9.0                  # the closure's centre is this far from the stage line at least ...
CLEAR_ENDS_M = 6.5                    # ... and the ends of its row this far (the stage road's width and a margin)
ARM_MAX_M = 80.0                      # an arm is followed this far for a place where the row is clear (an acute fork needs it)
DRIVEWAY_STUB_M = 40.0                # a driveway no longer than this is a stub, not a way off
EDGE_M = 40.0                         # within this of the stage's start or end the road that continues it is not a side road
DEDUPE_M = 6.0                        # two closures this close, facing the same way within DEDUPE_DEG, are one
DEDUPE_DEG = 30.0
BBOX_MARGIN_M = 60.0


class Proj:
    """Metres east/north of an origin (equirectangular: the same for the stage and the roads, so locally exact)."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = math.radians(1.0) * R * math.cos(math.radians(lat0))
        self.ky = math.radians(1.0) * R

    def fwd(self, lat, lon):
        return ((lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky)

    def inv(self, x, y):
        return (self.lat0 + y / self.ky, self.lon0 + x / self.kx)


def densify(pts, step):
    """pts [(x, y)] with no gap longer than step; also the cumulative distance of every returned point."""
    out, cum = [pts[0]], [0.0]
    for a, b in zip(pts, pts[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(d / step)))
        for i in range(1, n + 1):
            f = i / n
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            cum.append(cum[-1] + d / n)
    return out, cum


class Stage:
    """The stage line in metres, with the distance from any point to it (and the metres along it of the nearest point)."""
    CELL = 40.0

    def __init__(self, pts):
        self.pts, self.cum = densify(pts, 10.0)
        self.grid = {}
        for i, p in enumerate(self.pts):
            self.grid.setdefault((int(p[0] // self.CELL), int(p[1] // self.CELL)), []).append(i)
        self.length = self.cum[-1]
        self.box = (min(p[0] for p in self.pts), min(p[1] for p in self.pts),
                    max(p[0] for p in self.pts), max(p[1] for p in self.pts))

    def nearest(self, x, y):
        """(distance, metres along the stage) to the stage line, (inf, None) when it is further than a cell or two."""
        cx, cy = int(x // self.CELL), int(y // self.CELL)
        best, bs = math.inf, None
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for i in self.grid.get((gx, gy), ()):
                    for j in (i - 1, i):                      # the two segments at point i
                        if j < 0 or j + 1 >= len(self.pts):
                            continue
                        ax, ay = self.pts[j]
                        bx, by = self.pts[j + 1]
                        dx, dy = bx - ax, by - ay
                        l2 = dx * dx + dy * dy
                        f = 0.0 if l2 < 1e-12 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / l2))
                        d = math.hypot(x - (ax + f * dx), y - (ay + f * dy))
                        if d < best:
                            best, bs = d, self.cum[j] + f * (self.cum[j + 1] - self.cum[j])
        return best, bs

    def direction(self, s):
        """Unit direction of travel (east, north) at s metres along the stage."""
        k = max(1, min(len(self.pts) - 1, int(round(s / 10.0))))
        a, b = self.pts[k - 1], self.pts[min(len(self.pts) - 1, k)]
        l = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        return ((b[0] - a[0]) / l, (b[1] - a[1]) / l)

    def in_reach(self, bbox, margin):
        """False when a road's bounding box is nowhere near the stage's (a cheap way to skip most roads)."""
        return not (bbox[2] < self.box[0] - margin or bbox[0] > self.box[2] + margin
                    or bbox[3] < self.box[1] - margin or bbox[1] > self.box[3] + margin)


class Road:
    def __init__(self, rec, proj):
        self.cls, self.sub, self.elevated = rec.get("class"), rec.get("subclass"), bool(rec.get("elevated"))
        raw = [proj.fwd(la, lo) for la, lo in rec["line"]]
        self.bbox = (min(p[0] for p in raw), min(p[1] for p in raw), max(p[0] for p in raw), max(p[1] for p in raw))
        self.pts, self.cum = densify(raw, STEP_M)
        self.ends = (self.key(rec["line"][0]), self.key(rec["line"][-1]))

    @staticmethod
    def key(ll):
        """Segments that meet share an end point exactly (an Overture connector); this names that point."""
        return (round(ll[0], 6), round(ll[1], 6))

    @property
    def length(self):
        return self.cum[-1]


def drivable(rec):
    return (rec.get("class") not in NOT_FOR_CARS) and (rec.get("subclass") not in NOT_WAYS_OFF)


def bearing(a, b):
    """Degrees clockwise from north of the direction from a to b (metres east/north)."""
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360.0


def width_of(cls):
    """Metres across the closure of a road of this class (a whole number of tape lengths)."""
    return 2 * TAPE_M if cls in WIDE else TAPE_M


def row_clear(stage, p, brg, half):
    """The closure's row (`half` metres either side of p, across the side road) stays off the stage road."""
    r = math.radians(brg + 90.0)
    ex, ey = math.sin(r) * half, math.cos(r) * half
    return (stage.nearest(p[0], p[1])[0] >= CLEAR_CENTRE_M and stage.nearest(p[0] + ex, p[1] + ey)[0] >= CLEAR_ENDS_M
            and stage.nearest(p[0] - ex, p[1] - ey)[0] >= CLEAR_ENDS_M)


def walk(stage, at_end, road, i0, step, start_arc, s, seen, found, depth=0):
    """Follow the arm of `road` from sample i0 in direction step (+1/-1); the closure goes where the arm is OFFSET_M from
    the stage line and its row is clear (start_arc: the distance already travelled along the arm). At the end of the road the
    arm goes on into the roads that meet there (one closure each branch); a dead end is a stub and gets none."""
    seen = seen | {id(road)}
    travelled = start_arc
    i, last = i0, (len(road.pts) - 1 if step > 0 else 0)
    while True:
        if i == last:
            break
        j = i + step
        travelled += abs(road.cum[j] - road.cum[i])
        i = j
        if stage.nearest(road.pts[i][0], road.pts[i][1])[0] >= OFFSET_M:
            a = road.pts[max(0, min(len(road.pts) - 1, i - 2 * step))]
            b = road.pts[max(0, min(len(road.pts) - 1, i + 2 * step))]
            if a != b:
                brg = bearing(a, b)
                if row_clear(stage, road.pts[i], brg, width_of(road.cls) / 2):
                    found.append({"p": road.pts[i], "bearing": brg, "s": s, "road": road})
                    return
        if travelled > ARM_MAX_M:
            return                                       # (never clear: an arm that hugs the stage, treated as along it)
    if depth >= 4:
        return
    node = road.ends[1] if step > 0 else road.ends[0]
    for other in at_end.get(node, ()):
        if id(other) in seen or other.cls in NOT_FOR_CARS:
            continue
        forward = other.ends[0] == node
        walk(stage, at_end, other, 0 if forward else len(other.pts) - 1, 1 if forward else -1, travelled, s, seen,
             found, depth + 1)


def runs(flags):
    """[(first, last)] index ranges of the consecutive True flags."""
    out, a = [], None
    for i, f in enumerate(flags):
        if f and a is None:
            a = i
        elif not f and a is not None:
            out.append((a, i - 1))
            a = None
    if a is not None:
        out.append((a, len(flags) - 1))
    return out


def is_the_stage_continued(stage, f):
    """The road that goes on straight behind the start (the transfer arrives by it) or beyond the finish (the next leg
    leaves by it) is no side road: a closure there would shut the loop's own road."""
    if f["s"] <= EDGE_M:
        tx, ty = stage.direction(f["s"])
        r = math.radians(f["bearing"])
        return math.sin(r) * tx + math.cos(r) * ty < -0.5
    if f["s"] >= stage.length - EDGE_M:
        tx, ty = stage.direction(f["s"])
        r = math.radians(f["bearing"])
        return math.sin(r) * tx + math.cos(r) * ty > 0.5
    return False


def closures(stage_ll, recs):
    """The closures for a stage (a list of (lat, lon)) among road records ({line: [(lat, lon)], class, subclass,
    elevated}): a list of {lat, lon, bearing, s, class}, in order along the stage."""
    proj = Proj(sum(p[0] for p in stage_ll) / len(stage_ll), sum(p[1] for p in stage_ll) / len(stage_ll))
    stage = Stage([proj.fwd(la, lo) for la, lo in stage_ll])
    roads = [Road(r, proj) for r in recs if len(r["line"]) >= 2 and drivable(r)]
    at_end = {}
    for r in roads:
        for k in r.ends:
            at_end.setdefault(k, []).append(r)
    found = []
    for road in roads:
        if not stage.in_reach(road.bbox, NEAR_M):
            continue
        near, dist, info = [], [], []
        for p in road.pts:
            d, s = stage.nearest(p[0], p[1])
            near.append(d <= NEAR_M)
            dist.append(d)
            info.append(s)
        n = len(road.pts)
        for a, b in runs(near):
            if (b - a) * STEP_M > LONG_RUN_M:
                continue                                   # runs along the stage
            at_start, at_last = a == 0, b == n - 1
            if not (at_start or at_last) and road.elevated:
                continue                                   # crosses over or under it
            arms = []
            if at_start and at_last:
                if max(dist) <= OWN_M:
                    continue                               # the stage's own road
                # a short connector at the stage: the road goes on from its far end (the stage's end of it is the near one)
                far = n - 1 if dist[-1] > dist[0] else 0
                arms.append((far, 1 if far else -1))
                info[far] = info[n - 1 - far]
            else:
                if a > 0:
                    arms.append((a, -1))
                if b < n - 1:
                    arms.append((b, 1))
            for i0, step in arms:
                if (road.sub == "driveway") and road.length <= DRIVEWAY_STUB_M:
                    continue
                walk(stage, at_end, road, i0, step, 0.0, info[i0], set(), found)
    out = []
    for f in sorted(found, key=lambda f: f["s"]):
        if is_the_stage_continued(stage, f):
            continue
        if any(math.hypot(f["p"][0] - o["p"][0], f["p"][1] - o["p"][1]) < DEDUPE_M
               and abs((f["bearing"] - o["bearing"] + 180.0) % 360.0 - 180.0) < DEDUPE_DEG for o in out):
            continue
        out.append(f)
    res = []
    for f in out:
        lat, lon = proj.inv(*f["p"])
        res.append({"lat": round(lat, 7), "lon": round(lon, 7), "bearing": round(f["bearing"], 1), "s": round(f["s"], 1),
                    "class": f["road"].cls, "width": width_of(f["road"].cls)})
    return res


def fetch_roads(stage_ll):
    """The drivable road segments within the stage's bounding box (read from Overture with DuckDB): record dicts."""
    import duckdb
    lats, lons = [p[0] for p in stage_ll], [p[1] for p in stage_ll]
    m = BBOX_MARGIN_M / 111320.0
    s, n = min(lats) - m, max(lats) + m
    w, e = min(lons) - m / math.cos(math.radians(s)), max(lons) + m / math.cos(math.radians(s))
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    rows = con.execute(
        "SELECT ST_AsWKB(geometry), class, subclass, level_rules FROM read_parquet(?, hive_partitioning=1) "
        "WHERE subtype = 'road' AND bbox.xmin < ? AND bbox.xmax > ? AND bbox.ymin < ? AND bbox.ymax > ?",
        [parkinglot.SEGMENTS % parkinglot.RELEASE, e, w, n, s]).fetchall()
    out = []
    for wkb, cls, sub, levels in rows:
        line = parkinglot.decode_linestring(wkb)
        if len(line) >= 2:
            out.append({"line": line, "class": cls, "subclass": sub,
                        "elevated": any((lv or {}).get("value", 0) != 0 for lv in (levels or []))})
    return out


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    ed = sys.argv[1]
    from fetch_route import height
    legs = json.load(open(os.path.join(ed, "legs.json"), encoding="utf-8"))["legs"]
    os.makedirs(os.path.join(ed, "closures"), exist_ok=True)
    for leg in legs:
        if leg["type"] != "special":
            continue
        g = json.load(open(os.path.join(ed, "geometry", leg["id"] + ".json"), encoding="utf-8"))
        stage_ll = [(p[0], p[1]) for p in g["points"]]
        recs = fetch_roads(stage_ll)
        cl = closures(stage_ll, recs)
        for c in cl:
            c["h"] = round(float(height(c["lat"], c["lon"])), 2)
        with open(os.path.join(ed, "closures", leg["id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump({"id": leg["id"], "closures": cl}, f, separators=(",", ":"))
        print("%s: %d road segments read, %d closures along %.1f km" % (leg["id"], len(recs), len(cl), g["lengthM"] / 1000))


if __name__ == "__main__":
    main()
