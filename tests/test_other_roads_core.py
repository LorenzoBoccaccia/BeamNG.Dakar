"""The geometry's `otherRoads` through the extension: with Earth's and the game's stubs (the stage's own stub world of test_stage_curation).

    python -m pytest tests

A motorway beside the stage road, in the geometry as tools/structures.py writes it, keeps its ground where the stage's 90 m spike is taken out.
"""
import json
import math
import os

import pytest

lupa = pytest.importorskip("lupa")

from test_core import EDITION, lua, logs, conv  # noqa: E402,F401
from test_stage_curation import earth, started, put_block_on_stage_start, run_frames  # noqa: E402,F401

BESIDE_M = 8.0               # the motorway's centre line is this far east of the stage road (the stub stage runs about north north east)
LUA_HELPERS = r"""
local function dist(px, py, pts)
  local best = math.huge
  for i = 1, #pts - 1 do
    local ax, ay, bx, by = pts[i][1], pts[i][2], pts[i + 1][1], pts[i + 1][2]
    local dx, dy = bx - ax, by - ay
    local l2 = dx * dx + dy * dy
    local t = l2 > 0 and math.max(0, math.min(1, ((px - ax) * dx + (py - ay) * dy) / l2)) or 0
    best = math.min(best, math.sqrt((px - ax - dx * t) ^ 2 + (py - ay - dy * t) ^ 2))
  end
  return best
end
-- the cells within 30 m of (cx, cy) that were moved, beyond the stage road's own ground (6.5 m from the driveline) and on the other
-- road's (4.8 m from its centre line: the half width of 3 m and the margin of 2 m, less a little)
function movedBesideRoad(cx, cy)
  local nodes = FILES['/gameplay/missions/beamng_earth/rallyStage/dakar-testbed-ss1/rally/drivelineSpline.json'].nodes
  local line = {}
  for i = 1, #nodes do line[i] = { nodes[i][1], nodes[i][2] } end
  local n = 0
  for gx = 0, 255 do
    for gy = 0, 255 do
      local wx, wy = TB.px + gx, TB.py + gy
      if (wx - cx) ^ 2 + (wy - cy) ^ 2 <= 900 and TB.h[gx + gy * 256] ~= SNAP[gx + gy * 256]
         and dist(wx, wy, line) > 6.5 and dist(wx, wy, OW) <= 4.8 then n = n + 1 end
    end
  end
  return n
end
"""


def motorway_piece():
    """The geometry's points around the stage's 90 m, BESIDE_M east of the line, as an `otherRoads` piece in lat, lon."""
    pts = json.load(open(os.path.join(EDITION, "geometry", "ss1.json")))["points"]
    k, run = 0, 0.0
    while run < 90 and k < len(pts) - 1:
        run += 111200.0 * ((pts[k + 1][0] - pts[k][0]) ** 2 + ((pts[k + 1][1] - pts[k][1]) * math.cos(math.radians(pts[k][0]))) ** 2) ** 0.5
        k += 1
    east = BESIDE_M / (111200.0 * math.cos(math.radians(pts[0][0])))
    return [[p[0], p[1] + east] for p in pts[max(k - 12, 0):k + 12]]


@pytest.mark.parametrize("with_road", [False, True])
def test_a_road_beside_the_stage_in_the_geometry_keeps_its_ground_where_the_spike_beside_it_is_taken_out(earth, with_road):
    piece = motorway_piece()
    if with_road:
        earth.globals().TMP = conv(earth, [{"way": 1, "hw": 3.0, "pts": piece}])
        earth.execute("JSON['/dakar/editions/testbed-algarve/geometry/ss1.json'].otherRoads = TMP")
    started(earth)
    put_block_on_stage_start(earth)
    earth.execute("package.loaded['beamngEarth/terrain'].swap('b.ter')")
    run_frames(earth, 150)
    nodes = earth.eval("FILES['/gameplay/missions/beamng_earth/rallyStage/dakar-testbed-ss1/rally/drivelineSpline.json'].nodes")
    earth.globals().PIECE = conv(earth, [[p[0], p[1], 0] for p in piece])
    earth.execute("OW = require('beamngDakar/legs').toWorld(PIECE, geodesy.newAnchor(ANCHOR_LL[1], ANCHOR_LL[2]))")
    earth.execute(LUA_HELPERS)
    moved = earth.eval("movedBesideRoad")(nodes[5][1], nodes[5][2])
    assert earth.eval("sameAs(TB, SNAP)") is False                     # (the spike is taken out either way)
    if with_road:
        assert moved == 0
        assert logs(earth, "cells beside the road kept clear of other roads")
    else:
        assert moved > 20                                              # (the taper does reach where the road would be)
