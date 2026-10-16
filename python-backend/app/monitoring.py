"""Periodic, recoverable monitoring within the existing worker process.

Due records live in Redis. Claims use document versions and stable slots; research
uses ExecutionRegistry.admit with an atomic enabled/version guard. No second job
queue, cron daemon, or implicit external notification delivery exists.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
import uuid

from fastapi import HTTPException
import structlog

from app.platform_store import PlatformStore, now
from dexter_py.agent.execution_registry import (
    ExecutionCapacity,
    ExecutionConflict,
    TERMINAL,
)
from dexter_py.agent.financial_evidence import normalize_tool_evidence

logger = structlog.get_logger(__name__)


class MonitoringService:
    def __init__(self, registry, orchestrator):
        self.store = PlatformStore(registry)
        self.registry = registry
        self.orchestrator = orchestrator

    async def tick(self, limit=20):
        due = await self.store.redis.zrangebyscore(
            self.store.prefix + "due", "-inf", now(), start=0, num=limit
        )
        for member in due:
            kind, identity = member.split(":", 1)
            try:
                async with asyncio.timeout(45):
                    await self.evaluate(kind, identity)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "monitoring_evaluation_failed", error_category=type(exc).__name__
                )
                # A claimed record becomes eligible again after next_due; no local lock survives a crash.

    async def evaluate(self, kind, identity):
        store = self.store
        record = await store.raw(kind, identity)
        if not record or not record["data"]["enabled"]:
            await store.redis.zrem(store.prefix + "due", kind + ":" + identity)
            return
        data = record["data"]
        if data["next_due"] > now():
            return
        slot = data.get("active_slot") or int(data["next_due"])
        claim_id = str(uuid.uuid4())
        try:
            record = await store.mutate(
                kind,
                identity,
                record["owner"],
                record["version"],
                lambda d: d.update(
                    claim_id=claim_id, active_slot=slot, next_due=now() + 60
                ),
            )
        except HTTPException as exc:
            if exc.status_code in {404, 409}:
                return
            raise
        update = {}
        try:
            if kind == "schedule":
                pending = data.get("pending_execution")
                if pending:
                    execution = await self.registry.get(pending, record["owner"])
                    if execution and execution["state"] not in TERMINAL:
                        update = {"next_due": now() + 30}
                    else:
                        update = {
                            "pending_execution": None,
                            "active_slot": None,
                            "next_due": now() + data["interval_seconds"],
                            "error": None,
                            "notification_payload": {
                                "execution_id": pending,
                                "state": execution["state"] if execution else "EXPIRED",
                                "message": "Scheduled research finished"
                                if execution
                                else "Scheduled research expired before notification",
                            },
                        }
                else:
                    scope = data.get("scope") or {}
                    execution_id, _ = await self.registry.admit(
                        principal=record["owner"],
                        session_id=str(uuid.uuid4()),
                        query=data["query"],
                        model=data.get("configured_model", self.orchestrator.model),
                        request_session_id=None,
                        idempotency_key="schedule:" + identity + ":" + str(slot),
                        request_options=scope,
                        admission_guard=(store.key(kind, identity), record["version"]),
                    )
                    update = {
                        "pending_execution": execution_id,
                        "next_due": now() + 30,
                        "error": None,
                    }
            else:
                tools = self.orchestrator.execute_phase.tool_executor
                raw = await tools.execute(
                    "get_price_snapshot", {"ticker": data["symbol"]}
                )
                sources, evidence = normalize_tool_evidence(
                    [{"tool_results": [{"result": raw}]}]
                )
                prices = [
                    row
                    for row in evidence
                    if row.metric == "price"
                    and row.currency == data["currency"]
                    and row.freshness == "unverified"
                    and row.period_end is None
                ]
                if not prices:
                    raise ValueError("No current-enough, currency-compatible quote")
                price = max(prices, key=lambda item: item.data_as_of or "")
                condition = (
                    price.value > Decimal(data["threshold"])
                    if data["kind"] == "price_above"
                    else price.value < Decimal(data["threshold"])
                )
                crossing = condition and data.get("last_condition") is not True
                cooldown = (
                    not data.get("last_triggered")
                    or now() - data["last_triggered"] >= data["cooldown_seconds"]
                )
                # Commit observation and durable notification together below.
                update = {
                    "last_condition": condition,
                    "last_observed": str(price.value),
                    "observed_at": price.data_as_of,
                    "source_url": sources[0].source_url,
                    "retrieved_at": sources[0].retrieved_at.isoformat(),
                    "freshness": price.freshness,
                    "error": None,
                    "next_due": now() + data["interval_seconds"],
                    "active_slot": None,
                }
                if crossing and cooldown:
                    update["last_triggered"] = now()
                    update["notification_payload"] = {
                        "message": "Price threshold crossed",
                        "symbol": data["symbol"],
                        "value": str(price.value),
                        "currency": price.currency,
                        "observed_at": price.data_as_of,
                        "source_url": sources[0].source_url,
                        "freshness": price.freshness,
                    }
        except (
            ExecutionConflict,
            ExecutionCapacity,
            ValueError,
            RuntimeError,
            TimeoutError,
        ) as exc:
            failures = data.get("failure_count", 0) + 1
            update = {
                "error": type(exc).__name__,
                "failure_count": failures,
                "enabled": failures < 3,
                "next_due": now() + min(300, data["interval_seconds"]),
            }
        if not update.get("error"):
            update["failure_count"] = 0
        update["last_evaluation"] = now()
        logger.info(
            "monitoring_evaluated",
            rule_id=identity,
            kind=kind,
            error_category=update.get("error"),
            failure_count=update.get("failure_count", 0),
        )
        # CAS plus version/enable guard suppress late results from paused/deleted rules.
        try:
            payload = update.pop("notification_payload", None)
            if payload:
                await self.commit_alert(
                    record, update, payload.get("execution_id", slot), payload, kind
                )
            else:
                await store.mutate(
                    kind,
                    identity,
                    record["owner"],
                    record["version"],
                    lambda d: d.update(update),
                )
        except HTTPException as exc:
            if exc.status_code not in {404, 409}:
                raise
            if update.get("pending_execution"):
                await self.registry.cancel(update["pending_execution"], record["owner"])

    async def commit_alert(self, record, update, slot, payload, kind="alert"):
        """One transaction: crossing state, source observation and notification."""
        import json
        from redis.exceptions import WatchError

        store = self.store
        key = store.key(kind, record["id"])
        identity = store.stable_id(record["id"], str(slot))
        notification_key = store.key("notification", identity)
        async with store.redis.pipeline() as pipe:
            try:
                notification_index = store.index("notification", record["owner"])
                await pipe.watch(key, notification_index)
                oldest = await pipe.zrange(notification_index, 0, -500)
                current = await pipe.get(key)
                current = json.loads(current) if current else None
                if (
                    not current
                    or current["version"] != record["version"]
                    or not current["data"]["enabled"]
                ):
                    raise HTTPException(409, "Rule changed")
                current["data"].update(update)
                current["version"] += 1
                current["updated_at"] = now()
                notification = {
                    "schema_version": 1,
                    "id": identity,
                    "owner": record["owner"],
                    "version": 1,
                    "created_at": now(),
                    "updated_at": now(),
                    "expires_at": now() + store.retention,
                    "data": {
                        "rule_id": record["id"],
                        "rule_name": record["data"]["name"],
                        "channel": "in_app",
                        "delivery_status": "available_in_app",
                        "acknowledged": False,
                        **payload,
                    },
                }
                pipe.multi()
                pipe.set(
                    key,
                    json.dumps(current),
                    keepttl=True,
                )
                if oldest:
                    pipe.delete(*(store.key("notification", old) for old in oldest))
                    pipe.zrem(notification_index, *oldest)
                pipe.set(
                    notification_key,
                    json.dumps(notification),
                    ex=store.retention,
                    nx=True,
                )
                pipe.zadd(
                    store.index("notification", record["owner"]), {identity: now()}
                )
                pipe.expire(
                    store.index("notification", record["owner"]), store.retention
                )
                pipe.zadd(
                    store.prefix + "due",
                    {kind + ":" + record["id"]: update["next_due"]},
                )
                await pipe.execute()
            except WatchError:
                raise HTTPException(409, "Rule changed")

    async def run(self, stopping):
        while not stopping.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "monitoring_registry_unavailable", error_category=type(exc).__name__
                )
            try:
                await asyncio.wait_for(stopping.wait(), timeout=5)
            except TimeoutError:
                pass
