"""Redis-backed, fenced and idempotent financial research executions.

All state transitions, queue changes, event publication and final conversation
turn commits happen inside Redis Lua scripts. The registry must NOT use a
process-local fallback in production. Requires durable Redis persistence.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
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
             'state', state, 'execution_id', execution_id)
end
"""


ADMIT = _now() + _emit() + """
local old=redis.call('GET',KEYS[1])
if old then
  if old=='CANCELLED' then return {'CANCELLED',''} end
  if redis.call('HGET',KEYS[2] .. old,'fingerprint')~=ARGV[4] then
    return {'CONFLICT',old}
  end
  return {'EXISTING',old}
end
redis.call('HSET', KEYS[2] .. ARGV[1],
  'execution_id', ARGV[1], 'principal', ARGV[2], 'session_id', ARGV[3],
  'fingerprint', ARGV[4], 'query', ARGV[5], 'state', 'QUEUED',
  'created_at', tostring(now), 'updated_at', tostring(now),
  'deadline_at', tostring(now+tonumber(ARGV[6])), 'generation','0',
  'owner','', 'lease_expires_at','0', 'model_version',ARGV[7],
  'attempt_count','0')
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
  elseif state~='QUEUED' and state~='RUNNING' then
    redis.call('ZREM',KEYS[1],id)
  elseif deadline<=now and (state=='QUEUED' or expires<=now) then
    redis.call('HSET',k,'state','TIMED_OUT','updated_at',tostring(now),'completed_at',tostring(now))
    redis.call('ZREM',KEYS[1],id)
    emit(id,'TIMED_OUT')
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
    emit(id,'RUNNING')
    return {id,tostring(generation)}
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
if tonumber(redis.call('HGET',k,'deadline_at') or '0')<=now then
  outcome='TIMED_OUT'
end
if outcome~='SUCCEEDED' and outcome~='FAILED' and
   outcome~='CANCELLED' and outcome~='TIMED_OUT' then return 'INVALID' end
redis.call('HSET',k,'state',outcome,'updated_at',tostring(now),
            'completed_at',tostring(now),'owner','',
            'lease_expires_at','0','failure_code',ARGV[6])
if outcome=='SUCCEEDED' then
  redis.call('HSET',k,'result',ARGV[5])
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


class ExecutionConflict(ValueError):
    pass


class ExecutionRegistry:
    def __init__(self, client: Redis, *, prefix: str = "financialagentia:"):
        self.redis = client
        if not prefix or any(c in prefix for c in "{} \t\n"):
            raise ValueError("Invalid registry prefix")
        self.prefix = prefix
        self.execution_prefix = prefix + "exec:"
        self.events_prefix = prefix + "events:"
        self.queue = prefix + "queue:active"
        self.session_owner_prefix = prefix + "session_owner:"

    @classmethod
    def from_env(cls) -> "ExecutionRegistry":
        url = os.getenv("REDIS_URL")
        if not url:
            raise RuntimeError("REDIS_URL required for durable financial research execution")
        return cls(Redis.from_url(url, decode_responses=True),
                   prefix=os.getenv("EXECUTION_REDIS_PREFIX", "financialagentia:"))

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
                    deadline_seconds: int = 300) -> tuple[str, bool]:
        if not 1 <= len(query) <= 10000 or not query.strip():
            raise ValueError("Invalid research query")
        if not 8 <= len(idempotency_key) <= 128:
            raise ValueError("Idempotency key must contain 8-128 characters")
        if not 1 <= deadline_seconds <= 3600:
            raise ValueError("Invalid execution deadline")
        session_id = str(uuid.UUID(session_id))
        fingerprint = _sha(json.dumps([session_id, query, model], separators=(",", ":")))
        existing_key = self.prefix + "idem:" + _sha(principal + ":" + idempotency_key)
        execution_id = str(uuid.uuid4())
        reply = await self.redis.eval(ADMIT, 4, existing_key,
                                      self.execution_prefix, self.events_prefix,
                                      self.queue, execution_id, principal, session_id,
                                      fingerprint, query, deadline_seconds, model, 86400)
        if reply[0] == "CANCELLED":
            raise ExecutionConflict("Execution was cancelled before admission")
        if reply[0] == "CONFLICT":
            raise ExecutionConflict("Idempotency key already used for a different request")
        return reply[1], reply[0] == "NEW"

    async def cancel_by_key(self, principal: str, idempotency_key: str) -> str:
        if not 8 <= len(idempotency_key) <= 128:
            raise ValueError("Invalid idempotency key")
        key = self.prefix + "idem:" + _sha(principal + ":" + idempotency_key)
        return await self.redis.eval(
            CANCEL_BY_KEY, 4, key, self.execution_prefix,
            self.events_prefix, self.queue, principal)

    async def get(self, execution_id: str, principal: str) -> dict[str, Any] | None:
        result = await self.redis.hgetall(self._key(execution_id))
        if not result or result.get("principal") != principal:
            return None
        safe = {key: value for key, value in result.items()
                if key not in {"principal", "fingerprint", "query"}}
        if "result" in safe:
            safe["result"] = json.loads(safe["result"])
        return safe

    async def claim(self, worker_id: str, lease_seconds: int = 20) -> tuple[str, int] | None:
        reply = await self.redis.eval(CLAIM, 4, self.queue,
                                      self.execution_prefix, self.events_prefix,
                                      self.session_owner_prefix,
                                      worker_id, lease_seconds)
        return (reply[0], int(reply[1])) if reply else None

    async def heartbeat(self, execution_id: str, worker_id: str,
                        generation: int, lease_seconds: int = 20) -> str:
        return await self.redis.eval(HEARTBEAT, 2, self._key(execution_id),
                                     self.session_owner_prefix, worker_id, generation,
                                     lease_seconds, execution_id + ":" + str(generation))

    async def cancel(self, execution_id: str, principal: str) -> str:
        return await self.redis.eval(CANCEL, 4, self._key(execution_id),
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
                                     turn, 86400)

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
