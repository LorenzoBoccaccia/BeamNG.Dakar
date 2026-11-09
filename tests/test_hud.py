"""GitHub #72: where the car is, top right and once a second in the log. Pure Lua, needs only lupa."""
import re
from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")

from test_shape import DAKAR_EXT  # noqa: E402

CORE = Path(DAKAR_EXT) / "beamngDakar" / "core.lua"


@pytest.fixture()
def hud():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("package.path = package.path .. ';%s/?.lua'" % DAKAR_EXT.replace("\\", "/"))
    return rt.eval("(require('beamngDakar/hud'))"), rt


def lua_pts(rt, pts):
    return rt.eval("function(s) return load('return ' .. s)() end")(
        "{" + ",".join("{%s}" % ",".join(str(c) for c in p) for p in pts) + "}")


def test_along_gives_metres_along_and_off_the_line(hud):
    h, rt = hud
    pts = lua_pts(rt, [(0, 0, 10), (100, 0, 20), (100, 50, 30)])
    m, off, z = h.along(pts, 40, 3)
    assert m == pytest.approx(40) and off == pytest.approx(3) and z == pytest.approx(14)
    m, off, z = h.along(pts, 104, 20)
    assert m == pytest.approx(120) and off == pytest.approx(4) and z == pytest.approx(24)


def test_nearest_names_the_closest_line_and_none_when_far(hud):
    h, rt = hud
    lines = rt.eval("function(a, b) return { { id = 'road1', pts = a }, { id = 'ss1', pts = b } } end")(
        lua_pts(rt, [(0, 0), (100, 0)]), lua_pts(rt, [(0, 20), (100, 20)]))
    hit = h.nearest(lines, 50, 15)
    assert hit.index == 2 and hit.id == "ss1" and hit.m == pytest.approx(50) and hit.off == pytest.approx(5)
    assert h.nearest(lines, 50, 500) is None


def test_text_and_log_line_carry_the_place(hud):
    h, rt = hud
    s = rt.eval("""{ x = 379.04, y = 875.5, z = 301.2, lat = 42.1761234, lon = 13.8241234, ground = 300.0, speed = 10,
      line = { index = 1, id = 'ss1', m = 352.4, off = 0.8, z = 301.0 } }""")
    lines = list(h.text(s).values())
    assert lines[0] == "x 379.0  y 875.5  z 301.2"
    assert lines[1] == "42.176123, 13.824123"
    assert lines[2] == "ground 300.0 (car +1.2)  36 km/h"
    assert lines[3] == "line 1 ss1: 352 m along, 0.8 m off  line z 301.0"
    log = h.logLine(s)
    assert log.startswith("where: x 379.0") and "line 1 ss1: 352 m along" in log


def test_text_without_ground_latlon_or_line(hud):
    h, rt = hud
    s = rt.eval("{ x = 1, y = 2, z = 3, speed = 0 }")
    lines = list(h.text(s).values())
    assert lines == ["x 1.0  y 2.0  z 3.0", "ground ?  0 km/h", "no line within 60 m"]


def test_core_samples_every_period_logs_and_draws_top_right():
    src = CORE.read_text(encoding="utf-8")
    assert "require('beamngDakar/hud')" in src
    assert re.search(r"pcall\(hudTick\)", src) and re.search(r"pcall\(drawHud\)", src)
    assert "hud.PERIOD_S" in src and "ownLog('I', hud.logLine(s))" in src
    assert "im.ImVec2(1, 0)" in src          # pivot on the window's top-right corner
