-- Stage preload: the data along a special stage fetched before the player drives it.
--
-- BeamNG.Earth's server can prefetch the corridor of a planned route: a `route` message (id, lat0, lon0, lat1, lon1,
-- download = 'route') is answered with `route_result` (the points of the route its own router plans) and the server
-- then fetches the whole corridor into its disk caches (elevation, map cells for the roads and buildings, imagery) and
-- pushes `route_download { total, done, active }` until it is through (server/earthd.py route(), routeahead.py).
-- Earth's own GPS uses the same message for its route. This module asks for the stage's start to finish, reads what
-- comes back, and tells the caller when the corridor is in.
--
-- It is point to point and routed by Earth's router, not along the stage line: the route may take another road for
-- stretches, so how far it strays from the stage line is measured and logged (the corridor of the stage itself is what
-- matters; the route's is what is fetched). A newer route message supersedes the older one on the server, so the stages of
-- an edition are fetched one after the other, and so does the player's own GPS route if they have one.
--
-- Pure apart from the env it is given: send(msg) -> bool, take(id) -> the route_result of that id or nil, now() -> s,
-- log(level, msg). Messages: onDownload(msg) for each route_download, tick() every frame.
local M = {}

M.CAP_S = 120               -- the gate never waits longer than this for the first stage
M.PLAN_WAIT_S = 30          -- ... the router's answer
M.PUSH_WAIT_S = 10          -- ... the first route_download after the answer (the server pushes one even for nothing to fetch)
M.LOG_EVERY_S = 5
M.ID_BASE = 4100000         -- (Earth's GPS counts up from 1)
M.STRAY_STEP_M = 40
M.STRAY_FAR_M = 30

local sqrt, cos, rad = math.sqrt, math.cos, math.rad

-- How far a route (Earth's, { {lat, lon}, ... }) strays from the stage line ({ {lat, lon, h}, ... }): the line's points
-- (every STRAY_STEP_M) against the route's segments, and the route's points against the line.
-- Returns { lineMax, lineMean, lineFarShare (of the line's points further than STRAY_FAR_M), routeMax, routeKm, lineKm }.
function M.stray(route, line)
  if not (route and #route >= 2 and line and #line >= 2) then return nil end
  local lat0, lon0 = line[1][1], line[1][2]
  local kx, ky = 111320 * cos(rad(lat0)), 110574
  local function xy(p) return (p[2] - lon0) * kx, (p[1] - lat0) * ky end
  local function toXY(list)
    local X, Y = {}, {}
    for i, p in ipairs(list) do X[i], Y[i] = xy(p) end
    return X, Y
  end
  local function length(X, Y)
    local l = 0
    for i = 2, #X do l = l + sqrt((X[i] - X[i - 1]) ^ 2 + (Y[i] - Y[i - 1]) ^ 2) end
    return l
  end
  local function distTo(px, py, X, Y)
    local best = math.huge
    for i = 1, #X - 1 do
      local dx, dy = X[i + 1] - X[i], Y[i + 1] - Y[i]
      local l2 = dx * dx + dy * dy
      local t = l2 > 1e-9 and ((px - X[i]) * dx + (py - Y[i]) * dy) / l2 or 0
      if t < 0 then t = 0 elseif t > 1 then t = 1 end
      local ex, ey = px - (X[i] + dx * t), py - (Y[i] + dy * t)
      local d2 = ex * ex + ey * ey
      if d2 < best then best = d2 end
    end
    return sqrt(best)
  end
  local RX, RY = toXY(route)
  local LX, LY = toXY(line)
  -- the line thinned to every STRAY_STEP_M, the route to every 4th point beyond that: this is a log line, not a survey
  local function thin(X, Y, step)
    local ox, oy, acc = { X[1] }, { Y[1] }, 0
    for i = 2, #X do
      acc = acc + sqrt((X[i] - X[i - 1]) ^ 2 + (Y[i] - Y[i - 1]) ^ 2)
      if acc >= step or i == #X then ox[#ox + 1], oy[#oy + 1], acc = X[i], Y[i], 0 end
    end
    return ox, oy
  end
  local TX, TY = thin(LX, LY, M.STRAY_STEP_M)
  local sum, mx, far = 0, 0, 0
  for i = 1, #TX do
    local d = distTo(TX[i], TY[i], RX, RY)
    sum = sum + d
    if d > mx then mx = d end
    if d > M.STRAY_FAR_M then far = far + 1 end
  end
  local rtx, rty = thin(RX, RY, M.STRAY_STEP_M)
  local rmax = 0
  for i = 1, #rtx do
    local d = distTo(rtx[i], rty[i], LX, LY)
    if d > rmax then rmax = d end
  end
  return { lineMax = mx, lineMean = sum / #TX, lineFarShare = far / #TX, routeMax = rmax,
    routeKm = length(RX, RY) / 1000, lineKm = length(LX, LY) / 1000 }
end

local P = {}
P.__index = P

function M.new(env)
  return setmetatable({ env = env, queue = {}, cur = nil, nextId = M.ID_BASE, finished = {}, first = nil }, P)
end

local function log(self, level, msg) if self.env.log then self.env.log(level, msg) end end

-- Ask for the corridor of each stage, in order: requests = { { name, lat0, lon0, lat1, lon1, line = { {lat, lon, h}... } } }.
-- The first is the one the gate waits for. Returns the phase of the first at once ('offline' when nothing can be sent).
function P:start(requests)
  self.queue, self.cur, self.first, self.finished = {}, nil, nil, {}
  for _, r in ipairs(requests) do self.queue[#self.queue + 1] = r end
  self.startedAt = self.env.now()
  self.first = self.queue[1] and { name = self.queue[1].name } or nil
  self:advance()
  return self.cur and self.cur.phase or (self.first and self.finished[self.first.name]) or 'none'
end

function P:advance()
  local r = table.remove(self.queue, 1)
  if not r then self.cur = nil; return end
  self.nextId = self.nextId + 1
  local now = self.env.now()
  local cur = { req = r, id = self.nextId, phase = 'planning', since = now, total = 0, done = 0, loggedAt = -1e9, name = r.name }
  self.cur = cur
  local ok = self.env.send({ type = 'route', id = cur.id, lat0 = r.lat0, lon0 = r.lon0, lat1 = r.lat1, lon1 = r.lon1, download = 'route' })
  if not ok then
    log(self, 'W', 'preload ' .. r.name .. ': Earth\'s server is not connected; nothing to fetch ahead')
    self:finish('offline')
    return
  end
  log(self, 'I', string.format('preload %s: route asked of Earth\'s server (%.5f, %.5f -> %.5f, %.5f), whole corridor', r.name, r.lat0, r.lon0, r.lat1, r.lon1))
end

-- A stage's request is over (any way): remember how, and go on to the next stage.
function P:finish(phase)
  local cur = self.cur
  if not cur then return end
  self.finished[cur.name] = phase
  cur.phase = phase
  self.last = cur
  self.cur = nil
  self:advance()
end

-- Earth's route_download push { total, done, active }; only the one after our route's answer counts.
function P:onDownload(msg)
  local cur = self.cur
  if not cur or cur.phase ~= 'downloading' or type(msg) ~= 'table' then return end
  cur.pushed = true
  cur.total, cur.done = tonumber(msg.total) or 0, tonumber(msg.done) or 0
  if msg.active ~= true or (cur.total > 0 and cur.done >= cur.total) then
    log(self, 'I', string.format('preload %s: the corridor is in (%d of %d, %.0f s)', cur.name, cur.done, cur.total, self.env.now() - cur.since))
    self:finish('done')
  end
end

-- Every frame: the router's answer, the waits and the cap, the progress line.
function P:tick()
  local cur = self.cur
  if not cur then return end
  local now = self.env.now()
  if cur.phase == 'planning' then
    local res = self.env.take(cur.id)
    if res then
      if res.error or not res.points or #res.points < 2 then
        log(self, 'W', 'preload ' .. cur.name .. ': Earth\'s router gave no route (' .. tostring(res.error or 'empty') .. '); nothing fetched ahead')
        return self:finish('failed')
      end
      cur.phase, cur.answeredAt = 'downloading', now
      local s = M.stray(res.points, cur.req.line)
      cur.stray = s
      if s then
        log(self, 'I', string.format('preload %s: Earth\'s route is %.1f km (the stage line %.1f km); the stage line is %.1f m from it on average, ' ..
          '%.0f m at most, %.0f%% of it more than %d m away; the route is up to %.0f m from the stage line',
          cur.name, s.routeKm, s.lineKm, s.lineMean, s.lineMax, s.lineFarShare * 100, M.STRAY_FAR_M, s.routeMax))
      end
    elseif now - cur.since > M.PLAN_WAIT_S then
      log(self, 'W', string.format('preload %s: no answer from Earth\'s router after %d s; going on without', cur.name, M.PLAN_WAIT_S))
      return self:finish('failed')
    end
  elseif cur.phase == 'downloading' then
    if not cur.pushed and now - cur.answeredAt > M.PUSH_WAIT_S then
      log(self, 'W', string.format('preload %s: no download progress after %d s; going on without', cur.name, M.PUSH_WAIT_S))
      return self:finish('failed')
    end
    if cur.pushed and now - cur.loggedAt >= M.LOG_EVERY_S then
      cur.loggedAt = now
      log(self, 'I', string.format('preload %s: %d of %d (%.0f%%), %.0f s', cur.name, cur.done, cur.total,
        cur.total > 0 and cur.done / cur.total * 100 or 0, now - cur.since))
    end
  end
  -- the first stage is the one the caller waits for: it ends at the cap, the download itself goes on in the server
  if cur and self.first and cur.name == self.first.name and now - self.startedAt > M.CAP_S then
    log(self, 'W', string.format('preload %s: not through after %d s (%d of %d); going on, the server keeps fetching', cur.name, M.CAP_S, cur.done, cur.total))
    self:finish('capped')
  end
end

-- Is the first stage's corridor settled (in, failed, offline or capped)? True when there is nothing to wait for.
function P:gateOpen()
  if not self.first then return true end
  return self.finished[self.first.name] ~= nil
end

-- { phase, total, done, name } for the window and the log
function P:state()
  local c = self.cur
  if c then return { phase = c.phase, total = c.total, done = c.done, name = c.name } end
  local l = self.last
  if l then return { phase = l.phase, total = l.total, done = l.done, name = l.name } end
  return nil
end

return M
