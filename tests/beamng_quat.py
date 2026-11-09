"""BeamNG's quaternions, ported from the game's lua/common/mathlib.lua, so the tests can say which way an orientation Dakar
writes points without using anything of Dakar's own: the game's `q * v` (LuaQuat.__mul with a vector), its quaternion
product, quatFromDir (LuaQuat:setFromDir) and the half turn spawn.safeTeleport puts before a rotation it is given
(spawn.lua: `rot = quat(0,0,1,0) * rot`).

The game's `q * v` is v - w t + q x t with t = 2 q x v: the rotation by the conjugate of the textbook formula, so a turn about
+z of `a` takes +y to (sin a, cos a), the compass bearing a, and not to the angle a counter-clockwise from +x.
"""
import math


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def rotate(q, v):
    """The game's `quat * vec3`. q = (x, y, z, w)."""
    qv = q[:3]
    t = tuple(2 * c for c in cross(qv, v))
    c2 = cross(qv, t)
    return tuple(v[i] - q[3] * t[i] + c2[i] for i in range(3))


def mul(a, b):
    """The game's quaternion product (LuaQuat.__mul), (x, y, z, w)."""
    return (a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
            a[3] * b[1] + a[1] * b[3] + a[2] * b[0] - a[0] * b[2],
            a[3] * b[2] + a[2] * b[3] + a[0] * b[1] - a[1] * b[0],
            a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2])


def from_dir(d, up=(0, 0, 1)):
    """quatFromDir(d, up) (LuaQuat:setFromDir): the orientation whose +y is d."""
    n = math.sqrt(sum(c * c for c in d))
    y = tuple(c / n for c in d)
    x = cross(y, up)
    xn = math.sqrt(sum(c * c for c in x))
    x = tuple(c / xn for c in x)
    z = cross(x, y)
    zn = math.sqrt(sum(c * c for c in z))
    z = tuple(c / zn for c in z)
    m = [x, y, z]                              # (the game's matTable rows 0, 1, 2)
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = math.sqrt(trace + 1)
        w, s = s * 0.5, 0.5 / s
        q = [(m[2][1] - m[1][2]) * s, (m[0][2] - m[2][0]) * s, (m[1][0] - m[0][1]) * s]
    else:
        i = 0
        if m[1][1] > m[0][0]:
            i = 1
        if m[2][2] > m[i][i]:
            i = 2
        j = (i + 1) % 3
        k = (j + 1) % 3
        q = [0.0, 0.0, 0.0]
        s = math.sqrt((m[i][i] - m[j][j]) - (m[k][k] - 1))
        q[i] = s * 0.5
        s = 0.5 / s
        q[j] = (m[j][i] + m[i][j]) * s
        q[k] = (m[k][i] + m[i][k]) * s
        w = (m[k][j] - m[j][k]) * s
    n = math.sqrt(q[0] ** 2 + q[1] ** 2 + q[2] ** 2 + w ** 2)
    return (q[0] / n, q[1] / n, q[2] / n, w / n)


HALF_TURN = (0, 0, 1, 0)                       # quat(0,0,1,0)


def heading(q, v=(0, 1, 0)):
    """Degrees counter-clockwise from +x of the horizontal direction `v` (local axes: +y a control's way on, -y a vehicle's front)
    points in the world once the game applies q to it."""
    r = rotate(q, v)
    return math.degrees(math.atan2(r[1], r[0]))


def car_heading(q):
    """The way a vehicle object with orientation q faces: its front is local -y (checkpoints.lua, spawn.lua)."""
    return heading(q, (0, -1, 0))


def off(a, b):
    return abs((a - b + 180) % 360 - 180)
