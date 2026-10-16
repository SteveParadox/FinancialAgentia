"""Authenticated financial intelligence API, sharing existing principal and Redis."""

from __future__ import annotations

import json
import asyncio
import re
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .execution_api import ResearchScope, registry_and_principal
from .platform_store import ADMIN, EDITOR, PlatformStore, now
from dexter_py.tools.portfolio import (
    LedgerEntry,
    import_csv,
    reconstruct,
    value_portfolio,
)
from dexter_py.tools.financial_datasets import FinancialDataError, validate_ticker
from dexter_py.agent.financial_evidence import normalize_tool_evidence, freshness_label


def precise_json(value):
    return JSONResponse(jsonable_encoder(value, custom_encoder={Decimal: str}))


router = APIRouter(prefix="/api/v1/platform", tags=["financial intelligence"])


async def dependencies(request):
    registry, actor = await registry_and_principal(request)
    store = PlatformStore(registry)
    rate_key = store.prefix + "api_rate:" + actor
    rate = await store.redis.eval(
        "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
        1,
        rate_key,
    )
    if rate > 120:
        raise HTTPException(
            429, "Platform request limit reached", headers={"Retry-After": "60"}
        )
    return store, actor


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Named(Strict):
    name: str = Field(min_length=1, max_length=120, pattern=r"\S")


class PortfolioInput(Named):
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    benchmark: str | None = Field(default=None, max_length=20)

    @field_validator("benchmark")
    @classmethod
    def benchmark_valid(cls, value):
        return validate_ticker(value) if value else None


class LedgerInput(Strict):
    version: int = Field(ge=1)
    entries: list[LedgerEntry] = Field(default_factory=list, max_length=1000)
    csv: str | None = Field(default=None, max_length=500000)
    preview: bool = False


class Scenario(Strict):
    shocks: dict[str, Decimal] = Field(default_factory=dict, max_length=100)

    @field_validator("shocks")
    @classmethod
    def validate_shocks(cls, value):
        for symbol, shock in value.items():
            validate_ticker(symbol)
            if not shock.is_finite() or not -1 <= shock <= 10:
                raise ValueError("Shock must be a fraction between -1 and 10")
        return value


class MemoryInput(Strict):
    execution_id: uuid.UUID
    label: str = Field(min_length=1, max_length=120)


class InvitationInput(Strict):
    recipient: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: Literal["admin", "editor", "viewer"] = "viewer"


class MemberInput(Strict):
    version: int = Field(ge=1)
    member: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: Literal["admin", "editor", "viewer", "remove"]


class PublicationInput(Strict):
    execution_id: uuid.UUID
    title: str = Field(min_length=1, max_length=120)


class CommentInput(Strict):
    version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=3000)
    section: str = Field(default="report", max_length=80)
    comment_id: uuid.UUID | None = None


class ReviewInput(Strict):
    version: int = Field(ge=1)
    status: Literal["draft", "in_review", "reviewed"]


class RuleInput(Named):
    kind: Literal["price_above", "price_below", "research"]
    symbol: str | None = None
    threshold: Decimal | None = Field(
        default=None, gt=0, le=Decimal("1e12"), allow_inf_nan=False
    )
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    interval_seconds: int = Field(default=3600, ge=300, le=2592000)
    cooldown_seconds: int = Field(default=3600, ge=300, le=2592000)
    timezone: Literal["UTC"] = "UTC"
    query: str | None = Field(default=None, min_length=1, max_length=10000)
    scope: ResearchScope | None = None

    @field_validator("symbol")
    @classmethod
    def ticker_valid(cls, value):
        return validate_ticker(value) if value else None


class RuleUpdate(Strict):
    version: int = Field(ge=1)
    enabled: bool


@router.get("/me")
async def me(request: Request):
    _, actor = await dependencies(request)
    return {
        "principal": actor,
        "identity_note": "Invitation account ID; shared API keys share one identity",
    }


@router.get("/providers")
async def providers(request: Request):
    await dependencies(request)
    sec = getattr(request.app.state, "sec_provider", None)
    return {
        "providers": [
            {
                "id": "financialdatasets.ai",
                "configured": request.app.state.financial_provider is not None,
                "capabilities": ["quotes", "historical_prices", "fundamentals", "news"],
                "live_verified": False,
            },
            {
                "id": "sec.gov",
                "configured": sec is not None,
                "capabilities": ["filing_fundamentals"],
                "live_verified": False,
                "instruments": [
                    {
                        "symbol": symbol,
                        "issuer_id": "SEC:CIK:" + item["cik"],
                        "exchange": item.get("exchange"),
                        "name": item.get("name"),
                    }
                    for symbol, item in (sec.companies.items() if sec else [])
                ],
            },
        ],
        "policy": "Explicit source selection. No silent fallback between different dataset classes or currencies.",
    }


@router.get("/financial-data")
async def financial_data(
    request: Request,
    symbol: str = Query(min_length=1, max_length=20),
    dataset: Literal["quote", "statements", "history", "sec"] = "quote",
    start: str | None = None,
    end: str | None = None,
    as_of: str | None = None,
):
    store, actor = await dependencies(request)
    await provider_quota(store, actor, 1)
    try:
        if dataset == "sec":
            provider = getattr(request.app.state, "sec_provider", None)
            if provider is None:
                raise HTTPException(503, "SEC source is not configured")
            raw = await provider.facts(symbol, as_of)
        else:
            provider = request.app.state.financial_provider
            if provider is None:
                raise HTTPException(503, "Market data source is not configured")
            if dataset == "history":
                if not start or not end:
                    raise HTTPException(422, "Historical interval required")
                raw = await provider.retrieve(
                    "get_prices",
                    symbol,
                    start_date=start,
                    end_date=end,
                    interval="day",
                    interval_multiplier=1,
                )
            elif dataset == "statements":
                raw = await provider.retrieve(
                    "get_all_financial_statements", symbol, period="annual", limit=4
                )
            else:
                raw = await provider.retrieve("get_price_snapshot", symbol)
        sources, evidence = normalize_tool_evidence(
            [{"tool_results": [{"result": raw}]}]
        )
        from dexter_py.tools.data_contracts import (
            instrument_identity,
            MarketObservation,
        )

        instrument = instrument_identity(
            validate_ticker(symbol),
            raw["provider"],
            exchange=raw.get("exchange"),
            issuer_id=raw.get("issuer_id"),
            currency=raw.get("currency"),
        )
        observations = []
        if dataset == "quote":
            for row in evidence:
                source = next(
                    (source for source in sources if source.source_id == row.source_id),
                    None,
                )
                if row.metric == "price" and row.currency and row.data_as_of and source:
                    try:
                        observations.append(
                            MarketObservation(
                                instrument=instrument,
                                price=row.value,
                                currency=row.currency,
                                observation_timestamp=row.data_as_of,
                                retrieval_timestamp=source.retrieved_at,
                                source_url=source.source_url,
                                quote_kind=row.freshness,
                                adjustment_type=source.adjustment_status,
                            )
                        )
                    except ValueError:
                        pass  # Evidence remains explicitly unverified, not an authoritative quote.
        return {
            "instrument": instrument,
            "market_observations": observations,
            "sources": sources,
            "evidence": evidence,
            "limitations": raw.get("limitations", []),
            "coverage": raw.get("completeness", "provider response"),
        }
    except (FinancialDataError, ValueError) as exc:
        raise HTTPException(
            502 if isinstance(exc, FinancialDataError) else 422,
            "Financial data unavailable or invalid parameters",
        )


@router.post("/portfolios", status_code=201)
async def create_portfolio(request: Request, body: PortfolioInput):
    store, actor = await dependencies(request)
    return await store.create("portfolio", actor, {**body.model_dump(), "entries": []})


@router.get("/portfolios")
async def portfolios(
    request: Request,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(50, ge=1, le=100),
):
    store, actor = await dependencies(request)
    records = await store.list("portfolio", actor, offset, limit)
    return {
        "items": [
            {
                **record,
                "data": {k: v for k, v in record["data"].items() if k != "entries"},
            }
            for record in records
        ]
    }


@router.get("/portfolios/{identity}")
async def portfolio(request: Request, identity: str):
    store, actor = await dependencies(request)
    return await store.get("portfolio", identity, actor)


@router.post("/portfolios/{identity}/transactions")
async def transactions(request: Request, identity: str, body: LedgerInput):
    store, actor = await dependencies(request)
    original = await store.get("portfolio", identity, actor)
    try:
        if body.csv is not None and body.entries:
            raise ValueError("Supply entries or CSV, not both")
        entries = import_csv(body.csv) if body.csv is not None else body.entries
        if not entries:
            raise ValueError("At least one entry required")

        def merge(data):
            current = {entry["transaction_id"]: entry for entry in data["entries"]}
            added = []
            for entry in entries:
                item = entry.model_dump(mode="json")
                if item["transaction_id"] in current:
                    if current[item["transaction_id"]] != item:
                        raise ValueError(
                            "Transaction ID reused with different contents"
                        )
                else:
                    current[item["transaction_id"]] = item
                    added.append(item)
            if len(current) > 5000:
                raise ValueError("Portfolio ledger limit is 5000 entries")
            data["entries"].extend(added)
            ledger = reconstruct(data["entries"], data["currency"])
            if len(ledger["holdings"]) > 50:
                raise ValueError("Maximum 50 distinct current holdings")

        if body.preview:
            import copy

            preview = copy.deepcopy(original["data"])
            merge(preview)
            return precise_json(
                {
                    "preview": True,
                    "rows": [entry.model_dump(mode="json") for entry in entries],
                    "ledger": reconstruct(preview["entries"], preview["currency"]),
                }
            )
        return await store.mutate("portfolio", identity, actor, body.version, merge)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


async def provider_quota(store, actor, count):
    key = store.prefix + "provider_rate:" + actor
    total = await store.redis.eval(
        "local n=redis.call('INCRBY',KEYS[1],ARGV[1]); if n==tonumber(ARGV[1]) then redis.call('EXPIRE',KEYS[1],60) end; return n",
        1,
        key,
        max(1, count),
    )
    if total > 60:
        raise HTTPException(
            429, "Financial data request limit reached", headers={"Retry-After": "60"}
        )


async def portfolio_valuation(request, record, shocks=None):
    ledger = reconstruct(record["data"]["entries"], record["data"]["currency"])
    if len(ledger["holdings"]) > 50:
        raise HTTPException(422, "Maximum 50 distinct holdings for valuation")
    store = PlatformStore(request.app.state.execution_registry)
    await provider_quota(store, record["owner"], len(ledger["holdings"]))
    quotes = {}
    warnings = []
    provider = request.app.state.financial_provider
    if provider:
        semaphore = asyncio.Semaphore(5)

        async def retrieve_quote(symbol):
            async with semaphore:
                try:
                    async with asyncio.timeout(15):
                        raw = await provider.retrieve("get_price_snapshot", symbol)
                    sources, evidence = normalize_tool_evidence(
                        [{"tool_results": [{"result": raw}]}]
                    )
                    prices = [
                        row
                        for row in evidence
                        if row.metric == "price"
                        and row.currency == record["data"]["currency"]
                        and row.freshness == "unverified"
                        and row.period_end is None
                    ]
                    if prices:
                        row = max(prices, key=lambda item: item.data_as_of or "")
                        source = next(
                            source
                            for source in sources
                            if source.source_id == row.source_id
                        )
                        quotes[symbol] = {
                            "price": str(row.value),
                            "currency": row.currency,
                            "as_of": row.data_as_of,
                            "retrieved_at": source.retrieved_at.isoformat(),
                            "freshness": row.freshness,
                            "source_url": source.source_url,
                        }
                except (FinancialDataError, TimeoutError):
                    warnings.append(f"Quote unavailable for {symbol}")

        try:
            async with asyncio.timeout(30):
                await asyncio.gather(
                    *(retrieve_quote(symbol) for symbol in ledger["holdings"])
                )
        except TimeoutError:
            warnings.append(
                "Valuation retrieval deadline reached; incomplete totals remain unavailable"
            )
    result = value_portfolio(ledger, quotes, shocks)
    from dexter_py.tools.portfolio import xirr
    from datetime import date

    result["money_weighted_return"] = None
    if result["market_value"] is not None and record["data"]["entries"]:
        try:
            valuation_date = min(
                (date.fromisoformat(q["as_of"][:10]) for q in quotes.values()),
                default=date.today(),
            )
            if valuation_date < max(
                date.fromisoformat(e["date"]) for e in record["data"]["entries"]
            ):
                raise ValueError("Quote predates latest ledger entry")
            flows = [
                (
                    date.fromisoformat(e["date"]),
                    -float(e["amount"])
                    if e["type"] == "DEPOSIT"
                    else float(e["amount"]),
                )
                for e in record["data"]["entries"]
                if e["type"] in {"DEPOSIT", "WITHDRAWAL"}
            ]
            result["money_weighted_return"] = xirr(
                [*flows, (valuation_date, float(result["market_value"]))]
            )
        except ValueError:
            warnings.append(
                "Annualized XIRR unavailable for same-day, nonconventional, unbracketed cash flows or quotes predating transactions."
            )
    result["return_methodology"] = (
        "XIRR: investor deposits negative; withdrawals/final valuation positive; actual/365; only a unique conventional bounded root is accepted. Cash balances/dividends remain internal."
    )
    result["warnings"] = warnings
    return result


@router.get("/portfolios/{identity}/analytics")
async def analytics(request: Request, identity: str):
    store, actor = await dependencies(request)
    return precise_json(
        await portfolio_valuation(
            request, await store.get("portfolio", identity, actor)
        )
    )


@router.post("/portfolios/{identity}/scenarios")
async def scenarios(request: Request, identity: str, body: Scenario):
    store, actor = await dependencies(request)
    record = await store.get("portfolio", identity, actor)
    known = {
        entry["symbol"] for entry in record["data"]["entries"] if entry.get("symbol")
    }
    if set(body.shocks) - known:
        raise HTTPException(422, "Scenario contains instruments outside portfolio")
    return precise_json(await portfolio_valuation(request, record, body.shocks))


@router.post("/portfolios/{identity}/research", status_code=202)
async def portfolio_research(request: Request, identity: str):
    store, actor = await dependencies(request)
    record = await store.get("portfolio", identity, actor)
    holdings = reconstruct(record["data"]["entries"], record["data"]["currency"])[
        "holdings"
    ]
    symbols = sorted(holdings)[:5]
    if not symbols:
        raise HTTPException(422, "Portfolio has no holdings")
    from .execution_api import Admission, admit

    return await admit(
        request,
        Admission(
            query="Analyze financial health and risks for portfolio holdings: "
            + ", ".join(symbols),
            scope=ResearchScope(
                category="company" if len(symbols) == 1 else "comparison",
                symbols=symbols,
            ),
        ),
    )


@router.post("/memories", status_code=201)
async def save_memory(request: Request, body: MemoryInput):
    store, actor = await dependencies(request)
    record = await store.registry.get(str(body.execution_id), actor)
    if not record or record.get("state") != "SUCCEEDED" or not record.get("result"):
        raise HTTPException(404, "Completed saved research not found")
    report = record["result"]
    identity = store.stable_id(actor, str(body.execution_id))
    previous = await store.raw("memory", identity)
    if previous:
        return await store.get("memory", identity, actor)
    return await store.create(
        "memory",
        actor,
        {
            "label": body.label,
            "execution_id": str(body.execution_id),
            "query": record["query"][:1000],
            "summary": report["answer"][:6000],
            "researched_at": report["researched_at"],
            "source_ids": [row["source_id"] for row in report["sources"]],
            "policy": "Historical context only; all current findings must be retrieved again",
        },
        identity,
    )


@router.get("/memories")
async def memories(
    request: Request,
    search: str = Query("", max_length=120),
    offset: int = Query(0, ge=0, le=10000),
):
    store, actor = await dependencies(request)
    # Authorization before relevance filtering; no cross-user similarity corpus.
    records = await store.list("memory", actor, offset, 100)
    words = set(re.findall(r"[a-z0-9]+", search.lower()))
    records = [
        r
        for r in records
        if not words
        or words
        & set(
            re.findall(
                r"[a-z0-9]+", (r["data"]["label"] + " " + r["data"]["query"]).lower()
            )
        )
    ]
    return {"items": records, "personalization": "explicit selection only"}


@router.delete("/memories/{identity}", status_code=204)
async def delete_memory(request: Request, identity: str, version: int = Query(ge=1)):
    store, actor = await dependencies(request)
    await store.delete("memory", identity, actor, version)
    return Response(status_code=204)


@router.post("/workspaces", status_code=201)
async def create_workspace(request: Request, body: Named):
    store, actor = await dependencies(request)
    record = await store.create(
        "workspace",
        actor,
        {"name": body.name, "members": {actor: "owner"}, "publications": []},
    )
    await store.redis.zadd(store.prefix + "memberships:" + actor, {record["id"]: now()})
    await store.redis.expire(store.prefix + "memberships:" + actor, store.retention)
    return record


@router.get("/workspaces")
async def workspaces(request: Request):
    store, actor = await dependencies(request)
    return {"items": await store.workspaces(actor)}


@router.get("/workspaces/{identity}")
async def workspace(request: Request, identity: str):
    store, actor = await dependencies(request)
    return await store.workspace(identity, actor)


@router.post("/workspaces/{identity}/invitations", status_code=201)
async def invite(request: Request, identity: str, body: InvitationInput):
    store, actor = await dependencies(request)
    workspace = await store.workspace(identity, actor, ADMIN)
    if body.role == "admin" and workspace["data"]["members"].get(actor) != "owner":
        raise HTTPException(403, "Only owner may invite administrators")
    if body.recipient in workspace["data"]["members"]:
        raise HTTPException(409, "Already a member")
    return await store.create(
        "invitation",
        actor,
        {
            "workspace_id": identity,
            **body.model_dump(),
            "deadline": now() + 604800,
            "status": "pending",
        },
    )


@router.post("/invitations/{identity}/accept")
async def accept_invitation(request: Request, identity: str):
    store, actor = await dependencies(request)
    return await store.accept(identity, actor)


@router.post("/invitations/{identity}/revoke")
async def revoke_invitation(
    request: Request, identity: str, version: int = Query(ge=1)
):
    store, actor = await dependencies(request)
    return await store.mutate(
        "invitation",
        identity,
        actor,
        version,
        lambda data: data.update(status="revoked"),
    )


@router.post("/workspaces/{identity}/members")
async def member(request: Request, identity: str, body: MemberInput):
    store, actor = await dependencies(request)

    def change(data):
        current = data["members"].get(body.member)
        if not current or current == "owner":
            raise HTTPException(
                409, "Owner cannot be removed or demoted; member must exist"
            )
        if data["members"][actor] != "owner" and (
            current == "admin" or body.role == "admin"
        ):
            raise HTTPException(403, "Only owner may manage administrators")
        if body.role == "remove":
            del data["members"][body.member]
        else:
            data["members"][body.member] = body.role

    return await store.mutate(
        "workspace", identity, actor, body.version, change, workspace_role=ADMIN
    )


@router.post("/workspaces/{identity}/publications", status_code=201)
async def publish(request: Request, identity: str, body: PublicationInput):
    store, actor = await dependencies(request)
    workspace = await store.workspace(identity, actor, EDITOR)
    source = await store.registry.get(str(body.execution_id), actor)
    if not source or not source.get("result"):
        raise HTTPException(404, "Owned report not found")
    if len(workspace["data"]["publications"]) >= 100:
        raise HTTPException(429, "Workspace report limit reached")
    pub = await store.create(
        "publication",
        actor,
        {
            "workspace_id": identity,
            "title": body.title,
            "source_execution_id": str(body.execution_id),
            "report": source["result"],
            "comments": [],
            "review_status": "draft",
            "history": [
                {"version": 1, "author": actor, "type": "ai_generated", "at": now()}
            ],
        },
    )
    try:
        await store.mutate(
            "workspace",
            identity,
            actor,
            workspace["version"],
            lambda data: data["publications"].append(pub["id"]),
            workspace_role=EDITOR,
        )
    except HTTPException:
        await store.delete("publication", pub["id"], actor, pub["version"])
        raise
    return pub


async def accessible_publication(store, actor, identity, roles=None):
    pub = await store.raw("publication", identity)
    if not pub:
        raise HTTPException(404, "Shared research not found")
    workspace = await store.workspace(pub["data"]["workspace_id"], actor, roles)
    if identity not in workspace["data"]["publications"]:
        raise HTTPException(404, "Report sharing revoked")
    return pub, workspace


@router.get("/publications/{identity}")
async def publication(request: Request, identity: str):
    store, actor = await dependencies(request)
    pub, _ = await accessible_publication(store, actor, identity)
    return pub


@router.delete("/workspaces/{workspace_id}/publications/{identity}", status_code=204)
async def unshare(
    request: Request, workspace_id: str, identity: str, version: int = Query(ge=1)
):
    store, actor = await dependencies(request)
    await store.workspace(workspace_id, actor, ADMIN)

    def remove(data):
        if identity not in data["publications"]:
            raise HTTPException(404, "Shared research not found")
        data["publications"].remove(identity)

    await store.mutate(
        "workspace", workspace_id, actor, version, remove, workspace_role=ADMIN
    )
    return Response(status_code=204)


async def edit_publication(store, actor, identity, version, change):
    # Watch membership + publication together: revocation cannot race a write.
    from redis.exceptions import WatchError

    pub, workspace = await accessible_publication(store, actor, identity, EDITOR)
    key = store.key("publication", identity)
    workspace_key = store.key("workspace", workspace["id"])
    async with store.redis.pipeline() as pipe:
        try:
            await pipe.watch(key, workspace_key)
            current_pub, current_workspace = await pipe.mget(key, workspace_key)
            pub = json.loads(current_pub) if current_pub else None
            workspace = json.loads(current_workspace) if current_workspace else None
            if (
                not pub
                or not workspace
                or workspace["data"]["members"].get(actor) not in EDITOR
                or identity not in workspace["data"]["publications"]
            ):
                raise HTTPException(404, "Shared research not found")
            if pub["version"] != version:
                raise HTTPException(409, "Report changed; refresh before editing")
            change(pub["data"])
            if len(pub["data"]["history"]) >= 100:
                raise HTTPException(429, "Revision retention limit reached")
            pub["version"] += 1
            pub["updated_at"] = now()
            pub["data"]["history"].append(
                {
                    "version": pub["version"],
                    "author": actor,
                    "type": "human_annotation",
                    "at": now(),
                }
            )
            pipe.multi()
            pipe.set(key, json.dumps(pub), ex=max(1, int(pub["expires_at"] - now())))
            await pipe.execute()
            return pub
        except WatchError:
            raise HTTPException(409, "Concurrent report or membership change")


@router.post("/publications/{identity}/comments")
async def comment(request: Request, identity: str, body: CommentInput):
    store, actor = await dependencies(request)

    def change(data):
        if body.comment_id:
            comments = [
                c
                for c in data["comments"]
                if c["id"] == str(body.comment_id) and c["author"] == actor
            ]
            if not comments:
                raise HTTPException(404, "Owned comment not found")
            comment = comments[0]
            comment.setdefault("revisions", []).append(
                {"text": comment["text"], "at": now()}
            )
            comment.update(text=body.text, section=body.section, edited_at=now())
        else:
            if len(data["comments"]) >= 100:
                raise HTTPException(429, "Comment limit reached")
            data["comments"].append(
                {
                    "id": str(uuid.uuid4()),
                    "author": actor,
                    "text": body.text,
                    "section": body.section,
                    "at": now(),
                }
            )

    return await edit_publication(store, actor, identity, body.version, change)


@router.post("/publications/{identity}/review")
async def review(request: Request, identity: str, body: ReviewInput):
    store, actor = await dependencies(request)
    return await edit_publication(
        store,
        actor,
        identity,
        body.version,
        lambda data: data.update(review_status=body.status),
    )


@router.get("/publications/{identity}/export")
async def export(
    request: Request, identity: str, format: Literal["json", "markdown", "csv"] = "json"
):
    store, actor = await dependencies(request)
    pub, _ = await accessible_publication(store, actor, identity)
    report = pub["data"]["report"]
    if format == "json":
        content = json.dumps(report, indent=2)
        media = "application/json"
    elif format == "csv":
        import csv
        from io import StringIO

        stream = StringIO()
        writer = csv.writer(stream)
        writer.writerow(
            [
                "symbol",
                "metric",
                "value",
                "unit",
                "currency",
                "as_of",
                "source_id",
                "freshness",
            ]
        )
        for row in report["evidence"]:
            cells = [
                str(row.get(key, ""))
                for key in [
                    "symbol",
                    "metric",
                    "value",
                    "unit",
                    "currency",
                    "data_as_of",
                    "source_id",
                    "freshness",
                ]
            ]
            writer.writerow(
                [
                    "'" + value if value.startswith(("=", "+", "-", "@")) else value
                    for value in cells
                ]
            )
        content, media = stream.getvalue(), "text/csv"
    else:
        content = (
            "# "
            + pub["data"]["title"]
            + "\n\nAs of "
            + report["researched_at"]
            + "\n\n"
            + report["answer"]
        )
        content += "\n\n## Limitations\n" + "\n".join(report["limitations"])
        content += (
            "\n\n## Evidence\n```json\n"
            + json.dumps(report["evidence"], indent=2)
            + "\n```\n## Sources\n```json\n"
            + json.dumps(report["sources"], indent=2)
            + "\n```\n"
        )
        media = "text/markdown"
    return Response(
        content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="research-{identity}.{format if format != "markdown" else "md"}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.post("/monitoring", status_code=201)
async def create_rule(request: Request, body: RuleInput):
    store, actor = await dependencies(request)
    if (
        len(await store.list("schedule", actor, 0, 21))
        + len(await store.list("alert", actor, 0, 21))
        >= 20
    ):
        raise HTTPException(429, "Maximum 20 monitoring rules per account")
    if body.kind == "research":
        if not body.query or not body.query.strip() or body.interval_seconds < 3600:
            raise HTTPException(
                422, "Recurring research requires a question and interval >= 1 hour"
            )
        if not getattr(
            request.app.state.orchestrator.execute_phase.tool_executor, "tools", {}
        ):
            raise HTTPException(503, "Research providers not configured")
    elif not body.symbol or body.threshold is None:
        raise HTTPException(422, "Price rule requires symbol and threshold")
    elif request.app.state.financial_provider is None:
        raise HTTPException(503, "Quote source not configured")
    return await store.create(
        "schedule" if body.kind == "research" else "alert",
        actor,
        {
            **body.model_dump(mode="json"),
            "enabled": True,
            "next_due": now(),
            "last_condition": None,
            "last_triggered": None,
            "last_evaluation": None,
            "error": None,
            "pending_execution": None,
            "configured_model": request.app.state.orchestrator.model,
            "failure_count": 0,
        },
    )


@router.get("/monitoring")
async def monitoring(request: Request):
    store, actor = await dependencies(request)
    return {
        "items": await store.list("alert", actor) + await store.list("schedule", actor),
        "notifications": await store.list("notification", actor),
        "policy": "Periodic UTC polling. Crossing-only price alerts, cooldown, skip missed intervals. In-app delivery only.",
    }


@router.post("/monitoring/{kind}/{identity}")
async def update_rule(
    request: Request,
    kind: Literal["alert", "schedule"],
    identity: str,
    body: RuleUpdate,
):
    store, actor = await dependencies(request)
    record = await store.mutate(
        kind,
        identity,
        actor,
        body.version,
        lambda data: data.update(enabled=body.enabled, failure_count=0, error=None),
    )
    # Suppress queued work when a schedule is disabled; ongoing jobs use ordinary cancellation.
    if not body.enabled and record["data"].get("pending_execution"):
        await store.registry.cancel(record["data"]["pending_execution"], actor)
    return record


@router.delete("/monitoring/{kind}/{identity}", status_code=204)
async def delete_rule(
    request: Request,
    kind: Literal["alert", "schedule"],
    identity: str,
    version: int = Query(ge=1),
):
    store, actor = await dependencies(request)
    record = await store.get(kind, identity, actor)
    await store.delete(kind, identity, actor, version)
    if record["data"].get("pending_execution"):
        await store.registry.cancel(record["data"]["pending_execution"], actor)
    return Response(status_code=204)


@router.post("/notifications/{identity}/acknowledge")
async def acknowledge(request: Request, identity: str, version: int = Query(ge=1)):
    store, actor = await dependencies(request)
    return await store.mutate(
        "notification",
        identity,
        actor,
        version,
        lambda data: data.update(acknowledged=True),
    )


@router.get("/portfolios/{identity}/risk")
async def portfolio_risk(request: Request, identity: str, start: str, end: str):
    """Constant-current-quantity historical proxy, explicitly not investor performance."""
    from dexter_py.tools.portfolio import risk_statistics

    store, actor = await dependencies(request)
    record = await store.get("portfolio", identity, actor)
    ledger = reconstruct(record["data"]["entries"], record["data"]["currency"])
    symbols = list(ledger["holdings"])
    if not symbols or len(symbols) > 10:
        raise HTTPException(422, "Risk proxy requires 1..10 current holdings")
    provider = request.app.state.financial_provider
    if provider is None:
        raise HTTPException(503, "Historical provider not configured")
    benchmark = record["data"].get("benchmark")
    await provider_quota(store, actor, len(symbols) + bool(benchmark))
    histories = {}
    source_records = []
    try:

        async def retrieve_history(symbol):
            raw = await provider.retrieve(
                "get_prices",
                symbol,
                start_date=start,
                end_date=end,
                interval="day",
                interval_multiplier=1,
            )
            sources, evidence = normalize_tool_evidence(
                [{"tool_results": [{"result": raw}]}]
            )
            rows = [
                row
                for row in evidence
                if row.metric == "close"
                and row.currency == record["data"]["currency"]
                and row.value > 0
            ]
            values = {}
            for row in rows:
                day = row.data_as_of[:10]
                if day in values and values[day] != row.value:
                    raise ValueError("Conflicting prices in one daily observation")
                values[day] = row.value
            histories[symbol] = values
            source_records.extend(sources)

        async with asyncio.timeout(30):
            await asyncio.gather(
                *(
                    retrieve_history(symbol)
                    for symbol in sorted(
                        set(symbols + ([benchmark] if benchmark else []))
                    )
                )
            )
        dates = sorted(
            set.intersection(*(set(values) for values in histories.values()))
        )
        from datetime import date

        if len(dates) < 31 or any(
            (date.fromisoformat(b) - date.fromisoformat(a)).days > 4
            for a, b in zip(dates, dates[1:])
        ):
            raise ValueError(
                "Insufficient contiguous aligned daily prices; no gap filling"
            )
        values = [
            ledger["cash"]
            + sum(
                ledger["holdings"][symbol]["quantity"] * histories[symbol][day]
                for symbol in symbols
            )
            for day in dates
        ]
        returns = [float(b / a - 1) for a, b in zip(values, values[1:])]
        benchmark_returns = (
            [
                float(histories[benchmark][b] / histories[benchmark][a] - 1)
                for a, b in zip(dates, dates[1:])
            ]
            if benchmark
            else None
        )
        return {
            "statistics": risk_statistics(returns, benchmark_returns),
            "dates": dates,
            "proxy_values": [str(value) for value in values],
            "currency": record["data"]["currency"],
            "sources": source_records,
            "label": "Constant-current-quantity historical risk proxy",
            "limitations": [
                "Not actual portfolio history, TWR or investor return. Current quantities/cash held constant across dates; excludes historical transactions, distributions and FX.",
                "Adjustment status is provider-supplied or unspecified; corporate actions may distort the proxy. No forward-looking risk guarantee.",
            ],
        }
    except (FinancialDataError, ValueError, ArithmeticError, TimeoutError):
        raise HTTPException(
            422,
            "Risk proxy unavailable: insufficient aligned daily prices, currency mismatch or provider failure",
        )


@router.post("/portfolios/{identity}/valuations")
async def capture_valuation(request: Request, identity: str):
    store, actor = await dependencies(request)
    record = await store.get("portfolio", identity, actor)
    result = await portfolio_valuation(request, record)
    if result["market_value"] is None:
        raise HTTPException(
            422, "Cannot persist an incomplete valuation; inspect missing prices"
        )
    snapshot = jsonable_encoder(
        {
            "captured_at": now(),
            "ledger_version": record["version"],
            "market_value": result["market_value"],
            "cash": result["cash"],
            "net_contributions": result["net_contributions"],
            "currency": record["data"]["currency"],
            "holdings": result["holdings"],
        },
        custom_encoder={Decimal: str},
    )

    def save(data):
        data.setdefault("valuations", []).append(snapshot)
        data["valuations"] = data["valuations"][-366:]
        while len(json.dumps(data)) > 1_800_000 and len(data["valuations"]) > 1:
            data["valuations"].pop(0)

    updated = await store.mutate("portfolio", identity, actor, record["version"], save)
    return {"portfolio": updated, "snapshot": snapshot}


@router.get("/instruments")
async def instruments(request: Request, search: str = Query("", max_length=80)):
    await dependencies(request)
    from dexter_py.tools.data_contracts import instrument_identity

    provider = getattr(request.app.state, "sec_provider", None)
    records = []
    if provider:
        for symbol, item in provider.companies.items():
            if search.lower() in (symbol + " " + item.get("name", "")).lower():
                records.append(
                    instrument_identity(
                        symbol,
                        "sec.gov",
                        exchange=item.get("exchange"),
                        issuer_id="SEC:CIK:" + item["cik"],
                    )
                )
    return {
        "items": records[:100],
        "coverage": "Configured SEC issuer catalog only; no unsupported global security-search entitlement is assumed",
    }


class VersionInput(Strict):
    version: int = Field(ge=1)


class PortfolioUpdate(Strict):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"\S")
    benchmark: str | None = Field(default=None, max_length=20)

    @field_validator("benchmark")
    @classmethod
    def benchmark_valid(cls, value):
        return validate_ticker(value) if value else None


@router.patch("/portfolios/{identity}")
async def update_portfolio(request: Request, identity: str, body: PortfolioUpdate):
    store, actor = await dependencies(request)
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    if not changes or changes.get("name", "") is None:
        raise HTTPException(422, "At least one valid portfolio setting is required")
    return await store.mutate(
        "portfolio", identity, actor, body.version, lambda data: data.update(changes)
    )


@router.delete("/portfolios/{identity}", status_code=204)
async def delete_portfolio(request: Request, identity: str, version: int = Query(ge=1)):
    store, actor = await dependencies(request)
    await store.delete("portfolio", identity, actor, version)
    return Response(status_code=204)


@router.get("/invitations/{identity}")
async def invitation(request: Request, identity: str):
    store, actor = await dependencies(request)
    record = await store.raw("invitation", identity)
    if not record or actor not in {record["owner"], record["data"]["recipient"]}:
        raise HTTPException(404, "Invitation not found")
    return record


@router.post("/workspaces/{identity}/leave")
async def leave_workspace(request: Request, identity: str, body: VersionInput):
    store, actor = await dependencies(request)

    def leave(data):
        if data["members"].get(actor) == "owner":
            raise HTTPException(
                409, "Owner must retain ownership; delete workspace instead"
            )
        del data["members"][actor]

    await store.mutate(
        "workspace",
        identity,
        actor,
        body.version,
        leave,
        workspace_role={"admin", "editor", "viewer"},
    )
    return {"left": True}


@router.delete("/workspaces/{identity}", status_code=204)
async def delete_workspace(request: Request, identity: str, version: int = Query(ge=1)):
    from redis.exceptions import WatchError

    store, actor = await dependencies(request)
    key = store.key("workspace", identity)
    async with store.redis.pipeline() as pipe:
        try:
            await pipe.watch(key)
            raw = await pipe.get(key)
            record = json.loads(raw) if raw else None
            if not record or record["owner"] != actor:
                raise HTTPException(404, "Workspace not found")
            if record["version"] != version:
                raise HTTPException(409, "Workspace changed")
            publications = []
            for publication_id in record["data"]["publications"]:
                publication = await pipe.get(store.key("publication", publication_id))
                if publication:
                    publications.append(json.loads(publication))
            pipe.multi()
            pipe.delete(key)
            pipe.zrem(store.index("workspace", actor), identity)
            for member in record["data"]["members"]:
                pipe.zrem(store.prefix + "memberships:" + member, identity)
            for publication in publications:
                pipe.delete(store.key("publication", publication["id"]))
                pipe.zrem(
                    store.index("publication", publication["owner"]), publication["id"]
                )
            await pipe.execute()
            return Response(status_code=204)
        except WatchError:
            raise HTTPException(409, "Concurrent workspace change")
