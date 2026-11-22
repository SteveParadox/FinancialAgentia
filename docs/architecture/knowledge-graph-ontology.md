# Financial graph ontology v1.0

The strict contracts live in `python-backend/dexter_py/graph/models.py` and the additive wire catalog in `contracts/knowledge-graph.v1.schema.json`. Unknown schema versions fail validation; upgrades require explicit new models/migrations. Existing platform/research contracts are unchanged.

## Entities and identity

Entity types: Company, Person, Facility, Sector, Industry, Country, Region, FinancialIndicator, Filing, Event, Source, Security, Exchange, Currency. Security, Exchange and Currency preserve the distinction already present in financial instrument contracts. An issuer is not its traded security.

Each entity has a UUID, private principal/workspace scope, type, canonical name, aliases, authoritative identifiers and aware created/updated timestamps. Scope is required, with no public fallback. Names and aliases are display metadata, never identity keys. Metadata changes use expected revision and retain EntityRevision history. Identifier ownership is permanent in v1; reassignment requires a future reviewed identity workflow.

Identifiers are namespaced and normalized. CIK is 10 ASCII digits with authority SEC; LEI uses GLEIF; ISIN uses ANNA; MIC uses ISO10383; country/currency use ISO3166/ISO4217. These validate shape, not existence or checksums in authoritative registries. EXCHANGE_TICKER requires a Security, MIC authority and explicit assignment epoch; different venues or reused assignments remain distinct. A bare ticker cannot identify a Company. Namespaces and canonical authorities cannot be varied to evade collision checks. Distinct internal IDs claiming an existing scoped identifier fail atomically rather than silently merging legal entities.

## Predicates

All rows are directed subject → object. Cardinality is many-to-many; no unique executive, supplier or sector assumption is imposed. No inverse financial edges are stored. CUSTOMER_OF is semantically the inverse of SUPPLIES, but no reciprocal assertion is fabricated. COMPETES_WITH is symmetric in meaning; traversal returns assertions touching either endpoint without manufacturing reverse evidence. Owning shares does not establish subsidiary control.

| Predicate | Subject types | Object types | Meaning | Symmetric |
| --- | --- | --- | --- | --- |
| SUPPLIES | Company | Company | Subject provides goods/services to object. | No |
| CUSTOMER_OF | Company | Company | Subject purchases from object; inverse of SUPPLIES. | No |
| COMPETES_WITH | Company | Company | Documented competition; no dependency or causality inferred. | Yes |
| OWNS | Company, Person | Company, Facility, Security | Documented ownership interest; no implied control. | No |
| SUBSIDIARY_OF | Company | Company | Subject is a documented subsidiary of object. | No |
| OPERATES | Company | Facility | Company operates facility. | No |
| EMPLOYED_BY | Person | Company | Person employed by company. | No |
| EXECUTIVE_OF | Person | Company | Person holds documented executive role. | No |
| BELONGS_TO_SECTOR | Company, Industry | Sector | Documented classification, not ownership. | No |
| OPERATES_IN | Company | Country, Industry, Region | Documented business operations in geography/industry. | No |
| LOCATED_IN | Company, Exchange, Facility, Person, Region | Country, Region | Documented location. | No |
| EXPOSED_TO | Company, Facility, Security | Country, Currency, Event, FinancialIndicator, Region | Documented exposure; not verified financial impact. | No |
| REPORTED_IN | Company, Country, Currency, Event, Exchange, Facility, Filing, FinancialIndicator, Industry, Person, Region, Sector, Security | Filing, Source | Entity mentioned in document; not endorsement. | No |
| AFFECTED_BY | Company, Facility, Industry, Sector, Security | Event | Documented observed effect; do not infer magnitude. | No |
| INFLUENCES | Event, FinancialIndicator | FinancialIndicator, Industry, Sector | Explicit source-supported influence hypothesis; not independently established causality. | No |

For every predicate, verification requires at least one verified Source and a precise document/passage location on each evidence reference. Source identity is validated transactionally. Every predicate uses explicit effective dates when known, with unknown bounds left null. Extraction confidence never confers verification. The repository validates endpoint types/direction against the fixed server ontology, irrespective of caller-provided ontology definitions. No arbitrary LLM predicate or label is accepted.

## Assertions, evidence and time

Assertions contain UUID, scope, endpoints, allowlisted predicate, evidence references, observed_at, valid_from/to, server-recorded ingested_at, status, provenance, optional confidence, optional supersedes and schema version. Evidence includes source ID, bounded location and optional SHA-256 passage digest; source snapshots contain provider, URL/document ID, publication/retrieval timestamps, document type, digest, licensing/redistribution and verification status. No content fetching occurs in this foundation. GraphSource records are authoritative provenance; Source/Filing entity types represent document mentions and are not automatically trusted provenance records.

Valid time describes real-world effect; knowledge time describes when the database learned it. Publication, retrieval and observation are distinct from both. All timestamps require timezones. Intervals are half-open `[valid_from, valid_to)`. Missing dates are never inferred. A date-filtered query returns only assertions with both bounds established and containing that date; null does not mean indefinite validity. An unfiltered query retains unknown dates. This conservative choice may omit facts with a known start and unknown end; Phase 3 must provide an explicit uncertainty policy before widening semantics.

`known_at` selects assertions ingested by that instant and the latest lifecycle revision recorded by that instant. A late filing can describe a much earlier valid interval without appearing in earlier knowledge-time results. Assertion ingestion and lifecycle revision times come from the service clock; production requires synchronized trusted host clocks. Entity metadata is current in v1; full bitemporal entity/identifier lookup is deferred.

New persisted assertions must be proposed. Trusted reviewer code can move proposed → verified/contradicted/retracted/superseded, or verified → contradicted/retracted/superseded, with actor, reason and expected revision. Terminal decisions remain immutable; new evidence creates a new assertion. Sources are immutable snapshots, so re-verifying a document requires a new source snapshot. Competing assertions coexist and retain their own provenance. A supersedes reference is scoped and must describe the same endpoints/predicate; it never deletes or silently changes the prior assertion's status.

Retries reuse the caller's stable assertion UUID. A scoped semantic fingerprint (excluding UUID and ingestion time) also prevents storing the same observation/evidence twice under different UUIDs. Different observations, evidence, validity or supersession records remain independent. Logical inverse/symmetric duplicates are not automatically merged.

## Synthetic example

Create two scoped Company entities, persist a synthetic GraphSource, then save a proposed SUPPLIES assertion with a page reference. A trusted reviewer independently validates the source and evidence before transition to verified. All test fixtures use synthetic entities and are never loaded by production startup. See `tests/test_graph_repository.py::populate` for runnable construction.
