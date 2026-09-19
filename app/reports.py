"""Numeric facts are rendered by Python; models may only select evidence IDs."""

import json
import re

from pydantic import Field, field_validator

from app.schemas import Contract, ToolResult


class Evidence(Contract):
    id: str
    tool: str
    path: str
    content: dict
    text: str


class Commentary(Contract):
    text: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=6)

    @field_validator("text")
    @classmethod
    def no_model_numbers(cls, value: str) -> str:
        if re.search(r"[\d%％]", value):
            raise ValueError("Put numeric facts in evidence, not model-generated commentary")
        return value


class InsightDraft(Contract):
    summary_evidence_ids: list[str] = Field(default_factory=list, max_length=3)
    finding_evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    possible_causes: list[Commentary] = Field(default_factory=list, max_length=5)
    recommendations: list[Commentary] = Field(default_factory=list, max_length=5)

    def validate_evidence(self, evidence: list[Evidence]) -> None:
        allowed = {item.id for item in evidence}
        cited = self.summary_evidence_ids + self.finding_evidence_ids
        cited += [reference for item in self.possible_causes + self.recommendations for reference in item.evidence_ids]
        if any(reference not in allowed for reference in cited):
            raise ValueError("An insight cited nonexistent evidence")


class Report(Contract):
    summary: str
    key_findings: list[Evidence]
    anomalies: list[dict]
    possible_causes: list[dict]
    recommendations: list[Commentary]
    limitations: list[str]
    provenance: dict


def evidence_from(results: list[ToolResult]) -> list[Evidence]:
    evidence = []
    for tool_index, result in enumerate(results):
        records = result.records
        if result.tool == "dataset_summary":
            content = {k: result.statistics[k] for k in ("rows", "columns", "duplicates")}
            evidence.append(Evidence(id=f"e{tool_index}s", tool=result.tool,
                path=f"/tool_results/{tool_index}/statistics", content=content,
                text=f"Dataset: {content['rows']} rows, {content['columns']} columns, {content['duplicates']} duplicate rows."))
        indices = list(range(len(records)))
        if result.tool == "business_rule_check":
            indices = [i for i in indices if records[i]["triggered"]]
        # Bound evidence independently of the stored complete tool output.
        if len(indices) > 30:
            if result.tool == "growth_analysis":
                indices.sort(key=lambda i: records[i].get("growth_rate") if records[i].get("growth_rate") is not None else float("inf"))
                indices = indices[:15] + indices[-15:]
            elif result.tool == "anomaly_detection":
                indices.sort(key=lambda i: abs(records[i].get("deviation") or 0), reverse=True)
                indices = indices[:30]
            elif result.tool == "business_rule_check":
                priorities = {"critical": 0, "warning": 1, "info": 2}
                indices.sort(key=lambda i: priorities.get(records[i].get("severity"), 3))
                indices = indices[:30]
            elif result.tool == "ranking_analysis":
                indices = indices[:30]
            else:
                indices = indices[:15] + indices[-15:]
        for index in indices:
            record = records[index]
            if "metric" in record and "value" in record:
                location = ", ".join(f"{k}={v}" for k, v in record.get("dimension", {}).items()) or "all records"
                value = record["value"]
                text = f"{record['metric']} | {location} | {record.get('period') or 'selected scope'}: {value}"
                if record.get("growth_rate") is not None:
                    text += f"; growth vs {record['previous_period']}: {record['growth_rate']:.2%}"
                if record.get("reason"):
                    text += f" ({record['reason']})"
            else:
                text = f"{result.tool}: " + json.dumps(record, ensure_ascii=False, allow_nan=False)
            evidence.append(Evidence(id=f"e{tool_index}r{index}", tool=result.tool,
                path=f"/tool_results/{tool_index}/records/{index}", content=record, text=text))
    return evidence


def render_report(draft: InsightDraft, evidence: list[Evidence], results: list[ToolResult], provenance: dict) -> Report:
    draft.validate_evidence(evidence)
    lookup = {item.id: item for item in evidence}
    summary = " ".join(lookup[x].text for x in dict.fromkeys(draft.summary_evidence_ids))
    limitations = list(dict.fromkeys(w for result in results for w in result.warnings))
    limitations.extend([
        "Reports describe supplied observations, not causal proof or forecasts.",
        "LLM commentary is unverified; numeric facts come from deterministic tool output.",
        "Displayed evidence is bounded; inspect persisted tool_results for the complete analysis.",
        "Floating-point analytics are not a replacement for audited financial accounting.",
    ])
    if provenance.get("fallback_reasons"):
        limitations.append("Some model stages failed; deterministic fallbacks are disclosed in provenance.")
    return Report(summary=summary or "Analysis completed; see the structured tool results.",
        key_findings=[lookup[x] for x in dict.fromkeys(draft.finding_evidence_ids)],
        anomalies=[r for result in results if result.tool == "anomaly_detection" for r in result.records][:30],
        possible_causes=[{"kind": "unverified_hypothesis", **item.model_dump()} for item in draft.possible_causes],
        recommendations=draft.recommendations, limitations=limitations, provenance=provenance)
