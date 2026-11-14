"""tools/dem_diff.py: its own WGS84 frame, Web Mercator tiles and Terrarium decoding (no network).

    python -m pytest tests
"""
import io
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import dem_diff  # noqa: E402


def test_frame_round_trip():
    fr = dem_diff.Frame(42.17, 13.82)
    x = np.array([0.0, 559.3, -800.0, 1500.0])
    y = np.array([0.0, 861.1, 300.0, -1200.0])
    lat, lon, h = dem_diff.Frame.to_geodetic(fr, x, y)
    assert abs(lat[0] - 42.17) < 1e-9 and abs(lon[0] - 13.82) < 1e-9 and abs(h[0]) < 1e-6
    # (the game's overlay at 559.3, 861.1 read 42.177752, 13.826770)
    assert abs(lat[1] - 42.177752) < 2e-6 and abs(lon[1] - 13.826770) < 2e-6
    # back: the surface point's east / north are the plane point's, its up the curvature drop below the plane
    X, Y, Z = dem_diff.ecef(lat, lon, h)
    d = np.stack([X - fr.o[0], Y - fr.o[1], Z - fr.o[2]], -1)
    assert np.allclose(d @ fr.r[0], x, atol=1e-6) and np.allclose(d @ fr.r[1], y, atol=1e-6)
    assert np.allclose(fr.up(lat, lon, h), 0.0, atol=1e-6)
    assert fr.up(lat[3:], lon[3:], 0.0)[0] < -0.2      # 1.9 km out: the ellipsoid ~0.3 m below the plane


def test_tile_xy():
    x, y = dem_diff.tile_xy(0.0, 0.0, 1)
    assert np.isclose(x, 1.0) and np.isclose(y, 1.0)
    x, y = dem_diff.tile_xy(42.17, 13.82, 13)
    assert int(x) == 4410 and int(y) == 3035      # (the Mapterhorn z13 tile the block is in)


def test_decode_terrarium():
    from PIL import Image
    h = np.array([[0.0, 100.5], [-10.25, 2913.75]])
    v = h + 32768.0
    r = np.floor(v / 256)
    g = np.floor(v - r * 256)
    b = np.round((v - r * 256 - g) * 256)
    im = Image.fromarray(np.stack([r, g, b], -1).astype(np.uint8), "RGB")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    assert np.allclose(dem_diff.decode(buf.getvalue(), 2), h)
