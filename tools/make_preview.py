#!/usr/bin/env python3
r"""Draw the mission list pictures of an edition's loop from its route geometry (needs Pillow).

    python tools/make_preview.py editions/testbed-algarve

Writes preview.jpg (1920x1080) and thumbnail.jpg (200x200), the names the game's mission list looks for next to a
mission's info.json, into the loop's mission folder and into each leg's (a stage shows its own route). The picture is a
plain drawn map (no map data is fetched): the legs in the edition's geometry on a warm desert gradient, the service
park and the stage start marked, the title over it. Run it again after the geometry or the title changes.
"""
import argparse
import json
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MISSIONS = os.path.join(ROOT, "mod", "beamng_dakar", "gameplay", "missions", "beamng_earth")
FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

SAND_TOP, SAND_BOTTOM = (214, 150, 84), (92, 52, 38)
TRANSFER, SPECIAL, MARK = (236, 226, 208), (255, 112, 0), (255, 255, 255)


def font(size, bold=True):
    for name in (("bahnschrift.ttf", "arialbd.ttf") if bold else ("bahnschrift.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(os.path.join(FONTS, name), size)
        except OSError:
            pass
    return ImageFont.load_default()


def gradient(w, h):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / (h - 1)
        c = tuple(round(a + (b - a) * t) for a, b in zip(SAND_TOP, SAND_BOTTOM))
        for x in range(w):
            px[x, y] = c
    return img


def project(points, box, pad):
    """The legs' lat/lon points (equirectangular, east and north in metres) fitted into box = (x0, y0, x1, y1)."""
    lat0 = sum(p[0] for leg in points for p in leg) / sum(len(leg) for leg in points)
    k = math.cos(math.radians(lat0))
    en = [[((p[1]) * k, p[0]) for p in leg] for leg in points]
    xs = [p[0] for leg in en for p in leg]
    ys = [p[1] for leg in en for p in leg]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    bw, bh = box[2] - box[0] - 2 * pad, box[3] - box[1] - 2 * pad
    s = min(bw / max(x1 - x0, 1e-9), bh / max(y1 - y0, 1e-9))
    ox = box[0] + pad + (bw - (x1 - x0) * s) / 2
    oy = box[1] + pad + (bh - (y1 - y0) * s) / 2
    return [[(ox + (x - x0) * s, oy + (y1 - y) * s) for x, y in leg] for leg in en]


def draw_legs(d, legs, kinds, width, mark_r):
    for leg, kind in zip(legs, kinds):
        color = SPECIAL if kind == "special" else TRANSFER
        w = width if kind == "special" else max(2, round(width * 0.6))
        d.line(leg, fill=(0, 0, 0), width=w + max(2, w // 3), joint="curve")
        d.line(leg, fill=color, width=w, joint="curve")
    for pt in (legs[0][0], legs[-1][-1]):
        d.ellipse([pt[0] - mark_r, pt[1] - mark_r, pt[0] + mark_r, pt[1] + mark_r], fill=MARK, outline=(0, 0, 0), width=max(2, mark_r // 4))
    pt = legs[1][0] if len(legs) > 1 else None          # (the stage start: where the transfer ends)
    if pt:
        d.ellipse([pt[0] - mark_r, pt[1] - mark_r, pt[0] + mark_r, pt[1] + mark_r], fill=SPECIAL, outline=(255, 255, 255), width=max(2, mark_r // 3))


def make(edition, geometry, legs, title, subtitle):
    """(preview 1920x1080, thumbnail 200x200) of the given legs (indices into the edition's)."""
    kinds = [edition["legs"][i]["type"] for i in legs]
    pts = [geometry[edition["legs"][i]["id"]]["points"] for i in legs]
    W, H = 1920, 1080
    img = gradient(W, H)
    d = ImageDraw.Draw(img, "RGBA")
    for i in range(-H, W, 90):                          # (a faint grid of diagonal lines, like a roadbook page)
        d.line([(i, 0), (i + H, H)], fill=(255, 255, 255, 14), width=2)
    drawn = project(pts, (110, 50, W - 110, H - 260), 40)       # (above the title band)
    draw_legs(d, drawn, kinds, 14, 20)
    d.rectangle([0, H - 230, W, H], fill=(0, 0, 0, 150))
    d.text((90, H - 215), title.upper(), font=font(104), fill=(255, 255, 255))
    d.text((94, H - 100), subtitle, font=font(44, False), fill=(255, 190, 120))
    big = img.convert("RGB")
    t = gradient(200, 200)
    td = ImageDraw.Draw(t, "RGBA")
    tp = project(pts, (0, 0, 200, 200), 24)
    draw_legs(td, tp, kinds, 7, 6)
    return big, t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edition_dir")
    a = ap.parse_args()
    edition = json.load(open(os.path.join(a.edition_dir, "legs.json"), encoding="utf-8"))
    geometry = {}
    for leg in edition["legs"]:
        path = os.path.join(a.edition_dir, "geometry", leg["id"] + ".json")
        if not os.path.isfile(path):
            sys.exit("no geometry for %s: run tools/fetch_route.py first" % leg["id"])
        geometry[leg["id"]] = json.load(open(path, encoding="utf-8"))
    prefix = edition.get("missionPrefix", "dakar")
    km = sum(geometry[l["id"]]["lengthM"] for l in edition["legs"]) / 1000
    specials = [l for l in edition["legs"] if l["type"] == "special"]
    sk = sum(geometry[l["id"]]["lengthM"] for l in specials) / 1000
    title = edition.get("title") or prefix
    jobs = [(os.path.join(MISSIONS, "rallyLoop", prefix), list(range(len(edition["legs"]))), title,
             "%d km: %d transfer, %d special (%.0f km timed)" % (round(km), len(edition["legs"]) - len(specials), len(specials), sk))]
    for i, leg in enumerate(edition["legs"]):
        kind = "rallyStage" if leg["type"] == "special" else "rallyRoadSection"
        jobs.append((os.path.join(MISSIONS, kind, prefix + "-" + leg["id"]), [i], leg.get("title") or leg["id"],
                     "%.1f km %s" % (geometry[leg["id"]]["lengthM"] / 1000, "special stage" if leg["type"] == "special" else "road section")))
    for folder, idx, t, sub in jobs:
        if not os.path.isdir(folder):
            print("skip (no mission folder):", folder)
            continue
        big, small = make(edition, geometry, idx, t, sub)
        big.save(os.path.join(folder, "preview.jpg"), quality=88)
        small.save(os.path.join(folder, "thumbnail.jpg"), quality=90)
        print("wrote", os.path.relpath(folder, ROOT))


if __name__ == "__main__":
    main()
