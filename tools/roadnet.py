"""A road and track network from Overture Maps, and a router over it for stages that run off the tarmac.

OSRM (tools/fetch_route.py's default router) follows roads. A Dakar special runs on pistes: Overture's transportation
segments (which come from OpenStreetMap) carry them as `class` track (and unclassified, service, tertiary ...). This reads them
with DuckDB from the public S3 bucket, per tile of TILE_DEG degrees, caches each tile on disk (CACHE_DIR, not in git) and builds a
graph (nodes: Overture connectors; edges: the stretch of a segment between two of its connectors) to route over with
scipy's Dijkstra:

    net = roadnet.Network()
    path = net.route([[lat, lon], [lat, lon], ...], profile="piste")      # [(lat, lon), ...]

Two profiles weigh the classes: "piste" (tracks and small roads at their length, the large roads dear: a stage is not driven
on the N1) and "road" (the other way round). Where the network has nothing (the dunes, the hamada) or the way through it is
much longer than the straight line, two consecutive via points are joined by a straight line: the route says how many of its
metres are such a crossing (route_report), and fetch_route.py writes it into the geometry file so nobody takes it for a road.

Needs duckdb, numpy and scipy; network: overturemaps-us-west-2.s3.amazonaws.com (read only). Be gentle: one query per tile, once.
"""
import hashlib
import json
import math
import os
import pickle
import threading

R = 6371008.8
RELEASE = os.environ.get("OVERTURE_RELEASE", "2026-10-21.0")
SEGMENTS = "s3://overturemaps-us-west-2/release/%s/theme=transportation/type=segment/*"
CACHE_DIR = os.environ.get("DAKAR_CACHE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
TILE_DEG = 0.5
SNAP_M = 3000.0            # a via point is moved to the nearest node of the network when one is within this
GAP_FACTOR = 2.6           # a way through the network more than this times the straight line is a crossing instead
GAP_SLACK_M = 4000.0       # ... (and more than this much longer)
NOT_FOR_CARS = {"footway", "steps", "path", "cycleway", "bridleway", "pedestrian", "bus_guideway", "pedestrian_mall", "alley"}
# cost of a metre by class (1.0: as long as it is), per profile
PROFILES = {
    "piste": {"track": 1.0, "unclassified": 1.0, "service": 1.15, "residential": 1.6, "tertiary": 1.5, "secondary": 2.2,
              "primary": 4.0, "trunk": 8.0, "motorway": 20.0, "living_street": 2.0, "unknown": 1.4, "road": 1.4},
    "road": {"track": 4.0, "unclassified": 1.4, "service": 2.5, "residential": 1.3, "tertiary": 1.1, "secondary": 1.0,
             "primary": 0.9, "trunk": 0.85, "motorway": 0.85, "living_street": 2.0, "unknown": 2.0, "road": 2.0},
}
_nets = {}


def metres(a, b):
    dn = math.radians(b[0] - a[0]) * R
    de = math.radians(b[1] - a[1]) * R * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dn, de)


def line_length(pts):
    return sum(metres(a, b) for a, b in zip(pts, pts[1:]))


def decode_linestring(wkb):
    import struct
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


# ---------------------------------------------------------------------------------------------------------------------
# tiles: read from Overture once, kept on disk

def tile_key(lat, lon):
    return (int(math.floor(lat / TILE_DEG)), int(math.floor(lon / TILE_DEG)))


def tile_path(key):
    return os.path.join(CACHE_DIR, "overture-%s-v3" % RELEASE, "%d_%d.pkl" % key)


_local = threading.local()


def connect():
    """A DuckDB connection of this thread (one per thread: a connection is not shared)."""
    if getattr(_local, "con", None) is None:
        import duckdb
        con = duckdb.connect()
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute("SET s3_region='us-west-2';")
        _local.con = con
    return _local.con


def read_tile(key, log=print):
    """The road segments that touch tile `key`: [{id, cls, sub, surface, line, conns: [(connector id, at)]}]."""
    path = tile_path(key)
    if os.path.isfile(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    s, w = key[0] * TILE_DEG, key[1] * TILE_DEG
    n, e = s + TILE_DEG, w + TILE_DEG
    con = connect()
    last = None
    for attempt in range(4):
        try:
            rows = con.execute(
                "SELECT id, class, subclass, ST_AsWKB(geometry), connectors, road_surface, road_flags, level_rules FROM read_parquet(?, hive_partitioning=1) "
                "WHERE subtype = 'road' AND bbox.xmin < ? AND bbox.xmax > ? AND bbox.ymin < ? AND bbox.ymax > ?",
                [SEGMENTS % RELEASE, e, w, n, s]).fetchall()
            break
        except Exception as ex:                                     # (the bucket now and then drops a read: again, slower)
            last = ex
            import time
            time.sleep(3 * (attempt + 1))
    else:
        raise RuntimeError("Overture tile %s: %s" % (key, last))
    out = []
    for sid, cls, sub, wkb, conns, surf, flags, levels in rows:
        line = decode_linestring(wkb)
        if len(line) < 2 or cls in NOT_FOR_CARS:
            continue
        # (road_flags: is_bridge / is_tunnel, for the structures of a long stage: tools/structures.py)
        fl = sorted({v for f in (flags or []) for v in (f.get("values") or []) if v in ("is_bridge", "is_tunnel")})
        out.append({"id": sid, "cls": cls, "sub": sub, "surface": (surf[0]["value"] if surf else None), "line": line,
                    "flags": fl, "elevated": any((lv or {}).get("value", 0) != 0 for lv in (levels or [])), "conns": [(c["connector_id"], float(c["at"])) for c in (conns or [])]})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "wb") as f:
        pickle.dump(out, f)
    os.replace(path + ".tmp", path)
    log("tile %s: %d segments" % (key, len(out)))
    return out


def tiles_along(pts, margin_m=15000.0):
    """The tile keys covering a polyline of (lat, lon) and `margin_m` round it."""
    keys = set()
    step = 4000.0
    dlat = margin_m / 111320.0
    for a, b in zip(pts, pts[1:] if len(pts) > 1 else pts):
        d = metres(a, b)
        n = max(1, int(d // step))
        for i in range(n + 1):
            f = i / n
            la, lo = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
            dlon = margin_m / (111320.0 * max(0.2, math.cos(math.radians(la))))
            for x in (la - dlat, la, la + dlat):
                for y in (lo - dlon, lo, lo + dlon):
                    keys.add(tile_key(x, y))
    return sorted(keys)


def prefetch(keys, workers=4, log=print):
    """Read the tiles not on disk yet, `workers` at a time (the bucket is read only; four is polite)."""
    todo = [k for k in keys if not os.path.isfile(tile_path(k))]
    if not todo:
        return
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda k: read_tile(k, log), todo))


def roads_near(pts, margin_m=15000.0, log=print):
    """Segments (deduplicated) of the tiles along a polyline."""
    seen, out = set(), []
    keys = tiles_along(pts, margin_m)
    prefetch(keys, log=log)
    for key in keys:
        for seg in read_tile(key, log):
            if seg["id"] not in seen:
                seen.add(seg["id"])
                out.append(seg)
    return out


def roads_along(points, margin_m=60.0, log=lambda m: None):
    """The segments of the tiles along a line that come within about `margin_m` of it (a 220 m grid, so the margin is a few hundred
    metres at worst: callers measure the real distance): the line's points are every 20 m, a segment is sampled every 150 m."""
    cell = 0.002                                   # degrees: ~220 m
    near = set()
    for p in points:
        a, b = int(p[0] // cell), int(p[1] // cell)
        for da in (-1, 0, 1):
            for db in (-1, 0, 1):
                near.add((a + da, b + db))
    out = []
    for seg in roads_near([(p[0], p[1]) for p in points], margin_m, log):
        line = seg["line"]
        hit = False
        for a, b in zip(line, line[1:]):
            n = max(1, int(metres(a, b) // 150.0))
            for i in range(n + 1):
                f = i / n
                if (int((a[0] + (b[0] - a[0]) * f) // cell), int((a[1] + (b[1] - a[1]) * f) // cell)) in near:
                    hit = True
                    break
            if hit:
                break
        if hit:
            out.append(seg)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# graph

def substring(line, cum, a, b):
    """The part of a line (cumulative lengths `cum`) from fraction a to fraction b of its length, a < b, as points."""
    total = cum[-1]
    s0, s1 = a * total, b * total

    def at(s):
        for i in range(1, len(cum)):
            if cum[i] >= s or i == len(cum) - 1:
                span = cum[i] - cum[i - 1]
                f = (s - cum[i - 1]) / span if span > 1e-9 else 0.0
                f = min(1.0, max(0.0, f))
                p, q = line[i - 1], line[i]
                return (p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f)
        return line[-1]
    pts = [at(s0)]
    pts += [line[i] for i in range(len(line)) if s0 + 0.01 < cum[i] < s1 - 0.01]
    pts.append(at(s1))
    return pts


class Network:
    """The graph of the segments given (or read for the polyline `along`)."""

    def __init__(self, segments=None, log=print):
        self.log = log
        self.segments = segments if segments is not None else []
        self.built = False
        self._csr = {}

    @classmethod
    def along(cls, pts, margin_m=15000.0, log=print):
        return cls(roads_near(pts, margin_m, log), log)

    def build(self):
        import numpy as np
        from scipy.spatial import cKDTree
        ids, coords = {}, []
        edges = []                                  # (u, v, length m, class, points)
        for seg in self.segments:
            line = seg["line"]
            cum = [0.0]
            for a, b in zip(line, line[1:]):
                cum.append(cum[-1] + metres(a, b))
            if cum[-1] < 0.5:
                continue
            conns = sorted(seg["conns"], key=lambda c: c[1])
            # (segments without connectors in the data join the graph by their two ends)
            if len(conns) < 2:
                conns = [("%s#0" % seg["id"], 0.0), ("%s#1" % seg["id"], 1.0)]
            for (c0, a0), (c1, a1) in zip(conns, conns[1:]):
                if a1 - a0 < 1e-6:
                    continue
                pts = substring(line, cum, a0, a1)
                for cid, p in ((c0, pts[0]), (c1, pts[-1])):
                    if cid not in ids:
                        ids[cid] = len(coords)
                        coords.append(p)
                u, v = ids[c0], ids[c1]
                if u != v:
                    edges.append((u, v, cum[-1] * (a1 - a0), seg["cls"], pts))
        self.coords = coords
        self.edges = edges
        if coords:
            lat0 = sum(c[0] for c in coords) / len(coords)
            self.kx = math.radians(1.0) * R * math.cos(math.radians(lat0))
            self.ky = math.radians(1.0) * R
            xy = np.array([(c[1] * self.kx, c[0] * self.ky) for c in coords])
            self.tree = cKDTree(xy)
        self.built = True
        self.log("network: %d nodes, %d edges, %.0f km" % (len(coords), len(edges), sum(e[2] for e in edges) / 1000))
        return self

    def matrix(self, profile):
        if profile in self._csr:
            return self._csr[profile]
        import numpy as np
        from scipy.sparse import coo_matrix
        w = PROFILES[profile]
        n = len(self.coords)
        us, vs, cs = [], [], []
        best = {}
        for k, (u, v, length, cls, pts) in enumerate(self.edges):
            c = length * w.get(cls, 1.5)
            key = (min(u, v), max(u, v))
            if key not in best or c < best[key][0]:
                best[key] = (c, k)
        for (u, v), (c, k) in best.items():
            us += [u, v]
            vs += [v, u]
            cs += [c, c]
        m = coo_matrix((cs, (us, vs)), shape=(n, n)).tocsr()
        self._csr[profile] = (m, best)
        return self._csr[profile]

    def nearest_node(self, lat, lon, within_m=SNAP_M):
        if not self.built:
            self.build()
        if not self.coords:
            return None
        d, i = self.tree.query((lon * self.kx, lat * self.ky))
        return int(i) if d <= within_m else None

    def path_nodes(self, a, b, profile):
        """The node indices of the cheapest way from node a to node b, or None."""
        from scipy.sparse.csgraph import dijkstra
        m, _ = self.matrix(profile)
        dist, pred = dijkstra(m, directed=False, indices=a, return_predecessors=True)
        if not math.isfinite(dist[b]):
            return None
        out, i = [b], b
        while i != a:
            i = pred[i]
            out.append(int(i))
        return out[::-1]

    def edge_points(self, u, v, profile):
        """The points of the edge between nodes u and v, oriented u -> v."""
        _, best = self.matrix(profile)
        c, k = best[(min(u, v), max(u, v))]
        pts = self.edges[k][4]
        if metres(pts[0], self.coords[u]) > metres(pts[-1], self.coords[u]):
            pts = pts[::-1]
        return pts

    def route_pair(self, a, b, profile="piste"):
        """([(lat, lon), ...], kind): the way from point a to point b, kind 'network' or 'crossing' (a straight line)."""
        if not self.built:
            self.build()
        straight = metres(a, b)
        na, nb = self.nearest_node(*a), self.nearest_node(*b)
        if na is None or nb is None or na == nb:
            return [tuple(a), tuple(b)], "crossing"
        nodes = self.path_nodes(na, nb, profile)
        if nodes is None:
            return [tuple(a), tuple(b)], "crossing"
        pts = []
        for u, v in zip(nodes, nodes[1:]):
            seg = self.edge_points(u, v, profile)
            pts += seg if not pts else seg[1:]
        if not pts:
            return [tuple(a), tuple(b)], "crossing"
        # the lead from the via point to the network and the way out of it (straight: a few hundred metres)
        route = [tuple(a)] + pts + [tuple(b)]
        length = line_length(route)
        if length > GAP_FACTOR * straight and length - straight > GAP_SLACK_M:
            return [tuple(a), tuple(b)], "crossing"
        return route, "network"

    def route(self, via, profile="piste"):
        """The path through the via points: [(lat, lon), ...] (as route_report has it)."""
        pts, _ = self.route_report(via, profile)
        return pts

    def route_report(self, via, profile="piste"):
        """(path, report): report = {networkM, crossingM, crossings: [[from m, to m], ...]} along the path."""
        out, crossings, net, cross = [], [], 0.0, 0.0
        for a, b in zip(via, via[1:]):
            seg, kind = self.route_pair(a, b, profile)
            start = line_length(out) if out else 0.0
            if out and metres(out[-1], seg[0]) < 1.0:
                seg = seg[1:]
            elif out:
                seg = [seg[0]] + seg[1:]
            piece = ([out[-1]] if out else []) + list(seg)
            d = line_length(piece)
            if kind == "crossing":
                cross += d
                if crossings and abs(crossings[-1][1] - start) < 1.0:
                    crossings[-1][1] = start + d
                else:
                    crossings.append([start, start + d])
            else:
                net += d
            out += list(seg)
        return out, {"networkM": round(net), "crossingM": round(cross), "crossings": [[round(a), round(b)] for a, b in crossings]}


def network_for(via, margin_m=15000.0, log=print):
    """The built network for a leg's via points, kept for the next leg that needs the same one."""
    key = hashlib.sha1(json.dumps([[round(p[0], 3), round(p[1], 3)] for p in via]).encode()).hexdigest()
    if key not in _nets:
        _nets[key] = Network.along(via, margin_m, log).build()
    return _nets[key]
