#!/usr/bin/env python3
"""The in-game terrain block against the raw elevation tiles, fetched and sampled here without any of Earth's code.

The tiles are the public ones BeamNG.Earth reads (Terrarium encoding: h = R * 256 + G + B / 256 - 32768 m):

  mapterhorn     https://tiles.mapterhorn.com/{z}/{x}/{y}.webp   512 px, the deepest zoom it serves at the block
  aws-terrarium  https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png   256 px, z15

Each sample of the block's 1 m lattice (anchor frame: WGS84 east-north-up at the anchor, height 0) is put on the
ellipsoid, its height read from the tiles (bilinear between pixel centres, Web Mercator), and that surface point's
up coordinate in the anchor frame taken: what the block's PNG stores, built from the raw tiles alone. The session's
PNG (hm_<aid>_<cx>_<cy>.png; zBase and maxHeight from the server log, or --zbase / --max-height) minus it is the
heatmap: everything between the raw tiles and the game's ground.

  heatmap.png   per source: the in-game block minus the raw tiles (the block and --box), the stage line over it
  contours.png  the --box's contours over a hillshade: each source's raw tiles, and the in-game block
  points.png    with --point-source: the --box every --point-step m from SRTM 30 m (opentopodata) and / or the
                Copernicus DEM (Open-Meteo), beside the in-game block and the tiles, and each minus the in-game block
  dem_diff.npz  x, y, session, raw_<source> (ENU z), zoom per source
  report.txt    the zooms, tiles, and the differences' statistics in the block and the box

  python tools/dem_diff.py --anchor 42.17,13.82 --block 640,896 --box 470,780,680,960 \
      --session <game user folder>/temp/beamngEarth/<session> --geometry editions/svolte-di-popoli/geometry/ss1.json \
      --out dem-diff-out
"""
import argparse
import io
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request

import numpy as np

A = 6378137.0
F = 1 / 298.257223563
E2 = F * (2 - F)
SOURCES = {
    "mapterhorn": dict(url="https://tiles.mapterhorn.com/{z}/{x}/{y}.webp", size=512, zooms=range(17, 11, -1)),
    "aws-terrarium": dict(url="https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png", size=256,
                          zooms=range(15, 11, -1)),
}
UA = "dem-diff/1.0"
# point services (100 points a request, about one request a second), sampled on a coarser grid over the --box
POINT_SOURCES = {
    "srtm30m": "opentopodata SRTM 30 m (video_gen's osm-world ground)",
    "copernicus": "Open-Meteo elevation, Copernicus DEM",
}


def point_heights(name, lat, lon, cache):
    """Heights at the points (1-D) from point service name, its answers kept in cache (one file per request)."""
    import hashlib
    import time
    out = np.full(len(lat), np.nan)
    for i in range(0, len(lat), 100):
        la, lo = lat[i:i + 100], lon[i:i + 100]
        if name == "srtm30m":
            url = ("https://api.opentopodata.org/v1/srtm30m?interpolation=bilinear&locations="
                   + "|".join(f"{a:.6f},{b:.6f}" for a, b in zip(la, lo)))
        else:
            url = ("https://api.open-meteo.com/v1/elevation?latitude=" + ",".join(f"{a:.6f}" for a in la)
                   + "&longitude=" + ",".join(f"{b:.6f}" for b in lo))
        path = os.path.join(cache, name, hashlib.sha1(url.encode()).hexdigest() + ".json")
        fresh = not os.path.isfile(path)
        for attempt in range(6):
            try:
                data = fetch(url, path)
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 5:
                    raise
                wait = e.headers.get("Retry-After") if e.headers else None
                time.sleep(max(float(wait) if wait and wait.isdigit() else 0.0, 10.0 * (attempt + 1)))
        doc = json.loads(data)
        if name == "srtm30m":
            vals = [r.get("elevation") for r in doc["results"]]
        else:
            vals = doc["elevation"]
        out[i:i + len(vals)] = [np.nan if v is None else float(v) for v in vals]
        if fresh:
            time.sleep(1.1)
    return out


# -- WGS84
def ecef(lat, lon, h):
    la, lo = np.radians(lat), np.radians(lon)
    n = A / np.sqrt(1 - E2 * np.sin(la) ** 2)
    return ((n + h) * np.cos(la) * np.cos(lo), (n + h) * np.cos(la) * np.sin(lo), (n * (1 - E2) + h) * np.sin(la))


def geodetic(x, y, z):
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - E2))
    for _ in range(8):
        n = A / np.sqrt(1 - E2 * np.sin(lat) ** 2)
        h = p / np.cos(lat) - n
        lat = np.arctan2(z, p * (1 - E2 * n / (n + h)))
    n = A / np.sqrt(1 - E2 * np.sin(lat) ** 2)
    h = p / np.cos(lat) - n
    return np.degrees(lat), np.degrees(lon), h


class Frame:
    """East-north-up at (lat0, lon0, 0)."""

    def __init__(self, lat0, lon0):
        self.o = np.array(ecef(lat0, lon0, 0.0))
        la, lo = math.radians(lat0), math.radians(lon0)
        self.r = np.array([[-math.sin(lo), math.cos(lo), 0.0],
                           [-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la)],
                           [math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)]])

    def to_geodetic(self, e, n, u=0.0):
        e, n = np.asarray(e, np.float64), np.asarray(n, np.float64)
        u = np.broadcast_to(np.asarray(u, np.float64), e.shape)
        d = np.stack([e, n, u], -1) @ self.r
        return geodetic(d[..., 0] + self.o[0], d[..., 1] + self.o[1], d[..., 2] + self.o[2])

    def up(self, lat, lon, h):
        x, y, z = ecef(lat, lon, h)
        d = np.stack([x - self.o[0], y - self.o[1], z - self.o[2]], -1)
        return d @ self.r[2]


# -- tiles
def tile_xy(lat, lon, z):
    """Web Mercator tile coordinates (fractional) of lat / lon at zoom z."""
    s = 2.0 ** z
    x = (np.asarray(lon) + 180.0) / 360.0 * s
    la = np.radians(np.asarray(lat))
    y = (1.0 - np.log(np.tan(la) + 1.0 / np.cos(la)) / math.pi) / 2.0 * s
    return x, y


def decode(data, size):
    from PIL import Image
    im = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), np.float64)
    if im.shape[:2] != (size, size):
        raise ValueError(f"tile {im.shape[:2]}, not {size}²")
    return im[..., 0] * 256.0 + im[..., 1] + im[..., 2] / 256.0 - 32768.0


def fetch(url, path):
    if os.path.isfile(path):
        with open(path, "rb") as f:
            return f.read()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None
        raise
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return data


def source_heights(name, lat, lon, cache, zoom=None):
    """(heights at the points from source name's tiles, zoom, tiles read). The deepest zoom whose tile at the points'
    centre exists, unless zoom is given."""
    src = SOURCES[name]
    size = src["size"]
    clat, clon = float(np.mean(lat)), float(np.mean(lon))

    def get(z, x, y):
        return fetch(src["url"].format(z=z, x=x, y=y), os.path.join(cache, name, str(z), str(x), f"{y}.bin"))
    if zoom is None:
        for z in src["zooms"]:
            tx, ty = tile_xy(clat, clon, z)
            if get(z, int(tx), int(ty)) is not None:
                zoom = z
                break
        else:
            raise SystemExit(f"{name}: no tile at {clat}, {clon}")
    tx, ty = tile_xy(lat, lon, zoom)
    px = tx * size - 0.5            # pixel centres at k + 0.5
    py = ty * size - 0.5
    i0 = int(np.floor(px.min())) - 1
    j0 = int(np.floor(py.min())) - 1
    i1 = int(np.floor(px.max())) + 2
    j1 = int(np.floor(py.max())) + 2
    mosaic = np.full((j1 - j0 + 1, i1 - i0 + 1), np.nan)
    tiles = 0
    for ty_ in range(j0 // size, j1 // size + 1):
        for tx_ in range(i0 // size, i1 // size + 1):
            data = get(zoom, tx_, ty_)
            if data is None:
                continue
            h = decode(data, size)
            tiles += 1
            a0, b0 = tx_ * size, ty_ * size
            ia, ib = max(i0, a0), min(i1, a0 + size - 1)
            ja, jb = max(j0, b0), min(j1, b0 + size - 1)
            mosaic[ja - j0:jb - j0 + 1, ia - i0:ib - i0 + 1] = h[ja - b0:jb - b0 + 1, ia - a0:ib - a0 + 1]
    fx, fy = px - i0, py - j0
    i = np.floor(fx).astype(int)
    j = np.floor(fy).astype(int)
    wx, wy = fx - i, fy - j
    z = ((mosaic[j, i] * (1 - wx) + mosaic[j, i + 1] * wx) * (1 - wy)
         + (mosaic[j + 1, i] * (1 - wx) + mosaic[j + 1, i + 1] * wx) * wy)
    return z, zoom, tiles


def block_params(log_path, aid, cx, cy):
    pat = re.compile(rf"terrain \({aid}, {cx:.1f}, {cy:.1f}\) z\d+ in .*zBase (\S+) maxHeight (\S+)")
    got = None
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.search(line)
                if m:
                    got = float(m.group(1)), float(m.group(2))
    except OSError:
        pass
    return got


def pair(s):
    a, b = s.split(",")
    return float(a), float(b)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anchor", required=True, type=pair, help="lat,lon of the anchor (the server log's)")
    ap.add_argument("--aid", type=int, default=4)
    ap.add_argument("--block", required=True, type=pair, help="cx,cy of the block (anchor frame, m)")
    ap.add_argument("--session", required=True, help="the game's session folder (temp/beamngEarth/<session>)")
    ap.add_argument("--server-log", default=os.path.join(os.environ.get("LOCALAPPDATA", ""), "beamng-earth", "logs",
                                                         "server.log"))
    ap.add_argument("--zbase", type=float)
    ap.add_argument("--max-height", type=float)
    ap.add_argument("--box", help="x0,y0,x1,y1 drawn zoomed (anchor frame, m)")
    ap.add_argument("--geometry", help="a Dakar stage geometry json drawn over the maps")
    ap.add_argument("--source", action="append", choices=sorted(SOURCES), help="(default: both)")
    ap.add_argument("--zoom", type=int, help="the tiles' zoom (default: the deepest each source has)")
    ap.add_argument("--point-source", action="append", choices=sorted(POINT_SOURCES),
                    help="also a point service over the --box (repeatable)")
    ap.add_argument("--point-step", type=float, default=10.0, help="m between the point services' samples")
    ap.add_argument("--contour", type=float, default=1.0, help="m between contours in contours.png")
    ap.add_argument("--cache", default=os.path.join(os.path.expanduser("~"), ".cache", "dem-diff"))
    ap.add_argument("--out", default="dem-diff")
    a = ap.parse_args()
    from PIL import Image
    cx, cy = a.block
    name = f"hm_{a.aid}_{int(cx)}_{int(cy)}.png"
    q = np.asarray(Image.open(os.path.join(a.session, name)), np.float64)[::-1]     # row 0 = south
    n = q.shape[0]
    if a.zbase is not None and a.max_height is not None:
        zb, mh = a.zbase, a.max_height
    else:
        got = block_params(a.server_log, a.aid, cx, cy)
        if got is None:
            sys.exit(f"no zBase / maxHeight of block ({cx:g}, {cy:g}) in {a.server_log}: give --zbase and --max-height")
        zb, mh = got
    session = zb + q * (mh / 65536.0)
    xs = cx - n / 2.0 + np.arange(n)
    ys = cy - n / 2.0 + np.arange(n)
    X, Y = np.meshgrid(xs, ys)
    fr = Frame(*a.anchor)
    lat, lon, _ = fr.to_geodetic(X, Y)
    os.makedirs(a.out, exist_ok=True)
    raw = {}
    lines = [f"block ({cx:g}, {cy:g}) of anchor {a.aid} at {a.anchor[0]}, {a.anchor[1]}: {name}, zBase {zb:g}, "
             f"maxHeight {mh:g} (Q {mh / 65536:g} m)"]
    box = None
    if a.box:
        bx0, by0, bx1, by1 = (float(v) for v in a.box.split(","))
        box = (slice(max(0, int(by0 - ys[0])), int(by1 - ys[0]) + 1), slice(max(0, int(bx0 - xs[0])), int(bx1 - xs[0]) + 1))
    for sname in a.source or sorted(SOURCES):
        h, zoom, tiles = source_heights(sname, lat, lon, a.cache, a.zoom)
        u = fr.up(lat, lon, h)
        raw[sname] = (u, zoom)
        d = session - u
        px_m = 40075016.686 * math.cos(math.radians(a.anchor[0])) / (2 ** zoom * SOURCES[sname]["size"])
        lines.append(f"{sname}: z{zoom} ({px_m:.1f} m pixels), {tiles} tiles")
        for label, sl in [("block", (slice(None), slice(None)))] + ([("box", box)] if box else []):
            dd = d[sl]
            lines.append(f"  {label}: in-game - raw: median {np.nanmedian(dd):+.2f} m, {np.nanmin(dd):+.2f} .. "
                         f"{np.nanmax(dd):+.2f} m; |diff| > 0.5 m on {int((np.abs(dd) > 0.5).sum())} of {dd.size} "
                         f"samples, > 2 m on {int((np.abs(dd) > 2).sum())}")
    rep = "\n".join(lines)
    with open(os.path.join(a.out, "report.txt"), "w", encoding="utf-8") as f:
        f.write(rep + "\n")
    print(rep)
    np.savez_compressed(os.path.join(a.out, "dem_diff.npz"), x=xs, y=ys, session=session,
                        **{f"raw_{k.replace('-', '_')}": v[0] for k, v in raw.items()},
                        **{f"zoom_{k.replace('-', '_')}": v[1] for k, v in raw.items()})
    draw(a, xs, ys, session, raw, box, fr)
    if a.point_source and box:
        points(a, xs, ys, session, raw, box, fr)


def points(a, xs, ys, session, raw, box, fr):
    """The --box on a --point-step grid: the in-game block, the tiles and each point service, as contours, and each
    one minus the in-game block (points.png, points.txt)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    bx0, by0, bx1, by1 = (float(v) for v in a.box.split(","))
    gx = np.arange(bx0, bx1 + 0.01, a.point_step)
    gy = np.arange(by0, by1 + 0.01, a.point_step)
    GX, GY = np.meshgrid(gx, gy)
    ii = np.rint(GX - xs[0]).astype(int)
    jj = np.rint(GY - ys[0]).astype(int)
    lat, lon, _ = fr.to_geodetic(GX, GY)
    grids = {"in-game block": session[jj, ii]}
    for k, (u, z) in raw.items():
        grids[f"{k} z{z}"] = u[jj, ii]
    for k in a.point_source:
        h = point_heights(k, lat.ravel(), lon.ravel(), a.cache).reshape(GX.shape)
        grids[k] = fr.up(lat, lon, h)
    ref = grids["in-game block"]
    lines = [f"--box {a.box} every {a.point_step:g} m: {GX.size} points"]
    for k, g in grids.items():
        if k == "in-game block":
            continue
        d = g - ref
        lines.append(f"  {k} - in-game: median {np.nanmedian(d):+.2f} m, {np.nanmin(d):+.2f} .. {np.nanmax(d):+.2f} m; "
                     f"after its median: rms {np.sqrt(np.nanmean((d - np.nanmedian(d)) ** 2)):.2f} m")
    with open(os.path.join(a.out, "points.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    stage = None
    if a.geometry:
        with open(a.geometry, encoding="utf-8") as f:
            pts = np.asarray(json.load(f)["points"], np.float64)
        x0, y0, z0 = ecef(pts[:, 0], pts[:, 1], 0.0)
        d3 = np.stack([x0 - fr.o[0], y0 - fr.o[1], z0 - fr.o[2]], -1)
        stage = (d3 @ fr.r[0], d3 @ fr.r[1])
    names = list(grids)
    fig, axs = plt.subplots(2, len(names), figsize=(7 * len(names), 12), squeeze=False)
    lo_ = min(float(np.nanmin(g)) for g in grids.values())
    hi_ = max(float(np.nanmax(g)) for g in grids.values())
    levels = np.arange(np.floor(lo_), np.ceil(hi_) + a.contour, a.contour)
    for c, k in enumerate(names):
        g = grids[k]
        ax = axs[0, c]
        cs = ax.contour(gx, gy, g, levels=levels, colors="saddlebrown", linewidths=0.6)
        ax.clabel(cs, cs.levels[::5], fontsize=6, fmt="%.0f")
        ax.set_title(f"{k}: {a.contour:g} m contours (every {a.point_step:g} m)", fontsize=10)
        ax2 = axs[1, c]
        if k == "in-game block":
            ax2.axis("off")
        else:
            d = g - ref
            d = d - np.nanmedian(d)
            lim = max(0.5, float(np.nanmax(np.abs(d))))
            im = ax2.imshow(d, origin="lower", cmap="RdBu_r", vmin=-lim, vmax=lim, interpolation="nearest",
                            extent=(gx[0] - a.point_step / 2, gx[-1] + a.point_step / 2,
                                    gy[0] - a.point_step / 2, gy[-1] + a.point_step / 2))
            fig.colorbar(im, ax=ax2, shrink=0.8, label="minus the in-game block, less its median (m)")
            ax2.set_title(f"{k} - in-game (median removed)", fontsize=10)
        for axx in (ax, ax2):
            if stage is not None:
                axx.plot(stage[0], stage[1], "k-", lw=0.6)
            axx.set_xlim(gx[0], gx[-1])
            axx.set_ylim(gy[0], gy[-1])
            axx.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "points.png"), dpi=80)
    plt.close(fig)


def draw(a, xs, ys, session, raw, box, fr):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    stage = None
    if a.geometry:
        with open(a.geometry, encoding="utf-8") as f:
            pts = np.asarray(json.load(f)["points"], np.float64)
        x0, y0, z0 = ecef(pts[:, 0], pts[:, 1], 0.0)
        d = np.stack([x0 - fr.o[0], y0 - fr.o[1], z0 - fr.o[2]], -1)
        stage = (d @ fr.r[0], d @ fr.r[1])
    views = [("block", (slice(None), slice(None)))] + ([("box", box)] if box else [])
    fig, axs = plt.subplots(len(raw), len(views), figsize=(10 * len(views), 9 * len(raw)), squeeze=False)
    for r, (sname, (u, zoom)) in enumerate(raw.items()):
        d = session - u
        for c, (title, sl) in enumerate(views):
            ax = axs[r, c]
            sx, sy = xs[sl[1]], ys[sl[0]]
            dd = d[sl]
            lim = max(0.5, float(np.nanpercentile(np.abs(dd), 99.5)))
            im = ax.imshow(dd, origin="lower", cmap="RdBu_r", vmin=-lim, vmax=lim,
                           extent=(sx[0] - 0.5, sx[-1] + 0.5, sy[0] - 0.5, sy[-1] + 0.5), interpolation="nearest")
            fig.colorbar(im, ax=ax, shrink=0.8, label="in-game - raw tiles (m)")
            if stage is not None:
                ax.plot(stage[0], stage[1], "k-", lw=0.6)
                ax.set_xlim(sx[0] - 0.5, sx[-1] + 0.5)
                ax.set_ylim(sy[0] - 0.5, sy[-1] + 0.5)
            ax.set_title(f"in-game block minus {sname} z{zoom} ({title}); black: stage line", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "heatmap.png"), dpi=80)
    plt.close(fig)

    # the contours (every --contour m) over a hillshade: each source's raw tiles and the in-game block, the box
    sl = box if box else (slice(None), slice(None))
    sx, sy = xs[sl[1]], ys[sl[0]]
    panels = [(f"{k} z{z} (raw tiles)", u[sl]) for k, (u, z) in raw.items()] + [("in-game block", session[sl])]
    fig, axs = plt.subplots(1, len(panels), figsize=(10 * len(panels), 9), squeeze=False)
    lo_, hi_ = min(float(np.nanmin(p_)) for _t, p_ in panels), max(float(np.nanmax(p_)) for _t, p_ in panels)
    levels = np.arange(np.floor(lo_), np.ceil(hi_) + a.contour, a.contour)
    for ax, (title, h) in zip(axs[0], panels):
        gy, gx = np.gradient(h)
        shade = np.clip(0.5 + (-gx + gy) * 0.5, 0, 1)          # (light from the north-west)
        ext = (sx[0] - 0.5, sx[-1] + 0.5, sy[0] - 0.5, sy[-1] + 0.5)
        ax.imshow(shade, origin="lower", cmap="gray", vmin=0, vmax=1, extent=ext, interpolation="nearest")
        cs = ax.contour(sx, sy, h, levels=levels, colors="saddlebrown", linewidths=0.5)
        ax.clabel(cs, cs.levels[::5], fontsize=6, fmt="%.0f")
        if stage is not None:
            ax.plot(stage[0], stage[1], "c-", lw=0.8)
        ax.set_xlim(ext[0], ext[1])
        ax.set_ylim(ext[2], ext[3])
        ax.set_title(f"{title}: {a.contour:g} m contours (ENU z), hillshade; cyan: stage line", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "contours.png"), dpi=80)
    plt.close(fig)


if __name__ == "__main__":
    main()
