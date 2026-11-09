"""Where a parking lot is near a point, guessed from map data, for a service park that starts a loop in one.

Overpass (OpenStreetMap's query API) is not always reachable, so this reads Overture Maps' transportation segments
(which come from OpenStreetMap) straight from the public S3 bucket with DuckDB: the segments whose subclass is
'parking_aisle', the drivable aisles inside a lot. Aisles that touch or nearly touch form a lot; the lot is picked by
how much aisle it has and how near it is, and the point returned is the aisle vertex nearest to the lot's middle, so
it lies on drivable ground inside the lot. Not exact: a guess, as asked.

The lot's extent comes from the same release: the parking polygons of its `base` theme (class 'parking', OpenStreetMap's
amenity=parking areas) that touch the aisles, and the buildings near it, for the service booth that stands in the lot
(tools/booth.py, GitHub #57).

Needs duckdb (server/requirements.txt of BeamNG.Earth has it) and network access to overturemaps-us-west-2.s3.amazonaws.com.
"""
import math
import os
import struct

RELEASE = os.environ.get("OVERTURE_RELEASE", "2026-10-21.0")
SEGMENTS = "s3://overturemaps-us-west-2/release/%s/theme=transportation/type=segment/*"
INFRASTRUCTURE = "s3://overturemaps-us-west-2/release/%s/theme=base/type=infrastructure/*"
BUILDINGS = "s3://overturemaps-us-west-2/release/%s/theme=buildings/type=building/*"
JOIN_M = 8.0           # aisles whose vertices come this close belong to the same lot (they share their ends)
MIN_LENGTH_M = 80.0    # a lot has at least this much aisle (a few rows of bays)
NEAR_M = 400.0         # the score halves for every NEAR_M away from the wanted point
R = 6371008.8


def metres(a, b):
    """Distance in metres between (lat, lon) pairs (equirectangular: fine at these sizes)."""
    dn = math.radians(b[0] - a[0]) * R
    de = math.radians(b[1] - a[1]) * R * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dn, de)


def decode_linestring(wkb):
    """[(lat, lon), ...] of a WKB LineString (little or big endian); [] for anything else."""
    wkb = bytes(wkb)
    end = "<" if wkb[0] == 1 else ">"
    kind = struct.unpack(end + "I", wkb[1:5])[0] % 1000
    if kind != 2:
        return []
    n = struct.unpack(end + "I", wkb[5:9])[0]
    out = []
    for i in range(n):
        lon, lat = struct.unpack(end + "dd", wkb[9 + 16 * i:25 + 16 * i])
        out.append((lat, lon))
    return out


def decode_polygons(wkb):
    """The polygons of a WKB Polygon or MultiPolygon (little or big endian): [[ring, ...], ...], a ring a list of (lat, lon), the
    first ring of a polygon its outline and any others holes in it; [] for anything else."""
    wkb = bytes(wkb)

    def polygon(at):
        end = "<" if wkb[at] == 1 else ">"
        n = struct.unpack(end + "I", wkb[at + 5:at + 9])[0]
        at += 9
        rings = []
        for _ in range(n):
            m = struct.unpack(end + "I", wkb[at:at + 4])[0]
            at += 4
            rings.append([(struct.unpack(end + "d", wkb[at + 16 * i + 8:at + 16 * i + 16])[0],
                           struct.unpack(end + "d", wkb[at + 16 * i:at + 16 * i + 8])[0]) for i in range(m)])
            at += 16 * m
        return rings, at

    end = "<" if wkb[0] == 1 else ">"
    kind = struct.unpack(end + "I", wkb[1:5])[0] % 1000
    if kind == 3:
        return [polygon(0)[0]]
    if kind != 6:
        return []
    out, at = [], 9
    for _ in range(struct.unpack(end + "I", wkb[5:9])[0]):
        rings, at = polygon(at)
        out.append(rings)
    return out


def length(line):
    return sum(metres(a, b) for a, b in zip(line, line[1:]))


def lots(lines):
    """The aisles grouped into lots: a list of lists of lines (union of the lines whose vertices are within JOIN_M)."""
    parent = list(range(len(lines)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            if find(i) == find(j):
                continue
            if any(metres(p, q) <= JOIN_M for p in lines[i] for q in lines[j]):
                parent[find(i)] = find(j)
    groups = {}
    for i, line in enumerate(lines):
        groups.setdefault(find(i), []).append(line)
    return list(groups.values())


def pick_lot(lines, lat, lon):
    """The best lot near (lat, lon) and its aisles: ({ lat, lon, aisles, lengthM, distM }, [line, ...]) or None when no lot
    has enough aisle."""
    best = None
    for group in lots([l for l in lines if len(l) >= 2]):
        total = sum(length(l) for l in group)
        if total < MIN_LENGTH_M:
            continue
        verts = [p for l in group for p in l]
        c = (sum(p[0] for p in verts) / len(verts), sum(p[1] for p in verts) / len(verts))
        spot = min(verts, key=lambda p: metres(p, c))                # (on an aisle, next to the middle)
        dist = min(metres((lat, lon), p) for p in verts)
        score = total / (1.0 + dist / NEAR_M)
        if best is None or score > best[0]:
            best = (score, {"lat": round(spot[0], 7), "lon": round(spot[1], 7), "aisles": len(group),
                            "lengthM": round(total), "distM": round(metres((lat, lon), spot))}, group)
    return (best[1], best[2]) if best else None


def pick(lines, lat, lon):
    """The best lot near (lat, lon): { lat, lon, aisles, lengthM, distM } or None when no lot has enough aisle."""
    got = pick_lot(lines, lat, lon)
    return got[0] if got else None


def aisles(lat, lon, radius_m):
    """The parking aisles within radius_m of (lat, lon) as lists of (lat, lon), read from Overture with DuckDB."""
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
    rows = con.execute(
        "SELECT ST_AsWKB(geometry) FROM read_parquet(?, hive_partitioning=1) WHERE subclass = 'parking_aisle' "
        "AND bbox.xmin > ? AND bbox.xmax < ? AND bbox.ymin > ? AND bbox.ymax < ?",
        [SEGMENTS % RELEASE, lon - dlon, lon + dlon, lat - dlat, lat + dlat]).fetchall()
    return [decode_linestring(r[0]) for r in rows]


def find(lat, lon, radius_m=1500.0):
    """The best parking lot within radius_m of (lat, lon) (see pick), or None (none found, or no data)."""
    return pick(aisles(lat, lon, radius_m), lat, lon)


AREA_NEAR_M = 8.0      # a parking polygon with a vertex this close to an aisle belongs to its lot
AREA_RADIUS_M = 250.0  # the polygons and buildings are read this far round the lot's spot


def surroundings(lat, lon, radius_m=AREA_RADIUS_M):
    """(parking polygons, buildings) within radius_m of (lat, lon): two lists of polygons, a polygon a list of rings, a ring a
    list of (lat, lon) (decode_polygons), read from Overture with DuckDB."""
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
    box = [lon - dlon, lon + dlon, lat - dlat, lat + dlat]
    where = "bbox.xmax > ? AND bbox.xmin < ? AND bbox.ymax > ? AND bbox.ymin < ?"
    park = con.execute("SELECT ST_AsWKB(geometry) FROM read_parquet(?, hive_partitioning=1) WHERE class = 'parking' AND " + where,
                       [INFRASTRUCTURE % RELEASE] + box).fetchall()
    built = con.execute("SELECT ST_AsWKB(geometry) FROM read_parquet(?, hive_partitioning=1) WHERE " + where,
                        [BUILDINGS % RELEASE] + box).fetchall()
    return ([p for r in park for p in decode_polygons(r[0])], [p for r in built for p in decode_polygons(r[0])])


# The other roads round a lot (GitHub #59). The game draws them (BeamNG.Earth's roads) and a prop on one is scenery the car drives
# through: the booth (tools/booth.py) is kept off them as it is off the aisles. The roads are the classes and subclasses Earth's
# navgraph keeps (the drivable ones: its server/navgraph.py DRIVE and SKIP_SUBCLASSES, so the offline check and the runtime check agree
# about what a road is), with the half width each is drawn with, by guess.
ROADS_RADIUS_M = 250.0
ROAD_HALF_M = {"motorway": 6.0, "trunk": 5.5, "primary": 4.5, "secondary": 4.0, "tertiary": 3.5, "residential": 3.0,
               "unclassified": 3.0, "living_street": 3.0, "service": 2.5, "track": 2.0, "unknown": 3.0}
ROAD_SKIP_SUBCLASSES = {"driveway", "parking_aisle", "sidewalk", "crosswalk", "cycle_crossing"}
ROAD_HALF_DEFAULT_M = 3.0


def road_half(cls):
    """The half width in metres a road of this Overture class is drawn with, by guess."""
    return ROAD_HALF_M.get(cls, ROAD_HALF_DEFAULT_M)


def roads(lat, lon, radius_m=ROADS_RADIUS_M):
    """The drivable roads within radius_m of (lat, lon) that are not the lot's parking aisles or driveways: [{ class, line }], a line a
    list of (lat, lon), read from Overture with DuckDB (the transportation segments of subtype 'road')."""
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
    rows = con.execute(
        "SELECT class, subclass, ST_AsWKB(geometry) FROM read_parquet(?, hive_partitioning=1) WHERE subtype = 'road' AND "
        "bbox.xmax > ? AND bbox.xmin < ? AND bbox.ymax > ? AND bbox.ymin < ?",
        [SEGMENTS % RELEASE, lon - dlon, lon + dlon, lat - dlat, lat + dlat]).fetchall()
    out = []
    for cls, sub, wkb in rows:
        if sub in ROAD_SKIP_SUBCLASSES or (cls or "unknown") not in ROAD_HALF_M:
            continue
        line = decode_linestring(wkb)
        if len(line) >= 2:
            out.append({"class": cls or "unknown", "line": line})
    return out


def lot_area(polygons, lines):
    """The parking polygons that belong to the lot of `lines`: those with a vertex within AREA_NEAR_M of an aisle, or with an aisle
    vertex inside them."""
    out = []
    for poly in polygons:
        ring = poly[0]
        if any(to_lines(p, lines) <= AREA_NEAR_M for p in ring) or any(inside(v, ring) for l in lines for v in l):
            out.append(poly)
    return out


def inside(p, ring):
    """Is the (lat, lon) point inside the ring (ray casting)?"""
    x, y, hit = p[1], p[0], False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if (a[0] > y) != (b[0] > y) and x < (b[1] - a[1]) * (y - a[0]) / (b[0] - a[0]) + a[1]:
            hit = not hit
    return hit


def find_lot(lat, lon, radius_m=1500.0):
    """Like find, with the lot's aisles: (info, [line, ...]) or None. The info also carries `_area` (the lot's parking polygons)
    and `_buildings` (those near it) and `_roads` (the roads round it, GitHub #59) where the lookup worked: keys with an underscore are not written to the geometry as they are
    (tools/booth.py makes the booth from them)."""
    got = pick_lot(aisles(lat, lon, radius_m), lat, lon)
    if got:
        info = dict(got[0])
        try:
            parking, built = surroundings(info["lat"], info["lon"])
            info["_area"], info["_buildings"] = lot_area(parking, got[1]), built
        except Exception as e:                  # (the booth then takes the aisles as the lot: a booth beside the aisle)
            print("warning: no parking polygons for the lot at %.5f, %.5f: %s: %s" % (info["lat"], info["lon"], type(e).__name__, e))
        try:
            info["_roads"] = roads(info["lat"], info["lon"])
        except Exception as e:                  # (the booth is then kept off the aisles and the drive only)
            print("warning: no roads for the lot at %.5f, %.5f: %s: %s" % (info["lat"], info["lon"], type(e).__name__, e))
        return info, got[1]
    return None


# ---------------------------------------------------------------------------------------------------------------------
# Where the car starts in the lot (GitHub #55): on an aisle, inside the lot, driving toward the exit.
#
# The route out of the lot comes from the router (it leaves the aisles somewhere: the exit). The car is put on the lot's own
# aisles further back from that exit, so that the first metres of the loop are a drive through the lot toward the exit, and
# time control 0 (TC0) stands a few metres ahead of the car, still inside the lot. Nothing is behind the car that it has to
# come back to, and nothing ahead of it that it has to turn round for.

EXIT_M = 12.0          # a route point this far from every aisle has left the lot
ON_AISLE_M = 6.0       # one this close to an aisle is on it (the router's road and the map's aisle differ by a few metres)
CONNECT_M = 8.0        # aisle vertices of different aisles this close are joined (the lot's own tolerance, JOIN_M)
DENSE_M = 5.0          # the aisles are looked at every this many metres
SPAWN_MIN_M = 25.0     # the car starts this far from the exit along the aisles, at least (when the lot has that much) ...
SPAWN_MAX_M = 90.0     # ... and at most
SPAWN_FACING_DEG = 60.0  # the car starts toward the exit: its first 10 m are within this of the straight way to the exit
TC0_AHEAD_M = 20.0     # TC0 stands this far ahead of the car (the stock loops park the car 65-73 m before it; on TC0 itself
                       # the loop timed the player at once, GitHub #27) ...
TC0_SHARE = 0.6        # ... and at most this share of the way from the car to the exit


def bearing(a, b):
    """Degrees clockwise from north of the way from (lat, lon) a to b."""
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def seg_metres(p, a, b):
    """Distance in metres from p to the segment a-b (all (lat, lon))."""
    k = math.cos(math.radians(p[0]))
    ax, ay = math.radians(a[1] - p[1]) * R * k, math.radians(a[0] - p[0]) * R
    bx, by = math.radians(b[1] - p[1]) * R * k, math.radians(b[0] - p[0]) * R
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 < 1e-9 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    return math.hypot(ax + t * dx, ay + t * dy)


def to_lines(p, lines):
    """Distance in metres from p to the nearest aisle."""
    return min((seg_metres(p, a, b) for l in lines for a, b in zip(l, l[1:])), default=float("inf"))


def densify(line, step=DENSE_M):
    """The line with a vertex at least every `step` metres (the originals kept)."""
    out = [tuple(line[0])]
    for a, b in zip(line, line[1:]):
        n = max(1, int(math.ceil(metres(a, b) / step)))
        for i in range(1, n + 1):
            f = i / n
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return out


def exit_of(lines, route):
    """Where the route leaves the lot: its last point on an aisle (within ON_AISLE_M) before the first point that is further
    than EXIT_M from every aisle. None when the route never leaves the lot, or never was on an aisle."""
    last = None
    for p in route:
        d = to_lines(p, lines)
        if d > EXIT_M:
            return last
        if d <= ON_AISLE_M:
            last = p
    return None


def aisle_graph(lines):
    """(nodes, edges): the aisles' vertices (one every DENSE_M) and { node: [(node, metres)] } along and between aisles."""
    nodes, edges, owner = [], {}, []
    for k, line in enumerate(lines):
        prev = None
        for p in densify(line):
            nodes.append(p)
            owner.append(k)
            i = len(nodes) - 1
            edges[i] = []
            if prev is not None:
                d = metres(nodes[prev], p)
                edges[prev].append((i, d))
                edges[i].append((prev, d))
            prev = i
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if owner[i] != owner[j]:
                d = metres(nodes[i], nodes[j])
                if d <= CONNECT_M:
                    edges[i].append((j, d))
                    edges[j].append((i, d))
    return nodes, edges


BOOTH_CAP = 4          # a spot with room for this many gazebos of the service booth (tools/booth.py) is as good as any with more


def spawn_plan(lines, exit_at, booth=None):
    """The car's start in a lot whose exit is at `exit_at` (lat, lon): { path, exit, spawn }. path is the drive from the
    spawn along the aisles to the aisle vertex nearest the exit ((lat, lon) pairs, a vertex every DENSE_M or so), spawn its
    first point and exit its last. None when the lot has no aisle.

    booth (optional): a function of such a path giving how many gazebos of the service booth fit beside the car at its start
    (tools/booth.py, GitHub #57). Among the spots that start toward the exit the one with room for the most (up to BOOTH_CAP)
    is taken, the furthest back of those; where none has room the choice is as it was."""
    import heapq
    nodes, edges = aisle_graph([l for l in lines if len(l) >= 2])
    if not nodes:
        return None
    x = min(range(len(nodes)), key=lambda i: metres(nodes[i], exit_at))
    dist, prev, heap = {x: 0.0}, {}, [(0.0, x)]
    while heap:
        d, i = heapq.heappop(heap)
        if d > dist.get(i, float("inf")):
            continue
        for j, w in edges[i]:
            if d + w < dist.get(j, float("inf")):
                dist[j], prev[j] = d + w, i
                heapq.heappush(heap, (d + w, j))
    def facing(i):
        """How far (degrees) the first 10 m of the way from node i to the exit point away from the exit's direction."""
        j = i
        for _ in range(2):
            j = prev.get(j, x)
        if metres(nodes[i], nodes[x]) < 3.0 or metres(nodes[i], nodes[j]) < 3.0:
            return 0.0
        return abs((bearing(nodes[i], nodes[j]) - bearing(nodes[i], nodes[x]) + 180.0) % 360.0 - 180.0)

    reach = [i for i in dist if i != x and dist[i] <= SPAWN_MAX_M]
    ok = [i for i in reach if dist[i] >= SPAWN_MIN_M]
    # the furthest back that starts toward the exit; failing that the one that starts most toward it; in a small lot the
    # furthest it has
    pool = [i for i in ok if facing(i) <= SPAWN_FACING_DEG]

    def walk(i):
        out = [nodes[i]]
        while i != x:
            i = prev[i]
            out.append(nodes[i])
        return out
    if pool and booth:
        room = {i: min(BOOTH_CAP, booth(walk(i))) for i in pool}
        if max(room.values()) > 0:
            pool = [i for i in pool if room[i] == max(room.values())]
    if pool:
        s = max(pool, key=lambda i: dist[i])
    elif ok:
        s = min(ok, key=facing)
    else:
        near = [i for i in reach if facing(i) <= SPAWN_FACING_DEG] or reach
        s = max(near, key=lambda i: dist[i], default=x)
    path = walk(s)
    return {"path": path, "exit": nodes[x], "spawn": path[0]}
