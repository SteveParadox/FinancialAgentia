"""Version 1 specialist execution contract; exported in persisted report artifacts."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class AgentExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: Literal["market", "fundamental", "news", "quantitative", "risk", "research"]
    execution_id: str
    task_id: str
    input_schema_version: Literal[1] = 1
    output_schema_version: Literal[1] = 1
    status: Literal["completed", "failed", "skipped"]
    tools: list[str]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    error_code: str | None = None
    retry_eligible: bool = False
    findings: dict = Field(default_factory=dict)
    evidence_references: list[str] = Field(default_factory=list)
    uncertainty: str = "Provider limitations and coverage must be reviewed in the report"
    usage: dict | None = None  # Never estimate missing SDK usage as measured cost.


def specialist(tools: list[str]) -> str:
    if any("risk" in name for name in tools):
        return "risk"
    if any("technical" in name or "ratios" in name for name in tools):
        return "quantitative"
    if any("news" in name or "filings" in name for name in tools):
        return "news"
    if any("price" in name for name in tools):
        return "market"
    if any("financial" in name or "company" in name or "statements" in name or "sheets" in name for name in tools):
        return "fundamental"
    return "research"
