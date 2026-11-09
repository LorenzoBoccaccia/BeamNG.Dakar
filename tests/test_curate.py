"""Offline tests for curate.lua: a stage's source line cleaned into the line the loop is built on.

    python -m pytest tests

Pure Lua, needs only lupa. The synthetic roads are made in metres and turned into lat/lon near Faro; the real
stages (both editions) are checked against what the curation is for: grade and its change within the limits, the line
a few metres from the source at most, and what it found written in the summary.
"""
import json
import math
import os

import pytest

lupa = pytest.importorskip("lupa")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DAKAR_EXT = os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions")
LAT0, LON0 = 37.1, -8.0
M_LAT = 111132.92 - 559.82 * math.cos(2 * math.radians(LAT0)) + 1.175 * math.cos(4 * math.radians(LAT0))
M_LON = 111412.84 * math.cos(math.radians(LAT0)) - 93.5 * math.cos(3 * math.radians(LAT0))


@pytest.fixture(scope="module")
def lua():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end")
    rt.execute("package.path = package.path .. ';%s/?.lua'" % DAKAR_EXT.replace("\\", "/"))
    return rt


@pytest.fixture(scope="module")
def cur(lua):
    return lua.eval("(require('beamngDakar/curate'))")


def table(lua, rows):
    t = lua.table()
    for i, r in enumerate(rows, 1):
        row = lua.table()
        for j, v in enumerate(r, 1):
            row[j] = v
        t[i] = row
    return t


def to_ll(xyh):
    """(x, y, h) metres -> (lat, lon, h)"""
    return [(LAT0 + y / M_LAT, LON0 + x / M_LON, h) for x, y, h in xyh]


def to_xy(p):
    return ((p[2] - LON0) * M_LON, (p[1] - LAT0) * M_LAT, p[3])


def line(lua, cur, xyh, **opts):
    o = lua.table()
    for k, v in opts.items():
        o[k] = v
    r = cur.line(table(lua, to_ll(xyh)), o)
    assert r is not None
    return r


def points(r):
    p = r.points
    return [to_xy(p[i]) for i in range(1, len(p) + 1)]


def height_at(r, x):
    """The curated height where the line (a road along +x) is at x."""
    pts = points(r)
    for a, b in zip(pts, pts[1:]):
        if a[0] <= x <= b[0]:
            return a[2] + (b[2] - a[2]) * (x - a[0]) / (b[0] - a[0])
    raise AssertionError("x %s not on the line" % x)


def straight(length, step=15.0, h=lambda x: 100 + 0.05 * x):
    n = int(length / step)
    return [(i * step, 0.0, h(i * step)) for i in range(n + 1)]


def test_a_spike_in_the_height_profile_is_taken_out_and_counted(lua, cur):
    src = straight(1200)
    spike = 40
    src[spike] = (src[spike][0], 0.0, src[spike][2] + 15.0)       # one terrain-data point 15 m up
    r = line(lua, cur, src)
    rep = r.report
    assert rep.spikes == 1 and rep.spikeMaxM == pytest.approx(15.0, abs=2.0)      # (the 10 m resampling takes the peak off a 15 m spacing)
    x = src[spike][0]
    assert height_at(r, x) == pytest.approx(100 + 0.05 * x, abs=0.6)    # the climb's own height there
    assert rep.heightChangeMax > 10 and rep.heightChangeKeptMax < 1.0      # the change is the spike, nothing else


def test_spikes_at_an_unreasonable_frequency_flag_the_profile_rough(lua, cur):
    src = straight(1200)
    for k in range(10, 70, 6):                                    # ten spikes in 1.2 km
        src[k] = (src[k][0], 0.0, src[k][2] + (6.0 if (k // 6) % 2 else -6.0))
    rep = line(lua, cur, src).report
    assert rep.spikes >= 8 and rep.spikesPerKm > 4 and rep.rough is True
    assert line(lua, cur, straight(1200)).report.rough is False


def test_a_real_climb_is_kept(lua, cur):
    src = straight(1500, h=lambda x: 200 + 0.08 * x)               # 8 percent for 1.5 km: 120 m of climb
    r = line(lua, cur, src)
    rep = r.report
    assert rep.spikes == 0 and rep.spans == 0
    assert rep.heightChangeMax < 0.3
    assert height_at(r, 0) == pytest.approx(200, abs=1e-6) and height_at(r, 1500) == pytest.approx(320, abs=0.01)
    assert rep.gradeOut == pytest.approx(0.08, abs=0.002)


def test_the_first_and_last_points_are_the_sources(lua, cur):
    src = straight(900)
    r = line(lua, cur, src)
    a, b = r.points[1], r.points[len(r.points)]
    assert (a[1], a[2], a[3]) == pytest.approx(to_ll(src)[0], abs=1e-9)
    assert (b[1], b[2], b[3]) == pytest.approx(to_ll(src)[-1], abs=1e-7)


def test_a_viaduct_in_the_terrain_data_is_a_span_with_the_deck_line_across(lua, cur):
    # the terrain under a bridge is the valley: the profile dives 30 m and climbs out within 120 m
    def h(x):
        base = 100.0
        if 600 <= x <= 720:
            t = min(x - 600, 720 - x, 30)
            return base - t
        return base
    r = line(lua, cur, straight(1500, step=10, h=h))
    assert r.report.spans == 1
    sp = r.spans[1]
    assert str(sp.kind) == "bridge" and 560 <= sp.s0 <= 640 and 680 <= sp.s1 <= 770
    assert height_at(r, 660) == pytest.approx(100, abs=1.0)        # the deck, not the valley
    assert cur.spanAt(r, int(660 / 10) + 1) is not None            # the stretch there is left alone
    assert cur.spanAt(r, 20) is None and cur.spanAt(r, 120) is None
    assert r.report.heightChangeKeptMax < 1.0


def test_a_ridge_in_the_terrain_data_is_a_tunnel(lua, cur):
    def h(x):
        if 600 <= x <= 760:
            return 100 + min(x - 600, 760 - x, 35)
        return 100.0
    r = line(lua, cur, straight(1500, step=10, h=h))
    assert r.report.spans == 1 and str(r.spans[1].kind) == "tunnel"
    assert height_at(r, 680) == pytest.approx(100, abs=1.0)


def test_a_climb_with_no_way_down_is_not_a_span(lua, cur):
    # steep for a while (30 percent for 200 m: 60 m of climb), then level: a road, not a bridge or a tunnel
    def h(x):
        return 100 + 0.3 * max(0, min(x - 500, 200)) + (60 if x > 700 else 0) * 0
    r = line(lua, cur, straight(1500, step=10, h=h))
    assert r.report.spans == 0


def test_tagged_structures_from_the_geometry_are_spans_as_given(lua, cur):
    s = lua.table()
    s[1] = lua.table(); s[1]["from"] = 400; s[1]["to"] = 550; s[1]["kind"] = "bridge"
    r = line(lua, cur, straight(1500), structures=s)
    assert r.report.spans == 1 and r.spans[1].tagged is True
    assert cur.spanAt(r, 46) is not None and cur.spanAt(r, 10) is None


def test_profile_noise_is_held_to_a_road_like_change_of_grade(lua, cur):
    import random
    rnd = random.Random(7)
    src = straight(2000, step=12, h=lambda x: 100 + 0.05 * x)
    src = [(x, y, h + rnd.uniform(-1.6, 1.6)) for x, y, h in src]
    rep = line(lua, cur, src).report
    assert rep.gradeChangeSource > 0.02
    assert rep.gradeChangeOut <= cur.MAX_GRADE_CHANGE * 1.02
    assert rep.gradeOut <= cur.MAX_GRADE * 1.02
    assert rep.heightChangeMax <= cur.MAX_DEV_M + 1e-6


def test_the_height_never_leaves_the_source_by_more_than_the_limit(lua, cur):
    # an 8 m bump over 120 m of road (a hillside's noise: too gentle for a span, too wide for a spike), too sharp a crest
    # for a road: flattened as far as the limit allows
    def h(x):
        return 100 + (8 * math.sin((x - 700) / 120 * math.pi) if 700 <= x <= 820 else 0)
    r = line(lua, cur, straight(1500, step=10, h=h))
    assert r.report.spans == 0 and r.report.spikes == 0
    assert 1.0 < r.report.heightChangeMax <= cur.MAX_DEV_M + 1e-6
    assert r.report.gradeChangeOut < r.report.gradeChangeSource


def test_an_out_and_back_spur_at_a_via_point_is_removed(lua, cur):
    # the router went 30 m up a side road to the via point and back: the line must not
    road = [(x, 0.0, 100.0) for x in range(0, 401, 10)]
    spur = [(400.0, -y, 100.0) for y in range(5, 31, 5)] + [(400.0, -y, 100.0) for y in range(25, -1, -5)]
    src = road + spur + [(x, 0.0, 100.0) for x in range(410, 801, 10)]
    r = line(lua, cur, src)
    assert r.report.stubs == 1
    ys = [abs(p[1]) for p in points(r)]
    assert max(ys) < 1.0
    assert r.report.lengthM == pytest.approx(800, abs=3)          # not 860


def test_repeated_points_are_dropped(lua, cur):
    src = [(x, 0.0, 100.0) for x in range(0, 500, 10)]
    src = [p for x in src for p in (x, x)] + [(500.0, 0.0, 100.0)]
    r = line(lua, cur, src)
    assert r.report.lengthM == pytest.approx(500, abs=0.5)
    assert r.report.points == 51


def test_a_one_point_zigzag_is_put_back_on_the_road(lua, cur):
    src = [(x, 0.0, 100.0) for x in range(0, 401, 10)]
    src[20] = (200.0, 6.0, 100.0)                                 # one snapped point 6 m off
    r = line(lua, cur, src)
    assert r.report.kinks == 1
    assert max(abs(p[1]) for p in points(r)) < 0.5


def test_a_bend_is_not_a_kink_and_the_plan_is_not_smoothed(lua, cur):
    # a 90 degree bend of radius 12 m: the corner survives (the pace notes read it) and the line stays on the source
    pts = [(x, 0.0, 100.0) for x in range(0, 200, 10)]
    for a in range(0, 91, 15):
        th = math.radians(a)
        pts.append((200 + 12 * math.sin(th), 12 - 12 * math.cos(th), 100.0))
    pts += [(212.0, 12 + y, 100.0) for y in range(10, 200, 10)]
    r = line(lua, cur, pts)
    assert r.report.kinks == 0 and r.report.stubs == 0
    assert r.report.planShiftMax < 1.0
    xs = [p[0] for p in points(r)]
    assert max(xs) == pytest.approx(212.0, abs=0.5)               # it still reaches the outside of the bend


def test_a_line_that_is_too_short_is_refused(lua, cur):
    r, why = cur.line(table(lua, to_ll([(0, 0, 0), (5, 0, 0)])), None)
    assert r is None and "shorter" in str(why)


EDITIONS = [("testbed-algarve", 39000), ("svolte-di-popoli", 7250)]


@pytest.mark.parametrize("edition,length", EDITIONS)
def test_the_real_stages_are_curated_within_the_limits(lua, cur, edition, length):
    path = os.path.join(ROOT, "editions", edition, "geometry", "ss1.json")
    if not os.path.isfile(path):
        pytest.skip("run tools/fetch_route.py editions/%s first" % edition)
    src = json.load(open(path))["points"]
    r = cur.line(table(lua, src), None)
    rep = r.report
    assert rep.lengthM == pytest.approx(length, rel=0.02)
    assert rep.gradeOut <= cur.MAX_GRADE * 1.001                     # no grade steeper than a road's
    assert rep.gradeChangeOut <= cur.MAX_GRADE_CHANGE * 1.001
    assert rep.gradeOut <= rep.gradeSource                           # never steeper than the source
    assert rep.heightChangeMax <= cur.MAX_DEV_M + 1e-6               # and the height a few metres from the source's
    assert rep.planShiftMax < 6.0                                    # the line a few metres from the source at most
    assert rep.spans == 0                                            # (the terrain data holds no viaduct on either)
    text = str(cur.summary("ss1", r))
    assert "ss1 curated" in text and "spike" in text and "largest height change" in text
    print(text)


def test_the_curated_line_follows_the_source_in_the_testbed(lua, cur):
    path = os.path.join(ROOT, "editions", "testbed-algarve", "geometry", "ss1.json")
    if not os.path.isfile(path):
        pytest.skip("run tools/fetch_route.py editions/testbed-algarve first")
    src = json.load(open(path))["points"]
    r = cur.line(table(lua, src), None)
    # first and last are the source's; the point count follows the 10 m step
    assert r.points[1][1] == pytest.approx(src[0][0], abs=1e-9)
    assert r.points[len(r.points)][2] == pytest.approx(src[-1][1], abs=1e-6)
    assert len(r.points) == pytest.approx(r.report.lengthM / 10, abs=3)
