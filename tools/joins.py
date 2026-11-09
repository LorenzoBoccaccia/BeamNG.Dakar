"""Where one leg of a loop meets the next (GitHub #55): the end of a leg and the start of the leg after it.

A loop is TC0 -> liaison -> TC1 -> special -> TC2 -> liaison -> TC3 -> special ... and every join is one place: the end of
a leg (a time control, or a special's stop control) and the start of the next (the next time control, or a special's start
line). The player is driven into it from behind and carries on straight ahead, so at every join

  * the last RUN_M metres of the leg before it and the first RUN_M metres of the next are straight (no turn of more than
    MAX_TURN_DEG within either: a start line is on a straight), and
  * the way the leg arrives (its last CHORD_M metres) and the way the next leg leaves (its first CHORD_M metres) agree to
    within MAX_DIFF_DEG degrees.

A router leaves legs as it finds them: a transfer that comes down a side road and turns onto the stage's road ends where
the stage starts, but the stage then runs back the way the transfer came (Popoli: road1 turned south on the SR17 to the
foot of the hill, the stage started there and went north). align() moves the join along the road until it is right: past the
turn, on the next leg's road (the first leg is routed again to the new end, the next leg loses its first metres), or back
along the leg that ends there.

Pure (no network): the routing is passed in. Points are (lat, lon, ...) tuples; headings are degrees clockwise from north.
"""
import math

R = 6371008.8
RUN_M = 30.0             # the straight run before a join
MAX_TURN_DEG = 20.0      # the most the heading changes within it
STRICT_TURN_DEG = 8.0    # what a moved join looks for first (a bend 30 m before the line is a straight run only just)
MAX_DIFF_DEG = 15.0      # the most the arriving and the leaving headings differ
CHORD_M = 10.0           # a heading is the chord of this many metres
SAMPLE_M = 5.0           # headings are sampled this often
SHIFT_STEP_M = 10.0      # a join is moved along the road in steps of this many metres
SHIFT_MAX_M = 300.0      # at most this far
DETOUR_M = 60.0          # a leg routed again to a moved join is at most this much longer than the shift explains (one that is
                         # much longer found its way round: a U-turn at the next junction, a detour through a closed road)


def metres(a, b):
    dn = math.radians(b[0] - a[0]) * R
    de = math.radians(b[1] - a[1]) * R * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dn, de)


def bearing(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def angle_diff(a, b):
    """The angle between two headings, 0..180."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def cumulative(pts):
    c = [0.0]
    for a, b in zip(pts, pts[1:]):
        c.append(c[-1] + metres(a, b))
    return c


def length(pts):
    return cumulative(pts)[-1] if len(pts) > 1 else 0.0


def point_at(pts, s, c=None):
    """The point `s` metres along the path (clamped): (lat, lon), plus the height when the points carry one."""
    c = c or cumulative(pts)
    if s <= 0:
        return tuple(pts[0])
    if s >= c[-1]:
        return tuple(pts[-1])
    i = max(1, next(k for k in range(1, len(c)) if c[k] >= s))
    span = c[i] - c[i - 1]
    f = (s - c[i - 1]) / span if span > 1e-9 else 0.0
    a, b = pts[i - 1], pts[i]
    return tuple(a[k] + (b[k] - a[k]) * f for k in range(len(a)))


def heading_between(pts, s0, s1, c=None):
    """The heading of the chord from `s0` to `s1` metres along the path."""
    c = c or cumulative(pts)
    return bearing(point_at(pts, s0, c), point_at(pts, s1, c))


def arriving(pts, chord=CHORD_M):
    """The heading a path arrives with: its last `chord` metres."""
    c = cumulative(pts)
    return heading_between(pts, max(0.0, c[-1] - chord), c[-1], c)


def leaving(pts, chord=CHORD_M):
    """The heading a path leaves with: its first `chord` metres."""
    c = cumulative(pts)
    return heading_between(pts, 0.0, min(chord, c[-1]), c)


def tail_turn(pts, run=RUN_M, chord=CHORD_M, sample=SAMPLE_M):
    """The most any two headings of the path's last `run` metres differ (headings of `chord`-metre chords, one every
    `sample` metres): the turn within the straight run before the end."""
    c = cumulative(pts)
    total = c[-1]
    s0 = max(0.0, total - run)
    hs, s = [], s0
    while s + chord <= total + 1e-6:
        hs.append(heading_between(pts, s, s + chord, c))
        s += sample
    if len(hs) < 2:
        return 0.0
    return max(angle_diff(a, b) for a in hs for b in hs)


def head_turn(pts, run=RUN_M, chord=CHORD_M, sample=SAMPLE_M):
    """The same for the first `run` metres of a path."""
    return tail_turn(list(reversed(pts)), run, chord, sample)


def check(a, b, max_turn=MAX_TURN_DEG):
    """How well the leg `a` runs into the leg `b`: { tailTurn, headTurn, diff, ok } (degrees)."""
    tt, ht = tail_turn(a), head_turn(b)
    diff = angle_diff(arriving(a), leaving(b))
    return {"tailTurn": round(tt, 1), "headTurn": round(ht, 1), "diff": round(diff, 1),
            "ok": tt <= max_turn and ht <= max_turn and diff <= MAX_DIFF_DEG}


def project_s(pts, p, c=None):
    """The metres along `pts` of the vertex nearest `p`."""
    c = c or cumulative(pts)
    i = min(range(len(pts)), key=lambda k: metres(pts[k], p))
    return c[i]


def after(pts, t, join):
    """pts from `t` metres along, starting at the point `join` (the snapped place the previous leg ends at)."""
    c = cumulative(pts)
    k = next((i for i in range(len(pts)) if c[i] > t + 1.0), len(pts) - 1)
    return [tuple(join)] + [tuple(p) for p in pts[k:]]


def align(a, b, reroute_a, reroute_b, max_shift=SHIFT_MAX_M, step=SHIFT_STEP_M):
    """Make the leg `a` run into the leg `b` as check() asks, moving the join where they meet (their last and first point).

    a, b: lists of points. reroute_a(end) -> the leg a again, ending at `end` (a point of b's road); reroute_b(start) -> the
    leg b again, starting at `start` (a point of a's road). Candidates by growing shift t: the join `t` metres along b
    (a rerouted to it, b without its first t metres), then `t` metres before a's end (a cut short there, b rerouted from it).
    The first pass looks for a really straight run (STRICT_TURN_DEG), the second takes what MAX_TURN_DEG allows; a join that
    is right as it is stays. Returns (a, b, info): info = { mode: 'kept' | 'extend' | 'trim' | 'none', shiftM, report }
    where 'none' means nothing within max_shift made it right and the legs are returned as they were."""
    rep = check(a, b)
    if rep["ok"] and check(a, b, STRICT_TURN_DEG)["ok"]:
        return a, b, {"mode": "kept", "shiftM": 0.0, "report": rep}
    ca, cb = cumulative(a), cumulative(b)
    cache = {}

    def tried(kind, t, fn, arg):
        if (kind, t) not in cache:
            try:
                cache[(kind, t)] = fn(arg)
            except Exception:
                cache[(kind, t)] = None
        return cache[(kind, t)]

    for limit in (STRICT_TURN_DEG, MAX_TURN_DEG):
        if limit == MAX_TURN_DEG and rep["ok"]:
            return a, b, {"mode": "kept", "shiftM": 0.0, "report": rep}
        t = step
        while t <= max_shift:
            if t < cb[-1] - 2 * RUN_M:                                  # (the next leg keeps a run of its own)
                a2 = tried("a", t, reroute_a, point_at(b, t, cb))
                if a2 and len(a2) >= 2 and length(a2) <= ca[-1] + t + DETOUR_M:
                    b2 = after(b, t, a2[-1])
                    r = check(a2, b2, limit)
                    if r["ok"]:
                        return a2, b2, {"mode": "extend", "shiftM": t, "report": r}
            if t < ca[-1] - 2 * RUN_M:
                b2 = tried("b", t, reroute_b, point_at(a, ca[-1] - t, ca))
                if b2 and len(b2) >= 2 and length(b2) <= cb[-1] + t + DETOUR_M:
                    k = next(i for i in range(len(a)) if ca[i] >= ca[-1] - t)
                    a2 = [tuple(q) for q in a[:k]] + [tuple(b2[0])]
                    r = check(a2, b2, limit)
                    if r["ok"]:
                        return a2, b2, {"mode": "trim", "shiftM": t, "report": r}
            t += step
    return a, b, {"mode": "none", "shiftM": 0.0, "report": rep}


def vias_after(vias, pts, t, start):
    """The via points of a leg whose first t metres were cut: `start` and those of the old vias that lie further along
    `pts` than t (the ones before it would send a route back)."""
    c = cumulative(pts)
    keep = [v for v in vias[1:] if project_s(pts, v, c) > t + 5.0]
    return [list(start[:2])] + keep
