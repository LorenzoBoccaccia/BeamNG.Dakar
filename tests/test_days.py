"""days.lua (an edition that runs over several days) and preload.slice: the pure logic, no game."""
import math
import os

import pytest

lupa = pytest.importorskip("lupa")

HERE = os.path.dirname(os.path.abspath(__file__))
DAKAR_EXT = os.path.join(os.path.dirname(HERE), "mod", "beamng_dakar", "lua", "ge", "extensions", "beamngDakar").replace("\\", "/")

BASE = r"""
days = dofile('%(dir)s/days.lua')
base = {
  edition = 'rally', title = 'The Rally', missionPrefix = 'dk', shaping = { on = true },
  legs = {
    { id = 'P-in', type = 'transfer' }, { id = 'P', type = 'special' }, { id = 'P-out', type = 'transfer' },
    { id = 'T1', type = 'transfer' }, { id = 'F1', type = 'ferry', from = { name = 'A', lat = 1, lon = 2 }, to = { name = 'B', lat = 3, lon = 4 } },
    { id = 'T2', type = 'transfer' }, { id = 'R', type = 'rest' }, { id = 'S2-in', type = 'transfer' }, { id = 'S2', type = 'special' },
  },
  days = {
    { id = 'd01', label = 'Prologue', date = '2027-01-09', bivouac = 'Loule', legs = { 'P-in', 'P', 'T1', 'F1' }, ferry = 'F1' },
    { id = 'd02', label = 'Transfer', legs = { 'T2' } },
    { id = 'd03', label = 'Rest', kind = 'rest', legs = { 'R' } },
    { id = 'd04', label = 'Stage 2', legs = { 'S2-in', 'S2' } },
  },
}
"""


@pytest.fixture()
def rt():
    r = lupa.LuaRuntime(unpack_returned_tuples=True)
    r.execute(BASE % {"dir": DAKAR_EXT})
    return r


def test_a_one_loop_edition_has_no_days(rt):
    assert rt.eval("days.expand({ edition = 'x', legs = {} })") is None


def test_every_day_is_an_edition_view_of_its_own(rt):
    rt.execute("V = days.expand(base)")
    assert rt.eval("#V") == 4
    assert rt.eval("V[1].edition") == "rally/d01"
    assert rt.eval("V[1].baseEdition") == "rally"
    assert rt.eval("V[1].missionPrefix") == "dk-d01"
    assert rt.eval("V[1].title") == "The Rally: Prologue"
    assert rt.eval("V[1].teleportStart") is True
    assert rt.eval("V[1].shaping.on") is True


def test_a_days_loop_is_its_driven_legs_in_order_and_the_ferry_stays_out(rt):
    rt.execute("V = days.expand(base)")
    assert rt.eval("#V[1].legs") == 3
    assert [rt.eval("V[1].legs[%d].id" % i) for i in (1, 2, 3)] == ["P-in", "P", "T1"]
    assert rt.eval("V[1].ferry.id") == "F1"
    assert rt.eval("V[1].ferry.to.name") == "B"
    assert rt.eval("V[2].ferry") is None
    assert rt.eval("#V[2].legs") == 1


def test_a_rest_day_is_an_entry_without_a_loop(rt):
    rt.execute("V = days.expand(base)")
    assert rt.eval("V[3].kind") == "rest"
    assert rt.eval("#V[3].legs") == 0
    assert rt.eval("V[3].restLeg.id") == "R"
    assert rt.eval("#days.playable(V)") == 3


def test_the_day_after_skips_the_rest_days_and_names_them(rt):
    rt.execute("V = days.expand(base); nxt, rests = days.following(V, V[2])")
    assert rt.eval("nxt.edition") == "rally/d04"
    assert rt.eval("#rests") == 1
    assert rt.eval("rests[1].label") == "Rest"
    rt.execute("last, none = days.following(V, V[4])")
    assert rt.eval("last") is None


def test_find_and_the_ids(rt):
    assert rt.eval("days.find(base, 'rally/d02').index") == 2
    assert rt.eval("days.find(base, 'rally/d99')") is None
    a, b = rt.eval("days.splitId('rally/d02')")
    assert (a, b) == ("rally", "d02")
    a, b = rt.eval("days.splitId('rally')")
    assert (a, b) == ("rally", None)


def test_a_day_that_ends_where_it_began_has_no_end_stall_of_its_own(rt):
    assert rt.eval("days.endStall({ 37.0, -8.0, 5 }, { 37.003, -8.002, 5 }, { 37.002, -8.001, 5 })") is None


def test_the_end_stall_stands_past_the_last_point_along_the_way_on(rt):
    stall = rt.eval("days.endStall({ 37.0, -8.0, 5 }, { 36.0, -3.0, 9 }, { 36.0, -3.001, 8 })")
    # (the way on is east: the last point is east of the one before it)
    assert stall[1] == pytest.approx(36.0, abs=1e-6)
    metres = (stall[2] - (-3.0)) * 111320 * math.cos(math.radians(36.0))
    assert metres == pytest.approx(25.0, abs=0.5)
    assert stall[3] == 9


def test_preload_slice_cuts_a_long_stage_at_the_limit(rt):
    rt.execute("preload = dofile('%s/preload.lua')" % DAKAR_EXT)
    # a line due north, ~111 m per 0.001 degree: 1 degree = 111.32 km
    rt.execute("line = {}; for i = 0, 10 do line[#line + 1] = { 37 + i * 0.1, -8, i * 10 } end")
    rt.execute("cut = preload.slice(line, 40000)")
    n = rt.eval("#cut")
    assert n == 5                       # 0, 0.1, 0.2, 0.3 degrees and the cut inside the next segment
    assert rt.eval("cut[#cut][1]") == pytest.approx(37 + 40000 / 111320, abs=1e-5)
    # a line shorter than the limit comes back whole
    assert rt.eval("#preload.slice(line, 200000)") == 11
