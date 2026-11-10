#!/usr/bin/env python3
"""Fetch the road geometry and ground heights for an edition's legs.

    python tools/fetch_route.py editions/testbed-algarve

Reads <edition>/legs.json; for each leg routes its 'via' points over roads (OSRM's public demo server, driving
profile) and writes <edition>/geometry/<leg id>.json:

    {"id": ..., "type": ..., "lengthM": ..., "points": [[lat, lon, h], ...]}

h is the ground height in metres from AWS Terrain Tiles (terrarium, zoom 12), bilinear. BeamNG.Earth's own terrain is
open DEM data too and it does not apply a geoid, so this h is the right one to hand to its geodeticToWorld. Points are
resampled to at most MAX_STEP_M apart so the Lua side can use them as they are.

A leg may carry "parkingNear": <metres>: the leg then starts in the best parking lot found within that distance of
its first via point (tools/parkinglot.py: Overture's parking aisles read with DuckDB), the lot replacing that first via
point as the one OSRM routes from. The car starts on one of the lot's aisles, facing the exit (where the route leaves the
lot), and the leg starts at TC0, a few metres ahead of it inside the lot; the lot is recorded in the leg's
geometry file as "servicePark": { lat, lon (the car), spawn { lat, lon, h, bearing }, exit { lat, lon, bearing },
lead [[lat, lon, h], ...] (the car to TC0), lines (the lot's aisles), booth (the service booth beside the car: gazebos, tables,
tools and cones, tools/booth.py), area and buildings (the lot's parking polygons and the buildings near it, which the
booth was checked against) }. The car starts where the lot has room for the booth beside it. Without duckdb or network the leg
starts at its first via point, with a warning.

Every join between a leg and the next is made one place the player is driven into from behind (tools/joins.py, legplan.py):
a leg that does not run into the next one straight (a transfer that turns onto the stage's road, the stage then running back
the way the transfer came) has the join moved along the road, past the turn, and the geometry written to agree.

A special's file also gets "structures": [{"from": m, "to": m, "kind": "bridge"|"tunnel", "way": id}], the bridge and tunnel ways its
own road is on, metres along the line (tools/structures.py, from OpenStreetMap's tags), and "tunnelsBeside": [{"from": m, "to": m, "way": id}],
the stretches within 30 m of the axis of a tunnel way (a tunnel under or beside the road: Earth builds its portals and shapes the ground for it).
And "otherRoads": [{"way": id, "hw": m, "pts": [[lat, lon], ...]}], the pieces of the other drivable ways within 30 m of the line that are not its
own road, bridge or tunnel (the ground shaping does not move another road's ground). python tools/structures.py <edition> adds just those to
files already made.

A leg's "router" says how its via points are joined: "osrm" (the default: OSRM's public demo server, driving profile, long routes in pieces,
each answer kept in tools/.cache), "piste" or "road" (tools/roadnet.py: Overture's track and road network read per half-degree tile, a graph and
Dijkstra over it; a piste is a stage that runs off the tarmac, where OSRM knows nothing; where the network has nothing the via points are
joined by a straight line and the leg's file says how many metres of those it has: "offNetworkM"), "straight" (the via points as they are).

A leg of type "ferry" (no road, the player is taken from one port to the other) and a day of rest have no geometry. An edition with "days"
gets <edition>/index.json: for each driven leg { start, end, lengthM, spawn (where the car stands when the leg starts in a lot) }, which the
game reads for the window and the start triggers, so that a day's geometry file is read only when that day is played.

    python tools/fetch_route.py editions/2027-portugal-morocco             all the legs
    python tools/fetch_route.py editions/2027-portugal-morocco S2 S2-in    just these legs (index.json keeps the others)
    python tools/fetch_route.py editions/2027-portugal-morocco --plan      lengths only, against the official ones: no heights, nothing written

The ground heights are read from tiles kept in tools/.cache (the first run downloads them, a few hundred kilobytes each, from AWS).

Needs Pillow and numpy (and duckdb and scipy for parkingNear and the pistes). Network: router.project-osrm.org, s3.amazonaws.com and overpass-api.de (read only).
"""
import functools
import hashlib
import io
import json
import math
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import booth  # noqa: E402
import joins  # noqa: E402
import legplan  # noqa: E402
import roadnet  # noqa: E402
import structures  # noqa: E402

MAX_STEP_M = 20.0
ZOOM = 12
OSRM = "https://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson&continue_straight=true"
TILE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
R = 6371008.8
CACHE = roadnet.CACHE_DIR
OSRM_VIAS = 20             # a route of more via points than this is asked for in pieces
OSRM_PAUSE_S = 0.4         # between two questions to the demo server (it is shared: be gentle)
LONG_LEG_M = 60000.0       # a special longer than this gets its structures from the Overture tiles already read, not from Overpass in 1.5 km pieces
DRIVEN = legplan.DRIVEN


def get(url, tries=4):
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": "BeamNG.Dakar-tools/0.1 (route prep)"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:                          # (a shared server now and then drops a request: again, slower)
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


def cached(kind, key, fetch):
    """The bytes of `fetch()`, kept in CACHE/<kind>/<key>."""
    path = os.path.join(CACHE, kind, key)
    if os.path.isfile(path):
        with open(path, "rb") as f:
            return f.read()
    data = fetch()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)
    return data


def hav(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


_routes = {}


def osrm_piece(via):
    coords = ";".join("%.6f,%.6f" % (lon, lat) for lat, lon in via)
    if coords not in _routes:
        def fetch():
            time.sleep(OSRM_PAUSE_S)
            return get(OSRM.format(coords=coords))
        d = json.loads(cached("osrm", hashlib.sha1(coords.encode()).hexdigest() + ".json", fetch))
        if d.get("code") != "Ok":
            raise RuntimeError("OSRM: %s" % d.get("code"))      # (a join candidate the router cannot reach is skipped)
        _routes[coords] = [(lat, lon) for lon, lat in d["routes"][0]["geometry"]["coordinates"]]
    return list(_routes[coords])


def osrm_route(via):
    """The road route through the via points (lat, lon), in pieces of OSRM_VIAS points that share their end."""
    if len(via) <= OSRM_VIAS:
        return osrm_piece(via)
    out = []
    i = 0
    while i < len(via) - 1:
        piece = osrm_piece(via[i:i + OSRM_VIAS])
        out += piece if not out else piece[1:]
        i += OSRM_VIAS - 1
    return out


_reports = {}                # leg id -> the off-network report of the last route made for it


def route(via, leg=None):
    """The path through a leg's via points, as its "router" joins them (see the module's docstring)."""
    kind = (leg or {}).get("router", "osrm")
    if kind == "osrm":
        return osrm_route(via)
    if kind == "straight":
        return [tuple(v) for v in via]
    net = roadnet.network_for(via)
    pts, rep = net.route_report(via, "piste" if kind == "piste" else "road")
    if leg is not None:
        _reports[leg["id"]] = rep
    return pts


def resample(pts):
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(math.ceil(hav(a, b) / MAX_STEP_M)))
        for i in range(1, n + 1):
            f = i / n
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return out


def tile_bytes(x, y):
    return cached("terrarium", "%d/%d/%d.png" % (ZOOM, x, y), lambda: get(TILE.format(z=ZOOM, x=x, y=y)))


@functools.lru_cache(maxsize=96)
def tile(x, y):
    im = Image.open(io.BytesIO(tile_bytes(x, y))).convert("RGB")
    a = np.asarray(im, dtype=np.float64)
    return a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0


def prefetch_heights(pts, workers=6):
    """Download the ground tiles under a path before it is looked at: a 400 km stage is a hundred of them."""
    n = 2 ** ZOOM
    lat = np.radians(np.array([p[0] for p in pts]))
    lon = np.array([p[1] for p in pts])
    px = (lon + 180.0) / 360.0 * n * 256.0 - 0.5
    py = (1.0 - np.arcsinh(np.tan(lat)) / math.pi) / 2.0 * n * 256.0 - 0.5
    need = set()
    for dx in (0, 1):
        for dy in (0, 1):
            tx = (np.floor(px).astype(int) + dx) // 256 % n
            ty = (np.floor(py).astype(int) + dy) // 256
            need.update(zip(tx.tolist(), ty.tolist()))
    todo = [t for t in sorted(need) if not os.path.isfile(os.path.join(CACHE, "terrarium", "%d/%d/%d.png" % (ZOOM, t[0], t[1])))]
    if todo:
        print("  %d ground tiles to download" % len(todo), flush=True)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(lambda t: tile_bytes(*t), todo))


def height(lat, lon):
    n = 2 ** ZOOM
    fx = (lon + 180.0) / 360.0 * n
    fy = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    px, py = fx * 256.0 - 0.5, fy * 256.0 - 0.5
    x0, y0 = int(math.floor(px)), int(math.floor(py))
    tx, ty = px - x0, py - y0

    def at(ix, iy):
        t = tile((ix // 256) % n, iy // 256)
        return t[iy % 256, ix % 256]

    return (at(x0, y0) * (1 - tx) * (1 - ty) + at(x0 + 1, y0) * tx * (1 - ty)
            + at(x0, y0 + 1) * (1 - tx) * ty + at(x0 + 1, y0 + 1) * tx * ty)


def lot_record(it, ll):
    """The leg's servicePark record: the lot found, and with a plan where the car starts, which way and where the lot is left."""
    lot = {k: v for k, v in it["lot"].items() if not k.startswith("_")}
    plan = it["plan"]
    if plan and it["lead"]:
        lead = it["lead"]
        sp = lead[0]
        ahead = joins.point_at(lead, min(joins.length(lead), 6.0))
        lot["lat"], lot["lon"] = round(sp[0], 7), round(sp[1], 7)
        lot["spawn"] = {"lat": round(sp[0], 7), "lon": round(sp[1], 7), "h": round(float(height(sp[0], sp[1])), 2),
                        "bearing": round(joins.bearing(sp, ahead), 1)}
        ex = plan["exit"]
        lot["exit"] = {"lat": round(ex[0], 7), "lon": round(ex[1], 7), "bearing": plan["exitBearing"]}
        lot["lead"] = [ll(p) for p in lead]
        lot["lines"] = [[[round(a, 6), round(b, 6)] for a, b in l] for l in it["lot"]["_lines"]]
        # the service booth beside the car, made from the lot's polygons and the drive just planned
        lot.update(booth.record(sp[:2], lead, it["pts"], it["lot"]["_lines"], it["lot"].get("_area"), it["lot"].get("_buildings"),
                                height, it["lot"].get("_roads")))
    return lot


def length_of(pts):
    return sum(hav(a, b) for a, b in zip(pts, pts[1:]))


def official_km(leg):
    return leg.get("specialKm") if leg.get("type") == "special" else leg.get("officialKm")


def read_index(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"legs": {}}


def main():
    args = sys.argv[1:]
    plan_only = "--plan" in args
    args = [a for a in args if a != "--plan"]
    if not args:
        raise SystemExit(__doc__)
    ed, only = args[0], set(args[1:])
    base = json.load(open(os.path.join(ed, "legs.json"), encoding="utf-8"))
    legs = [l for l in base["legs"] if l.get("type", "transfer") in DRIVEN]
    os.makedirs(os.path.join(ed, "geometry"), exist_ok=True)

    def find_lot(lat, lon, radius):
        import parkinglot
        return parkinglot.find_lot(lat, lon, radius)

    def ll(p):
        return [round(p[0], 7), round(p[1], 7), round(float(height(p[0], p[1])), 2)]

    def router(via, leg=None):
        return resample(route(via, leg))

    unknown = only - {l["id"] for l in legs}
    if unknown:
        raise SystemExit("not a driven leg: %s" % ", ".join(sorted(unknown)))
    # (a plan of lengths alone is not joined, nor in a lot: it asks the router for the routes and nothing else)
    items = legplan.plan_edition([l for l in legs if l["id"] in only] if (plan_only and only) else legs, router, None if plan_only else find_lot, join=not plan_only)
    index_path = os.path.join(ed, "index.json")
    index = read_index(index_path)
    for it in items:
        leg, pts = it["leg"], it["pts"]
        if only and leg["id"] not in only:
            continue
        length = length_of(pts)
        want = official_km(leg)
        rep = _reports.get(leg["id"]) or {}
        if plan_only:
            print("%-7s %-8s %8.1f km   official %-6s %s%s" % (leg["id"], leg.get("router", "osrm"), length / 1000, want if want is not None else "-",
                  "(%+.1f%%)" % ((length / 1000 / want - 1) * 100) if want else "", ("  off-network %.0f km" % (rep["crossingM"] / 1000)) if rep else ""))
            continue
        prefetch_heights(pts)
        out = {"id": leg["id"], "type": leg["type"], "lengthM": round(length, 1), "points": [ll(p) for p in pts]}
        if rep:
            out["offNetworkM"] = rep["crossingM"]
        if it["lot"]:
            out["servicePark"] = lot_record(it, ll)
        if leg["type"] == "special":
            # the bridges and tunnels the stage's own road is on, from OpenStreetMap's tags: the ground shaping
            # leaves them alone and meets their ends (tools/structures.py)
            try:
                found, beside, others = structures.record_all(pts, long_leg=length > LONG_LEG_M)
                if found:
                    out["structures"] = found
                if beside:
                    out["tunnelsBeside"] = beside
                if others:
                    out["otherRoads"] = others
            except Exception as e:                  # (no network: the file has none, and curate.lua finds spans from the profile)
                print("%s: no structures (%s)" % (leg["id"], e), file=sys.stderr)
        if it["joinIn"] and it["joinIn"]["mode"] != "kept":
            out["joinIn"] = {k: it["joinIn"][k] for k in ("mode", "shiftM")}
        with open(os.path.join(ed, "geometry", leg["id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(out, f, separators=(",", ":"))
        entry = {"start": out["points"][0], "end": out["points"][-1], "lengthM": out["lengthM"]}
        if "servicePark" in out and out["servicePark"].get("spawn"):
            sp = out["servicePark"]["spawn"]
            entry["spawn"] = [sp["lat"], sp["lon"], sp["h"]]
        index["legs"][leg["id"]] = entry
        hs = [p[2] for p in out["points"]]
        print("%s: %d points, %.1f km, height %.0f..%.0f m" % (leg["id"], len(pts), length / 1000, min(hs), max(hs)), flush=True)
    if base.get("days") and not plan_only:
        index["legs"] = {k: v for k, v in index["legs"].items() if k in {l["id"] for l in legs}}
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, separators=(",", ":"))
        print("index.json: %d legs" % len(index["legs"]))


if __name__ == "__main__":
    main()
