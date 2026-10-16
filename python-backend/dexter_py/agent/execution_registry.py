"""Redis-backed, fenced and idempotent financial research executions.

All state transitions, queue changes, event publication and final conversation
turn commits happen inside Redis Lua scripts. The registry must NOT use a
process-local fallback in production. Requires durable Redis persistence.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from typing import Any

from redis.asyncio import Redis


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now(lua: bool = True) -> str:
    return "local tick=redis.call('TIME'); local now=tonumber(tick[1])+tonumber(tick[2])/1000000"


def _emit() -> str:
    return """
local function emit(execution_id, state)
  redis.call('XADD', KEYS[3] .. execution_id, 'MAXLEN', '~', 200, '*',
             'state', state, 'execution_id', execution_id,
             'attempt', redis.call('HGET',KEYS[2] .. execution_id,'attempt_count') or '0',
             'generation', redis.call('HGET',KEYS[2] .. execution_id,'generation') or '0')
end
"""


ADMIT = _now() + _emit() + """
local old=redis.call('GET',KEYS[1])
if old and old~='CANCELLED' and redis.call('EXISTS',KEYS[2] .. old)==0 then
  redis.call('DEL',KEYS[1]); old=false
end
if old then
  if old=='CANCELLED' then return {'CANCELLED',''} end
  if redis.call('HGET',KEYS[2] .. old,'fingerprint')~=ARGV[4] then
    return {'CONFLICT',old}
  end
  return {'EXISTING',old}
end
local recent=redis.call('INCR',KEYS[6])
if recent==1 then redis.call('EXPIRE',KEYS[6],60) end
if recent>10 then return {'CAPACITY',''} end
redis.call('ZREMRANGEBYSCORE',KEYS[5],'-inf',now-86400)
local active=0
for _,id in ipairs(redis.call('ZRANGE',KEYS[5],0,-1)) do
  local state=redis.call('HGET',KEYS[2] .. id,'state')
  if state=='QUEUED' or state=='RUNNING' or state=='CANCEL_REQUESTED' then active=active+1 end
end
if active>=tonumber(ARGV[9]) or redis.call('ZCARD',KEYS[4])>=1000 then return {'CAPACITY',''} end
redis.call('ZADD',KEYS[5],now,ARGV[1])
redis.call('EXPIRE',KEYS[5],90000)
redis.call('HSET', KEYS[2] .. ARGV[1],
  'execution_id', ARGV[1], 'principal', ARGV[2], 'session_id', ARGV[3],
  'fingerprint', ARGV[4], 'query', ARGV[5], 'state', 'QUEUED',
  'created_at', tostring(now), 'updated_at', tostring(now),
  'deadline_at', tostring(now+tonumber(ARGV[6])), 'generation','0',
  'owner','', 'lease_expires_at','0', 'model_version',ARGV[7],
  'attempt_count','0','protocol_version','1','max_attempts',ARGV[10])
redis.call('HSET',KEYS[2] .. ARGV[1],'request_options',ARGV[11],
  'parent_execution_id',ARGV[12],'configuration_version','2')
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[8]), 'NX')
redis.call('ZADD', KEYS[4], now, ARGV[1])
emit(ARGV[1],'QUEUED')
return {'NEW',ARGV[1]}
"""

CLAIM = _now() + _emit() + """
local ids=redis.call('ZRANGE', KEYS[1], 0, 255)
for _,id in ipairs(ids) do
  local k=KEYS[2] .. id
  local state=redis.call('HGET',k,'state')
  local deadline=tonumber(redis.call('HGET',k,'deadline_at') or '0')
  local expires=tonumber(redis.call('HGET',k,'lease_expires_at') or '0')
  if state=='CANCEL_REQUESTED' and expires<=now then
    redis.call('HSET',k,'state','CANCELLED','updated_at',tostring(now),'completed_at',tostring(now))
    redis.call('ZREM',KEYS[1],id)
    emit(id,'CANCELLED')
    redis.call('EXPIRE',k,86400)
    redis.call('EXPIRE',KEYS[3] .. id,86400)
  elseif state~='QUEUED' and state~='RUNNING' and state~='CANCEL_REQUESTED' then
    redis.call('ZREM',KEYS[1],id)
  elseif deadline<=now and (state=='QUEUED' or expires<=now) then
    redis.call('HSET',k,'state','TIMED_OUT','updated_at',tostring(now),'completed_at',tostring(now))
    redis.call('ZREM',KEYS[1],id)
    emit(id,'TIMED_OUT')
    redis.call('EXPIRE',k,86400)
    redis.call('EXPIRE',KEYS[3] .. id,86400)
  elseif (state=='QUEUED' or state=='RUNNING') and expires<=now and
      tonumber(redis.call('HGET',k,'attempt_count') or '0')>=tonumber(redis.call('HGET',k,'max_attempts') or '3') then
    redis.call('HSET',k,'state','FAILED','failure_code','ATTEMPTS_EXHAUSTED',
      'updated_at',tostring(now),'completed_at',tostring(now))
    redis.call('ZREM',KEYS[1],id)
    emit(id,'FAILED')
    redis.call('EXPIRE',k,86400)
    redis.call('EXPIRE',KEYS[3] .. id,86400)
  elseif state=='QUEUED' or (state=='RUNNING' and expires<=now) then
    local session_lock=KEYS[4] .. redis.call('HGET',k,'principal') .. ':' .. redis.call('HGET',k,'session_id')
    if redis.call('EXISTS',session_lock)==0 then
    local generation=redis.call('HINCRBY',k,'generation',1)
    redis.call('HINCRBY',k,'attempt_count',1)
    redis.call('HSET',k,'state','RUNNING','owner',ARGV[1],
               'lease_expires_at',tostring(now+tonumber(ARGV[2])),
               'updated_at',tostring(now),'started_at',tostring(now))
    redis.call('SET',session_lock,id .. ':' .. tostring(generation),'EX',tonumber(ARGV[2]))
    redis.call('ZADD',KEYS[1],now+tonumber(ARGV[2]),id)
    emit(id,'RUNNING')
    return {id,tostring(generation)}
    else
      redis.call('ZADD',KEYS[1],now+tonumber(ARGV[2]),id)
    end
  end
end
return {}
"""

HEARTBEAT = _now() + """
local k=KEYS[1]
if redis.call('HGET',k,'owner')~=ARGV[1]
   or redis.call('HGET',k,'generation')~=ARGV[2]
   or tonumber(redis.call('HGET',k,'lease_expires_at') or '0')<=now then
  return 'LOST'
end
local state=redis.call('HGET',k,'state')
if state=='CANCEL_REQUESTED' then return 'CANCEL' end
if state~='RUNNING' then return 'LOST' end
local session_lock=KEYS[2] .. redis.call('HGET',k,'principal') .. ':' .. redis.call('HGET',k,'session_id')
if redis.call('GET',session_lock)~=ARGV[4] then return 'LOST' end
redis.call('EXPIRE',session_lock,tonumber(ARGV[3]))
redis.call('ZADD',KEYS[3],now+tonumber(ARGV[3]),ARGV[5])
redis.call('HSET',k,'lease_expires_at',tostring(now+tonumber(ARGV[3])),
                 'updated_at',tostring(now))
return 'OK'
"""

CANCEL = _now() + _emit() + """
local k=KEYS[1]
if redis.call('HGET',k,'principal')~=ARGV[1] then return 'NOT_FOUND' end
local state=redis.call('HGET',k,'state')
if state=='QUEUED' then
  redis.call('HSET',k,'state','CANCELLED','cancel_requested_at',tostring(now),
    'completed_at',tostring(now),'updated_at',tostring(now))
  redis.call('ZREM',KEYS[4],ARGV[2])
  emit(ARGV[2],'CANCELLED')
  redis.call('EXPIRE',k,86400)
  redis.call('EXPIRE',KEYS[3] .. ARGV[2],86400)
  return 'CANCELLED'
end
if state=='RUNNING' then
  redis.call('HSET',k,'state','CANCEL_REQUESTED','cancel_requested_at',tostring(now),
    'updated_at',tostring(now))
  emit(ARGV[2],'CANCEL_REQUESTED')
  return 'CANCEL_REQUESTED'
end
return state or 'NOT_FOUND'
"""

FINISH = _now() + _emit() + """
local k=KEYS[1]
local state=redis.call('HGET',k,'state')
if redis.call('HGET',k,'owner')~=ARGV[2]
  or redis.call('HGET',k,'generation')~=ARGV[3]
  or tonumber(redis.call('HGET',k,'lease_expires_at') or '0')<=now then
  return 'LOST'
end
local outcome=ARGV[4]
if state=='CANCEL_REQUESTED' then outcome='CANCELLED'
elseif state~='RUNNING' then return 'LOST' end
if outcome~='CANCELLED' and tonumber(redis.call('HGET',k,'deadline_at') or '0')<=now then
  outcome='TIMED_OUT'
end
if outcome~='SUCCEEDED' and outcome~='FAILED' and
   outcome~='CANCELLED' and outcome~='TIMED_OUT' then return 'INVALID' end
redis.call('HSET',k,'state',outcome,'updated_at',tostring(now),
            'completed_at',tostring(now),'owner','',
            'lease_expires_at','0','failure_code',ARGV[6])
if (outcome=='SUCCEEDED' or outcome=='FAILED') and ARGV[5]~='{}' then
  redis.call('HSET',k,'result',ARGV[5])
end
if outcome=='CANCELLED' then redis.call('HSET',k,'failure_code','EXECUTION_CANCELLED') end
if outcome=='TIMED_OUT' then redis.call('HSET',k,'failure_code','EXECUTION_TIMED_OUT') end
if outcome=='SUCCEEDED' then
  redis.call('RPUSH',KEYS[5],ARGV[7])
  redis.call('LTRIM',KEYS[5],-20,-1)
  redis.call('EXPIRE',KEYS[5],tonumber(ARGV[8]))
end
local session_lock=KEYS[6] .. redis.call('HGET',k,'principal') .. ':' .. redis.call('HGET',k,'session_id')
if redis.call('GET',session_lock)==ARGV[1] .. ':' .. ARGV[3] then
  redis.call('DEL',session_lock)
end
redis.call('ZREM',KEYS[4],ARGV[1])
emit(ARGV[1],outcome)
redis.call('EXPIRE',k,tonumber(ARGV[8]))
redis.call('EXPIRE',KEYS[3] .. ARGV[1],tonumber(ARGV[8]))
return outcome
"""


PROGRESS = _now() + """
local k=KEYS[1]
if redis.call('HGET',k,'owner')~=ARGV[1]
   or redis.call('HGET',k,'generation')~=ARGV[2]
   or redis.call('HGET',k,'state')~='RUNNING'
   or tonumber(redis.call('HGET',k,'lease_expires_at') or '0')<=now then
  return 'LOST'
end
redis.call('HSET',k,'current_phase',ARGV[3],'updated_at',tostring(now))
redis.call('XADD',KEYS[2],'MAXLEN','~',200,'*',
  'state','RUNNING','type','phase.started','phase',ARGV[3])
return 'OK'
"""


CANCEL_BY_KEY = _now() + _emit() + """
local existing=redis.call('GET',KEYS[1])
if not existing then
  redis.call('SET',KEYS[1],'CANCELLED','EX',86400,'NX')
  return 'CANCEL_PENDING'
end
if existing=='CANCELLED' then return 'CANCEL_PENDING' end
local k=KEYS[2] .. existing
if redis.call('HGET',k,'principal')~=ARGV[1] then return 'NOT_FOUND' end
local state=redis.call('HGET',k,'state')
if state=='QUEUED' then
  redis.call('HSET',k,'state','CANCELLED','cancel_requested_at',tostring(now),
    'updated_at',tostring(now),'completed_at',tostring(now))
  redis.call('ZREM',KEYS[4],existing)
  emit(existing,'CANCELLED')
  redis.call('EXPIRE',k,86400)
  redis.call('EXPIRE',KEYS[3] .. existing,86400)
  return 'CANCELLED'
end
if state=='RUNNING' then
  redis.call('HSET',k,'state','CANCEL_REQUESTED','cancel_requested_at',tostring(now),
    'updated_at',tostring(now))
  emit(existing,'CANCEL_REQUESTED')
  return 'CANCEL_REQUESTED'
end
return state or 'NOT_FOUND'
"""


TERMINAL = frozenset({"SUCCEEDED", "CANCELLED", "FAILED", "TIMED_OUT"})

# Source authorization, eligibility, admission and successor linkage share one
# transaction. Different concurrent retry tokens cannot create two successors.
RETRY = """
if redis.call('HGET',KEYS[7],'principal')~=ARGV[2] then return {'NOT_FOUND',''} end
local state=redis.call('HGET',KEYS[7],'state')
if state~='FAILED' and state~='TIMED_OUT' then return {'INELIGIBLE',''} end
local child=redis.call('HGET',KEYS[7],'retry_execution_id')
if child then
  if redis.call('GET',KEYS[1])~=child then return {'CONFLICT',child} end
end
local function admission()
""" + ADMIT + """
end
local reply=admission()
if reply[1]=='NEW' or reply[1]=='EXISTING' then
  redis.call('HSET',KEYS[7],'retry_execution_id',reply[2])
end
return reply
"""


class ExecutionConflict(ValueError):
    pass


class ExecutionCapacity(ValueError):
    pass


class RetryIneligible(ValueError):
    pass


class ExecutionRegistry:
    def __init__(self, client: Redis, *, prefix: str = "financialagentia:"):
        self.redis = client
        self.retention_seconds = int(os.getenv("RESEARCH_RETENTION_SECONDS", "86400"))
        if not 3600 <= self.retention_seconds <= 31536000:
            raise ValueError("Research retention must be between one hour and one year")
        if not prefix or any(c in prefix for c in "{} \t\n"):
            raise ValueError("Invalid registry prefix")
        self.prefix = prefix
        self.execution_prefix = prefix + "exec:"
        self.events_prefix = prefix + "events:"
        self.queue = prefix + "queue:active"
        self.session_owner_prefix = prefix + "session_owner:"

    def _policy(self, script: str) -> str:
        # Only validated numeric configuration is substituted. No request text
        # ever becomes executable Lua. Legacy 24-hour defaults remain unchanged.
        return script.replace("86400", str(self.retention_seconds)).replace("90000", str(self.retention_seconds + 3600))

    @classmethod
    def from_env(cls) -> "ExecutionRegistry":
        url = os.getenv("REDIS_URL")
        if not url:
            raise RuntimeError("REDIS_URL required for durable financial research execution")
        return cls(Redis.from_url(url, decode_responses=True, socket_connect_timeout=3, socket_timeout=5),
                   prefix=os.getenv("EXECUTION_REDIS_PREFIX", "financialagentia:"))

    async def worker_heartbeat(self, worker_id: str):
        tick = await self.redis.time()
        await self.redis.zadd(self.prefix + "workers", {worker_id: float(tick[0]) + 30})
        await self.redis.expire(self.prefix + "workers", 60)

    async def workers_ready(self) -> bool:
        tick = await self.redis.time()
        await self.redis.zremrangebyscore(self.prefix + "workers", "-inf", float(tick[0]))
        return bool(await self.redis.zcard(self.prefix + "workers"))

    async def close(self):
        await self.redis.aclose()

    async def health(self) -> bool:
        return bool(await self.redis.ping())

    def principal(self, subject: str) -> str:
        return _sha(subject)

    def events_key(self, execution_id: str) -> str:
        return self.events_prefix + str(uuid.UUID(execution_id))

    def _key(self, execution_id: str) -> str:
        return self.execution_prefix + str(uuid.UUID(execution_id))

    def history_key(self, principal: str, session_id: str) -> str:
        return self.prefix + "history:" + principal + ":" + str(uuid.UUID(session_id))

    async def admit(self, *, principal: str, session_id: str, query: str,
                    idempotency_key: str, model: str,
                    deadline_seconds: int = 300,
                    request_session_id: str | None = "supplied",
                    max_active: int = 5, max_attempts: int = 3,
                    request_options: dict | None = None,
                    parent_execution_id: str | None = None,
                    admission_guard: tuple[str, int] | None = None) -> tuple[str, bool]:
        if not 1 <= len(query) <= 10000 or not query.strip():
            raise ValueError("Invalid research query")
        if not 8 <= len(idempotency_key) <= 128:
            raise ValueError("Idempotency key must contain 8-128 characters")
        if not 1 <= deadline_seconds <= 3600:
            raise ValueError("Invalid execution deadline")
        session_id = str(uuid.UUID(session_id))
        if not 1 <= max_active <= 100 or not 1 <= max_attempts <= 10:
            raise ValueError("Invalid execution limits")
        # Preserve absence in the request fingerprint, rather than the randomly
        # generated session. Retries of an admission without a session are identical.
        fingerprint_session = session_id if request_session_id == "supplied" else request_session_id
        options = json.dumps(request_options or {}, sort_keys=True, separators=(",", ":"))
        # Keep legacy fingerprints identical when no new options were supplied.
        inputs = [fingerprint_session, query, model]
        if request_options or parent_execution_id:
            inputs += [options, parent_execution_id]
        fingerprint = _sha(json.dumps(inputs, separators=(",", ":")))
        existing_key = self.prefix + "idem:" + _sha(principal + ":" + idempotency_key)
        execution_id = str(uuid.uuid4())
        keys = [existing_key, self.execution_prefix, self.events_prefix,
                self.queue, self.prefix + "jobs:" + principal, self.prefix + "admission_rate:" + principal]
        if parent_execution_id:
            keys.append(self._key(parent_execution_id))
        script = self._policy(RETRY if parent_execution_id else ADMIT)
        if admission_guard:
            if parent_execution_id:
                raise ValueError("Retry cannot use admission guard")
            guard_key, guard_version = admission_guard
            if not guard_key.startswith(self.prefix + "platform:v1:schedule:") or type(guard_version) is not int:
                raise ValueError("Invalid schedule admission guard")
            keys.append(guard_key)
            script = ("local raw=redis.call('GET',KEYS[7]); if not raw then return {'CONFLICT',''} end; "
                      "local guard=cjson.decode(raw); if not guard.data.enabled or guard.version~=" + str(guard_version) +
                      " then return {'CONFLICT',''} end; " + script)
        reply = await self.redis.eval(script, len(keys), *keys,
                                      execution_id, principal, session_id,
                                      fingerprint, query, deadline_seconds, model, self.retention_seconds, max_active, max_attempts,
                                      options, parent_execution_id or "")
        if reply[0] == "NOT_FOUND":
            raise LookupError("Execution not found")
        if reply[0] == "INELIGIBLE":
            raise RetryIneligible("Only failed or timed-out executions can be retried")
        if reply[0] == "CAPACITY":
            raise ExecutionCapacity("Active research limit reached")
        if reply[0] == "CANCELLED":
            raise ExecutionConflict("Execution was cancelled before admission")
        if reply[0] == "CONFLICT":
            raise ExecutionConflict("Idempotency key already used for a different request")
        return reply[1], reply[0] == "NEW"

    async def retry(self, execution_id: str, principal: str, idempotency_key: str,
                    model: str, **limits) -> tuple[str, bool]:
        record = await self.get(execution_id, principal)
        if record is None:
            raise LookupError("Execution not found")
        return await self.admit(principal=principal, session_id=record["session_id"],
                                query=record["query"], model=model, idempotency_key=idempotency_key,
                                request_options=record.get("request_options", {}),
                                parent_execution_id=execution_id, **limits)

    async def cancel_by_key(self, principal: str, idempotency_key: str) -> str:
        if not 8 <= len(idempotency_key) <= 128:
            raise ValueError("Invalid idempotency key")
        key = self.prefix + "idem:" + _sha(principal + ":" + idempotency_key)
        return await self.redis.eval(
            self._policy(CANCEL_BY_KEY), 4, key, self.execution_prefix,
            self.events_prefix, self.queue, principal)

    async def get(self, execution_id: str, principal: str, *, include_artifacts: bool = False) -> dict[str, Any] | None:
        result = await self.redis.hgetall(self._key(execution_id))
        if not result or result.get("principal") != principal:
            return None
        safe = {key: value for key, value in result.items()
                if key not in {"principal", "fingerprint", "owner"} and (include_artifacts or key != "artifacts")}
        for key in ("result", "artifacts", "request_options"):
            if key in safe:
                safe[key] = json.loads(safe[key])
        return safe

    async def claim(self, worker_id: str, lease_seconds: int = 20) -> tuple[str, int] | None:
        if not worker_id or type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("Invalid lease")
        reply = await self.redis.eval(self._policy(CLAIM), 4, self.queue,
                                      self.execution_prefix, self.events_prefix,
                                      self.session_owner_prefix,
                                      worker_id, lease_seconds)
        return (reply[0], int(reply[1])) if reply else None

    async def heartbeat(self, execution_id: str, worker_id: str,
                        generation: int, lease_seconds: int = 20) -> str:
        return await self.redis.eval(HEARTBEAT, 3, self._key(execution_id),
                                     self.session_owner_prefix, self.queue, worker_id, generation,
                                     lease_seconds, execution_id + ":" + str(generation), execution_id)

    async def cancel(self, execution_id: str, principal: str) -> str:
        return await self.redis.eval(self._policy(CANCEL), 4, self._key(execution_id),
                                     self.execution_prefix, self.events_prefix,
                                     self.queue, principal, execution_id)

    async def finish(self, execution_id: str, worker_id: str, generation: int,
                     outcome: str, *, result: dict | None = None,
                     failure_code: str = "") -> str:
        record = await self.redis.hgetall(self._key(execution_id))
        if not record:
            return "LOST"
        key = self.history_key(record["principal"], record["session_id"])
        payload = json.dumps(result, separators=(",", ":"), default=str) if result else "{}"
        turn = json.dumps({"execution_id": execution_id,
                           "query": record["query"],
                           "answer": (result or {}).get("answer", "")},
                          separators=(",", ":"))
        return await self.redis.eval(FINISH, 6, self._key(execution_id),
                                     self.execution_prefix, self.events_prefix,
                                     self.queue, key, self.session_owner_prefix,
                                     execution_id, worker_id,
                                     generation, outcome, payload, failure_code,
                                     turn, self.retention_seconds)

    async def publish_phase(self, execution_id: str, worker_id: str,
                            generation: int, phase: str) -> str:
        if phase not in {"understand", "plan", "execute", "reflect", "answer", "validate"}:
            raise ValueError("Unknown research phase")
        return await self.redis.eval(
            PROGRESS, 2, self._key(execution_id), self.events_key(execution_id),
            worker_id, generation, phase)

    async def history(self, principal: str, session_id: str) -> list[dict]:
        items = await self.redis.lrange(self.history_key(principal, session_id), 0, -1)
        return [json.loads(item) for item in items]

    async def list_jobs(self, principal: str, limit: int = 20, before: float = float("inf"),
                        cursor: str | None = None) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("Invalid history limit")
        index = self.prefix + "jobs:" + principal
        offset = 0
        if cursor:
            cursor = str(uuid.UUID(cursor))
            rank = await self.redis.zrevrank(index, cursor)
            if rank is None:
                raise ValueError("Invalid history cursor")
            offset = rank + 1
        fields = ["execution_id", "session_id", "query", "state", "created_at", "updated_at",
                  "completed_at", "current_phase", "model_version", "failure_code", "retry_execution_id"]
        records = []
        # Lazy removal of expired hashes prevents an empty page from hiding
        # older, still-retained research. Bounded by the owner index size.
        while len(records) < limit:
            if cursor or before == float("inf"):
                ids = await self.redis.zrevrange(index, offset + len(records), offset + limit - 1)
            else:
                ids = await self.redis.zrevrangebyscore(index, f"({before}", "-inf",
                                                       start=len(records), num=limit-len(records))
            if not ids:
                break
            values = await asyncio.gather(*(self.redis.hmget(self._key(id), fields + ["principal"]) for id in ids))
            expired = [id for id, row in zip(ids, values) if row[-1] != principal]
            if expired:
                await self.redis.zrem(index, *expired)
            records.extend({key: value for key, value in zip(fields, row) if value is not None}
                           for row in values if row[-1] == principal)
        return records

    async def checkpoint(self, execution_id: str, worker_id: str, generation: int,
                         artifacts: dict) -> str:
        payload = json.dumps(artifacts, separators=(",", ":"), default=str)
        if len(payload.encode()) > 1_000_000:
            raise ValueError("Intermediate research exceeds artifact budget")
        script = _now() + """
local k=KEYS[1]
if redis.call('HGET',k,'owner')~=ARGV[1] or redis.call('HGET',k,'generation')~=ARGV[2]
   or redis.call('HGET',k,'state')~='RUNNING'
   or tonumber(redis.call('HGET',k,'lease_expires_at') or '0')<=now then return 'LOST' end
redis.call('HSET',k,'artifacts',ARGV[3],'updated_at',tostring(now))
return 'OK'
"""
        return await self.redis.eval(script, 1, self._key(execution_id),
                                     worker_id, generation, payload)

    async def event_stream(self, execution_id: str, principal: str, last_id: str = "0-0"):
        """Resume Redis Stream events; the terminal state can also be fetched via get()."""
        if not await self.get(execution_id, principal):
            raise LookupError("Execution not found")
        key = self.events_key(execution_id)
        while True:
            batch = await self.redis.xread({key: last_id}, count=20, block=1000)
            if batch:
                for _, entries in batch:
                    for event_id, fields in entries:
                        last_id = event_id
                        yield event_id, fields
                        if fields.get("state") in TERMINAL:
                            return
            else:
                state = await self.get(execution_id, principal)
                if not state or state.get("state") in TERMINAL:
                    return
                yield None, {}
