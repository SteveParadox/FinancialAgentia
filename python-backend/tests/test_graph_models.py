"""Synthetic ontology acceptance cases; no real financial claims."""

from datetime import timedelta
import json
from uuid import uuid4
import pytest
from pydantic import ValidationError
from dexter_py.graph.models import (
    EntityType,
    GraphAssertion,
    GraphEntity,
    GraphEntityIdentifier,
    GraphEvidenceReference,
    GraphOntologyDefinition,
    GraphScope,
    GraphSource,
    VerificationStatus,
    now,
    validate_direction,
)
from dexter_py.graph.config import GraphConfig

SCOPE = GraphScope(kind="principal", identity="synthetic")


def entity(kind="Company", **kwargs):
    return GraphEntity(
        scope=SCOPE, entity_type=kind, canonical_name="Synthetic entity", **kwargs
    )


def assertion(**kwargs):
    return GraphAssertion(
        scope=SCOPE,
        subject_id=uuid4(),
        object_id=uuid4(),
        predicate="SUPPLIES",
        observed_at=now(),
        provenance="synthetic fixture",
        **kwargs,
    )


@pytest.mark.parametrize("kind", list(EntityType))
def test_entity_types(kind):
    assert entity(kind).entity_type == kind


@pytest.mark.parametrize(
    "change",
    [
        {"entity_type": "Unknown"},
        {"canonical_name": " "},
        {"schema_version": "2.0"},
        {"extra": "ignored"},
    ],
)
def test_invalid_entities(change):
    with pytest.raises(ValidationError):
        GraphEntity.model_validate({**entity().model_dump(), **change})


def test_identifiers_normalize_and_prevent_ambiguity():
    cik = GraphEntityIdentifier(namespace="SEC_CIK", value="123", authority="SEC")
    assert cik.value == "0000000123"
    assert (
        cik.key
        == GraphEntityIdentifier(
            namespace="SEC_CIK", value="0000000123", authority="SEC"
        ).key
    )
    with pytest.raises(ValidationError):
        entity(identifiers=(cik, cik))
    with pytest.raises(ValidationError):
        GraphEntityIdentifier(
            namespace="EXCHANGE_TICKER", value="TEST", authority="XNAS"
        )
    a = GraphEntityIdentifier(
        namespace="EXCHANGE_TICKER",
        value="TEST",
        authority="XNAS",
        assignment="2024-01-01",
    )
    b = GraphEntityIdentifier(
        namespace="EXCHANGE_TICKER",
        value="TEST",
        authority="XNYS",
        assignment="2024-01-01",
    )
    assert a.key != b.key
    assert a.key != a.model_copy(update={"assignment": "2025-01-01"}).key
    with pytest.raises(ValidationError):
        entity(identifiers=(a,))


@pytest.mark.parametrize(
    "namespace,value,authority",
    [
        ("LEI", "bad", "GLEIF"),
        ("SEC_CIK", "１２３", "SEC"),
        ("SEC_CIK", "123", "fake"),
        ("ISIN", "bad", "ANNA"),
        ("MIC", "BAD", "ISO10383"),
    ],
)
def test_bad_identifiers(namespace, value, authority):
    with pytest.raises(ValidationError):
        GraphEntityIdentifier(namespace=namespace, value=value, authority=authority)


def test_predicate_direction_and_scope():
    person, company = entity("Person"), entity()
    row = GraphAssertion(
        scope=SCOPE,
        subject_id=person.id,
        object_id=company.id,
        predicate="EXECUTIVE_OF",
        observed_at=now(),
        provenance="fixture",
    )
    validate_direction(person, row, company)
    with pytest.raises(ValueError):
        validate_direction(company, row, person)
    wrong = row.model_copy(update={"subject_id": company.id, "object_id": person.id})
    with pytest.raises(ValueError):
        validate_direction(company, wrong, person)
    with pytest.raises(ValueError):
        validate_direction(
            person.model_copy(
                update={"scope": GraphScope(kind="workspace", identity="other")}
            ),
            row,
            company,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"predicate": "CAUSES_GUARANTEED_RETURN"},
        {"verification_status": "verified"},
        {"valid_from": now(), "valid_to": now() - timedelta(days=1)},
        {"observed_at": now() + timedelta(days=1)},
        {"ingested_at": "2024-01-01T00:00:00"},
        {"extraction_confidence": float("nan")},
        {"schema_version": "9.0"},
    ],
)
def test_invalid_assertions(change):
    with pytest.raises(ValidationError):
        GraphAssertion.model_validate({**assertion().model_dump(), **change})


def test_unknown_dates_preserved_and_confidence_is_not_verification():
    row = assertion(extraction_confidence=1)
    assert row.valid_from is None and row.valid_to is None
    assert row.verification_status == VerificationStatus.PROPOSED


def test_provenance_validation():
    with pytest.raises(ValidationError):
        GraphSource(
            scope=SCOPE,
            provider="fixture",
            document_type="filing",
            license="restricted",
        )
    with pytest.raises(ValidationError):
        GraphSource(
            scope=SCOPE,
            provider="fixture",
            url="https://user:password@example.com/a",
            document_type="filing",
            license="restricted",
        )
    with pytest.raises(ValidationError):
        GraphEvidenceReference(source_id="bad", location="p2")
    ref = GraphEvidenceReference(source_id=uuid4(), location="p2")
    with pytest.raises(ValidationError):
        assertion(evidence=(ref, ref))


def test_deterministic_roundtrip_and_schema():
    row = assertion()
    assert GraphAssertion.model_validate_json(row.canonical_json()) == row
    assert row.canonical_json() == row.canonical_json()
    ontology = GraphOntologyDefinition()
    assert len(ontology.predicates) == 15
    assert json.loads(ontology.canonical_json())["schema_version"] == "1.0"
    assert GraphAssertion.model_json_schema()["additionalProperties"] is False


def test_configuration_opt_in_and_secret_redaction():
    assert GraphConfig().enabled is False
    with pytest.raises(ValidationError):
        GraphConfig(enabled=True)
    config = GraphConfig(enabled=True, password="fixture-secret")
    assert "fixture-secret" not in repr(config)
    with pytest.raises(ValidationError) as exc:
        GraphConfig(uri="bolt://user:pass@host:7687", password="private-fixture-secret")
    assert "private-fixture-secret" not in str(exc.value)
    assert "user:pass" not in str(exc.value)


def test_equivalent_timezone_serialization_and_source_date_integrity():
    from datetime import timezone

    first = assertion()
    shifted = GraphAssertion.model_validate(
        {
            **first.model_dump(),
            "observed_at": first.observed_at.astimezone(timezone(timedelta(hours=1))),
        }
    )
    assert shifted.canonical_json() == first.canonical_json()
    with pytest.raises(ValidationError):
        GraphSource(
            scope=SCOPE,
            provider="test",
            document_id="fixture",
            document_type="test",
            license="test",
            published_at=now() + timedelta(days=1),
        )


def test_future_entity_and_source_timestamps_are_rejected():
    future = now() + timedelta(days=1)
    with pytest.raises(ValidationError):
        GraphEntity(
            scope=SCOPE,
            entity_type="Company",
            canonical_name="Synthetic future company",
            created_at=future,
            updated_at=future,
        )
    with pytest.raises(ValidationError):
        GraphSource(
            scope=SCOPE,
            provider="Synthetic fixture",
            document_id="fixture:future",
            document_type="test filing",
            license="test only",
            retrieved_at=future,
        )
