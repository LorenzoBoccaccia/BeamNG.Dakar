#!/usr/bin/env python3
"""Bridges and tunnels along a stage's own road, from OpenStreetMap tags.

    python tools/structures.py editions/svolte-di-popoli [leg id ...]

Reads <edition>/geometry/<leg id>.json of the special stages (or the legs named) and writes `"structures"` into it: a list of
{ "from": m, "to": m, "kind": "bridge" | "tunnel", "way": <OSM way id> } in metres along the line, the spans where the stage's own road
is a bridge or a tunnel way. The rest of the file is left as it is (the route is not asked again). tools/fetch_route.py calls
record() for the specials it writes. mod/.../curate.lua reads them as the spans the ground shaping leaves alone, and shape.lua pins
the shaped ground to the deck's height at each end of one.

Only the stage's own way counts. A viaduct over the road (the A25 over the Svolte di Popoli's SR17) is a bridge way near the line too,
but the line is not on it: it crosses at an angle and the road itself is another way, so a span needs the bridge or tunnel way to be
the nearest drivable way that runs the same way as the line (within ANGLE_DEG of it) for the whole stretch, and the stage's own road
at that place must not be a way without the tag. A culvert is not a tunnel the road is in.

A tunnel the line does not run in but passes over or beside (the A25's tunnel under the hill the Svolte di Popoli's stage road
climbs) is recorded as `"tunnelsBeside"`: [{ "from": m, "to": m, "way": <OSM way id> }], the stretches of the line within
TUNNEL_NEAR_M of a tunnel way's axis. Earth builds the tunnel's tube and portals and shapes the terrain for them (holes at the mouths, the
ground raised over a low arch), so the ground shaping leaves the ground along those stretches as Earth made it.

Network: overpass-api.de, and api.openstreetmap.org when Overpass does not answer (read only). The matching (spans()) is pure.
"""
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

R_EARTH = 6371008.8
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
SAMPLE_M = 4.0              # the line is looked at every this many metres
MATCH_M = 9.0               # a way within this of the line (centre to centre) can be the road the line runs on
ANGLE_DEG = 20.0            # ... when it runs within this of the line's direction (an overpass crosses at a larger angle; the A25
                            # crosses the SR17 at 22 and 31 degrees)
TIE_M = 1.5                 # a bridge or tunnel way must be this much nearer than a plain one to be the road the line is on
MERGE_GAP_M = 12.0          # two spans of one kind closer than this along the line are one
MIN_SPAN_M = 6.0            # a shorter stretch is a mismatch, not a bridge
CHUNK_M = 1500.0           # the Overpass queries cover the line in pieces this long
TUNNEL_NEAR_M = 30.0        # a tunnel way's axis this near the line: Earth's portal holes and arch raise reach about 12 m from the axis, and the shaping
                            # reaches 11.5 m from the line (shape.CORE_M + shape.BLEND_M), a point of the line 10 m apart
AROUND_M = TUNNEL_NEAR_M + 5.0
DRIVABLE = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "service", "track",
            "living_street", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "road"}
NOT_A_TUNNEL = {"no", "culvert", "flooded", "avalanche_protector"}      # (a culvert carries water under the road, not the road)
NOT_A_BRIDGE = {"no"}
_overpass_down = []         # [True] once Overpass did not answer: the OpenStreetMap API for the rest of the run


def kind_of(tags):
    """'bridge', 'tunnel' or None for a way's tags."""
    if tags.get("bridge") and tags["bridge"] not in NOT_A_BRIDGE:
        return "bridge"
    if tags.get("tunnel") and tags["tunnel"] not in NOT_A_TUNNEL:
        return "tunnel"
    return None


def _scales(lat):
    p = math.radians(lat)
    return (111132.92 - 559.82 * math.cos(2 * p) + 1.175 * math.cos(4 * p),
            111412.84 * math.cos(p) - 93.5 * math.cos(3 * p))


def hav(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(h))


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2)) if l2 > 1e-9 else 0.0
    return math.hypot(px - (ax + dx * t), py - (ay + dy * t))


def _angle_between(dx1, dy1, dx2, dy2):
    """The angle between two directions in degrees, 0..90 (a road has no front)."""
    a = abs(math.degrees(math.atan2(dx1 * dy2 - dy1 * dx2, dx1 * dx2 + dy1 * dy2)))
    return min(a, 180.0 - a)


def spans(points, ways, sample_m=SAMPLE_M):
    """The bridge and tunnel spans of a line. points: [(lat, lon, ...)]; ways: [{"id", "tags", "geometry": [(lat, lon), ...]}].
    Returns [{"from", "to", "kind", "way"}] in metres along the line (rounded to 0.1 m), in order."""
    if len(points) < 2:
        return []
    lat0, lon0 = points[0][0], points[0][1]
    mlat, mlon = _scales(lat0)

    def xy(p):
        return ((p[1] - lon0) * mlon, (p[0] - lat0) * mlat)

    line = [xy(p) for p in points]
    segs = []                                       # the drivable ways' segments: (ax, ay, bx, by, kind, way id)
    for w in ways:
        tags = w.get("tags") or {}
        if tags.get("highway") not in DRIVABLE:
            continue
        g = [xy(p) for p in w["geometry"]]
        k = kind_of(tags)
        for a, b in zip(g, g[1:]):
            segs.append((a[0], a[1], b[0], b[1], k, w["id"]))
    # the line at an even step: (s, x, y, direction)
    samples, s_off = [], 0.0
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        seg = math.hypot(bx - ax, by - ay)
        if seg < 1e-6:
            continue
        n = max(1, int(math.ceil(seg / sample_m)))
        for q in range(n):
            f = (q + 0.5) / n
            samples.append((s_off + seg * f, ax + (bx - ax) * f, ay + (by - ay) * f, (bx - ax) / seg, (by - ay) / seg))
        s_off += seg
    tagged = []                                     # per sample: (kind, way id) of the road it is on, or None
    for s, x, y, dx, dy in samples:
        near_plain, near_struct = None, None        # the nearest way without a structure tag, and the nearest with one
        for ax, ay, bx, by, k, wid in segs:
            if min(ax, bx) - MATCH_M > x or max(ax, bx) + MATCH_M < x or min(ay, by) - MATCH_M > y or max(ay, by) + MATCH_M < y:
                continue
            if _angle_between(dx, dy, bx - ax, by - ay) > ANGLE_DEG:
                continue                            # (an overpass or an underpass crossing the line: not the road it runs on)
            d = _seg_dist(x, y, ax, ay, bx, by)
            if d > MATCH_M:
                continue
            if k:
                if near_struct is None or d < near_struct[0]:
                    near_struct = (d, k, wid)
            elif near_plain is None or d < near_plain[0]:
                near_plain = (d, k, wid)
        # (a structure way counts only when it is clearly the road: at an overpass's crossing both ways are on the line)
        if near_struct and (near_plain is None or near_struct[0] + TIE_M < near_plain[0]):
            tagged.append((near_struct[1], near_struct[2]))
        else:
            tagged.append(None)
    out = []
    i = 0
    while i < len(samples):
        t = tagged[i]
        if not t:
            i += 1
            continue
        j = i
        while j + 1 < len(samples) and tagged[j + 1] and tagged[j + 1][0] == t[0]:
            j += 1
        out.append({"from": samples[i][0] - sample_m / 2, "to": samples[j][0] + sample_m / 2, "kind": t[0], "way": t[1]})
        i = j + 1
    merged = []
    for sp in out:
        if merged and merged[-1]["kind"] == sp["kind"] and sp["from"] - merged[-1]["to"] < MERGE_GAP_M:
            merged[-1]["to"] = sp["to"]
        else:
            merged.append(dict(sp))
    total = s_off
    res = []
    for sp in merged:
        sp["from"], sp["to"] = max(0.0, sp["from"]), min(total, sp["to"])
        if sp["to"] - sp["from"] >= MIN_SPAN_M:
            res.append({"from": round(sp["from"], 1), "to": round(sp["to"], 1), "kind": sp["kind"], "way": sp["way"]})
    return res


def tunnels_beside(points, ways, near_m=TUNNEL_NEAR_M, sample_m=SAMPLE_M):
    """The stretches of a line within near_m of the axis of a tunnel way (the line's own road included: the ground shaping leaves it alone
    either way). points: [(lat, lon, ...)]; ways as in spans(). Returns [{"from", "to", "way"}] in metres along the line (0.1 m), in order."""
    if len(points) < 2:
        return []
    lat0, lon0 = points[0][0], points[0][1]
    mlat, mlon = _scales(lat0)

    def xy(p):
        return ((p[1] - lon0) * mlon, (p[0] - lat0) * mlat)

    segs = []
    for w in ways:
        tags = w.get("tags") or {}
        if tags.get("highway") not in DRIVABLE or kind_of(tags) != "tunnel":
            continue
        g = [xy(p) for p in w["geometry"]]
        for a, b in zip(g, g[1:]):
            segs.append((a[0], a[1], b[0], b[1], w["id"]))
    if not segs:
        return []
    line = [xy(p) for p in points]
    out, s_off, cur = [], 0.0, None
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        seg = math.hypot(bx - ax, by - ay)
        if seg < 1e-6:
            continue
        n = max(1, int(math.ceil(seg / sample_m)))
        for q in range(n):
            f = (q + 0.5) / n
            x, y, s = ax + (bx - ax) * f, ay + (by - ay) * f, s_off + seg * f
            best = None
            for sx, sy, ex, ey, wid in segs:
                if min(sx, ex) - near_m > x or max(sx, ex) + near_m < x or min(sy, ey) - near_m > y or max(sy, ey) + near_m < y:
                    continue
                d = _seg_dist(x, y, sx, sy, ex, ey)
                if d <= near_m and (best is None or d < best[0]):
                    best = (d, wid)
            if best:
                if cur and cur["way"] == best[1] and s - cur["to"] <= sample_m * 1.5:
                    cur["to"] = s + sample_m / 2
                else:
                    cur = {"from": max(0.0, s - sample_m / 2), "to": s + sample_m / 2, "way": best[1]}
                    out.append(cur)
        s_off += seg
    merged = []
    for t in out:
        if merged and t["from"] - merged[-1]["to"] < MERGE_GAP_M:
            merged[-1]["to"] = max(merged[-1]["to"], t["to"])
        else:
            merged.append(dict(t))
    return [{"from": round(t["from"], 1), "to": round(min(t["to"], s_off), 1), "way": t["way"]} for t in merged]


def fetch_ways(points):
    """The drivable ways near a line, with their tags and geometry, from Overpass (in pieces along the line)."""
    pts = [(p[0], p[1]) for p in points]
    ways = {}
    chunk, run = [pts[0]], 0.0
    pieces = []
    for a, b in zip(pts, pts[1:]):
        run += hav(a, b)
        chunk.append(b)
        if run >= CHUNK_M:
            pieces.append(chunk)
            chunk, run = [b], 0.0
    if len(chunk) > 1:
        pieces.append(chunk)
    for piece in pieces:
        # (the piece's box and a margin: the public servers answer a box at once and are slow with a polyline's `around`)
        pad = AROUND_M / 111000.0
        s_, n_ = min(p[0] for p in piece) - pad, max(p[0] for p in piece) + pad
        w_, e_ = min(p[1] for p in piece) - pad * 1.4, max(p[1] for p in piece) + pad * 1.4
        q = '[out:json][timeout:90];way["highway"](%.6f,%.6f,%.6f,%.6f);out tags geom;' % (s_, w_, n_, e_)
        data = None
        for attempt in range(0 if _overpass_down else 3):      # (the public servers are busy at times: the other mirror, then a pause)
            url = OVERPASS[attempt % len(OVERPASS)]
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(),
                                             headers={"User-Agent": "BeamNG.Dakar-tools/0.1 (structures)"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())
                break
            except Exception as e:
                print("  overpass %s: %s" % (url, e), file=sys.stderr)
                time.sleep(3 * (attempt + 1))
        if data is None:
            _overpass_down[:] = [True]                  # (not asked again for the other pieces of this run)
        if data is not None:
            found = [{"id": el["id"], "tags": el.get("tags", {}), "geometry": [(n["lat"], n["lon"]) for n in el["geometry"]]}
                     for el in data.get("elements", []) if el.get("type") == "way" and el.get("geometry")]
        else:
            print("  no Overpass answer: the OpenStreetMap API's map call instead", file=sys.stderr)
            found = osm_api_ways(s_, w_, n_, e_)
        for w in found:
            ways[w["id"]] = w
    return list(ways.values())


def osm_api_ways(s, w, n, e):
    """The highway ways of a small box from the OpenStreetMap API (0.6 map call: the ways that touch the box with all their nodes)."""
    url = "https://api.openstreetmap.org/api/0.6/map?bbox=%.6f,%.6f,%.6f,%.6f" % (w, s, e, n)
    req = urllib.request.Request(url, headers={"User-Agent": "BeamNG.Dakar-tools/0.1 (structures)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        root = ET.fromstring(r.read())
    nodes = {nd.get("id"): (float(nd.get("lat")), float(nd.get("lon"))) for nd in root.iter("node")}
    out = []
    for el in root.iter("way"):
        tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
        if "highway" not in tags:
            continue
        geom = [nodes[nd.get("ref")] for nd in el.findall("nd") if nd.get("ref") in nodes]
        if len(geom) >= 2:
            out.append({"id": int(el.get("id")), "tags": tags, "geometry": geom})
    return out


def record(points):
    """The structures of a leg's points from OpenStreetMap: [{from, to, kind, way}]."""
    return spans(points, fetch_ways(points))


def record_all(points):
    """(structures, tunnelsBeside) of a leg's points: one fetch of the ways."""
    ways = fetch_ways(points)
    return spans(points, ways), tunnels_beside(points, ways)


def update_file(path):
    """Writes the structures into a geometry file, leaving the rest as it is. Returns the list."""
    with open(path, encoding="utf-8") as f:
        geo = json.load(f)
    found, beside = record_all(geo["points"])
    for key, val in (("structures", found), ("tunnelsBeside", beside)):
        if val:
            geo[key] = val
        else:
            geo.pop(key, None)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(geo, f, separators=(",", ":"))
    return found


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    ed = sys.argv[1]
    legs = json.load(open(os.path.join(ed, "legs.json"), encoding="utf-8"))["legs"]
    want = sys.argv[2:] or [l["id"] for l in legs if l["type"] == "special"]
    for lid in want:
        path = os.path.join(ed, "geometry", lid + ".json")
        found = update_file(path)
        print("%s: %d structure(s)%s" % (lid, len(found), "".join(
            "\n  %s %.0f..%.0f m (OSM way %s)" % (s["kind"], s["from"], s["to"], s["way"]) for s in found)))
        beside = json.load(open(path, encoding="utf-8")).get("tunnelsBeside", [])
        print("%s: %d stretch(es) beside a tunnel%s" % (lid, len(beside), "".join(
            "\n  %.0f..%.0f m (OSM way %s)" % (s["from"], s["to"], s["way"]) for s in beside)))


if __name__ == "__main__":
    main()
