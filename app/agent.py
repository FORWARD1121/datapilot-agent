"""Bounded workflow with explicit state and independently validated tool execution."""

import json
from dataclasses import dataclass, field
from time import perf_counter

import pandas as pd
from pydantic import ValidationError

from app.core import AppError, ensure_json
from app.llm import MockProvider, Provider, default_plan
from app.profiling import profile
from app.reports import Report, evidence_from, render_report
from app.schemas import Intent, Plan, ToolArgs, ToolResult
from app.tools import ToolRegistry


@dataclass
class AgentState:
    query: str
    intent: Intent | None = None
    plan: Plan | None = None
    results: list[ToolResult] = field(default_factory=list)
    report: Report | None = None
    trace: list[dict] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)

    def record(self, stage: str, source: str, **details) -> None:
        self.trace.append({"stage": stage, "source": source, **details})


def validate_plan(plan: Plan, intent: Intent, registry: ToolRegistry, frame: pd.DataFrame) -> None:
    plan = Plan.model_validate(plan.model_dump())
    expected = ToolArgs.model_validate(intent.model_dump(exclude={"analysis_type"}))
    allowed = {step.tool for step in default_plan(intent).steps} | {"dataset_summary"}
    required = {"summary": "dataset_summary", "trend": "trend_analysis", "comparison": "group_aggregate",
                "ranking": "ranking_analysis", "anomaly": "anomaly_detection", "growth": "growth_analysis",
                "correlation": "correlation_analysis", "business_diagnosis": "business_rule_check"}[intent.analysis_type]
    if required not in {step.tool for step in plan.steps}:
        raise AppError("plan_missing_task", "Plan does not execute the requested analysis")
    signatures = set()
    for step in plan.steps:
        if step.tool not in allowed or step.arguments != expected:
            raise AppError("plan_changed_intent", "Plan may not alter the validated intent or its scope")
        signature = step.model_dump_json()
        if signature in signatures:
            raise AppError("duplicate_tool_call", "Duplicate tool calls are not permitted")
        signatures.add(signature)
        registry.validate(step.tool, step.arguments, frame)


class Agent:
    def __init__(self, registry: ToolRegistry, provider: Provider):
        self.registry, self.provider = registry, provider

    def run(self, frame: pd.DataFrame, query: str, override: Intent | None = None) -> AgentState:
        state = AgentState(query=query, provenance={"configured_provider": self.provider.name, "fallback_reasons": []})
        fallback = MockProvider()
        active = self.provider
        dataset_profile = profile(frame)
        context = {key: dataset_profile[key] for key in ("numeric_columns", "categorical_columns", "date_columns")}
        if override is not None:
            state.intent = Intent.model_validate(override.model_dump())
            intent_source = "explicit_request"
        else:
            try:
                state.intent = active.intent(query, context)
                validate_plan(default_plan(state.intent), state.intent, self.registry, frame)
                intent_source = active.name
            except (AppError, ValidationError) as exc:
                if active.name == "mock":
                    raise
                state.provenance["fallback_reasons"].append(getattr(exc, "code", "invalid_model_intent"))
                state.intent = fallback.intent(query, context)
                active, intent_source = fallback, "mock_fallback"
        state.provenance["intent_source"] = intent_source
        state.record("intent", intent_source, analysis_type=state.intent.analysis_type)
        # Even explicit intents are validated before any tool is invoked.
        validate_plan(default_plan(state.intent), state.intent, self.registry, frame)
        try:
            state.plan = active.plan(state.intent, self.registry)
            validate_plan(state.plan, state.intent, self.registry, frame)
            plan_source = active.name
        except (AppError, ValidationError) as exc:
            state.provenance["fallback_reasons"].append(getattr(exc, "code", "invalid_model_plan"))
            state.plan = default_plan(state.intent)
            validate_plan(state.plan, state.intent, self.registry, frame)
            plan_source = "deterministic_fallback"
        state.provenance["plan_source"] = plan_source
        state.record("plan", plan_source, steps=len(state.plan.steps))
        for index, step in enumerate(state.plan.steps):
            started = perf_counter()
            result = self.registry.run(step.tool, step.arguments, frame)
            state.results.append(result)
            state.record("tool", "python", index=index, tool=step.tool,
                         elapsed_ms=round((perf_counter() - started) * 1000, 3))
        ensure_json([result.model_dump(mode="json") for result in state.results])
        state.record("result_validation", "python", status="passed")
        evidence = evidence_from(state.results)
        # The complete results remain stored; only bounded evidence goes to the model.
        while evidence and len(json.dumps([e.model_dump() for e in evidence], ensure_ascii=False).encode()) > 60000:
            evidence.pop()
        try:
            draft = active.insights(query, evidence)
            draft.validate_evidence(evidence)
            insight_source = active.name
        except (AppError, ValueError) as exc:
            state.provenance["fallback_reasons"].append(getattr(exc, "code", "invalid_model_insight"))
            draft = fallback.insights(query, evidence)
            insight_source = "mock_fallback"
        state.provenance["insight_source"] = insight_source
        state.record("insights", insight_source, evidence_count=len(evidence))
        state.report = render_report(draft, evidence, state.results, state.provenance)
        state.record("report", "deterministic_renderer")
        return state
