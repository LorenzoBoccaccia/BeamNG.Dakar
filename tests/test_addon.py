"""BeamNG.Dakar as a BeamNG.Earth 1.9.0 add-on (GitHub #51): the release file, its layout and its manifest, against what
Earth's loader checks (server/addons.py at v1.9.0-beta: check_mod_zip, parse_options, _repo_id; server/addonrun.py
mod_code). Those rules are written out here, not imported: Earth's server code is not run by these tests."""
import importlib.util
import json
import os
import re
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MANIFEST = os.path.join(ROOT, "mod", "beamng_dakar", "beamngEarth_addon.json")

# addons.py MOD_TOP: the game's folders; a mod zip with none of them at its top is refused ("not laid out as a mod")
MOD_TOP = ("lua/", "ui/", "scripts/", "vehicles/", "levels/", "art/", "gameplay/", "settings/", "mods_data/",
           "campaigns/", "core/", "flowEditor/", "locales/")
ZIP_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.zip")          # addons.py _ZIP (a modFile)
OPT_KEY = re.compile(r"[a-z][A-Za-z0-9_]{0,30}")                           # addons.py _OPT_KEY


@pytest.fixture()
def install():
    spec = importlib.util.spec_from_file_location("dakar_install", os.path.join(ROOT, "tools", "install.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def addon_zip(install, tmp_path):
    path = str(tmp_path / "out" / install.ADDON_ZIP)
    install.build_zip(path)
    return path


def names(path):
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        return [n.replace("\\", "/") for n in z.namelist()]


def test_the_zip_is_a_mod_zip_the_loader_accepts(addon_zip):
    ns = names(addon_zip)
    assert ns
    for n in ns:       # addons.check_mod_zip: nothing absolute, no .., no drive letters
        assert not n.startswith(("/", "\\")) and ".." not in n.split("/") and ":" not in n
    assert any(n.startswith(MOD_TOP) for n in ns)
    # the game's folders at its top: nothing inside a folder of the mod's own (Earth's check, and the game would not mount it)
    assert {n.split("/")[0] for n in ns} <= {"lua", "scripts", "gameplay", "dakar", "beamngEarth_addon.json"}


def test_the_zip_name_is_the_one_earth_gives_this_repository(install):
    # a repository pasted into Settings > Add-ons: its id is the repository's name in lower case (addons._repo_id), and
    # its mod file <id>.zip; the release's file must be that name, and the extension asks its options under the id
    repo_id = re.sub(r"[^a-z0-9]+", "-", "BeamNG.Dakar".lower()).strip("-")
    assert install.ADDON_ZIP == repo_id + ".zip" and ZIP_NAME.fullmatch(install.ADDON_ZIP)
    code = open(os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions", "beamngDakar", "earthapi.lua"),
                encoding="utf-8").read()
    assert "M.ADDON_ID = '%s'" % repo_id in code


def test_the_zip_has_the_code_the_modscript_the_missions_and_every_edition(addon_zip):
    ns = set(names(addon_zip))
    assert "lua/ge/extensions/beamngDakar/core.lua" in ns and "lua/ge/extensions/beamngDakar/earthapi.lua" in ns
    assert "scripts/beamng_dakar/modScript.lua" in ns
    assert "beamngEarth_addon.json" in ns
    for edition in ("svolte-di-popoli", "testbed-algarve"):
        legs = json.load(open(os.path.join(ROOT, "editions", edition, "legs.json"), encoding="utf-8"))
        assert "dakar/editions/%s/legs.json" % edition in ns
        for leg in legs["legs"]:
            assert "dakar/editions/%s/geometry/%s.json" % (edition, leg["id"]) in ns
            if leg["type"] == "special":
                assert "dakar/editions/%s/closures/%s.json" % (edition, leg["id"]) in ns
        assert "gameplay/missions/beamng_earth/rallyLoop/%s/info.json" % legs["missionPrefix"] in ns


def test_the_extensions_the_loader_would_unload_are_ours_and_not_earths(addon_zip):
    # addonrun.mod_code: the GE extensions of a zip (lua/ge/extensions/a/b.lua -> a_b), unloaded before an update and
    # on Turn off / Remove; BeamNG.Earth's own names (beamngEarth...) are never listed, and a file that replaces one of the
    # game's own is only applied after a restart
    ext = [n[len("lua/ge/extensions/"):-4].replace("/", "_") for n in names(addon_zip)
           if n.startswith("lua/ge/extensions/") and n.endswith(".lua")]
    assert "beamngDakar_core" in ext and all(e.startswith("beamngDakar_") for e in ext)


def test_the_zip_is_the_copy_install(install, tmp_path, monkeypatch, addon_zip):
    # one list of files for both (install.mod_files): the add-on cannot differ from the unpacked install
    user = tmp_path / "user"
    (user / "mods" / "unpacked" / "beamng_earth").mkdir(parents=True)
    monkeypatch.setattr(sys, "argv", ["install.py", "--user-dir", str(user)])
    install.main()
    dst = user / "mods" / "unpacked" / "beamng_dakar"
    copied = sorted(os.path.relpath(os.path.join(b, f), dst).replace(os.sep, "/") for b, _d, fs in os.walk(dst) for f in fs)
    assert copied == sorted(set(names(addon_zip)))


def test_the_zip_option_builds_the_file_and_touches_no_user_folder(install, tmp_path, monkeypatch):
    out = tmp_path / "dist" / "x.zip"
    monkeypatch.setattr(sys, "argv", ["install.py", "--user-dir", str(tmp_path / "no-such-folder"), "--zip", str(out)])
    install.main()
    assert out.is_file() and not (tmp_path / "no-such-folder").exists()


def test_an_unpacked_install_beside_the_add_on_is_warned_about(install, tmp_path, monkeypatch, capsys):
    user = tmp_path / "user"
    (user / "mods" / "unpacked" / "beamng_earth").mkdir(parents=True)
    monkeypatch.setattr(sys, "argv", ["install.py", "--user-dir", str(user), "--dry-run"])
    install.main()
    assert "warning" not in capsys.readouterr().out
    (user / "mods" / install.ADDON_ZIP).write_bytes(b"")
    install.main()
    assert "both would load the extension" in capsys.readouterr().out


def test_the_manifest_is_options_earth_accepts():
    man = json.load(open(MANIFEST, encoding="utf-8"))
    assert set(man) <= {"options", "run"} and os.path.getsize(MANIFEST) <= 64 * 1024
    opts = man["options"]
    assert 0 < len(opts) <= 16
    keys = set()
    for o in opts:          # addons.parse_options
        assert OPT_KEY.fullmatch(o["key"]) and o["key"] not in keys
        keys.add(o["key"])
        assert o["type"] in ("int", "bool", "text", "choice")
        assert len(o.get("label", o["key"])) <= 60 and len(o.get("help", "")) <= 300
        if o["type"] == "bool":
            assert isinstance(o["default"], bool)
    # the extension reads this option under this key (core.lua applyOptions)
    core = open(os.path.join(ROOT, "mod", "beamng_dakar", "lua", "ge", "extensions", "beamngDakar", "core.lua"),
                encoding="utf-8").read()
    assert all(("values.%s" % k) in core for k in keys)


def test_the_release_workflow_attaches_the_file_earth_looks_for(install):
    wf = open(os.path.join(ROOT, ".github", "workflows", "release.yml"), encoding="utf-8").read()
    assert "tools/install.py --zip" in wf and "dist/%s" % install.ADDON_ZIP in wf
    assert "tags" in wf and "contents: write" in wf
