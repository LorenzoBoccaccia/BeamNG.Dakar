#!/usr/bin/env python3
r"""Install BeamNG.Dakar into the BeamNG.drive user folder (next to BeamNG.Earth's mod), or build its add-on zip.

    python tools/install.py                       (the default user folder)
    python tools/install.py --user-dir "D:\BeamNG user folder\current"
    python tools/install.py --link                (development: junctions to this folder, no copy; edits are live)
    python tools/install.py --zip                 (the add-on file for BeamNG.Earth 1.9.0: dist/beamng-dakar.zip)
    python tools/install.py --dry-run

Copies mod/beamng_dakar/ to <user folder>/mods/unpacked/beamng_dakar/ and each edition's legs.json, geometry/ and
closures/ (tools/stage_closures.py) to <...>/beamng_dakar/dakar/editions/<edition>/. Run it again after changing anything; it replaces the installed copy.

--link instead makes <user folder>/mods/unpacked/beamng_dakar a real folder holding directory junctions (symlinks on
Linux) to mod/beamng_dakar/*, and dakar/editions to this repo's editions/, so what you edit here is what the game
reads (restart the extension or the level to reload Lua). A junction needs no administrator rights on Windows.
Never delete the installed folder with a recursive delete that follows links: this script removes links as links.
BeamNG.Earth's own mod (install.py in that repo) must be installed too: the extension taps it.

--zip writes the same files as one mod zip, the layout BeamNG.Earth 1.9.0's Settings > Add-ons installs (the game's
folders at the zip's top). That is the file a GitHub release carries (.github/workflows/release.yml); this builds it for
a local try (README.md: "As an add-on"). It touches nothing in the user folder.
Do not have both installs: the unpacked folder and the add-on's zip would each load the extension.
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD_SRC = os.path.join(ROOT, "mod", "beamng_dakar")
EDITIONS = os.path.join(ROOT, "editions")
NAME = "beamng_dakar"
# the add-on's file in the mods folder: Earth's Add-ons page names it after the repository (addons.py: <id>.zip, the id
# the repository's name in lower case), and finds the release's file by its .zip ending
ADDON_ZIP = "beamng-dakar.zip"


def default_user_dir():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "BeamNG", "BeamNG.drive", "current")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "BeamNG", "BeamNG.drive", "current")


def is_link(path):
    return os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def remove_tree(path):
    """Remove a folder without ever following a link out of it: links are removed as links."""
    if is_link(path):
        os.rmdir(path) if os.name == "nt" else os.unlink(path)
        return
    for name in os.listdir(path):
        child = os.path.join(path, name)
        if is_link(child):
            os.rmdir(child) if os.name == "nt" else os.unlink(child)
        elif os.path.isdir(child):
            remove_tree(child)
        else:
            os.remove(child)
    os.rmdir(path)


def make_link(target, link):
    """A directory junction on Windows (no administrator rights), a symlink elsewhere."""
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("mklink /J failed: %s" % (r.stderr or r.stdout).strip())
    else:
        os.symlink(target, link, target_is_directory=True)


def install_links(dst):
    os.makedirs(dst)
    for name in sorted(os.listdir(MOD_SRC)):
        make_link(os.path.join(MOD_SRC, name), os.path.join(dst, name))
        print("  link %s" % name)
    os.makedirs(os.path.join(dst, "dakar"))
    make_link(EDITIONS, os.path.join(dst, "dakar", "editions"))
    print("  link dakar/editions")


def mod_files():
    """[(path inside the mod, file here)] of everything the game reads: the mod folder as it is, and each edition's
    legs.json, geometry/ and closures/ (where a special stage's side roads are closed off) under dakar/editions/.
    The copy install and the add-on zip are both this list, so they cannot differ."""
    out = []
    for base, _dirs, names in os.walk(MOD_SRC):
        for n in names:
            src = os.path.join(base, n)
            out.append((os.path.relpath(src, MOD_SRC).replace(os.sep, "/"), src))
    for ed in sorted(os.listdir(EDITIONS)):
        src = os.path.join(EDITIONS, ed)
        if not os.path.isfile(os.path.join(src, "legs.json")):
            continue
        out.append(("dakar/editions/%s/legs.json" % ed, os.path.join(src, "legs.json")))
        for sub in ("geometry", "closures"):
            for base, _dirs, names in os.walk(os.path.join(src, sub)):
                for n in names:
                    f = os.path.join(base, n)
                    out.append(("dakar/editions/%s/%s" % (ed, os.path.relpath(f, src).replace(os.sep, "/")), f))
    return sorted(out)


def build_zip(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    files = mod_files()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, src in files:
            z.write(src, rel)
    print("wrote %s (%d files)" % (path, len(files)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-dir", default=default_user_dir())
    ap.add_argument("--link", action="store_true", help="development: junctions to this folder instead of a copy")
    ap.add_argument("--zip", nargs="?", const=os.path.join(ROOT, "dist", ADDON_ZIP), metavar="FILE",
                    help="build the add-on zip for BeamNG.Earth 1.9.0 (default dist/%s) and install nothing" % ADDON_ZIP)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.zip:
        if not a.dry_run:
            build_zip(a.zip)
        return
    if not os.path.isdir(a.user_dir):
        sys.exit("BeamNG user folder not found: %s (use --user-dir)" % a.user_dir)
    earth = os.path.join(a.user_dir, "mods", "unpacked", "beamng_earth")
    if not os.path.isdir(earth):
        print("warning: BeamNG.Earth's mod is not installed at %s; Dakar needs it" % earth)
    if os.path.isfile(os.path.join(a.user_dir, "mods", ADDON_ZIP)):
        print("warning: the add-on %s is installed in mods too (BeamNG.Earth's Add-ons page); remove one of the two, "
              "both would load the extension" % ADDON_ZIP)
    dst = os.path.join(a.user_dir, "mods", "unpacked", NAME)
    print("install %s -> %s" % (MOD_SRC, dst))
    if a.dry_run:
        return
    if os.path.lexists(dst):
        remove_tree(dst)
    if a.link:
        install_links(dst)
        print("done (linked). Start the game on the BeamNG.Earth level, then in the GE console: beamngDakar_core.start('testbed-algarve'), or 'svolte-di-popoli' for the Svolte di Popoli")
        return
    for rel, src in mod_files():
        os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
        shutil.copy2(src, os.path.join(dst, rel))
    print("done. Start the game on the BeamNG.Earth level, then in the GE console: beamngDakar_core.start('testbed-algarve'), or 'svolte-di-popoli' for the Svolte di Popoli")


if __name__ == "__main__":
    main()
