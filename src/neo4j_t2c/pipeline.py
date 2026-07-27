"""Retrieval-grounded Text-to-Cypher execution with universal schema grounding."""

from __future__ import annotations

import logging
import os

from neo4j_t2c.adapters.legacy import legacy_database_names
from neo4j_t2c.execution.safety import (
    cap_execution_cypher as _cap_execution_cypher,
)
from neo4j_t2c.execution.safety import (
    execution_row_cap as _execution_row_cap,
)
from neo4j_t2c.execution.safety import (
    result_quality_feedback as _result_quality_feedback,
)
from neo4j_t2c.execution.safety import (
    result_summary as _result_summary,
)
from neo4j_t2c.execution.schema_validation import (
    relationship_schema_feedback as _relationship_schema_feedback,
)
from neo4j_t2c.generation.cleanup import (
    clean_cypher_query,
)
from neo4j_t2c.generation.postprocessing import (
    repair_backticked_label_with_inline_map as _repair_backticked_label_with_inline_map,
)
from neo4j_t2c.generation.prompts import (
    build_coder_messages as _build_coder_messages,
)
from neo4j_t2c.grounding.context import (
    get_entity_resolution_context as _get_entity_resolution_context,
)
from neo4j_t2c.grounding.context import (
    get_grounded_schema as _get_grounded_schema,
)
from neo4j_t2c.grounding.context import (
    get_learned_profile_evidence as _get_learned_profile_evidence,
)
from neo4j_t2c.grounding.context import (
    get_runtime_property_context as _get_runtime_property_context,
)
from neo4j_t2c.grounding.context import (
    has_profile_for_current_database as _has_profile_for_current_database,
)
from neo4j_t2c.grounding.context import (
    schema_grounding_mode as _schema_grounding_mode,
)
from neo4j_t2c.observability.tracing import (
    new_trace_id,
    trace_event,
    verbose_trace_enabled,
)
from neo4j_t2c.planning import (
    SemanticContract,
    assess_uncertainty,
    build_initial_graph_program,
    render_schema_slice,
    run_adaptive_search,
    verification_feedback,
    verify_candidate,
)
from neo4j_t2c.profiles.paths import profile_path
from neo4j_t2c.runtime import PipelineDependencies

logger = logging.getLogger(__name__)
DEFAULT_CYPHER_RETRIES = 2


def _cypher_retries() -> int:
    try:
        return max(0, int(os.getenv("T2C_CYPHER_RETRIES", str(DEFAULT_CYPHER_RETRIES))))
    except ValueError:
        return DEFAULT_CYPHER_RETRIES


def _adaptive_planning_mode() -> str:
    mode = os.getenv(
        "T2C_ADAPTIVE_PLANNING_MODE",
        "shadow",
    ).strip().lower()
    return mode if mode in {"shadow", "active"} else "shadow"


def _graph_schema(graph: object) -> str:
    schema = getattr(graph, "schema", None)
    if isinstance(schema, str):
        return schema
    return str(getattr(graph, "get_schema", "") or "")


def invoke_chain(
    question: str,
    schema: str = "",
    trace_id: str | None = None,
    *,
    dependencies: PipelineDependencies | None = None,
    execute: bool | None = None,
    max_retries: int | None = None,
) -> dict | str:
    trace_id = trace_id or new_trace_id()
    legacy_names = legacy_database_names() if dependencies is None else None
    database_name = dependencies.database if dependencies is not None else legacy_names[0]
    physical_database = (
        dependencies.physical_database
        if dependencies is not None
        else legacy_names[1]
    )
    profile_store = (
        dependencies.profile_store if dependencies is not None else None
    )
    if dependencies is None:
        from models.graph import get_graph, maybe_refresh_schema
        from models.llm import get_cypher_llm

        maybe_refresh_schema()
        graph = get_graph()
        llm = get_cypher_llm()
    else:
        graph = dependencies.graph
        llm = dependencies.cypher_model

    full_schema = schema or _graph_schema(graph)
    grounding_debug = {}
    adaptive_evidence_context = ""
    semantic_contract: SemanticContract | None = None
    planning_mode = _adaptive_planning_mode()

    trace_event(
        logger,
        trace_id,
        "CHAIN-01",
        "Text-to-Cypher request received",
        {
            "database": database_name,
            "physical_database": physical_database,
            "question": question,
            "schema_source": "request/CSV" if schema else "Neo4j runtime",
            "schema_chars": len(full_schema),
            "verbose_trace": verbose_trace_enabled(),
        },
    )

    # Use grounded schema when no explicit schema is provided. If an analyzed
    # profile exists, skip the extra grounding LLM by default; the profile
    # already carries schema paths, recipes, examples, and value hints.
    profile_available = _has_profile_for_current_database(
        database=database_name,
        profile_store=profile_store,
    )
    if not schema and _schema_grounding_mode() != "llm" and profile_available:
        db_schema = full_schema
        trace_event(
            logger,
            trace_id,
            "CHAIN-02",
            "Profile exists: skip schema-grounding LLM and use runtime schema plus profile context",
            {
                "profile_location": (
                    f"{type(profile_store).__name__}:{database_name}"
                    if profile_store is not None
                    else str(profile_path(database_name))
                ),
                "schema_grounding_mode": _schema_grounding_mode(),
            },
        )
    elif not schema:
        trace_event(
            logger,
            trace_id,
            "CHAIN-02",
            "No explicit schema supplied: call the schema-grounding LLM",
        )
        db_schema, grounding_debug = _get_grounded_schema(
            question,
            full_schema,
            trace_id,
            database=database_name,
            grounding_model=(
                dependencies.schema_grounding_model
                if dependencies is not None
                else None
            ),
        )
    else:
        db_schema = full_schema
        trace_event(
            logger,
            trace_id,
            "CHAIN-02",
            "Explicit schema supplied: schema-grounding LLM is bypassed",
            {
                "reason": "The evaluator/request already supplied schema text",
                "schema_chars": len(schema),
            },
        )

    trace_event(
        logger,
        trace_id,
        "CHAIN-04",
        "Static domain prompt omitted; runtime schema and retrieved evidence control context",
        {"profile_available": profile_available},
        verbose_only=True,
    )
    max_cypher_retries = (
        _cypher_retries()
        if max_retries is None
        else max(0, int(max_retries))
    )
    should_execute = (
        execute
        if execute is not None
        else os.getenv("T2C_GENERATE_ONLY", "").strip().lower()
        not in {"1", "true", "yes", "on"}
    )
    planning_enabled = should_execute or planning_mode == "active"
    learned_evidence = _get_learned_profile_evidence(
        question,
        trace_id,
        database=database_name,
        profile_store=profile_store,
        reranker_model=llm if planning_enabled else None,
    )
    learned_context = learned_evidence.context
    semantic_payload = learned_evidence.structured.get("semantic_contract")
    if semantic_payload:
        semantic_contract = SemanticContract.model_validate(semantic_payload)
        graph_program = build_initial_graph_program(
            semantic_contract,
            learned_evidence.structured,
        )
        uncertainty = assess_uncertainty(
            graph_program,
            learned_evidence.structured,
        )
        planning_model = (
            dependencies.semantic_planning_model
            if dependencies is not None
            else llm
        )
        search_outcome = run_adaptive_search(
            state=graph_program,
            uncertainty=uncertainty,
            profile_context=learned_evidence.structured,
            runtime_schema=full_schema,
            graph=graph,
            model=planning_model,
        )
        planning_debug = {
            "semantic_contract": semantic_contract.model_dump(mode="json"),
            "graph_program": graph_program.model_dump(mode="json"),
            "uncertainty": uncertainty.model_dump(mode="json"),
            "search": search_outcome.model_dump(mode="json"),
            "mode": planning_mode,
        }
        if search_outcome.observations:
            first_observation = search_outcome.observations[0]
            planning_debug["selected_action"] = (
                first_observation.action.model_dump(mode="json")
            )
            planning_debug["observation"] = (
                first_observation.model_dump(mode="json")
            )
        if planning_mode == "active":
            adaptive_evidence_context = search_outcome.evidence_context
            sliced_schema = render_schema_slice(
                full_schema,
                search_outcome.final_state,
            )
            if sliced_schema:
                db_schema = sliced_schema
                planning_debug["schema_slice"] = {
                    "chars": len(sliced_schema),
                    "full_schema_chars": len(full_schema),
                }
        grounding_debug["adaptive_planning"] = planning_debug
        trace_event(
            logger,
            trace_id,
            "CHAIN-04A",
            f"Adaptive graph program planning evaluated in {planning_mode} mode",
            planning_debug,
        )
    trace_event(
        logger,
        trace_id,
        "CHAIN-04C",
        "Learned profile context inserted before user input",
        learned_context,
        verbose_only=True,
    )
    entity_evidence = _get_entity_resolution_context(
        question=question,
        graph=graph,
        trace_id=trace_id,
    )
    entity_context = entity_evidence.context
    grounding_debug["vector"] = entity_evidence.vector_resolution
    trace_event(
        logger,
        trace_id,
        "CHAIN-04F",
        "Indexed entity/literal context inserted before user input",
        entity_context,
        verbose_only=True,
    )
    property_context = _get_runtime_property_context(
        question=question,
        runtime_schema=full_schema,
        learned_context=f"{learned_context}\n\n{entity_context}",
        graph=graph,
        trace_id=trace_id,
    )
    current_cypher = ""
    last_error: str | None = None

    for attempt in range(1 + max_cypher_retries):
        logger.info("[Synthesizer] Attempt %d/%d", attempt + 1, 1 + max_cypher_retries)
        messages = _build_coder_messages(
            schema=db_schema,
            learned_context=learned_context,
            evidence_context="\n\n".join(
                part
                for part in (
                    adaptive_evidence_context,
                    entity_context,
                    property_context,
                )
                if part
            ),
            question=question,
            current_cypher=current_cypher,
            last_error=last_error,
        )
        trace_event(
            logger,
            trace_id,
            "CHAIN-05",
            f"Role-separated prompt sent to the Cypher LLM (attempt {attempt + 1})",
            {
                "system": messages[0].content,
                "user": messages[1].content,
            },
            verbose_only=True,
        )
        try:
            response = llm.invoke(messages)
        except Exception as exc:
            trace_event(
                logger,
                trace_id,
                "CHAIN-ERROR",
                "Cypher LLM request failed",
                {
                    "attempt": attempt + 1,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise
        raw_cypher_response = response.content.strip()
        trace_event(
            logger,
            trace_id,
            "CHAIN-06",
            "Raw return from the Cypher LLM: one text string",
            {
                "return_type": type(response.content).__name__,
                "raw_text": raw_cypher_response,
            },
        )
        current_cypher = clean_cypher_query(raw_cypher_response)
        current_cypher = _repair_backticked_label_with_inline_map(current_cypher)
        trace_event(
            logger,
            trace_id,
            "CHAIN-07",
            "Final cleaned Cypher before Neo4j validation",
            current_cypher,
        )

        schema_feedback = (
            _relationship_schema_feedback(current_cypher, full_schema)
            if should_execute
            else None
        )
        if schema_feedback:
            trace_event(
                logger,
                trace_id,
                "CHAIN-07B",
                "Generated relationship structure contradicts runtime schema",
                {
                    "feedback": schema_feedback,
                    "will_retry": attempt < max_cypher_retries,
                },
            )
            if attempt < max_cypher_retries:
                last_error = schema_feedback
                continue
            return f"CYPHER_ERROR: {schema_feedback}"

        if not should_execute:
            trace_event(
                logger,
                trace_id,
                "CHAIN-08",
                "Generate-only mode enabled; skip Neo4j EXPLAIN/execution and let evaluator execute",
            )
            return {
                "query": current_cypher,
                "result": [],
                "trace_id": trace_id,
                "intermediate_steps": [
                    {
                        "query": current_cypher,
                        "grounding": grounding_debug,
                        "trace_id": trace_id,
                    }
                ],
            }

        try:
            logger.info("[Cypher] Validating syntax via EXPLAIN...")
            graph.query(f"EXPLAIN {current_cypher}")
            trace_event(
                logger,
                trace_id,
                "CHAIN-08",
                "Neo4j EXPLAIN accepted the Cypher syntax",
            )

            execution_cypher, execution_was_capped = _cap_execution_cypher(current_cypher)
            if execution_was_capped:
                trace_event(
                    logger,
                    trace_id,
                    "CHAIN-08B",
                    "API-side execution was capped to protect memory; generated Cypher is unchanged",
                    {
                        "generated_cypher": current_cypher,
                        "execution_cypher": execution_cypher,
                        "row_cap": _execution_row_cap(),
                    },
                )

            logger.info("[Cypher] Executing: %s", execution_cypher)
            raw_result = graph.query(execution_cypher)
            trace_event(
                logger,
                trace_id,
                "CHAIN-09",
                "Neo4j execution completed",
                _result_summary(raw_result),
            )

            if semantic_contract is not None:
                verification = verify_candidate(
                    contract=semantic_contract,
                    cypher=current_cypher,
                    rows=raw_result,
                    graph=graph if planning_mode == "active" else None,
                    row_cap=_execution_row_cap(),
                )
                grounding_debug["candidate_verification"] = (
                    verification.model_dump(mode="json")
                )
                trace_event(
                    logger,
                    trace_id,
                    "CHAIN-09V",
                    "Semantic contract and metamorphic verification completed",
                    grounding_debug["candidate_verification"],
                )
                if (
                    planning_mode == "active"
                    and verification.contradictions
                    and attempt < max_cypher_retries
                ):
                    last_error = verification_feedback(verification)
                    continue

            quality_feedback = _result_quality_feedback(
                raw_result,
                retry_empty=execute is True,
            )
            if quality_feedback and attempt < max_cypher_retries:
                trace_event(
                    logger,
                    trace_id,
                    "CHAIN-10",
                    "Neo4j returned low-quality projected rows; retry with feedback",
                    {
                        "feedback": quality_feedback,
                        "sample": _result_summary(raw_result),
                    },
                )
                last_error = quality_feedback
                continue

            return {
                "query": current_cypher,
                "result": raw_result,
                "trace_id": trace_id,
                "intermediate_steps": [
                    {
                        "query": current_cypher,
                        "grounding": grounding_debug,
                        "trace_id": trace_id,
                    }
                ],
            }
        except Exception as exc:
            error_msg = str(exc)
            logger.warning("[Cypher] Validation/execution error: %s", error_msg)
            trace_event(
                logger,
                trace_id,
                "CHAIN-08",
                f"Neo4j validation/execution failed on attempt {attempt + 1}",
                {
                    "error": error_msg,
                    "will_retry": attempt < max_cypher_retries,
                },
            )
            if attempt < max_cypher_retries:
                last_error = f"Neo4j validation/execution error: {error_msg}"
                continue
            return f"CYPHER_ERROR: {error_msg}"

    return {
        "query": current_cypher,
        "result": [],
        "trace_id": trace_id,
        "intermediate_steps": [
            {
                "query": current_cypher,
                "grounding": grounding_debug,
                "trace_id": trace_id,
            }
        ],
    }
