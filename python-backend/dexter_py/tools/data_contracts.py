"""Canonical identity/quote contracts. Unknown venue/entitlement stays unknown."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FinancialInstrument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str
    symbol: str
    provider: str
    exchange: str | None = None
    issuer_id: str | None = None
    trading_currency: str | None = None
    asset_class: Literal["equity_or_etf_unclassified", "filing_entity"]
    identity_quality: Literal["source_qualified_symbol", "explicit_issuer_mapping"]
    limitations: list[str] = Field(default_factory=list)


class MarketObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument: FinancialInstrument
    price: Decimal = Field(gt=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    observation_timestamp: datetime
    retrieval_timestamp: datetime
    source_url: str
    quote_kind: Literal["unverified", "stale", "historical", "end_of_day"]
    adjustment_type: str = "unspecified"
    market_session: str = "unspecified"

    @model_validator(mode="after")
    def timestamps(self):
        if not self.observation_timestamp.tzinfo or not self.retrieval_timestamp.tzinfo:
            raise ValueError("Timezone-aware quote timestamps required")
        if self.observation_timestamp > self.retrieval_timestamp:
            raise ValueError("Future quote observation")
        return self


def instrument_identity(
    symbol, provider, *, exchange=None, issuer_id=None, currency=None
):
    return FinancialInstrument(
        instrument_id=(
            provider + ":" + exchange + ":" + symbol
            if exchange
            else provider + ":symbol:" + symbol
        ),
        symbol=symbol,
        provider=provider,
        exchange=exchange,
        issuer_id=issuer_id,
        trading_currency=currency,
        asset_class="filing_entity"
        if provider == "sec.gov"
        else "equity_or_etf_unclassified",
        identity_quality="explicit_issuer_mapping"
        if issuer_id
        else "source_qualified_symbol",
        limitations=[]
        if exchange
        else [
            "Trading venue unresolved; source-qualified identity must not be merged by ticker alone"
        ],
    )
