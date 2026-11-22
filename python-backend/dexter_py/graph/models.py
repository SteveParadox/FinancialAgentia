"""Version-one financial ontology. No LLM-generated predicates or verification."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import re
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
    field_validator,
)

SCHEMA_VERSION: Literal["1.0"] = "1.0"
Text = Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]


def now() -> datetime:
    return datetime.now(timezone.utc)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    @field_validator(
        "created_at",
        "updated_at",
        "published_at",
        "retrieved_at",
        "observed_at",
        "valid_from",
        "valid_to",
        "ingested_at",
        check_fields=False,
    )
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(timezone.utc) if value is not None else None

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )


class EntityType(StrEnum):
    COMPANY = "Company"
    PERSON = "Person"
    FACILITY = "Facility"
    SECTOR = "Sector"
    INDUSTRY = "Industry"
    COUNTRY = "Country"
    REGION = "Region"
    FINANCIAL_INDICATOR = "FinancialIndicator"
    FILING = "Filing"
    EVENT = "Event"
    SOURCE = "Source"
    SECURITY = "Security"
    EXCHANGE = "Exchange"
    CURRENCY = "Currency"


class GraphRelationshipPredicate(StrEnum):
    SUPPLIES = "SUPPLIES"
    CUSTOMER_OF = "CUSTOMER_OF"
    COMPETES_WITH = "COMPETES_WITH"
    OWNS = "OWNS"
    SUBSIDIARY_OF = "SUBSIDIARY_OF"
    OPERATES = "OPERATES"
    EMPLOYED_BY = "EMPLOYED_BY"
    EXECUTIVE_OF = "EXECUTIVE_OF"
    BELONGS_TO_SECTOR = "BELONGS_TO_SECTOR"
    OPERATES_IN = "OPERATES_IN"
    LOCATED_IN = "LOCATED_IN"
    EXPOSED_TO = "EXPOSED_TO"
    REPORTED_IN = "REPORTED_IN"
    AFFECTED_BY = "AFFECTED_BY"
    INFLUENCES = "INFLUENCES"


class VerificationStatus(StrEnum):
    PROPOSED = "proposed"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    RETRACTED = "retracted"
    SUPERSEDED = "superseded"


class GraphScope(Strict):
    kind: Literal["principal", "workspace"]
    identity: Annotated[
        str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9:_-]+$")
    ]

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.identity}"


class GraphEntityIdentifier(Strict):
    namespace: Literal[
        "SEC_CIK",
        "LEI",
        "EXCHANGE_TICKER",
        "ISIN",
        "MIC",
        "ISO_COUNTRY",
        "ISO_CURRENCY",
    ]
    value: Text
    authority: Text
    # Ticker reuse must identify an assignment, not just a venue/symbol pair.
    assignment: Text | None = None

    @model_validator(mode="after")
    def normalized(self) -> Self:
        value = self.value.strip().upper()
        authorities = {
            "SEC_CIK": "SEC",
            "LEI": "GLEIF",
            "ISIN": "ANNA",
            "MIC": "ISO10383",
            "ISO_COUNTRY": "ISO3166",
            "ISO_CURRENCY": "ISO4217",
        }
        if self.namespace != "EXCHANGE_TICKER":
            if (
                self.authority != authorities[self.namespace]
                or self.assignment is not None
            ):
                raise ValueError(
                    "Use canonical namespace authority; assignment is only valid for tickers"
                )
        if self.namespace == "SEC_CIK":
            if not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 10:
                raise ValueError("CIK must contain 1..10 ASCII digits")
            value = value.zfill(10)
        patterns = {
            "LEI": r"[A-Z0-9]{20}",
            "ISIN": r"[A-Z]{2}[A-Z0-9]{9}[0-9]",
            "MIC": r"[A-Z0-9]{4}",
            "ISO_COUNTRY": r"[A-Z]{2}",
            "ISO_CURRENCY": r"[A-Z]{3}",
        }
        if self.namespace in patterns and not re.fullmatch(
            patterns[self.namespace], value
        ):
            raise ValueError("Malformed authoritative identifier")
        if self.namespace == "EXCHANGE_TICKER":
            if (
                not re.fullmatch(r"[A-Z0-9._-]{1,20}", value)
                or not re.fullmatch(r"[A-Z0-9]{4}", self.authority)
                or not self.assignment
            ):
                raise ValueError(
                    "Ticker requires MIC authority and explicit assignment epoch"
                )
        object.__setattr__(self, "value", value)
        return self

    @property
    def key(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


class GraphEntity(Strict):
    id: UUID = Field(default_factory=uuid4)
    scope: GraphScope
    entity_type: EntityType
    canonical_name: Text
    aliases: tuple[Text, ...] = Field(default=(), max_length=50)
    identifiers: tuple[GraphEntityIdentifier, ...] = Field(default=(), max_length=30)
    created_at: AwareDatetime = Field(default_factory=now)
    updated_at: AwareDatetime = Field(default_factory=now)
    schema_version: Literal["1.0"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Entity update precedes creation")
        if len({item.key for item in self.identifiers}) != len(self.identifiers):
            raise ValueError("Duplicate identifier")
        allowed = {
            "EXCHANGE_TICKER": {EntityType.SECURITY},
            "ISIN": {EntityType.SECURITY},
            "SEC_CIK": {EntityType.COMPANY},
            "LEI": {EntityType.COMPANY},
            "MIC": {EntityType.EXCHANGE},
            "ISO_COUNTRY": {EntityType.COUNTRY},
            "ISO_CURRENCY": {EntityType.CURRENCY},
        }
        if any(
            self.entity_type not in allowed[item.namespace] for item in self.identifiers
        ):
            raise ValueError("Identifier namespace incompatible with entity type")
        return self


class GraphSource(Strict):
    id: UUID = Field(default_factory=uuid4)
    scope: GraphScope
    provider: Text
    url: HttpUrl | None = None
    document_id: Text | None = None
    published_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime = Field(default_factory=now)
    document_type: Text
    content_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = (
        None
    )
    license: Text
    redistribution: Literal["prohibited", "restricted", "permitted", "unknown"] = (
        "unknown"
    )
    verification_status: VerificationStatus = VerificationStatus.PROPOSED
    schema_version: Literal["1.0"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def location(self) -> Self:
        if self.published_at and self.published_at > self.retrieved_at:
            raise ValueError("Source publication cannot follow retrieval")
        if self.url is None and self.document_id is None:
            raise ValueError("Source needs URL or stable document ID")
        if self.url and (self.url.username or self.url.password):
            raise ValueError("Source URL must not contain credentials")
        return self


class GraphEvidenceReference(Strict):
    source_id: UUID
    location: Text
    passage_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = (
        None
    )


class GraphAssertion(Strict):
    id: UUID = Field(default_factory=uuid4)
    scope: GraphScope
    subject_id: UUID
    predicate: GraphRelationshipPredicate
    object_id: UUID
    evidence: tuple[GraphEvidenceReference, ...] = Field(default=(), max_length=30)
    observed_at: AwareDatetime
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    ingested_at: AwareDatetime = Field(default_factory=now)
    verification_status: VerificationStatus = VerificationStatus.PROPOSED
    provenance: Text
    extraction_confidence: float | None = Field(
        default=None, ge=0, le=1, allow_inf_nan=False
    )
    supersedes: UUID | None = None
    schema_version: Literal["1.0"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if self.subject_id == self.object_id:
            raise ValueError("Self assertions are not supported")
        if self.valid_from and self.valid_to and self.valid_from >= self.valid_to:
            raise ValueError("Valid time interval must be nonempty")
        if self.observed_at > self.ingested_at:
            raise ValueError("Observation cannot follow ingestion")
        if (
            self.verification_status == VerificationStatus.VERIFIED
            and not self.evidence
        ):
            raise ValueError("Verified assertions require evidence")
        if len({(e.source_id, e.location) for e in self.evidence}) != len(
            self.evidence
        ):
            raise ValueError("Duplicate evidence reference")
        if self.supersedes == self.id:
            raise ValueError("Assertion cannot supersede itself")
        return self


class PredicateDefinition(Strict):
    predicate: GraphRelationshipPredicate
    subject_types: tuple[EntityType, ...]
    object_types: tuple[EntityType, ...]
    meaning: str
    symmetric: bool = False
    cardinality: Literal["many-to-many"] = "many-to-many"
    direction: Literal["subject-to-object"] = "subject-to-object"
    inverse_storage: Literal["derived"] = "derived"
    evidence_requirement: str = "Verified source plus precise passage/document location; confidence is insufficient."
    temporal_requirement: str = "Record known effective dates only; unknown bounds remain null. Half-open intervals."


C, P, F = EntityType.COMPANY, EntityType.PERSON, EntityType.FACILITY
ALL = frozenset(EntityType)


def definition(
    predicate: str,
    subjects: set[EntityType] | frozenset[EntityType],
    objects: set[EntityType] | frozenset[EntityType],
    meaning: str,
    symmetric: bool = False,
) -> PredicateDefinition:
    return PredicateDefinition(
        predicate=GraphRelationshipPredicate(predicate),
        subject_types=tuple(sorted(subjects)),
        object_types=tuple(sorted(objects)),
        meaning=meaning,
        symmetric=symmetric,
    )


DEFINITIONS = (
    definition("SUPPLIES", {C}, {C}, "Subject provides goods/services to object."),
    definition(
        "CUSTOMER_OF", {C}, {C}, "Subject purchases from object; inverse of SUPPLIES."
    ),
    definition(
        "COMPETES_WITH",
        {C},
        {C},
        "Documented competition; no dependency or causality inferred.",
        True,
    ),
    definition(
        "OWNS",
        {C, P},
        {C, F, EntityType.SECURITY},
        "Documented ownership interest; no implied control.",
    ),
    definition(
        "SUBSIDIARY_OF", {C}, {C}, "Subject is a documented subsidiary of object."
    ),
    definition("OPERATES", {C}, {F}, "Company operates facility."),
    definition("EMPLOYED_BY", {P}, {C}, "Person employed by company."),
    definition("EXECUTIVE_OF", {P}, {C}, "Person holds documented executive role."),
    definition(
        "BELONGS_TO_SECTOR",
        {C, EntityType.INDUSTRY},
        {EntityType.SECTOR},
        "Documented classification, not ownership.",
    ),
    definition(
        "OPERATES_IN",
        {C},
        {EntityType.COUNTRY, EntityType.REGION, EntityType.INDUSTRY},
        "Documented business operations in geography/industry.",
    ),
    definition(
        "LOCATED_IN",
        {C, P, F, EntityType.EXCHANGE, EntityType.REGION},
        {EntityType.COUNTRY, EntityType.REGION},
        "Documented location.",
    ),
    definition(
        "EXPOSED_TO",
        {C, F, EntityType.SECURITY},
        {
            EntityType.EVENT,
            EntityType.COUNTRY,
            EntityType.REGION,
            EntityType.FINANCIAL_INDICATOR,
            EntityType.CURRENCY,
        },
        "Documented exposure; not verified financial impact.",
    ),
    definition(
        "REPORTED_IN",
        ALL - {EntityType.SOURCE},
        {EntityType.FILING, EntityType.SOURCE},
        "Entity mentioned in document; not endorsement.",
    ),
    definition(
        "AFFECTED_BY",
        {C, F, EntityType.SECURITY, EntityType.SECTOR, EntityType.INDUSTRY},
        {EntityType.EVENT},
        "Documented observed effect; do not infer magnitude.",
    ),
    definition(
        "INFLUENCES",
        {EntityType.EVENT, EntityType.FINANCIAL_INDICATOR},
        {EntityType.FINANCIAL_INDICATOR, EntityType.SECTOR, EntityType.INDUSTRY},
        "Explicit source-supported influence hypothesis; not independently established causality.",
    ),
)
PREDICATES = {row.predicate: row for row in DEFINITIONS}


class GraphOntologyDefinition(Strict):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    entity_types: tuple[EntityType, ...] = tuple(EntityType)
    predicates: tuple[PredicateDefinition, ...] = DEFINITIONS


def validate_direction(
    subject: GraphEntity, assertion: GraphAssertion, obj: GraphEntity
) -> None:
    spec = PREDICATES[assertion.predicate]
    if (
        subject.id != assertion.subject_id
        or obj.id != assertion.object_id
        or subject.scope != assertion.scope
        or obj.scope != assertion.scope
    ):
        raise ValueError("Assertion endpoints or scopes do not match")
    if (
        subject.entity_type not in spec.subject_types
        or obj.entity_type not in spec.object_types
    ):
        raise ValueError("Invalid predicate direction or endpoint types")
