# 0001 - BeamNG rally loop, waypoints, and BeamNG.Earth's moving origin

Research date 2026-11-08. Sources: the game's own files (rally loop Lua and mission data under the Steam install),
BeamNG.Earth's source (`mod/beamng_earth/lua/ge/extensions/beamngEarth/core.lua`, `rebase.lua`,
`docs/ARCHITECTURE.md`), and the Dakar organiser's statements. Nothing here was run in the game yet; where a
statement is an inference it says so.

## 1. What a "rally loop" is in BeamNG (verified from game files)

Not one scenario: a chain of missions.

| Mission type | Folder (in a mod: `gameplay/missions/<level>/<type>/<id>/`) | Holds |
|---|---|---|
| `rallyLoop` | `info.json`, `mainPrefab.prefab.json`, `trafficGatedRoads.grzones.json` | up to **4** stages: `stage1..4_rallyStage` + `stage1..4_rallyRoadSection`, plus a `return_rallyRoadSection`; `serviceStallPos/Rot`, `startTrigger {level,pos,rot,radius}`, `loopTimes` (time-card allowances), `startingSSNumber` (a continuation loop can start at SS5 etc.) |
| `rallyStage` (timed special) | `race.race.json`, `loopPrefab.prefab.json`, `rally/drivelineSpline.json`, `rally/notebooks/`, `rally/voicepacks/` | `pathnodes[]` (each `pos [x,y,z]`, `normal`, `radius`), start positions (`SS_start_line`, `SS_stop_control`, ...), a recorded driveline, a pacenote notebook |
| `rallyRoadSection` (timed liaison, road rules, speed limits) | its own `race.race.json` | the transfer between service, stage start, and stage finish |

Stock examples: `italy/rallyLoop/ngrc25` (3 stages + 3 road sections + a return) and
`utah/rallyLoop/ngrc26-rally-utah`. The loop runs the schedule (time controls, early/late penalties, service,
SS numbering) in `lua/ge/extensions/gameplay/rally/loop/rallyLoopManager.lua`.

Consequences for Dakar:

- **A "day" is one loop, up to 4 special stages.** Dakar has 1 special per day, so one loop = one stage day (or
  several days chained with `startingSSNumber`). The 4-slot cap is a hard limit of the stock editor.
- **A mission is tied to one level** (`startTrigger.level`). Ours is the Earth level `beamng_earth`.
- **Waypoints are world-space numbers baked into files** (`pos` arrays in `race.race.json`, the driveline point list,
  `serviceStallPos`, `startTrigger.pos`, prefab object positions). They are read into memory at mission start
  (`rallyUtil.loadRacePath`, `DrivelineV3:loadDrivelineFromFile`, `Snaproad(pointList)`,
  `DrivelineRoute:loadRouteFromRecordedDriveline`).
- A pacenote notebook is expected but a synthetic empty one is created when none is found
  (`createSyntheticEmptyNotebook`); whether a stage works without `drivelineSpline.json` is **unverified** (the
  loader has a "no driveline" branch that is commented out), so assume we must generate one.

## 2. How BeamNG.Earth places things (verified from this repo)

- The game only ever sees a local ENU frame (+X east, +Y north, +Z up) of a few km around an **anchor** (lat, lon).
  There is no fixed world frame: the same (x, y) means different places over time.
- **Rebase (floating origin):** when the player is ~8-10 km from the anchor, `core.lua finishRebase` applies one rigid
  transform `p' = R*p + t` (translation plus a rotation of about d/R) to the vehicles and, one by one, to every module
  that holds world-space content (`roads.transform`, `trees.transform`, `farfield.transform`, `navgraph.transform`,
  `camshift.shift`, ...). Each module has its own `transform(xf)`; **there is no generic hook that outside code can
  subscribe to**.
- **Teleport (`commitTeleport`)** re-anchors with no transform at all: a new frame, the old contents dropped.
- The vehicle's reset position and recovery points are remapped too (`rebase.updateResetState`).
- Everything rally-related (race path nodes, snaproad, driveline route, time-control planes, prefab markers, service
  stall) is **not** known to Earth, so at the first rebase it would stay in the old frame while the car and the world
  move. *(Inference from the code above; confirm with the probe in section 5.)*

So the answer to "how do waypoints interact with Earth": **stock stage files cannot be used as they are. A stage
authored as x/y/z in one frame is only right until the next rebase, and a stage file is only right for the anchor it
was authored against.**

## 3. Size of the problem

Real Dakar specials are 300-500 km. At a 10 km rebase distance that is **30-50 rebases inside one timed stage**, so
"load the stage at its anchor and hope" cannot work. Teleports are fine between legs (they are what a ferry is), but
a special stage has to survive continuous rebasing.

## 4. Options

| | Approach | Pro | Con |
|---|---|---|---|
| A | Author each stage in lat/lon; a Dakar extension converts to the current anchor's frame when the leg loads (teleport to the leg's anchor) | no changes to Earth | breaks at the first rebase |
| **B (recommended)** | A, plus Earth fires a hook on rebase (`extensions.hookNotify('onBeamngEarthRebase', xf)` from `finishRebase`, and one from `commitTeleport`), and a Dakar adapter applies `xf` to the live rally objects (race path nodes, snaproad points, driveline route, planes, markers, service stall) | stays on the stock `rallyLoop` / `rallyStage` / `rallyRoadSection` missions the user asked for; the hook is a few lines in Earth and useful to other mods | needs the rally objects' internals mapped (section 5, step 2); materialising a 450 km stage's markers must be windowed ahead of the car |
| C | Our own mission type `dakarLoop` copying `rallyLoop.flow.json`, with a geodetic position getter (rally logic works in lat/lon, never in frame coordinates) | immune to rebasing | forks the stock feature; loses its updates; much more code |

Fallback order: B, then C for the parts B cannot reach.

## 5. First experiments (plan; results of the offline part in section 8)

1. **Rebase probe.** Hand-place one `rallyStage` and one `rallyRoadSection` in the Earth level near an anchor, start
   the loop, force a rebase (`probes/p6_rebase.lua` in BeamNG.Earth does the move), and log what moves and what does
   not (race path pos, snaproad, markers, countdown planes, time controls).
2. **Map the in-memory geometry.** List every table the loop and stage managers keep positions in after
   `rebuildAssets`, so the adapter has an exact list to transform.
3. **Earth hook.** Add `onBeamngEarthRebase` / `onBeamngEarthTeleport` to BeamNG.Earth (separate issue there).
4. **Generator.** `tools/` script: lat/lon leg file -> `race.race.json` + `drivelineSpline.json` + mission `info.json`
   in the current anchor's frame (uses BeamNG.Earth's `geodesy` maths).
5. **Ferry leg.** A `ferry` leg is a road-section end plus a teleport plus the next road-section start, with the time
   card paused or advanced by the crossing time (the loop manager has `recoveryClockAdvance` and time-control skipping
   to build on).

## 6. Data we do and do not have for Dakar 2027

- The route is verified at headline level (dates, 7,824 km, 4,341 km of specials, Loule-Faro to Marrakech, six
  bivouacs, rest day in Agadir: FIA, FIM and ASO) and the per-leg table in
  `editions/2027-portugal-morocco/legs.json` sums to those totals; the per-leg detail comes from one secondary source
  (an assistance team's write-up of the statement) and still needs a cross-check. **Real stage tracks are not published**:
  organisers hand roadbooks to competitors only. Public data is city-to-city, stage distances and a map image. The
  geometry has to be authored: bivouac positions, road-following liaisons (OSM), and plausible special tracks across
  the named terrain (Rekkam plateau, Merzouga, Erg Chegaga, Plage Blanche, Dakhla lagoon). We will say so in the
  project and not present it as the official track.
- Earth's terrain (open DEM) will not resolve dunes, and OSM has few desert tracks; stage surface and physics need a
  separate look.
- **A stale official page:** `dakar.com/en/overall-route` still showed the original Saudi route (KAEC loop,
  1-15 Jan 2027, 5,320 km specials) when read on 2026-11-08. That route was withdrawn: ASO announced on 2026-10-25
  that 2027 would not run in Saudi Arabia and on 2026-10-30 that it relocates to Portugal and Morocco (FIA, FIM,
  dakar.com news item "Portugal and Morocco to exceptionally host the 49th edition", Al Jazeera/Reuters). Saudi Arabia
  returns in 2028. Do not use the Saudi table.

## 7. Layout decision

Historical editions (Paris-Dakar with the ship crossings, and the like) are planned, so nothing edition-specific
sits at the repo root:

```
editions/<year>-<name>/legs.json     route as legs: special | transfer | ferry (teleport) | rest
editions/<year>-<name>/stages/       per-leg lat/lon geometry
mod/                                  Lua extension + generated mission data (later)
tools/                                generators (later)
docs/research/                        notes like this one
```

## 8. Testbed results so far (2026-11-08, offline; not yet run in the game)

`editions/testbed-algarve/` is a 19 km transfer and a 39 km timed stage on public roads near Faro. The Dakar extension
(`mod/beamng_dakar`) taps Earth from outside (no change to BeamNG.Earth): `rebase.moveVehicles` (phase 1 of a rebase,
carries the frame change `xf`) and `mpanchor.announceOwn` (every anchor commit, documented in Earth as "called right
after st.anchor is set"). `tests/` runs the extension and Earth's real `geodesy.lua` in an embedded Lua with stubbed
game functions (11 tests).

- **One transform is enough.** The ENU frames are rigid maps of the same ECEF space, so Earth's own `xf` moves any rally
  position correctly. Over a chain of rebases along the stage the error does **not** grow with the count: 0.34 m after
  3 rebases (10 km apart), 0.48 m after 19 (2 km apart). What remains comes from `geodeticToWorld` putting x, y at
  height 0 and only z at the height. A pathnode is 6 m wide. So the adapter can move the live rally data by `xf`; it
  does not have to re-derive every number from lat/lon at each rebase (re-deriving stays available as a periodic
  snap if the in-game probe finds drift).
- **The loop's files are frame-specific, so they are written at start.** Earth picks the anchor of a teleport itself, so
  the extension waits for the anchor commit, then writes `race.race.json` and `rally/drivelineSpline.json` for that
  anchor into the user folder's mission directories and patches the loop mission's service stall and start trigger in
  memory.
- **Still unknown (needs the game):** whether the stock loop accepts the generated files (schedule validation,
  driveline loading), which in-memory tables hold positions (the probe logs what stays behind: `DAKAR-PROBE` lines),
  whether moving the race path alone is enough (the loop builds a schedule up front that caches positions too), and the
  timing between the car's move (phase 1) and the commit (a few frames).

## 9. First runs in the game (2026-11-08)

- **Starting from the mission list works.** The extension taps `gameplay_missions_missionManager.startWithFade/start`,
  holds the click, teleports (Earth picks the anchor), writes the route files for that anchor and then runs the game's
  own start with the settings the player picked (the car included). Without our files a click gave "race file not found"
  and a placeholder position (0, 0, 0) that put the car under the terrain, which is why the start is held.
- **The stock route builder needs margins the files must have.** `finishLineDistOffset is nil ... driveline is not long
  enough` and no schedule when the driveline ended exactly on the stop position and the last race node sat on it too.
  Measured on Italy and Utah: the driveline starts 14-25 m before the start position and runs 0-18 m past the stop
  position; the last race node (`endNode`) is 16-150 m before the stop position. `legs.lua` now writes a 22 m lead-in,
  a 12 m run-out and a last node at least 30 m before the stop position.
- **Terrain:** the Terrarium heights the geometry carries differ from the game's ground by about 3 m at the start
  (measured -2.6 .. -3.3 m), so start positions and props are snapped to the game's ground with a ray cast.
- **Props are made at runtime** (stock rally assets, near the player, rebuilt at every origin change), not as a
  `mainPrefab`, because their positions are frame-specific.
- Still to see: route guidance and time-control markers after the schedule builds, `DAKAR-PROBE` lines at a rebase
  (needs a drive of more than 10 km), whether moving the race path alone is enough with the loop's cached positions.

## 10. Start, ground and landmarks (2026-11-08, evening)

- **A start the game did not hold.** One run began without any line from our start tap: the loop used the files an earlier
  start had left, for another anchor (time control 0 410 m away, nothing near the car). Taps now log when they are
  installed and every start request, and a loop that starts any other way is stopped and started again through ours
  (`onAnyMissionChanged`), with the mission's default settings.
- **Heights.** Race path, driveline, time controls and the car's place are written at the game's ground: the difference
  to the terrain data at the first point (`core_terrain.getTerrainHeight`, else a ray) is added to every height. Props use
  the terrain height at their own place. An answer more than 25 m from the data is not believed (an empty block says 0).
  Far from the start the difference may not be the same; a measured value per place would be better.
- **Parking lot.** Overpass was unreachable, so `tools/parkinglot.py` reads Overture's `parking_aisle` segments with
  DuckDB, joins those whose ends meet into lots, takes the lot with the most aisle (discounted by distance) and the aisle
  vertex nearest its middle. At the testbed: 10 aisles, 2.4 km of aisle, 425 m from the old start. A guess, as asked.
- **Landmarks.** The game's shape archive has no start arch; the tallest suitable pieces are `s_tower_01` (15 m) and
  `s_flag_floor_feather_01/02` (9.7 m), made from 9 km away (`far` props), the others from 2.5 km.
- **What the roads wait covers.** First a circle of 300 m round the player, up to 2 minutes; the server's log showed a few
  road tiles stalling ~187 s on the unreachable tag host while others took 3-6 s, so one slow tile off the player's way could
  hold the start. Now `legs.corridor`: samples every 120 m along the route from the player to the next timed stop (a leg
  end more than 25 m ahead), at most 600 m, each asked of `roads.readyAround` with 60 m; off the route a circle of 150 m;
  at most 60 s. At the testbed the start is a lot, and the first 600 m of the route wind within ~190 m of the spawn.
