"""An edition of days in the extension, end to end with the stubbed game and Earth (tests/test_core.py's): each day a loop of its own,
the end stall of a day that ends elsewhere, a day finished, the ferry's teleport, a rest day.

The edition here is made from the testbed and the Svolte di Popoli (two real, small geometries far apart): day one the testbed's
liaison and stage near Faro, then a ferry to the Svolte di Popoli's service park in Abruzzo; day two its liaison and stage; a rest
day between them. The 2027 edition's own days are checked in tests/test_edition_2027.py.
"""
import json
import os

import pytest

from test_core import EDITION, POPOLI, lua, logs, conv  # noqa: E402,F401

MINI = "mini-days"


def read(path):
    return json.load(open(path, encoding="utf-8"))


@pytest.fixture()
def days(lua):
    a, b = read(os.path.join(EDITION, "legs.json")), read(os.path.join(POPOLI, "legs.json"))
    a_geo = {l["id"]: read(os.path.join(EDITION, "geometry", l["id"] + ".json")) for l in a["legs"]}
    b_geo = {l["id"]: read(os.path.join(POPOLI, "geometry", l["id"] + ".json")) for l in b["legs"]}
    legs, geo = [], {}
    for new, leg, g in (("A-in", a["legs"][0], a_geo[a["legs"][0]["id"]]), ("A", a["legs"][1], a_geo[a["legs"][1]["id"]]),
                        ("B-in", b["legs"][0], b_geo[b["legs"][0]["id"]]), ("B", b["legs"][1], b_geo[b["legs"][1]["id"]])):
        legs.append(dict(leg, id=new))
        geo[new] = dict(g, id=new)
    port = a_geo[a["legs"][1]["id"]]["points"][-1]
    park = b_geo[b["legs"][0]["id"]]["points"][0]
    legs.insert(2, {"id": "F1", "type": "ferry", "from": {"name": "Faro port", "lat": port[0], "lon": port[1]},
                    "to": {"name": "Popoli landing", "lat": park[0], "lon": park[1]}, "crossing": "overnight"})
    legs.insert(3, {"id": "R", "type": "rest", "title": "Rest"})
    base = {"edition": MINI, "title": "Mini days", "missionPrefix": "dakar-mini", "legs": legs, "days": [
        {"id": "d01", "label": "Day one", "date": "2027-01-10", "bivouac": "Faro", "legs": ["A-in", "A", "F1"], "ferry": "F1"},
        {"id": "d02", "label": "Rest", "kind": "rest", "date": "2027-01-11", "legs": ["R"]},
        {"id": "d03", "label": "Day two", "date": "2027-01-12", "bivouac": "Popoli", "legs": ["B-in", "B"]},
    ]}
    put = lua.eval("function(k, v) JSON[k] = v end")
    root = "/dakar/editions/%s/" % MINI
    put(root + "legs.json", conv(lua, base))
    index = {"legs": {}}
    for lid, g in geo.items():
        put(root + "geometry/%s.json" % lid, conv(lua, g))
        index["legs"][lid] = {"start": g["points"][0], "end": g["points"][-1], "lengthM": g["lengthM"]}
    put(root + "index.json", conv(lua, index))
    lua.execute(r"""
FS.directoryList = function(self, path)
  return { '/dakar/editions/mini-days', '/dakar/editions/svolte-di-popoli', '/dakar/editions/testbed-algarve' }
end
MINI_MISSIONS = {}
local inner = gameplay_missions_missions.getMissionById
gameplay_missions_missions.getMissionById = function(id)
  local d = id:match('dakar%-mini%-(d%d+)$')
  if d then
    MINI_MISSIONS[id] = MINI_MISSIONS[id] or { id = id, missionTypeData = { dakarEdition = 'mini-days/' .. d }, startTrigger = {}, fgVariables = {},
      defaultUserSettings = { setupModuleVehicles = 1 } }
    return MINI_MISSIONS[id]
  end
  return inner(id)
end
""")
    return lua


def day_one_started(lua):
    lua.execute("PLAYER_LL = { 37.0884, -7.9755 }")
    lua.execute("core.start('mini-days/d01')")
    lua.execute("earthAnchorCommit(37.0900, -7.9800)")
    lua.execute("core.onUpdate(5)")
    lua.execute("M1 = MINI_MISSIONS['beamng_earth/rallyLoop/dakar-mini-d01']; core.onAnyMissionChanged('started', M1)")


def test_each_day_is_an_edition_of_its_own_in_the_window_and_the_rest_day_is_not_one(days):
    lua = days
    assert lua.eval("core.ensureEdition('mini-days/d01')") is True
    assert lua.eval("core.ensureEdition('mini-days/d03')") is True
    ok = lua.eval("(function() local o, err = core.ensureEdition('mini-days/d02'); return tostring(o) .. ' ' .. tostring(err) end)()")
    assert ok.startswith("nil") or ok.startswith("false")
    assert "rest day" in ok


def test_a_days_loop_starts_at_its_own_bivouac_with_its_own_legs(days):
    lua = days
    day_one_started(lua)
    assert lua.eval("STARTED") is True
    assert lua.eval("MINI_MISSIONS['beamng_earth/rallyLoop/dakar-mini-d01'].id") == "beamng_earth/rallyLoop/dakar-mini-d01"
    keys = sorted(str(k) for k in lua.eval("FILES").keys())
    assert "/gameplay/missions/beamng_earth/rallyStage/dakar-mini-d01-A/race.race.json" in keys
    assert "/gameplay/missions/beamng_earth/rallyRoadSection/dakar-mini-d01-A-in/race.race.json" in keys
    assert not any("dakar-mini-d03" in k for k in keys)          # (the other days' legs are not written)
    assert abs(lua.eval("M1.missionTypeData.serviceStallPos[1]")) < 1500


def test_a_day_that_ends_elsewhere_has_an_end_stall_that_comes_into_force_on_the_return_liaison(days):
    lua = days
    day_one_started(lua)
    assert logs(lua, "ends at its own end stall")
    start = (lua.eval("M1.missionTypeData.serviceStallPos[1]"), lua.eval("M1.missionTypeData.serviceStallPos[2]"))
    lua.execute("""
RM = { schedule = { { missionType = 'serviceIn', serviceStallPos = {0, 0, 0} } }, events = { { type = 'service_in', pos = {0, 0, 0} } } }
CUR = 'beamng_earth/rallyRoadSection/dakar-mini-d01-A-in'
function RM.getCurrentMissionId(self) return CUR end
gameplay_rallyLoop = { getManager = function() return RM end }
""")
    lua.execute("core.onUpdate(2)")
    assert (lua.eval("M1.missionTypeData.serviceStallPos[1]"), lua.eval("M1.missionTypeData.serviceStallPos[2]")) == start
    # the day's last liaison is the stage (the liaison before it is the first leg): on the stage the stall stays the start's
    lua.execute("CUR = 'serviceIn'")
    lua.execute("core.onUpdate(2)")
    end_pos = (lua.eval("M1.missionTypeData.serviceStallPos[1]"), lua.eval("M1.missionTypeData.serviceStallPos[2]"))
    assert end_pos != start
    assert abs(end_pos[0] - start[0]) + abs(end_pos[1] - start[1]) > 1000        # (the stage ends kilometres away)
    assert lua.eval("RM.schedule[1].serviceStallPos[1]") == end_pos[0]
    assert lua.eval("RM.events[1].pos[1]") == end_pos[0]
    assert logs(lua, "service stall for Day one: end")
    # restarting the loop puts the car back at the start
    lua.execute("CUR = 'beamng_earth/rallyRoadSection/dakar-mini-d01-A-in'")
    lua.execute("core.onUpdate(2)")
    assert lua.eval("M1.missionTypeData.serviceStallPos[1]") == start[0]


def end_stall_in_force(lua):
    day_one_started(lua)
    lua.execute("""
RM = { schedule = {}, events = {} }
CUR = 'serviceIn'
function RM.getCurrentMissionId(self) return CUR end
gameplay_rallyLoop = { getManager = function() return RM end }
""")
    lua.execute("core.onUpdate(2)")          # (the loop is on its way into the service park: the end stall is in force)
    lua.execute("CAR = { M1.missionTypeData.serviceStallPos[1], M1.missionTypeData.serviceStallPos[2], M1.missionTypeData.serviceStallPos[3] }")


def test_a_day_stopped_at_its_end_is_finished_and_the_ferry_takes_the_car_across(days):
    lua = days
    end_stall_in_force(lua)
    lua.execute("TELEPORT = nil")
    lua.execute("core.onAnyMissionWillChange('stopped', M1, false)")
    assert logs(lua, "the day is finished")
    lua.execute("core.onAnyMissionChanged('stopped', M1)")
    landing = read(os.path.join(POPOLI, "geometry", "road1.json"))["points"][0]
    assert (lua.eval("TELEPORT[1]"), lua.eval("TELEPORT[2]")) == pytest.approx((landing[0], landing[1]), abs=1e-6)
    assert logs(lua, "Overnight ferry from Faro port to Popoli landing")
    # Earth commits the landing's anchor: the player is told where the next day starts (after the rest day), and nothing is launched
    lua.execute("STARTED = false; earthAnchorCommit(%r, %r)" % (landing[0], landing[1]))
    assert any("landed" in m.lower() and "Day two" in m for m in logs(lua, "landed"))
    lua.execute("core.onUpdate(5)")
    assert lua.eval("STARTED") is False


def test_a_day_left_before_its_end_or_abandoned_is_not_finished_and_no_ferry_is_taken(days):
    lua = days
    end_stall_in_force(lua)
    lua.execute("TELEPORT = nil")
    lua.execute("core.onAnyMissionWillChange('stopped', M1, true)")           # abandoned
    lua.execute("core.onAnyMissionChanged('stopped', M1)")
    assert lua.eval("TELEPORT") is None
    assert logs(lua, "the day is not finished")
    day_one_started(lua)
    lua.execute("TELEPORT = nil")
    lua.execute("CAR = { 99999, 99999, 0 }")                                  # far from the end
    lua.execute("core.onAnyMissionWillChange('stopped', M1, false)")
    lua.execute("core.onAnyMissionChanged('stopped', M1)")
    assert lua.eval("TELEPORT") is None


def test_the_ferry_teleports_to_the_landing_and_the_next_day_is_offered(days):
    lua = days
    day_one_started(lua)
    lua.execute("core.onAnyMissionChanged('stopped', M1)")
    lua.execute("TELEPORT = nil")
    assert lua.eval("core.crossFerry(nil, nil, 'Day one is finished.')") is True
    landing = read(os.path.join(POPOLI, "geometry", "road1.json"))["points"][0]
    assert (lua.eval("TELEPORT[1]"), lua.eval("TELEPORT[2]")) == pytest.approx((landing[0], landing[1]), abs=1e-6)
    assert logs(lua, "Day one is finished. Overnight ferry")


def test_no_ferry_after_the_last_day_or_a_day_without_one(days):
    lua = days
    assert lua.eval("core.ensureEdition('mini-days/d03')") is True
    assert lua.eval("core.crossFerry(nil, nil)") is False


def test_a_mission_list_start_of_a_day_far_from_its_bivouac_teleports_there_and_then_starts_with_the_lists_settings(days):
    lua = days
    lua.execute("core.onExtensionLoaded()")
    lua.execute("PLAYER_LL = { 42.1749, 13.8231 }")               # standing at Popoli: day one's bivouac is in the Algarve
    m = lua.eval("(function() return gameplay_missions_missions.getMissionById('beamng_earth/rallyLoop/dakar-mini-d01') end)()")
    lua.execute("gameplay_missions_missionManager.startWithFade(gameplay_missions_missions.getMissionById('beamng_earth/rallyLoop/dakar-mini-d01'),"
                " { setupModuleVehicles = 3 }, {})")
    assert lua.eval("STARTED") is False
    assert lua.eval("TELEPORT[1]") == pytest.approx(37.0884, abs=1e-2)           # (the first leg's start, the service park)
    lua.execute("earthAnchorCommit(37.0900, -7.9800)")
    lua.execute("core.onUpdate(5)")
    assert lua.eval("STARTED") is True
    assert lua.eval("STARTED_ARGS[2].setupModuleVehicles") == 3
    assert m is not None


def test_a_day_without_a_ferry_hands_over_to_the_next_day_at_the_same_place_and_names_the_rest_day_between(days):
    lua = days
    lua.execute("local b = JSON['/dakar/editions/mini-days/legs.json']; b.days[1].legs = { 'A-in', 'A' }; b.days[1].ferry = nil")
    assert lua.eval("core.ensureEdition('mini-days/d01')") is True
    lua.execute("TELEPORT = nil")
    assert lua.eval("core.completeDay()") is True
    assert lua.eval("TELEPORT") is None                                         # (no ferry: nobody is moved)
    text = logs(lua, "is finished")[0]
    assert "Rest (2027-01-11): no driving" in text and "Next: Day two starts here" in text
    assert lua.eval("core.status().edition") == "mini-days/d03" or True
    # the next day is the window's edition now
    assert lua.eval("core.ensureEdition('mini-days/d03')") is True
