"""Offline tests for preload.lua: the stage corridor prefetch through Earth's route message.

    python -m pytest tests

The server's side is a stub that answers the way earthd.py route() and routeahead.py do: a route_result for the id,
then route_download pushes { total, done, active } until the corridor is in. Pure Lua, needs only lupa.
"""
import math
import os

import pytest

lupa = pytest.importorskip("lupa")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DAKAR_EXT = os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions").replace("\\", "/")

PRELUDE = r"""
math.atan2 = math.atan2 or function(y, x) return math.atan(y, x) end
package.path = package.path .. ';%(dakar)s/?.lua'
preload = require('beamngDakar/preload')
NOW, SENT, RESULTS, LOGS, CONNECTED = 0, {}, {}, {}, true
function newPreload()
  return preload.new({
    send = function(msg) if not CONNECTED then return false end; SENT[#SENT + 1] = msg; return true end,
    take = function(id) local r = RESULTS[id]; RESULTS[id] = nil; return r end,
    now = function() return NOW end,
    log = function(l, m) LOGS[#LOGS + 1] = l .. ' ' .. m end,
  })
end
-- a stage: from A to B, 3 km east along 37.1 N, with the lat/lon/h points of its line every 100 m
LINE = {}
for i = 0, 30 do LINE[#LINE + 1] = { 37.1, -8.0 + i * 100 / (111320 * math.cos(math.rad(37.1))), 100 + i } end
function request(name, shift)
  return { name = name, lat0 = LINE[1][1], lon0 = LINE[1][2], lat1 = LINE[#LINE][1], lon1 = LINE[#LINE][2], line = LINE }
end
-- Earth's router's answer: the same road, shifted north by `shiftM` metres
function routeOf(shiftM)
  local pts = {}
  for i = 0, 30 do pts[#pts + 1] = { 37.1 + shiftM / 110574, LINE[i + 1][2] } end
  return { points = pts, dist = 3000 }
end
"""


@pytest.fixture()
def lua():
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute(PRELUDE % {"dakar": DAKAR_EXT})
    return rt


def logs(lua, needle):
    log = lua.eval("LOGS")
    return [str(log[i]) for i in range(1, len(log) + 1) if needle in str(log[i])]


def test_it_asks_earths_server_for_the_whole_corridor_start_to_finish(lua):
    lua.execute("P = newPreload(); P:start({ request('ss1') })")
    sent = lua.eval("SENT")
    assert len(sent) == 1
    m = sent[1]
    assert m.type == "route" and m.download == "route"                 # the message Earth's GPS sends, for the whole route
    assert m.lat0 == pytest.approx(37.1) and m.lat1 == pytest.approx(37.1) and m.lon1 > m.lon0
    assert m.id > lua.eval("preload.ID_BASE")                          # (an id of ours: Earth's GPS counts up from 1)
    assert lua.eval("P:gateOpen()") is False and lua.eval("P:state().phase") == "planning"


def test_the_gate_opens_when_the_server_reports_the_corridor_in(lua):
    lua.execute("P = newPreload(); P:start({ request('ss1') })")
    lua.execute("RESULTS[SENT[1].id] = routeOf(10); NOW = 2; P:tick()")        # Earth's router has answered
    assert lua.eval("P:state().phase") == "downloading" and lua.eval("P:gateOpen()") is False
    lua.execute("P:onDownload({ total = 40, done = 5, active = true }); NOW = 6; P:tick()")
    assert lua.eval("P:state().done") == 5 and lua.eval("P:state().total") == 40 and lua.eval("P:gateOpen()") is False
    assert logs(lua, "5 of 40")                                          # progress is logged
    lua.execute("P:onDownload({ total = 40, done = 31, active = true })")
    assert lua.eval("P:gateOpen()") is False
    lua.execute("P:onDownload({ total = 40, done = 40, active = false })")      # the server is through
    assert lua.eval("P:gateOpen()") is True and lua.eval("P:state().phase") == "done"
    assert logs(lua, "the corridor is in")


def test_done_is_also_done_at_or_past_total_and_nothing_to_fetch_opens_it_at_once(lua):
    lua.execute("P = newPreload(); P:start({ request('ss1') }); RESULTS[SENT[1].id] = routeOf(0); P:tick()")
    lua.execute("P:onDownload({ total = 12, done = 12, active = true })")
    assert lua.eval("P:gateOpen()") is True
    lua.execute("P = newPreload(); P:start({ request('ss1') }); RESULTS[SENT[2].id] = routeOf(0); P:tick()")
    lua.execute("P:onDownload({ total = 0, done = 0, active = false })")          # the corridor was cached already
    assert lua.eval("P:gateOpen()") is True and lua.eval("P:state().phase") == "done"


def test_a_push_before_the_routes_answer_is_not_ours(lua):
    # (the player's own GPS route, or an older one: only what follows our route's answer counts)
    lua.execute("P = newPreload(); P:start({ request('ss1') })")
    lua.execute("P:onDownload({ total = 3, done = 3, active = false })")
    assert lua.eval("P:gateOpen()") is False


def test_the_gate_gives_up_at_the_cap_and_the_download_goes_on_in_the_server(lua):
    lua.execute("P = newPreload(); P:start({ request('ss1') }); RESULTS[SENT[1].id] = routeOf(0); P:tick()")
    lua.execute("P:onDownload({ total = 1000, done = 10, active = true })")
    lua.execute("NOW = preload.CAP_S - 1; P:tick()")
    assert lua.eval("P:gateOpen()") is False
    lua.execute("NOW = preload.CAP_S + 1; P:tick()")
    assert lua.eval("P:gateOpen()") is True and lua.eval("P:state().phase") == "capped"
    assert logs(lua, "10 of 1000") and logs(lua, "going on")
    assert lua.eval("preload.CAP_S") == 120


def test_no_answer_from_the_router_does_not_hold_the_start(lua):
    lua.execute("P = newPreload(); P:start({ request('ss1') }); NOW = preload.PLAN_WAIT_S + 1; P:tick()")
    assert lua.eval("P:gateOpen()") is True and lua.eval("P:state().phase") == "failed"


def test_a_route_error_does_not_hold_the_start(lua):
    lua.execute("P = newPreload(); P:start({ request('ss1') }); RESULTS[SENT[1].id] = { points = {}, error = 'no route' }; P:tick()")
    assert lua.eval("P:gateOpen()") is True and lua.eval("P:state().phase") == "failed"
    assert logs(lua, "no route")


def test_no_download_push_after_the_answer_does_not_hold_the_start(lua):
    lua.execute("P = newPreload(); P:start({ request('ss1') }); RESULTS[SENT[1].id] = routeOf(0); P:tick()")
    lua.execute("NOW = preload.PUSH_WAIT_S + 1; P:tick()")
    assert lua.eval("P:gateOpen()") is True and lua.eval("P:state().phase") == "failed"


def test_without_a_server_there_is_nothing_to_wait_for(lua):
    lua.execute("CONNECTED = false; P = newPreload(); P:start({ request('ss1') })")
    assert lua.eval("P:gateOpen()") is True and lua.eval("P:state().phase") == "offline"
    assert lua.eval("#SENT") == 0 and logs(lua, "not connected")


def test_no_stages_no_gate(lua):
    lua.execute("P = newPreload(); P:start({})")
    assert lua.eval("P:gateOpen()") is True and lua.eval("#SENT") == 0


def test_the_stages_are_fetched_one_after_the_other_and_the_gate_waits_for_the_first_only(lua):
    # a newer route message supersedes the older on the server: the second is asked once the first is through
    lua.execute("P = newPreload(); P:start({ request('ss1'), request('ss2') })")
    assert lua.eval("#SENT") == 1
    lua.execute("RESULTS[SENT[1].id] = routeOf(0); P:tick(); P:onDownload({ total = 4, done = 4, active = false })")
    assert lua.eval("P:gateOpen()") is True                              # the first is in: the start goes on
    assert lua.eval("#SENT") == 2 and lua.eval("SENT[2].id") > lua.eval("SENT[1].id")
    assert lua.eval("P:state().name") == "ss2" and lua.eval("P:state().phase") == "planning"
    lua.execute("RESULTS[SENT[2].id] = routeOf(0); NOW = 50; P:tick(); P:onDownload({ total = 9, done = 2, active = true })")
    assert lua.eval("P:state().done") == 2 and lua.eval("P:gateOpen()") is True      # (in the background: the gate is not held)
    lua.execute("NOW = preload.CAP_S + 10; P:tick()")                    # (the cap is for the first stage only)
    assert lua.eval("P:state().phase") == "downloading"


def test_how_far_earths_route_strays_from_the_stage_line_is_logged(lua):
    lua.execute("P = newPreload(); P:start({ request('ss1') }); RESULTS[SENT[1].id] = routeOf(25); P:tick()")
    line = logs(lua, "Earth's route is")
    assert line and "3.0 km" in line[0]
    s = lua.eval("P.cur.stray")
    assert s.lineMean == pytest.approx(25, abs=1.5) and s.lineMax == pytest.approx(25, abs=1.5)
    assert s.lineFarShare == 0 or s.lineFarShare == pytest.approx(0, abs=0.01)       # 25 m is within 30 m of it
    assert s.routeKm == pytest.approx(3.0, abs=0.05) and s.lineKm == pytest.approx(3.0, abs=0.05)


def test_a_route_that_takes_another_road_is_measured_as_straying(lua):
    # the router goes 400 m north for the middle third of the way
    lua.execute("""
      local pts = {}
      for i = 0, 30 do
        local d = (i >= 10 and i <= 20) and 400 or 0
        pts[#pts + 1] = { 37.1 + d / 110574, LINE[i + 1][2] }
      end
      ROUTE = { points = pts, dist = 3200 }
      S = preload.stray(pts, LINE)
    """)
    s = lua.eval("S")
    assert s.lineMax == pytest.approx(400, abs=15) and s.routeMax == pytest.approx(400, abs=15)
    assert 0.25 < s.lineFarShare < 0.5
    assert lua.eval("preload.stray({}, LINE)") is None
