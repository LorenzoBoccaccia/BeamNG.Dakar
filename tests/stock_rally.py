"""The stock rally loop's distance code, ported to Python from the game's Lua, so the tests can say what the
briefing and the progress bar show for the files Dakar writes.

Ported from BeamNG.drive's lua/ge/extensions:
  - editor/toolUtilities/geom.lua: catmullRomConformToTerrain (the game's way to make a driveline's points) and
    catmullRomFree (what core.lua's tap on zSnap.conformSplineGeometry has a Dakar driveline made with), with
    catmullRomCentripetalFast and mathlib's monotonicSteffen;
  - gameplay/rally/driveline/drivelineV3.lua: _findSegmentRange and calculateDistanceBetweenPoints (the briefing's
    Special Stage Distance: rallyManager.getRaceDistanceMeters, from the SS start line to the last race node);
  - gameplay/rally/driveline/drivelineRoute.lua: the points the route is made of, a race node or pace note waypoint put in at
    the segment next to the driveline's nearest point (the progress bar's scale is the route's length to the finish).
    The route's own point merging (raceRoute) is not ported: the length is the 3D polyline through the points.
"""
import math


def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _sign(v):
    return (v > 0) - (v < 0)


def monotonic_steffen(y0, y1, y2, y3, x0, x1, x2, x3, x):
    x1x0, x2x1, x3x2 = x1 - x0, x2 - x1, x3 - x2
    d0, d1, d2 = (y1 - y0) / (x1x0 + 1e-30), (y2 - y1) / (x2x1 + 1e-30), (y3 - y2) / (x3x2 + 1e-30)
    m1 = (_sign(d0) + _sign(d1)) * min(abs(d0), abs(d1), 0.5 * abs((x2x1 * d0 + x1x0 * d1) / (x2 - x0 + 1e-30)))
    m2 = (_sign(d1) + _sign(d2)) * min(abs(d1), abs(d2), 0.5 * abs((x3x2 * d1 + x2x1 * d2) / (x3 - x1 + 1e-30)))
    xx1 = x - x1
    xrel = xx1 / max(x2x1, 1e-30)
    return y1 + xx1 * (m1 + xrel * (d1 - m1 + (xrel - 1) * (m1 + m2 - 2 * d1)))


def catmull_rom_centripetal(p0, p1, p2, p3, t, s=0.5):
    s = s * 2
    d1 = max(math.sqrt(dist(p0, p1)), 1e-30)
    d2 = max(math.sqrt(dist(p1, p2)), 1e-30)
    d3 = max(math.sqrt(dist(p2, p3)), 1e-30)
    sd2, tt, t_1 = s * d2, t * t, t - 1
    t_1sq, c21 = t_1 * t_1, s * t_1 * (t * t_1 + tt)
    m1c, m2c = t * t_1sq * sd2, tt * t_1 * sd2
    m1c_d1, m1c_d1d2 = m1c / d1, m1c / (d1 + d2)
    m2c_d2d3, m2c_d3 = m2c / (d2 + d3), m2c / d3
    a, b = t_1sq * (2 * t + 1) - c21, c21 - tt * (2 * t - 3)
    return [
        (p1[i] - p0[i]) * m1c_d1 + (p0[i] - p2[i]) * m1c_d1d2 + (p1[i] - p3[i]) * m2c_d2d3 + (p3[i] - p2[i]) * m2c_d3
        + a * p1[i] + b * p2[i]
        for i in range(3)
    ]


def _spline_points(nodes, divisions, z_of):
    """The division points of an open spline through `nodes`: z_of(p0, p1, p2, p3, t, point) sets each point's height."""
    n, out, start = len(nodes), [], 0
    for j in range(1, n):
        i1, i2, i3, i4 = max(1, j - 1), j, j + 1, min(n, j + 2)
        p0, p1, p2, p3 = nodes[i1 - 1], nodes[i2 - 1], nodes[i3 - 1], nodes[i4 - 1]
        nd = divisions(dist(p1, p2))
        for k in range(start, nd + 1):
            t = k / nd
            p = catmull_rom_centripetal(p0, p1, p2, p3, t)
            p[2] = z_of(p0, p1, p2, p3, t, p)
            out.append(p)
        start = 1
    return out


def points_on_terrain(nodes, ground, min_div=10, min_spacing=2.0, max_spacing=20.0):
    """The game's own driveline points (geom.catmullRomConformToTerrain with driveline/util's 10 and 2 m): the heights are
    the terrain's, ground(x, y); the Earth terrain block answers a constant outside the ~2 km it covers."""
    def divisions(seg):
        return min(max(min_div, math.floor(seg / max_spacing + 0.5)), max(1, math.floor(seg / min_spacing)))
    return _spline_points(nodes, divisions, lambda p0, p1, p2, p3, t, p: ground(p[0], p[1]))


def points_free(nodes, min_div=10, max_spacing=20.0):
    """geom.catmullRomFree: the nodes' own heights (what core.lua's tap on zSnap.conformSplineGeometry makes of a Dakar driveline)."""
    def divisions(seg):
        return min(2000, max(min_div, math.floor(seg / max_spacing + 0.5)))
    return _spline_points(nodes, divisions,
                          lambda p0, p1, p2, p3, t, p: monotonic_steffen(p0[2], p1[2], p2[2], p3[2], 0, 1, 2, 3, t + 1))


def _xnorm(p, a, b):
    v = [b[i] - a[i] for i in range(3)]
    return sum(v[i] * (p[i] - a[i]) for i in range(3)) / (sum(c * c for c in v) + 1e-30)


def find_segment_range(pts, p1, p2):
    """drivelineV3:_findSegmentRange: each point on the segment whose INFINITE line is nearest to it (3D), the earlier first."""
    best = [None, None]
    least = [math.inf, math.inf]
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        for n, p in enumerate((p1, p2)):
            x = _xnorm(p, a, b)
            d = dist(p, [a[k] + (b[k] - a[k]) * x for k in range(3)])
            if d < least[n]:
                least[n], best[n] = d, (i, x)
    (i1, x1), (i2, x2) = best
    if i1 > i2 or (i1 == i2 and x1 > x2):
        i1, i2, x1, x2 = i2, i1, x2, x1
    return i1, x1, i2, x2


def distance_between(pts, p1, p2):
    """drivelineV3:calculateDistanceBetweenPoints (metres; a point beside a straight extension of a segment can make it negative)."""
    i1, x1, i2, x2 = find_segment_range(pts, p1, p2)
    if i1 == i2:
        return dist(pts[i1], pts[i1 + 1]) * (x2 - x1)
    total = dist(pts[i1], pts[i1 + 1]) * (1.0 - x1)
    for i in range(i1 + 1, i2):
        total += dist(pts[i], pts[i + 1])
    return total + dist(pts[i2], pts[i2 + 1]) * x2


def polyline_length(pts):
    return sum(dist(a, b) for a, b in zip(pts, pts[1:]))


def _segment_sq_dist(p, a, b):
    v = [b[i] - a[i] for i in range(3)]
    vv = sum(c * c for c in v)
    t = 0.0 if vv < 1e-12 else max(0.0, min(1.0, sum(v[i] * (p[i] - a[i]) for i in range(3)) / vv))
    return sum((p[i] - a[i] - v[i] * t) ** 2 for i in range(3))


class _Route:
    """The route's points: the driveline's, and the race nodes and waypoints put in next to the nearest driveline point."""

    def __init__(self, line):
        self.pts = [("driveline_%d" % i, p) for i, p in enumerate(line, 1)]
        self.rebuild()

    def rebuild(self):
        self.tree = list(self.pts)                      # (the game's kd tree: built over the points as they are then)
        self.idx = {k: i for i, (k, _) in enumerate(self.pts)}

    def insert(self, key, pos):
        near = min(self.tree, key=lambda t: sum((t[1][k] - pos[k]) ** 2 for k in range(3)))
        i = self.idx[near[0]]
        lo, hi = max(0, i - 4), min(len(self.pts) - 2, i + 3)
        at = min(range(lo, hi + 1), key=lambda k: _segment_sq_dist(pos, self.pts[k][1], self.pts[k + 1][1]))
        self.pts.insert(at, (key, pos))
        for k, v in self.idx.items():
            if v >= at:
                self.idx[k] = v + 1
        self.idx[key] = at


def route_lengths(line, start, stop, nodes, waypoints):
    """drivelineRoute.loadRouteFromRecordedDriveline's route over the driveline points `line`: the length along it, in 3D, from the
    start position to the stop control, and to each race node: (total, [length to node 1, 2, ...])."""
    r = _Route(line)
    r.insert("startPos", start)
    r.insert("stopZone", stop)
    for i, p in enumerate(nodes, 1):
        r.insert("node%d" % i, p)
    r.rebuild()
    for i, p in enumerate(waypoints):
        r.insert("wp%d" % i, p)
    keys = [k for k, _ in r.pts]
    a, b = keys.index("startPos"), keys.index("stopZone")
    route = r.pts[a:b + 1]
    acc, at = 0.0, {}
    for n, (k, p) in enumerate(route):
        if n:
            acc += dist(p, route[n - 1][1])
        at[k] = acc
    return acc, [at.get("node%d" % i, 0.0) for i in range(1, len(nodes) + 1)]
