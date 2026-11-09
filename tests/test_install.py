"""tools/install.py puts every edition and every edition's missions where the game reads them (GitHub #40: a second
edition), by copy and by link."""
import importlib.util
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EDITIONS = ["svolte-di-popoli", "testbed-algarve"]
MISSIONS = [("rallyLoop", "dakar-popoli"), ("rallyRoadSection", "dakar-popoli-road1"), ("rallyStage", "dakar-popoli-ss1"),
            ("rallyLoop", "dakar-testbed"), ("rallyRoadSection", "dakar-testbed-road1"), ("rallyStage", "dakar-testbed-ss1")]


@pytest.fixture()
def install():
    spec = importlib.util.spec_from_file_location("dakar_install", os.path.join(ROOT, "tools", "install.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def user_dir(tmp_path):
    (tmp_path / "mods" / "unpacked" / "beamng_earth").mkdir(parents=True)
    return str(tmp_path)


def run(install, monkeypatch, user_dir, *args):
    monkeypatch.setattr(sys, "argv", ["install.py", "--user-dir", user_dir, *args])
    install.main()
    return os.path.join(user_dir, "mods", "unpacked", "beamng_dakar")


def check(dst):
    for edition in EDITIONS:
        base = os.path.join(dst, "dakar", "editions", edition)
        assert os.path.isfile(os.path.join(base, "legs.json"))
        for leg in json.load(open(os.path.join(base, "legs.json"), encoding="utf-8"))["legs"]:
            assert os.path.isfile(os.path.join(base, "geometry", leg["id"] + ".json"))
            if leg["type"] == "special":
                assert os.path.isfile(os.path.join(base, "closures", leg["id"] + ".json"))
    for kind, name in MISSIONS:
        folder = os.path.join(dst, "gameplay", "missions", "beamng_earth", kind, name)
        info = json.load(open(os.path.join(folder, "info.json"), encoding="utf-8"))
        assert info["missionType"] == kind
        assert os.path.isfile(os.path.join(folder, "preview.jpg")) and os.path.isfile(os.path.join(folder, "thumbnail.jpg"))
    assert os.path.isfile(os.path.join(dst, "lua", "ge", "extensions", "beamngDakar", "core.lua"))


def test_a_copy_install_has_both_editions_and_their_missions(install, monkeypatch, user_dir):
    check(run(install, monkeypatch, user_dir))


def test_a_linked_install_has_both_editions_and_their_missions(install, monkeypatch, user_dir):
    dst = run(install, monkeypatch, user_dir, "--link")
    check(dst)
    run(install, monkeypatch, user_dir)              # a copy replaces the links, as links, and leaves the repo alone
    check(dst)
    assert os.path.isfile(os.path.join(ROOT, "editions", "svolte-di-popoli", "legs.json"))


def test_each_loop_names_its_edition_and_its_legs_missions():
    # how the extension maps a mission to its edition: missionTypeData.dakarEdition, and the loop's mission id is
    # beamng_earth/rallyLoop/<missionPrefix> (core.lua loopMissionId); the stages and road sections it runs are <prefix>-<leg id>
    missions = os.path.join(ROOT, "mod", "beamng_dakar", "gameplay", "missions", "beamng_earth")
    for edition in EDITIONS:
        legs = json.load(open(os.path.join(ROOT, "editions", edition, "legs.json"), encoding="utf-8"))
        loop = json.load(open(os.path.join(missions, "rallyLoop", legs["missionPrefix"], "info.json"), encoding="utf-8"))
        assert loop["missionTypeData"]["dakarEdition"] == edition
        stage = 0
        for leg in legs["legs"]:
            kind = "rallyStage" if leg["type"] == "special" else "rallyRoadSection"
            assert os.path.isfile(os.path.join(missions, kind, legs["missionPrefix"] + "-" + leg["id"], "info.json"))
            if leg["type"] == "special":
                stage += 1
                assert "%s-%s)" % (legs["missionPrefix"], leg["id"]) in loop["missionTypeData"]["stage%d_rallyStage" % stage]
                assert ("%s-road%d)" % (legs["missionPrefix"], stage)) in loop["missionTypeData"]["stage%d_rallyRoadSection" % stage]
