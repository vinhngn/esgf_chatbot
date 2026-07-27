"""Schema-independent contracts used before Cypher rendering."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QueryOperation(StrEnum):
    RETRIEVE = "retrieve"
    COUNT = "count"
    AGGREGATE = "aggregate"
    RANK = "rank"
    COMPARE = "compare"
    PATH = "path"
    EXISTS = "exists"


class SortDirection(StrEnum):
    NONE = "none"
    ASCENDING = "ascending"
    DESCENDING = "descending"


class EvidenceActionType(StrEnum):
    VERIFY_TARGET_LABEL = "verify_target_label"
    VERIFY_RELATIONSHIP = "verify_relationship"


class SemanticConstraint(BaseModel):
    """One question-derived constraint, before schema grounding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str = ""
    attribute: str = ""
    operator: str = Field(min_length=1)
    value: str = ""


class SemanticContract(BaseModel):
    """The meaning of a question without Neo4j labels or relationship names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_concepts: tuple[str, ...] = ()
    operation: QueryOperation = QueryOperation.RETRIEVE
    metric: str = ""
    relations: tuple[str, ...] = ()
    grouping: tuple[str, ...] = ()
    projections: tuple[str, ...] = ()
    constraints: tuple[SemanticConstraint, ...] = ()
    order: SortDirection = SortDirection.NONE
    limit: int | None = Field(default=None, ge=1)
    ambiguities: tuple[str, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator(
        "target_concepts",
        "relations",
        "grouping",
        "projections",
        "ambiguities",
        mode="before",
    )
    @classmethod
    def normalize_text_sequence(cls, value):
        if value is None:
            return ()
        return tuple(
            text
            for item in value
            if (text := str(item).strip())
        )


class IRDirection(StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"
    UNDIRECTED = "undirected"


class QueryNodeIR(BaseModel):
    """One typed node variable in the Neo4j query IR."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    labels: tuple[str, ...] = ()
    concepts: tuple[str, ...] = ()


class QueryRelationshipIR(BaseModel):
    """One directed relationship pattern between IR node variables."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    start_variable: str
    end_variable: str
    types: tuple[str, ...] = ()
    direction: IRDirection = IRDirection.OUTGOING
    semantic_relation: str = ""
    minimum_hops: int = Field(default=1, ge=0)
    maximum_hops: int | None = Field(default=1, ge=0)


class QueryProjectionIR(BaseModel):
    """A schema-grounded value requested by the semantic contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    variable: str
    property_name: str
    concept: str = ""


class Neo4jQueryIR(BaseModel):
    """Small public IR inspired by Neo4j QueryGraph and PlannerQuery."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: QueryOperation
    nodes: tuple[QueryNodeIR, ...] = ()
    relationships: tuple[QueryRelationshipIR, ...] = ()
    projections: tuple[QueryProjectionIR, ...] = ()
    constraints: tuple[SemanticConstraint, ...] = ()
    metric: str = ""
    grouping: tuple[str, ...] = ()
    order: SortDirection = SortDirection.NONE
    limit: int | None = Field(default=None, ge=1)
    ambiguities: tuple[str, ...] = ()


class GraphProgramState(BaseModel):
    """A partial, typed graph program assembled from grounded evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    semantic_contract: SemanticContract
    target_label_hypotheses: tuple[str, ...] = ()
    relationship_hypotheses: tuple[str, ...] = ()
    projection_hypotheses: tuple[str, ...] = ()
    unresolved_slots: tuple[str, ...] = ()
    rejected_hypotheses: tuple[str, ...] = ()
    query_ir: Neo4jQueryIR | None = None
    schema_mapping_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )


class LabelHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: str = Field(min_length=1)
    label: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class PropertyHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: str = Field(min_length=1)
    label: str = Field(min_length=1)
    property_name: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class EdgeHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relation: str = Field(min_length=1)
    start_label: str = Field(min_length=1)
    relationship_type: str = Field(min_length=1)
    end_label: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class SchemaMapping(BaseModel):
    """LLM-proposed mappings that have been validated against runtime schema."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    labels: tuple[LabelHypothesis, ...] = ()
    properties: tuple[PropertyHypothesis, ...] = ()
    edges: tuple[EdgeHypothesis, ...] = ()
    ambiguities: tuple[str, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class UncertaintyReport(BaseModel):
    """Explainable compute-allocation decision for graph program search."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    search_budget: int = Field(ge=1)
    should_expand: bool
    signals: dict[str, float]
    reasons: tuple[str, ...] = ()


class EvidenceAction(BaseModel):
    """One bounded read-only observation that can reduce plan uncertainty."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_type: EvidenceActionType
    hypothesis: str = Field(min_length=1)
    cypher: str = Field(min_length=1)
    resolves_slot: str = Field(min_length=1)
    information_gain: float = Field(gt=0.0)
    estimated_cost: float = Field(gt=0.0)
    prior_weight: float = Field(default=1.0, gt=0.0)
    alternative_count: int = Field(default=1, ge=1)
    target_label: str = ""
    start_label: str = ""
    relationship_type: str = ""
    end_label: str = ""

    @property
    def utility(self) -> float:
        return (
            self.information_gain
            * self.prior_weight
            / self.estimated_cost
        )


class EvidenceObservation(BaseModel):
    """Result of executing one evidence action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: EvidenceAction
    supported: bool
    error: str | None = None


class MetamorphicProbe(BaseModel):
    """One bounded follow-up execution and its expected invariant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    cypher: str = Field(min_length=1)
    passed: bool
    detail: str = ""
    error: str | None = None


class VerificationVerdict(BaseModel):
    """Contract and execution-consistency verdict for one candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    contradictions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    probes: tuple[MetamorphicProbe, ...] = ()
