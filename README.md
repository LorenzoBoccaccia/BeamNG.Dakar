# BeamNG.Dakar

A sister project of [BeamNG.Earth](https://github.com/TecnaGamer/BeamNG.Earth): the Dakar Rally route driven on the
real Earth in BeamNG.drive, using the game's rally loop feature (timed special stages joined by timed transfers), not
a single scenario. The first edition is the 2027 Dakar (Portugal and Morocco). Historical editions, with teleports
for the ship crossings, come later, so each edition lives in its own folder under `editions/`.

Status: research. See [docs/research/0001-rally-loop-and-moving-origin.md](docs/research/0001-rally-loop-and-moving-origin.md).

The stage tracks are our own reconstruction from public route information; the organisers do not publish the real ones.

## Installing

Two ways; use one of them, not both (each would load the extension). BeamNG.Earth must be installed either way.

**As an add-on (BeamNG.Earth 1.9.0 and later).** A release of this repository carries one file, `beamng-dakar.zip`
(pushing a tag `v*` builds and attaches it: `.github/workflows/release.yml`). In the game, Settings > Add-ons > add
from GitHub, paste `LorenzoBoccaccia/BeamNG.Dakar` (or the repository's link) and Install; Update, Turn off and Remove
are on the same page, and the add-on's Options tab has the ground shaping switch. Earth reads GitHub without a login,
so this needs a public repository with a release: a private one is "no repository on GitHub". To try the file without
a release (or while the repository is private), build it and put it on Earth's local list:

```
python tools/install.py --zip                 (writes dist/beamng-dakar.zip; touches nothing in the game's folders)
```

then in `%LOCALAPPDATA%\beamng-earth\addons\local.json` (Earth's data folder; created if it is not there):

```
{"format": 1, "addons": [{"id": "beamng-dakar", "name": "BeamNG.Dakar", "author": "LorenzoBoccaccia",
  "repo": "LorenzoBoccaccia/BeamNG.Dakar", "kind": "beamng-mod", "summary": "Rally loops on BeamNG.Earth",
  "licence": "Not licensed yet", "asset": "\\.zip$", "modFile": "beamng-dakar.zip",
  "local": {"file": "C:\\path\\to\\BeamNG.Dakar\\dist\\beamng-dakar.zip", "tag": "local"}}]}
```

**Unpacked in the user folder (BeamNG.Earth 1.8.2, and development on any version).** `python tools/install.py` copies
the mod and the editions into `<user folder>/mods/unpacked/beamng_dakar`; `python tools/install.py --link` makes
junctions instead, so edits are live (the commands are under Testbed below). Remove this folder before installing the
add-on.

On 1.9.0 the extension asks BeamNG.Earth's modding API (`beamngEarth_api`, docs/ADDONS.md in that repository) for the
player's position, the teleport, a GPS destination (set to the service park when you start a loop far from it), the
messages on screen and the add-on's options; on 1.8.2 `earthapi.lua` gets the same from Earth's internals. What the API
has no equivalent of stays a tap (the list is in core.lua's header): a rebase, the anchor commit, a new terrain block and
the corridor download's progress.

## Svolte di Popoli

The second edition, `editions/svolte-di-popoli`: the Svolte di Popoli hillclimb (SR17, Abruzzo) as a rally loop with a
single special stage, **Svolte di Popoli** in the mission list. A 0.2 km transfer from a car park above contrada
Canapine to the start on the SR17 (256 m), then the SR17 up its hairpins to San Benedetto in Perillis at the gate of
the Navelli plateau (723 m): 7.4 km, about 470 m of climb (it.wikipedia: 7.53 km, 250 to 725 m, 6.3 percent). The
service park and the start are north of the SR17's underpass under the railway, which is blocked in the game. The
stage's `via` points lie on the SR17 line chained from OpenStreetMap, close enough that OSRM follows its hairpins. Drive
to the service park (42.1749, 13.8231, above contrada Canapine) and start it from the mission list like the testbed; the BeamNG.Dakar
window has a button per edition for the teleport. Built with the same tools as the testbed below, on
`editions/svolte-di-popoli`.

## Testbed

```
python tools/fetch_route.py editions/testbed-algarve      (once: road geometry and heights, needs Pillow and numpy; with
                                                          duckdb a leg with "parkingNear" starts in the best parking lot near its first point: the car
                                                          on an aisle facing the exit, TC0 a few metres ahead; and every join between legs is made
                                                          one straight run, driven into from behind: tools/joins.py)
python tools/stage_closures.py editions/testbed-algarve    (once: where the side roads meet each special stage, from Overture Maps with duckdb)
python tools/make_preview.py editions/testbed-algarve      (the mission list pictures, preview.jpg and thumbnail.jpg, drawn from the route)
python tools/install.py                                   (copies the mod and the editions into the BeamNG user folder)
python tools/install.py --link                            (development instead: junctions to this repo, edits are live)
python tools/install.py --zip                             (the add-on file for BeamNG.Earth 1.9.0, see Installing above)
```

BeamNG.Earth must be installed and running (its `python launcher.py` starts the server and the game). On the Earth
level, drive to the testbed's service park near Faro (37.0884, -7.9755) and start **Dakar testbed** from the mission
list like any rally loop. You are not moved: within 300 m of the service park the extension holds that start just long
enough to write the route for the anchor you are in, then runs the game's own start with the settings you picked (the
car included); farther away it tells you which way and how far the service park is and starts nothing. A start (and the window's
teleport) first holds the game, with Earth's own pause, until the roads along the route from where you are to the next
timed stop (at most the next 600 m of it, 60 m either side) are loaded, at most a minute (the pause key or the window's
button skips it), so the loop's clock does not run over a wilderness; a slow tile elsewhere does not hold you. A small
**BeamNG.Dakar** window shows where the service park is, the distance, a button to teleport there for testing (and one
to teleport and start), a button to stop a running mission and the switch for the rebase experiment. The same from the
game's GE Lua console:

```
beamngDakar_core.start('testbed-algarve')      teleports to the service park, builds the loop for that anchor, starts it
beamngDakar_core.start('testbed-algarve', true) only teleports to the service park
beamngDakar_core.setAdapt(true)                at a rebase, move the race path with Earth's transform (experiment)
```

The ground arrows of a liaison and the distance the time-control panel shows (`guide.lua`, `core.lua`): the game plans them
from the car to the next time control over the navgraph, which BeamNG.Earth only builds for about a kilometre round the player.
A control kilometres away is outside it, and the planner then goes to the graph point nearest the control in a straight line
and ends with one straight segment over whatever lies between, so the arrows left the planned road (the log's `liaison ...
guidance` line says where the car is on the line). When the game sets the liaison's route, the extension plans it again with one
target on the planned line ahead of the car: the farthest point within 1.5 km along it that the local graph covers, moved on in
150 m steps as the car drives, and the planner's own cost options, which make service ways, tracks and living streets (the
navgraph's drivability 0.5 and below) ten times dear. It is a single target, not a chain of waypoints: missing a point of the line
or leaving it for another road does not matter, the car is led to the line ahead from where it is (the nearest point of the whole
line while it is off it). The rest of the line beyond the target is added to the planner's distance, again after each
re-plan the planner makes of its own, so the panel reads the distance along the line to the control.

The loop's props (service park with table, chairs, flags and cones; start boards and sensors; finish entry and finish
boards) are defined along each leg in `props.lua` and made only near the player, again at every origin change, each on
the terrain's height, and put on the ground the game really has once the player is within 400 m (a prop made far away
cannot know it). The service park's cones, flags, table and chairs stand along the car's way from its stall to TC0, and the
time-control board at TC0 closes them, its printed face to the car that arrives (every rally signboard has its printed face on
the model's -y, so it stands with its +y along the way on, as the stock loops stand theirs). The end of every transfer, the time control before the special, has a staging area like the service park:
the stock time-control board, its entry board 50 m before, a table and chairs, flags and cones down the approach. Tall
landmarks (a 15 m tower and four 9.7 m feather flags at the service park, that staging area and the finish) are made from
9 km away so the way is visible from far. The car itself starts in a service booth in the parking lot, as in the stock service
parks: the car beside a BLASTR gazebo (the stock 3 x 6 m rally gazebo) with a table, chairs, tool boxes, an extinguisher, cones
and flags under and round it, and the gazebos of other teams in a row along the aisle. `tools/booth.py` places it from the lot's
parking polygons (Overture) so that all of it is inside the lot, outside any building and clear of the drive to TC0 and out;
`tools/fetch_route.py` writes it into the first leg's geometry (`servicePark.booth`, with `area` and `buildings` for checking it) and
moves the car along the aisle to where the booth has room. It stands 17-20 m behind TC0's own staging area. The tower of the staging area stands on the outer side of the road's last bend
by default; a transfer leg in `legs.json` can say `"staging": {"side": "left" | "right", "landmarkS": 10, "reach": 16}`
(the side, the metres before the control, the metres from the road). The special's start has the same table and flags only
when no transfer ends there. The whole route is written at the height of the
ground the game has, measured at the start (the terrain data is a few metres off it).
A special stage is a fixed road, so its corners are read from its geometry (`pacenotes.lua`: where the road turns tighter
than about 170 m radius, by more than 14 degrees, one way) and written, with the rest of the loop at every start, as the
stock notebook `rally/notebooks/structured.notebook.json` of the stage: a corner start and a corner end waypoint on the
route and a corner item per note. The game measures each corner between its two waypoints (direction, the number 1-6, the
length words) and fills the distance calls, and the stock co-driver voices play it; hairpins and square corners are
named, and a long corner that opens or tightens says so. A liaison has no notes.
A special stage runs on public roads, so every side road that meets it is a way off the stage: `tools/stage_closures.py`
finds them (`editions/<edition>/closures/<leg>.json`) and each is closed 10 m from the stage, across the side road, the way
the stock stages close their exits: one 6 m length of the stock rally road-closure tape, two side by side on a wide road,
made near the player like the other props.
No prop stands on a road (a table stood on the road out of the lot). The props have no collision, so one on a road is
scenery the car drives through. When a prop is made, and again whenever the roads changed while the player is within 400 m of it,
`roadcheck.lua` looks at its footprint (`props.FOOTPRINTS`: the table about 1 m, a gazebo's cover 3 x 1.5 m half sizes, a cone 0.3 m)
against the game's navgraph (`map.getMap().nodes`, which BeamNG.Earth builds from the roads it streams, a few tiles at a time, so
a prop made far away is checked again when the player comes near) and against our own stage and transfer road (a 5 m carriageway):
within a road's half width, the radius of the navgraph's nodes along the link, and 0.75 m of it, the prop is not made, or deleted,
and the log says which and how far from the road (once). A gazebo (three shapes) goes whole. The closure tapes are left, they
stand across side roads on purpose. The boards beside the road (5.5 m) stand on a stage road; on a wider road (Earth's navgraph
gives the SR17 a half width of 5 m) the props that do a job (the start, finish and time control boards, the start sensors, the
tower, the cones: `props.PUSHABLE`) are pushed out along the road's normal until their footprint clears the road and the margin,
4 m at most (the first run deleted the Popoli start's boards and sensors); only when that fails are they dropped, like the
decorations (tables, chairs, flags, gazebos).
The booth is kept off the other roads round the lot offline as well (`servicePark.roads`, Overture's drivable roads).

### Stage curation

A special's source line is OSRM's route with the terrain data's heights, which is not a rally road: the router makes
spurs at its via points and the terrain data puts a hillside's noise into the profile (the Svolte di Popoli's climbs 51
percent in places; the real road averages 6). Three things, all transient, nothing in BeamNG.Earth changed and nothing
written to its files:

- **The curated line** (`curate.lua`, pure): out-and-back spurs, repeated points and one-point zigzags taken out of the
  plan (the plan is not smoothed: outside a repair every point is on the source line); the line at an even 10 m; the
  height profile cleaned of single-point spikes (more than 2.5 m from the median of the points within 40 m), held to a
  20 percent grade and a 4 percent change of grade per 10 m, never more than 5 m from the source's height; and the
  bridges and tunnels marked: where the profile dives and climbs back (or rises and falls) at a grade no road has, inside
  600 m and deeper than 8 m, that is a viaduct over a valley or a ridge over a tunnel, its height is the straight line
  across (a geometry file may also carry `"structures": [{"from": m, "to": m, "kind": "bridge"}]`, metres along the
  line, from OpenStreetMap tags: `tools/structures.py <edition>` writes them for the specials, only for the way the
  stage's own road is on, not for a viaduct that crosses it). The driveline, race path, pace notes and props use it, and the ground shaping its plan (not its heights: the ground is Earth's); the log
  of every launch has a `stage curation:` line per special with what it found (spurs, zigzags, spikes, spans, the
  largest grade and height change).
- **The preload** (`preload.lua`): when a loop with a special starts, Earth's server is asked for the start-to-finish
  route of the stage (the message Earth's GPS sends, `download = 'route'`), which fetches the whole corridor (elevation,
  roads and buildings, imagery) into its caches and pushes `route_download` progress (read through a tap on
  `gps.onRouteDownload`). A start from the main menu keeps the loading screen up until the server says the corridor is
  in, 120 s at most (also when it has no server, no answer or no progress: it never holds longer than that); an in-game
  start does not wait, the download runs behind it. Earth's router plans its own way, not along the stage line, so how
  far its route strays from the stage line is logged. A newer route supersedes the older on the server: the stages of an
  edition are fetched one after the other, and a GPS route the player sets meanwhile takes over from it.
- **The ground shaping** (`shape.lua`): while the loop runs, every terrain block Earth brings in (taps on
  `terrain.load` and `terrain.swap`) is looked at along the stage and changed, in memory, only where the elevation data
  has a defect the road does not, and nowhere else: every other cell of the road is as Earth made it. Only the stage
  road is worked on: its surface (5.5 m either side of the line: the road's half width, `legs.ROAD_WIDTH` / 2, and half a metre)
  and the ground directly under it, with a taper of 6 m to blend into the ground beside it (it was 6.5 m and 12 m, 37 m of
  ground for a 10 m road); other roads, a tunnel's arch and holes, embankments and verges beyond the taper are Earth's. Earth paints
  the asphalt into the ground photo and draws the centre and edge lines as a mesh 2.5 cm over its own ground, which does
  not follow ours, so every cell we moved buried the lines or left them floating. What is fixed, found on the game's
  own ground under the curated line (its plan; not its heights, which are the offline terrain data and stood 10 m above
  the game's ground at the Popoli stage's start):
  - the ground under an overpass and 20 m past it, where the elevation data has the deck as a ridge (a static collider more
    than 2.5 m over the line, as wide as a road (the same height 2 m to either side of the line), that does not continue
    from the ground at either end: the A25 over the Svolte di Popoli's SR17): the line there is the straight line
    between the ground beyond, up to 8 m of change;
  - a big spike on the line (curate.despike with `BUMPS_ONLY`: a point 2 m or more over the straight line between the points
    k steps either side of it, median over k = 1 to 4, 10 m steps: a steady grade, a crest of 4 percent change per 10 m and a
    hillside are not; an edition sets its own in legs.json, `"shaping": { "spikeM": 3 }` is the Popoli stage's, whose
    hairpins are cut into a steep hillside the curated line is a few metres off), with the points within 15 m of it (the
    taper): the line there is the straight line between the points beyond, up to 4 m of change. Only a point above that
    line is a defect: the elevation data errs upward along a road (a roof, a canopy, a viaduct's deck baked in), and a dip
    is a sag the road dips into and climbs out of (the Popoli stage's, at 796 m: a 20 percent climb out of it), or a gully
    under a deck whose collider is not in the game yet or is out of the rays' reach. Filling one raised the ground 4 m
    under Earth's road lines. The rim of a dip stands over the line across it too, and is no spike either (a deeper dip
    within reach of a point rules it out);
  - nothing beside a tunnel: Earth builds a tunnel's tube and portals (a colliding mesh) and shapes the terrain for them
    (holes at the mouths, the ground raised over a low arch), so the points of the line within 30 m of a tunnel's axis
    are left as Earth made them, and the stretches next to them are not worked on. Which: the geometry's
    `"tunnelsBeside": [{"from": m, "to": m, "way": id}]`, metres along the line, from OpenStreetMap's tunnel ways
    (`tools/structures.py <edition>` writes them with the structures; the Popoli stage's A25 tunnel at 651 to 736 m and 907
    to 997 m), and at run time the tubes Earth has built in the game now (the points along their axes that Earth's
    `ambience.tunnelLights()` gives, when it has that call). The data is what is certain: Earth's tubes are known only for
    the road tiles loaded;
  - nothing on another road's ground: the geometry's `"otherRoads": [{"way": id, "hw": m, "pts": [[lat, lon], ...]}]` (`tools/structures.py`
    writes them with the structures, from OpenStreetMap's other drivable ways within 30 m of the line: a motorway beside the stage road, a road that
    crosses it, a road that leaves it; not the stage's own road, which is what is within 5 m of the line, and not bridges and tunnels: the ground
    under a deck is what is below it, a tunnel's is Earth's) are kept clear of beyond the stage road's own 5.5 m: a cell on a road's carriageway,
    shoulders and 2 m of margin is not moved, and the taper of the stage's fix eases to nothing over 3 m before it. The ground under the stage
    road itself follows the stage road, a crossing included. Earth's own road grading (below) is not ours to change, and neither is what it
    leaves beside a tunnel: Earth grades every road it draws, a motorway's profile over 40 m, with its own cut and fill slopes, and leaves the ground over a
    covered tunnel (its half width and 5 m) as the elevation data has it. The Popoli stage's 650 to 735 m crosses over the A25's tunnel (the line is
    within a metre of its axis there; the plain A25 ways are 35 m off and its viaduct 55 m), which is the ground in that stretch;
  - the ground at either end of a deck, met to the deck's top: a bridge OpenStreetMap tags (`structures` in the
    geometry), and a deck of our own road the game's colliders show, tagged or not (the culvert after the Popoli
    underpass). The latter is a run of points under a wide collider (as wide as a road) that stands over the ground, one point
    of it more than 2.5 m, whose top where it begins and where it ends continues from the road beyond it (within 1 m and 8 cm a
    metre of the gap, at either end: an overpass stands metres over the road there), however far its middle stands over a gully, and
    whose top does not jump (a taller structure beside the road does; a grade and a slight hump do not). The deck's end is found
    by rays every metre along the line from the road, its top measured a little onto the deck and taken back to the end along its
    grade, and the ground ramped to it over the line: a smoothstep as long as the curated road's grade change limit
    (`curate.MAX_GRADE_CHANGE`) asks for that change, 15 m at least, up to 8 m of change, with nothing beyond the end moved (the
    ground under the deck is left). The approach and the exit are the same. A narrow collider (a wall, barrier, rail, pole) is
    neither a deck nor an end (one taken for a deck at the Popoli stage's first hairpin moved the ground and stranded the
    lines);
  - a fix that asks for more than 4 m is not cut off at the cap, which leaves a plateau with the defect's steep sides: its whole
    delta is scaled to the cap and the remainder is spread over the stretch (the log says it).
  A fix is a delta, not a flat core: a cell moves by `w * (the line's fixed height - the ground under the line)` at the
  nearest point of the line, `w` the falloff (1 within 5.5 m of the line, a smoothstep to 0 over the next 6 m), so the cross
  slope and camber and what Earth graded stay and only the defect goes. The delta falls to 0 at the first point past the
  taper, so nothing steps where it starts. Only the stretches fixed and their tapers are worked on (the log says how many,
  of what kind, how long, and the largest change; the cells of the road core that moved 4 cm or more are the lines
  stranded, only there). DecalRoads (other Earth settings) are regenerated after a job (`reclip.lua`, throttled). Bridges and
  tunnels themselves are left alone (the curated spans, from the OpenStreetMap tags or found in the profile, and a deck of ours
  the colliders show, 8 m long at least), as the block's rim is. The log names every stretch fixed (its kind, metres along the
  line, the largest change and where: x, y) and every collider over the line and what it was taken for. Where the car is,
  in the same terms, is shown top right on the Earth level and logged once a second (`where:` lines, `hud.lua`): world
  x, y, z, latitude and longitude, the ground under the car, the speed, and the nearest line with the metres along it, so a
  screenshot can be matched to the shaping's log.
  A menu start does the stage's ground before the stage starts, behind the loading screen (it is held anyway): when the
  launch is reached and the terrain block is in, the ground along the stages is shaped as far as the block reaches, with
  10 ms of work a frame instead of 1.5, and the launch waits for it (20 s at most, and never into the screen's own 420 s
  watchdog: with less than 30 s left of it the shaping is left to the drive). The log says how much of the line the block
  held, the stretches fixed, the cells, the work and the time (`stage ground shaped behind the loading screen`). The loop's
  start sets the same stages again, which is not a second job; a block that arrives later is shaped as it comes, as before,
  and the pre-shaped ground goes back when the launch gives up. An in-game start has no loading screen and shapes as the car goes.
  It is done again after every commit and anchor change; the originals go back when the loop stops, the switch in the
  BeamNG.Dakar window (or the add-on's option) is turned off, or the extension unloads, when the block is still the one shaped
  (Earth's next commit restores everything by itself anyway). The work is spread over frames (1.5 ms of height maths a frame
  plus the grid update of what it touched, the stretch at the car first and in a bigger first slice after a commit), but the
  ground under a moving car never changes between two physics frames (at a swap Earth's own ground came back under the
  car and our shaping landed some frames later, a step of metres twice). The swap's tap shapes the fixed stretches round the
  car (what it covers in half a second and 22 m more, at most 45 m) in the same Lua call, before the next physics step, to
  the heights the old block had there (what lies within the freeze radius whatever it costs, the rest only under 7 ms); a later frame, and a restore, leave the cells within 12 m of a car that moves faster than 2 m/s (and what it covers
  in 0.4 s) and do them once it has passed. No cell is moved more than 4 m (8 m under an overpass). The window has the
  switch (on by default) and a line: stretches fixed, cells written, the largest change, how long ago. From the console:
  `beamngDakar_core.setShaping(false)`.

Every rebase logs `DAKAR-PROBE` lines (where the rally data is against the lat/lon truth). Offline tests:
`python -m pytest tests` (needs `lupa`, and BeamNG.Earth next to this repo or `BEAMNG_EARTH_DIR`).

