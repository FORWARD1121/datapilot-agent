"""Offline mock and OpenAI-compatible HTTP providers with bounded repair."""

import json
import re
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from app.core import AppError, Settings
from app.reports import Commentary, Evidence, InsightDraft
from app.schemas import Intent, Plan, Scope, Step, ToolArgs
from app.tools import ToolRegistry

ROOT = Path(__file__).parent


def prompt(name: str) -> str:
    return (ROOT / "prompts" / f"{name}.txt").read_text(encoding="utf-8")


def strict_json(text: str):
    if len(text) > 65536:
        raise ValueError("Model output exceeds size limit")

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Nonfinite JSON constant")

    # Bound nesting independently of the interpreter's recursion limit.
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 64:
                raise ValueError("Model JSON exceeds nesting limit")
        elif char in "]}":
            depth -= 1
    return json.loads(text, object_pairs_hook=object_pairs, parse_constant=invalid_constant)


def default_plan(intent: Intent) -> Plan:
    args = ToolArgs.model_validate(intent.model_dump(exclude={"analysis_type"}))
    mapping = {"summary": ["dataset_summary"], "comparison": ["group_aggregate"],
               "ranking": ["ranking_analysis"], "anomaly": ["anomaly_detection"],
               "growth": ["growth_analysis"], "correlation": ["correlation_analysis"],
               "trend": ["trend_analysis", "growth_analysis"],
               "business_diagnosis": ["group_aggregate", "business_rule_check", "anomaly_detection"]}
    names = mapping[intent.analysis_type]
    if intent.analysis_type == "business_diagnosis" and args.scope.date_column:
        names = ["trend_analysis", "growth_analysis", *names]
    return Plan(steps=[Step(tool=name, arguments=args.model_copy(deep=True)) for name in names])


class Provider(Protocol):
    name: str

    def intent(self, query: str, context: dict) -> Intent: ...
    def plan(self, intent: Intent, registry: ToolRegistry) -> Plan: ...
    def insights(self, query: str, evidence: list[Evidence]) -> InsightDraft: ...


class MockProvider:
    name = "mock"

    def intent(self, query: str, context: dict) -> Intent:
        aliases = json.loads((ROOT / "configs" / "aliases.json").read_text(encoding="utf-8"))
        lowered = query.lower()
        analysis_type = next((kind for kind, words in aliases["analysis"].items() if any(word in lowered for word in words)), None)
        if analysis_type is None:
            raise AppError("intent_unrecognized", "Offline mode needs a supported analysis keyword or an explicit intent")
        numeric = context["numeric_columns"]
        metrics = [name for name, words in aliases["metrics"].items()
                   if any(word in lowered for word in words) and
                   (name in numeric or name == "profit_margin" and {"profit", "sales_amount"}.issubset(numeric))]
        metrics += [name for name in numeric if name.lower() in lowered and name not in metrics]
        if "profit_margin" in metrics and "profit" in metrics:
            metrics.remove("profit")
        if not metrics and numeric:
            metrics = ["sales_amount"] if "sales_amount" in numeric else numeric[:1]
        if analysis_type == "business_diagnosis" and {"profit", "sales_amount"}.issubset(numeric):
            metrics = ["sales_amount", "profit"]
        if analysis_type == "correlation" and len(metrics) < 2:
            metrics = numeric[:2]
        if analysis_type == "ranking":
            metrics = metrics[:1]
        dimensions = [name for name, words in aliases["dimensions"].items()
                      if name in context["categorical_columns"] and any(word in lowered for word in words)]
        dimensions += [name for name in context["categorical_columns"] if name.lower() in lowered and name not in dimensions]
        if not dimensions and analysis_type in {"ranking", "comparison"}:
            dimensions = context["categorical_columns"][:1]
        date_column = context["date_columns"][0] if context["date_columns"] else None
        months = re.search(r"(?:last|recent|最近|近)\s*(\d+|十二|一|两|二|三|六)\s*(?:months?|个?月)", lowered)
        translated = {"一": 1, "两": 2, "二": 2, "三": 3, "六": 6, "十二": 12}
        last_n = (translated.get(months[1]) or int(months[1])) if months else None
        top = re.search(r"(?:top|bottom|前)\s*(\d+)", lowered)
        if last_n and not date_column:
            raise AppError("missing_date", "Time filters require a parsed date column")
        if analysis_type not in {"trend", "growth", "business_diagnosis", "anomaly"} and not last_n:
            date_column = None
        scope = Scope(date_column=date_column, last_n_months=last_n)
        return Intent(analysis_type=analysis_type, metrics=metrics[:8], dimensions=dimensions[:2], scope=scope,
                      top_k=int(top[1]) if top else 5, ascending=any(x in lowered for x in ("bottom", "最低")),
                      comparison="year_over_year" if "同比" in lowered or "year-over-year" in lowered else "previous_period")

    def plan(self, intent: Intent, registry: ToolRegistry) -> Plan:
        return default_plan(intent)

    def insights(self, query: str, evidence: list[Evidence]) -> InsightDraft:
        # Prefer actual anomalies, triggered rules, and the largest observed declines.
        priority = sorted(evidence, key=lambda e: (
            0 if e.tool == "business_rule_check" else 1 if e.tool == "anomaly_detection" else 2,
            e.content.get("growth_rate") if e.content.get("growth_rate") is not None else 0))
        ids = [item.id for item in priority[:8]]
        recommendations = [Commentary(text="Review source records behind the cited evidence and verify business context before acting.", evidence_ids=ids[:3])] if ids else []
        return InsightDraft(summary_evidence_ids=ids[:3], finding_evidence_ids=ids,
                            recommendations=recommendations)


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings, self.transport = settings, transport

    def _request(self, payload: dict) -> dict:
        settings = self.settings
        if not settings.llm_api_key.get_secret_value() or not settings.llm_model.strip():
            raise AppError("llm_not_configured", "Set LLM_API_KEY and LLM_MODEL", 503)
        url = urlsplit(settings.llm_base_url)
        if url.username or url.password or url.query or url.fragment or not url.hostname:
            raise AppError("invalid_llm_url", "LLM_BASE_URL must be a credential-free API base URL", 503)
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise AppError("invalid_llm_url", "LLM endpoints require HTTPS except loopback development", 503)
        endpoint = settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}"}
        try:
            with httpx.Client(timeout=settings.llm_timeout_seconds, transport=self.transport,
                              follow_redirects=False, trust_env=False) as client:
                with client.stream("POST", endpoint, headers=headers,
                                   json={"model": settings.llm_model, "temperature": 0, "max_tokens": 2500, **payload}) as response:
                    if response.status_code != 200:
                        raise AppError("llm_unavailable", "Model endpoint returned an unsuccessful response", 503)
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 262144:
                            raise AppError("llm_response_too_large", "Model response exceeded the size limit", 503)
            return strict_json(body.decode("utf-8"))
        except (httpx.HTTPError, UnicodeError, ValueError):
            raise AppError("llm_unavailable", "Model endpoint returned an invalid response or timed out", 503) from None

    def _structured(self, name: str, context: dict, schema: type[BaseModel]):
        system = prompt(name)
        if name == "insight_generator":
            system += "\n" + prompt("report_generator")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(
            {"data": context, "output_schema": schema.model_json_schema()}, ensure_ascii=False, allow_nan=False)}]
        for attempt in range(2):
            response = self._request({"messages": messages, "response_format": {"type": "json_object"}})
            try:
                content = response["choices"][0]["message"]["content"]
                return schema.model_validate(strict_json(content))
            except (KeyError, IndexError, TypeError, ValueError):
                if attempt:
                    break
                messages.append({"role": "user", "content": "The response did not match the JSON schema. Return one valid JSON object only. Do not add fields."})
        raise AppError("llm_invalid_output", "Model output failed validation after one repair attempt", 503)

    def intent(self, query: str, context: dict) -> Intent:
        return self._structured("intent_parser", {"query": query, "dataset_schema": context}, Intent)

    def plan(self, intent: Intent, registry: ToolRegistry) -> Plan:
        messages = [{"role": "system", "content": prompt("analysis_planner")},
                    {"role": "user", "content": intent.model_dump_json()}]
        for attempt in range(2):
            response = self._request({"messages": messages, "tools": registry.definitions(), "tool_choice": "required"})
            try:
                calls = response["choices"][0]["message"]["tool_calls"]
                return Plan.model_validate({"steps": [{"tool": call["function"]["name"],
                    "arguments": strict_json(call["function"]["arguments"])} for call in calls]})
            except (KeyError, IndexError, TypeError, ValueError):
                if attempt:
                    break
                messages.append({"role": "user", "content": "Return between one and eight registered tool calls with valid arguments."})
        raise AppError("llm_invalid_plan", "Model returned an invalid tool plan", 503)

    def insights(self, query: str, evidence: list[Evidence]) -> InsightDraft:
        draft = self._structured("insight_generator", {"query": query, "evidence": [x.model_dump() for x in evidence]}, InsightDraft)
        try:
            draft.validate_evidence(evidence)
        except ValueError:
            raise AppError("unsupported_evidence", "Model cited unavailable evidence", 503) from None
        return draft


def make_provider(settings: Settings) -> Provider:
    return MockProvider() if settings.llm_provider == "mock" else OpenAICompatibleProvider(settings)
