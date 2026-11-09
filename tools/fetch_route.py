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
python tools/structures.py <edition> adds just those to files already made.

Needs Pillow and numpy (and duckdb for parkingNear). Network: router.project-osrm.org, s3.amazonaws.com and overpass-api.de (read only).
"""
import io
import json
import math
import os
import sys
import urllib.request

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import booth  # noqa: E402
import joins  # noqa: E402
import legplan  # noqa: E402
import structures  # noqa: E402

MAX_STEP_M = 20.0
ZOOM = 12
OSRM = "https://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson&continue_straight=true"
TILE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
R = 6371008.8
_tiles = {}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "BeamNG.Dakar-tools/0.1 (route prep)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def hav(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


_routes = {}


def route(via):
    coords = ";".join("%.6f,%.6f" % (lon, lat) for lat, lon in via)
    if coords not in _routes:
        d = json.loads(get(OSRM.format(coords=coords)))
        if d.get("code") != "Ok":
            raise RuntimeError("OSRM: %s" % d.get("code"))      # (a join candidate the router cannot reach is skipped)
        _routes[coords] = [(lat, lon) for lon, lat in d["routes"][0]["geometry"]["coordinates"]]
    return list(_routes[coords])


def resample(pts):
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(math.ceil(hav(a, b) / MAX_STEP_M)))
        for i in range(1, n + 1):
            f = i / n
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return out


def tile(x, y):
    if (x, y) not in _tiles:
        im = Image.open(io.BytesIO(get(TILE.format(z=ZOOM, x=x, y=y)))).convert("RGB")
        a = np.asarray(im, dtype=np.float64)
        _tiles[(x, y)] = a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0
    return _tiles[(x, y)]


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


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    ed = sys.argv[1]
    legs = json.load(open(os.path.join(ed, "legs.json"), encoding="utf-8"))["legs"]
    os.makedirs(os.path.join(ed, "geometry"), exist_ok=True)

    def find_lot(lat, lon, radius):
        import parkinglot
        return parkinglot.find_lot(lat, lon, radius)

    def ll(p):
        return [round(p[0], 7), round(p[1], 7), round(float(height(p[0], p[1])), 2)]

    for it in legplan.plan_edition(legs, lambda via: resample(route(via)), find_lot):
        leg, pts = it["leg"], it["pts"]
        length = sum(hav(a, b) for a, b in zip(pts, pts[1:]))
        out = {"id": leg["id"], "type": leg["type"], "lengthM": round(length, 1), "points": [ll(p) for p in pts]}
        if it["lot"]:
            out["servicePark"] = lot_record(it, ll)
        if leg["type"] == "special":
            # the bridges and tunnels the stage's own road is on, from OpenStreetMap's tags: the ground shaping
            # leaves them alone and meets their ends (tools/structures.py)
            try:
                found, beside = structures.record_all(pts)
                if found:
                    out["structures"] = found
                if beside:
                    out["tunnelsBeside"] = beside
            except Exception as e:                  # (no network: the file has none, and curate.lua finds spans from the profile)
                print("%s: no structures (%s)" % (leg["id"], e), file=sys.stderr)
        if it["joinIn"] and it["joinIn"]["mode"] != "kept":
            out["joinIn"] = {k: it["joinIn"][k] for k in ("mode", "shiftM")}
        with open(os.path.join(ed, "geometry", leg["id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(out, f, separators=(",", ":"))
        hs = [p[2] for p in out["points"]]
        print("%s: %d points, %.1f km, height %.0f..%.0f m" % (leg["id"], len(pts), length / 1000, min(hs), max(hs)))


if __name__ == "__main__":
    main()
