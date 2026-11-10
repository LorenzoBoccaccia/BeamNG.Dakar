#!/usr/bin/env python3
"""Write the mission folders (info.json) of an edition of days: one rally loop per driven day, and the day's legs.

    python tools/make_missions.py editions/2027-portugal-morocco

An edition with "days" in its legs.json (days.lua) is played one day at a time: each day is a loop of its own in the game's mission
list. This writes, from the Dakar testbed's three info.json files as the template (the schema the game wants, so a game update
that changes it is followed by changing the testbed's files once):

  rallyLoop/<prefix>-<day id>/info.json         the day's loop: dakarEdition "<edition>/<day id>", its liaison, special and return liaison
                                                  in the stage slots, startingSSNumber the special's number (a day after the first carries
                                                  on the time controls of the day before: its first is TC<n-1>B)
  rallyStage/<prefix>-<day id>-<leg id>/        a special: times from its length (the editions' own scale: gold = baseline, silver +20 %, bronze +50 %)
  rallyRoadSection/<prefix>-<day id>-<leg id>/  a liaison

A day with no special (a transfer day) has its liaison as the loop's return section and no stage. A rest day and a ferry have no mission.
The lengths come from <edition>/index.json (tools/fetch_route.py), so the geometry must be built first. Run again after the legs change.
Previews: python tools/make_preview.py <edition>.
"""
import copy
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MISSIONS = os.path.join(ROOT, "mod", "beamng_dakar", "gameplay", "missions", "beamng_earth")
TEMPLATE = {"rallyLoop": "dakar-testbed", "rallyStage": "dakar-testbed-ss1", "rallyRoadSection": "dakar-testbed-road1"}
GOLD_MS = 18.0            # a special's gold time: its length at this speed (65 km/h: the pistes are slower than the testbed's road)
DATE = 1794184296         # (the editions' mission files all carry one date)
DRIVEN = ("special", "transfer")


def template(kind):
    with open(os.path.join(MISSIONS, kind, TEMPLATE[kind], "info.json"), encoding="utf-8") as f:
        return json.load(f)


def fix_layers(info, kind, name):
    for layer in info.get("layers", []):
        layer["dir"] = layer["dir"].replace(TEMPLATE[kind], name)
    return info


def clean_km(m):
    return "%.0f km" % (m / 1000.0) if m >= 10000 else "%.1f km" % (m / 1000.0)


def number_of(day, legs):
    """The special's number of a day (P and S1 are the first: their loops start at TC0), else the one after the last special before it."""
    m = re.match(r"^S(\d+)$", next((l["id"] for l in legs if l["type"] == "special"), ""))
    if m:
        return int(m.group(1))
    return None


def write(path, info):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(info, f, indent=2, sort_keys=True)
        f.write("\n")


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    ed = sys.argv[1]
    base = json.load(open(os.path.join(ed, "legs.json"), encoding="utf-8"))
    if not base.get("days"):
        raise SystemExit("%s has no days: its missions are written by hand" % ed)
    index = json.load(open(os.path.join(ed, "index.json"), encoding="utf-8"))["legs"]
    by = {l["id"]: l for l in base["legs"]}
    prefix = base.get("missionPrefix", "dakar")
    title = base.get("title", prefix)
    last_n = 1
    written = 0
    for day in base["days"]:
        if day.get("kind") == "rest":
            continue
        driven = [by[i] for i in day["legs"] if by[i]["type"] in DRIVEN]
        if not driven:
            continue
        dprefix = prefix + "-" + day["id"]
        n = number_of(day, driven)
        if n is None:
            n = last_n if not any(l["type"] == "special" for l in driven) else 1
        last_n = n
        names = {}
        # the legs
        for leg in driven:
            kind = "rallyStage" if leg["type"] == "special" else "rallyRoadSection"
            name = dprefix + "-" + leg["id"]
            info = fix_layers(copy.deepcopy(template(kind)), kind, name)
            length = index[leg["id"]]["lengthM"]
            info["name"] = "%s: %s: %s" % (title, day["label"], leg.get("title") or leg["id"])
            info["date"] = DATE
            info["devMission"] = True
            if kind == "rallyStage":
                gold = round(length / GOLD_MS)
                info["description"] = "%s: a timed stage of %s (published special length %s km)." % (day["title"], clean_km(length), leg.get("specialKm", "?"))
                td = info["missionTypeData"]
                td["baselineTime"] = float(gold)
                td["goldTime"] = td["goldTimeTotal"] = float(gold)
                td["silverTime"] = td["silverTimeTotal"] = float(round(gold * 1.2))
                td["bronzeTime"] = td["bronzeTimeTotal"] = float(round(gold * 1.5))
            else:
                info["description"] = "%s: a road section of %s." % (day["title"], clean_km(length))
            write(os.path.join(MISSIONS, kind, name, "info.json"), info)
            names[leg["id"]] = "%s (beamng_earth/%s/%s)" % (info["name"], kind, name)
            written += 1
        # the loop: [liaison, special] pairs then the return liaison; a day with no special has only the return
        loop = fix_layers(copy.deepcopy(template("rallyLoop")), "rallyLoop", dprefix)
        loop["name"] = "%s: %s" % (title, day["label"])
        loop["description"] = "%s (%s, bivouac: %s). %s" % (day["title"], day["date"], day.get("bivouac", ""),
                                                          "The route is approximate where ASO has not published it: see the edition's notes.")
        loop["date"] = DATE
        loop["devMission"] = False
        td = loop["missionTypeData"]
        td["dakarEdition"] = "%s/%s" % (base["edition"], day["id"])
        td["startingSSNumber"] = n
        for k in range(1, 5):
            td["stage%d_rallyRoadSection" % k] = "<none>"
            td["stage%d_rallyStage" % k] = "<none>"
        td["return_rallyRoadSection"] = "<none>"
        specials = [l for l in driven if l["type"] == "special"]
        liaisons = [l for l in driven if l["type"] == "transfer"]
        if specials:
            td["stage1_rallyRoadSection"] = names[liaisons[0]["id"]]
            td["stage1_rallyStage"] = names[specials[0]["id"]]
            if len(liaisons) > 1:
                td["return_rallyRoadSection"] = names[liaisons[-1]["id"]]
        else:
            td["return_rallyRoadSection"] = names[liaisons[0]["id"]]
        write(os.path.join(MISSIONS, "rallyLoop", dprefix, "info.json"), loop)
        written += 1
    print("%d mission files written" % written)


if __name__ == "__main__":
    main()
