"""GitHub #71: the wall of ground at the exit of the short bridge after the A25 underpass (Popoli SS1, deck 330 to 360 m along line 1, a metre
over the ground).

PR #70 (GitHub #69) met the ground to a deck's top at its ends with a ramp whose height was the deck's top minus the ground AT THE DECK'S END, the
edge of the ditch under it, and applied that height to the ground of the road beyond, which Earth had already graded to the deck's level: the road
past the end was raised by the ditch's depth, a face of metres across the road (the game's log: a stretch 'deck end, 360 to 420 m, +2.2 m'). The
ground past a deck end now follows the deck's top, with the ground of the road beyond measured along it (lowered where it is over the deck, raised
where under it) and joined to it with the grade and grade change of a road.

    python -m pytest tests
"""
import pytest

pytest.importorskip("lupa")

from test_shape import lua, setup, shape_it, world  # noqa: E402,F401  (the stub block and its fixture)

# A 6 percent grade with a ditch under the deck, from x = 95 to 120.6 and 3.2 m deep, with sides 1 m wide: the edge under the end of the deck (the
# last point of the line on it, x = 120) is far lower than the road beyond it, which Earth graded (the plane). The deck's top is 0.5 m over the
# road, from x = 96 to 120.4.
DITCH = ("function GROUND(x, y) local z = 100 + 0.06 * x + 0.1 * y; if x > 95 and x < 120.6 then "
         "local t = math.min((x - 95) / 1, (120.6 - x) / 1, 1); z = z - 3.2 * t * t * (3 - 2 * t) end; return z + (RISE and RISE(x, y) or 0) end")
DECK = "DECK = function(x, y, g) if x > 96 and x < 120.4 then return 100 + 0.06 * x + 0.1 * y + 0.5 end end"
FROM = 122          # (the road past the deck's end, x)


def top(x):
    return 100 + 0.06 * x + 0.5


def earth(lua, x, y=0):
    return lua.eval("GROUND(%r, %r)" % (float(x), float(y)))


def profile(lua, x0, x1, y=0):
    return [(x, world(lua, x, y)) for x in range(x0, x1 + 1)]


def scene(lua, rise=None):
    lua.execute("BUMPS = {}")
    if rise:
        lua.execute("RISE = " + rise)
    lua.execute(DITCH)
    lua.execute(DECK)
    setup(lua, line="roadLine(40, 220, 110)")
    shape_it(lua)


def steps(zs):
    return [abs(b - a) for (_, a), (_, b) in zip(zs, zs[1:])]


def grade_change(zs, step=10):
    """The largest change of grade per metre of a profile every metre, from the heights `step` metres either side."""
    z = [v for _, v in zs]
    return max(abs(z[k - step] - 2 * z[k] + z[k + step]) / (step * step) for k in range(step, len(z) - step))


def limit(lua):
    return lua.eval("require('beamngDakar/curate').MAX_GRADE_CHANGE")


def logs(lua):
    t = lua.eval("LOGS")
    return [str(t[i]) for i in range(1, len(t) + 1)]


def test_the_ground_past_a_decks_end_does_not_stand_over_the_deck_when_the_ditch_under_it_is_deep(lua):
    scene(lua)
    assert lua.eval("logged('is a deck the road is on, left alone (the ground meets its top at both ends)')")
    p = profile(lua, FROM, 190)
    # the wall of PR #70: the road past the end raised by the ditch's depth at the end, 2 m over the deck's top line
    assert max(z - top(x) for x, z in p) < 0.1
    assert world(lua, FROM, 0) == pytest.approx(top(FROM), abs=0.1)         # (the ground meets the deck's top at its end)
    # ... no step in it, and the grade change of a road
    assert max(steps(p)) < 0.15
    assert grade_change(profile(lua, FROM, 200)) <= limit(lua) * 1.1
    # the road beyond the ramp is as Earth made it, and the ditch under the deck too
    assert world(lua, 215, 0) == pytest.approx(earth(lua, 215), abs=1e-6)
    for x in (100, 108, 116):
        assert world(lua, x, 0) == pytest.approx(earth(lua, x), abs=1e-6)
    # across the road (3 m to either side) the ground has Earth's cross slope on the same ramp: no ridge over the deck's end
    for y in (-3, 3):
        assert world(lua, 125, y) - (top(125) + 0.1 * y) < 0.15


def test_the_approach_is_met_too_when_the_ditch_is_too_deep_for_the_ground_at_the_end(lua):
    scene(lua)
    # (the deck begins at x = 96; the road's ground is 0.5 m under its top, the ditch's edge at the lip 3.7 m: the ground that goes up to it is met)
    p = profile(lua, 70, 94)
    assert max(steps(p)) < 0.15
    assert world(lua, 94, 0) == pytest.approx(top(94), abs=0.1)
    assert world(lua, 20, 0) == pytest.approx(earth(lua, 20), abs=1e-6)
    assert grade_change(profile(lua, 40, 94)) <= limit(lua) * 1.1


def test_the_ground_of_a_bank_over_the_deck_is_cut_down_to_it(lua):
    # a hillside that rises right after the bridge (the Svolte climb to the hairpins): the road's ground past the end is 1 m over the deck's top
    # and the ramp lowers it, no wall across the road
    scene(lua, rise="function(x, y) if x >= 121 then return 1.5 end return 0 end")
    p = profile(lua, FROM, 200)
    assert world(lua, FROM, 0) == pytest.approx(top(FROM), abs=0.15)
    assert max(z - top(x) for x, z in profile(lua, FROM, 130)) < 0.35
    assert max(steps(p)) < 0.15
    assert grade_change(p) <= limit(lua) * 1.1
    assert world(lua, 215, 0) == pytest.approx(earth(lua, 215), abs=1e-6)
    assert lua.eval("E:status().capped") == 0


def test_a_hillside_that_climbs_steeper_than_the_deck_is_joined_with_a_road_grade_change(lua):
    scene(lua, rise="function(x, y) if x >= 122 then return 0.12 * (x - 122) end return 0 end")
    p = profile(lua, FROM, 220)
    assert max(steps(p)) < 0.3
    assert world(lua, FROM, 0) == pytest.approx(top(FROM), abs=0.15)
    assert grade_change(p) <= limit(lua) * 1.1
    assert world(lua, 220, 0) == pytest.approx(earth(lua, 220), abs=1e-6)       # (the ramp is as long as the line goes, and meets the ground at its end)
    assert not logged_warning(lua)


def logged_warning(lua):
    return any("WARNING" in line for line in logs(lua))


def test_the_guard_warns_about_a_ground_the_ramp_could_not_bring_to_the_deck(lua):
    # a hillside 1 m over the deck right past its end, and a cap on the change of a cell (a tenth of what it asks, as the cap is 8 m at a deck end
    # and a wall that big is not made by a bridge): the ground is left standing over what was asked for, and the check along the road says so
    lua.execute("shape.OVERPASS_CHANGE_M = 0.4")
    scene(lua, rise="function(x, y) if x >= 121 then return 1.5 end return 0 end")
    warn = [line for line in logs(lua) if "WARNING" in line and "exit deck end at x 120, y 0" in line]
    assert warn and "off the height asked for" in warn[0]


def test_a_usual_deck_end_has_no_warning(lua):
    scene(lua)
    assert not logged_warning(lua)


def test_every_deck_end_has_its_profile_in_the_log(lua):
    scene(lua)
    prof = [line for line in logs(lua) if "deck end profile" in line]
    assert len(prof) == 2 and "(approach," in prof[0] and "(exit," in prof[1]
    line = prof[1]
    assert "earth/target/written/ray" in line
    # the profile every 5 m from the end: the ground under the deck's end is the ditch (-2.x), the road beyond is the deck's top
    entries = {int(e.split(":")[0]): e.split(":")[1].split("/") for e in line.split("over the top line of the deck going on, ")[1].split()}
    assert min(entries) == -10 and 0 in entries and 5 in entries and 10 in entries
    earth5, target5, written5 = (float(v) for v in entries[5][:3])
    assert target5 == pytest.approx(0.0, abs=0.1) and written5 == pytest.approx(0.0, abs=0.1)      # (the ground follows the deck's top line)
    assert earth5 == pytest.approx(-0.5, abs=0.01)                                                 # (Earth had the road 0.5 m under it)
    assert entries[5][3] == "-"                                                                    # (no collider there: the stub's ray finds none)
    assert entries[0][2] != "-" and float(entries[-10][3]) == pytest.approx(0.0, abs=0.01)         # (the deck's own top is what a ray hits on it)
