"""API or internals: what Dakar asks of BeamNG.Earth goes through Earth 1.9.0's modding API (beamngEarth_api)
when Earth has it, and through Earth's own internals (1.8.2) when it does not.

earthapi.lua is tested on its own with both Earths, and the extension (core.lua) with a stub of the 1.9.0 API next to the
stubs test_core.py has for 1.8.2 (which stay the "no API" case: every test there is the internals path). The stub has
the shape docs/ADDONS.md gives (version / has / ready / position / teleport / gpsSet / gpsClear / gps / notify /
addonOptions), as the real api.lua of v1.9.0-beta returns them.
"""
import os

import pytest

lupa = pytest.importorskip("lupa")

from test_core import DAKAR_EXT, lua, logs   # noqa: E402,F401  (the extension's stubs: the "no API" Earth; skips without Earth)

API = r"""
API_CALLS, API_MESSAGES, API_READY, API_TELEPORT, API_REFUSAL = {}, {}, true, nil, nil
GPS, OPTIONS = nil, nil
FEATURES = { pose = true, place = true, teleport = true, gps = true, notify = true, events = true, options = true }
beamngEarth_api = {
  version = function() return 1, 1 end,
  has = function(name) return FEATURES[name] == true end,
  ready = function() return API_READY end,
  position = function() return PLAYER_LL[1], PLAYER_LL[2] end,
  teleport = function(lat, lon, name)
    API_CALLS[#API_CALLS + 1] = 'teleport'
    if API_REFUSAL then return false, API_REFUSAL end
    API_TELEPORT = { lat, lon, name }
    return true
  end,
  gpsSet = function(lat, lon, name) GPS = { lat = lat, lon = lon, name = name }; return true end,
  gpsClear = function() GPS = nil end,
  gps = function() return GPS end,
  notify = function(text, seconds) API_MESSAGES[#API_MESSAGES + 1] = text; return true end,
  addonOptions = function(id) return id == 'beamng-dakar' and OPTIONS or nil end,
}
"""


@pytest.fixture()
def api(lua):
    lua.execute(API)
    return lua


# --- earthapi.lua on its own ---------------------------------------------------------------------------------------

def test_without_the_api_earthapi_asks_earths_internals(lua):
    lua.execute("ea = require('beamngDakar/earthapi')")
    assert lua.eval("ea.via()") == "internals"
    lat, lon = lua.eval("ea.position()")
    assert (lat, lon) == pytest.approx((lua.eval("PLAYER_LL[1]"), lua.eval("PLAYER_LL[2]")))
    assert lua.eval("ea.ready()") is True and lua.eval("ea.options()") is None
    assert lua.eval("ea.gpsSet(1, 2, 'x')") is False
    lua.execute("ea.notify('hello', 5)")
    assert lua.eval("MESSAGES[#MESSAGES]") == "hello"
    # Earth 1.8.2's teleport, kept out of the recent places as before
    lua.execute("RECORDED = nil; beamngEarth_core.teleport = function(a, b, c, opts) RECORDED = opts end")
    assert lua.eval("(ea.teleport(1, 2, 'x'))") is True and lua.eval("RECORDED.record") is False


def test_with_the_api_earthapi_asks_the_api(api):
    api.execute("ea = require('beamngDakar/earthapi')")
    assert api.eval("ea.via('teleport')") == "api"
    api.execute("ea.notify('hello', 5)")
    assert api.eval("API_MESSAGES[1]") == "hello" and api.eval("#MESSAGES") == 0
    assert api.eval("(ea.teleport(1, 2, 'x'))") is True
    assert api.eval("API_TELEPORT[3]") == "x" and api.eval("TELEPORT") is None
    api.execute("API_REFUSAL = 'Loading the ground: wait a moment'")
    ok, why = api.eval("ea.teleport(1, 2, 'x')")
    assert ok is False and why == "Loading the ground: wait a moment"
    api.execute("API_READY = false")
    assert api.eval("ea.ready()") is False
    api.execute("OPTIONS = { groundShaping = false }")
    assert api.eval("ea.options().groundShaping") is False


def test_the_silent_teleport_is_always_earths_own_call(api):
    # the API's teleport is the map's Go and shows in the recent places; the one that only puts the player back on the
    # ground where they are must not
    api.execute("ea = require('beamngDakar/earthapi')")
    assert api.eval("(ea.teleport(1, 2, 'x', true))") is True
    assert api.eval("TELEPORT[1]") == 1 and api.eval("#API_CALLS") == 0


def test_only_version_1_and_only_the_features_it_has_are_used(api):
    api.execute("ea = require('beamngDakar/earthapi')")
    api.execute("FEATURES.teleport = nil")                          # (an API without it: Earth's own call)
    assert api.eval("ea.via('teleport')") == "internals"
    assert api.eval("(ea.teleport(1, 2, 'x'))") is True and api.eval("#API_CALLS") == 0
    api.execute("beamngEarth_api.version = function() return 2, 0 end")      # (a new major version: not ours to use)
    assert api.eval("ea.via()") == "internals" and api.eval("ea.options()") is None
    api.execute("beamngEarth_api = {}")                              # (something that is not the API)
    assert api.eval("ea.via()") == "internals"


def test_the_gps_is_cleared_only_where_we_set_it(api):
    api.execute("ea = require('beamngDakar/earthapi')")
    api.execute("ea.gpsSet(42.17, 13.83, 'Popoli')")
    api.execute("ea.gpsClearIf(40.0, 10.0)")                        # the player chose another place since
    assert api.eval("GPS.name") == "Popoli"
    api.execute("ea.gpsClearIf(42.17, 13.83)")
    assert api.eval("GPS") is None


# --- the extension on the API ---------------------------------------------------------------------------------------

def test_a_start_teleports_through_the_api_and_launches_as_before(api):
    api.execute("core.start('testbed-algarve')")
    assert api.eval("API_TELEPORT[1]") == pytest.approx(37.0870, abs=1e-3)
    assert str(api.eval("API_TELEPORT[3]")).startswith("Dakar: ")      # (the name Earth's recent places show)
    assert api.eval("TELEPORT") is None                              # Earth's internal call was not made
    assert api.eval("STARTED") is False
    api.execute("earthAnchorCommit(37.0900, -7.9800)")               # the anchor tap is still how the commit is seen
    api.execute("core.onUpdate(5)")
    assert api.eval("STARTED") is True


def test_a_teleport_earth_refuses_says_why_and_leaves_nothing_waiting(api):
    api.execute("API_REFUSAL = 'Load the BeamNG.Earth level first'")
    api.execute("core.start('testbed-algarve')")
    assert "Load the BeamNG.Earth level first" in str(api.eval("API_MESSAGES[#API_MESSAGES]"))
    api.execute("earthAnchorCommit(37.0900, -7.9800); core.onUpdate(5)")
    assert api.eval("STARTED") is False and not logs(api, "writing the route")
    api.execute("API_REFUSAL = nil")                                  # (the next try goes through)
    api.execute("core.start('testbed-algarve')")
    api.execute("earthAnchorCommit(37.0900, -7.9800); core.onUpdate(5)")
    assert api.eval("STARTED") is True


def test_messages_go_to_the_api_and_the_position_is_the_apis(api):
    api.execute("core.onExtensionLoaded()")
    api.execute("PLAYER_LL = { 38.7, -9.15 }")                       # Lisbon
    api.execute("gameplay_missions_missionManager.startWithFade(MISSION, { a = 1 }, {})")
    msg = str(api.eval("API_MESSAGES[#API_MESSAGES]"))
    assert "service park" in msg and "south-east" in msg and api.eval("#MESSAGES") == 0
    assert api.eval("STARTED") is False


def test_far_from_the_service_park_the_gps_leads_there_until_the_loop_starts(api):
    api.execute("core.onExtensionLoaded()")
    api.execute("PLAYER_LL = { 38.7, -9.15 }")
    api.execute("gameplay_missions_missionManager.startWithFade(MISSION, { a = 1 }, {})")
    assert api.eval("GPS.lat") == pytest.approx(37.0870, abs=1e-3)
    assert "GPS" in str(api.eval("API_MESSAGES[#API_MESSAGES]"))
    api.execute("PLAYER_LL = SERVICE_LL")                            # (they drove there)
    api.execute("gameplay_missions_missionManager.startWithFade(MISSION, { a = 1 }, {})")
    assert api.eval("GPS") is None and api.eval("STARTS") == 1       # the loop starts and the GPS is off


def test_without_the_api_there_is_no_gps_and_the_message_is_unchanged(lua):
    lua.execute("core.onExtensionLoaded()")
    lua.execute("PLAYER_LL = { 38.7, -9.15 }")
    lua.execute("gameplay_missions_missionManager.startWithFade(MISSION, { a = 1 }, {})")
    assert "GPS" not in str(lua.eval("MESSAGES[#MESSAGES]"))


def test_a_main_menu_start_waits_until_earth_says_it_can_teleport(api):
    api.execute("core.onExtensionLoaded()")
    api.execute("API_READY = false")                                  # (the ground under the player is still loading)
    api.execute("gameplay_missions_missionManager.startWithLoadingLevel(MISSION, nil, nil, 'gameplaySelector')")
    api.execute("core.onUpdate(1); core.onUpdate(1)")
    assert api.eval("API_TELEPORT") is None and api.eval("LOADING.beamngDakar") is True
    api.execute("API_READY = true; core.onUpdate(1)")
    assert api.eval("API_TELEPORT[1]") == pytest.approx(37.0870, abs=1e-3)


def test_the_player_put_back_on_the_ground_is_not_a_recorded_teleport(api):
    api.execute("core.onExtensionLoaded()")
    api.execute("FOREGROUND = 'beamng_earth/rallyLoop/dakar-testbed'")
    api.execute("core.onAnyMissionChanged('started', MISSION)")
    api.execute("core.onUpdate(1)")
    assert api.eval("TELEPORT[1]") == api.eval("PLAYER_LL[1]") and api.eval("#API_CALLS") == 0


def test_the_add_ons_options_set_the_ground_shaping(api):
    api.execute("dumps = function(t) return t end")                   # (status() as a table)
    api.execute("core.onExtensionLoaded()")
    api.execute("core.onUpdate(3)")                                   # (Earth has no list of add-ons yet: nothing)
    assert api.eval("core.status().shaping") is None
    api.execute("OPTIONS = { groundShaping = false }")
    api.execute("core.onUpdate(3)")                                   # the list is there: the saved value
    assert api.eval("core.status().shaping.enabled") is False
    api.execute("core.onBeamNGEarthAddonOptions({ id = 'beamng-dakar', values = { groundShaping = true } })")
    assert api.eval("core.status().shaping.enabled") is True
    api.execute("core.onBeamNGEarthAddonOptions({ id = 'another-addon', values = { groundShaping = false } })")
    assert api.eval("core.status().shaping.enabled") is True          # (another add-on's options are not ours)
    api.execute("core.onBeamNGEarthAddonOptions({ id = 'beamng-dakar', values = {} })")
    assert api.eval("core.status().shaping.enabled") is True          # (a value that is not there changes nothing)


def test_earths_1_9_0_taps_are_the_same_as_1_8_2s(api):
    # the API has no event for a rebase, a terrain block or the corridor download: those taps stay, API or not
    api.execute("core.onExtensionLoaded()")
    log = logs(api, "tap installed on")
    names = {line.rsplit(" ", 1)[1] for line in log}
    assert {"beamngEarth/rebase.moveVehicles", "beamngEarth/mpanchor.announceOwn"} <= names
    assert os.path.isfile(os.path.join(DAKAR_EXT, "beamngDakar", "earthapi.lua"))
