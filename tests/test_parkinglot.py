"""tools/parkinglot.py without the network: the WKB decoding and the choice of a lot from synthetic aisles."""
import math
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import parkinglot as pl  # noqa: E402

LAT, LON = 37.0, -8.0


def at(north_m, east_m):
    return (LAT + north_m / 111320.0, LON + east_m / (111320.0 * math.cos(math.radians(LAT))))


def row(north_m, east0, east1):
    return [at(north_m, east0), at(north_m, east1)]


def test_a_wkb_linestring_decodes_in_both_byte_orders():
    pts = [(-8.0, 37.0), (-7.999, 37.001)]
    little = b"\x01" + struct.pack("<II", 2, 2) + b"".join(struct.pack("<dd", lon, lat) for lon, lat in pts)
    big = b"\x00" + struct.pack(">II", 2, 2) + b"".join(struct.pack(">dd", lon, lat) for lon, lat in pts)
    assert pl.decode_linestring(little) == [(37.0, -8.0), (37.001, -7.999)] == pl.decode_linestring(big)
    assert pl.decode_linestring(b"\x01" + struct.pack("<I", 1) + b"\x00" * 16) == []       # a point: not a line


def lot(north0, east0, rows, width=100, gap=30):
    """A lot: `rows` aisles `gap` m apart and `width` m long, joined by a cross aisle at the west end."""
    out = [row(north0 + gap * k, east0, east0 + width) for k in range(rows)]
    out.append([at(north0 + gap * k, east0) for k in range(rows)])        # (a vertex at each row: where aisles meet)
    return out


def test_aisles_that_touch_make_one_lot_and_far_ones_another():
    groups = pl.lots(lot(0, 0, 3) + lot(500, 0, 2))
    assert sorted(len(g) for g in groups) == [3, 4]


def test_the_lot_is_the_one_with_more_aisle_unless_it_is_far_away():
    big_far = lot(800, 0, 6)                                      # about 600 m of aisle, 800 m off
    small_near = lot(60, 0, 3)                                    # about 300 m of aisle, 60 m off
    got = pl.pick(big_far + small_near, LAT, LON)
    assert got["distM"] < 200
    assert pl.pick(big_far, LAT, LON)["distM"] > 700               # alone, it is the one


def test_too_little_aisle_is_no_lot_and_the_spot_is_on_an_aisle():
    assert pl.pick([row(10, 0, 30), row(10, 30, 60)], LAT, LON) is None      # 60 m: a driveway
    lines = lot(0, 0, 3)
    got = pl.pick(lines, LAT, LON)
    spot = (got["lat"], got["lon"])
    assert any(pl.metres(spot, v) < 0.5 for l in lines for v in l)
