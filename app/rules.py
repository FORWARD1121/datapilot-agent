"""A configuration-driven rule engine with explicit missing-evidence handling."""

import json
import operator
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import Field, ValidationError

from app.core import AppError
from app.schemas import Contract, ToolArgs
from app.tools import groups, growth_rate, metric_value


OPERATORS = {"<": operator.lt, "<=": operator.le, ">": operator.gt,
             ">=": operator.ge, "==": operator.eq, "!=": operator.ne}


class Condition(Contract):
    metric: str = Field(min_length=1, max_length=100)
    operator: Literal["<", "<=", ">", ">=", "==", "!="]
    threshold: float


class Rule(Condition):
    rule_id: str = Field(min_length=1, max_length=60)
    severity: Literal["info", "warning", "critical"] = "warning"
    message: str = Field(min_length=1, max_length=400)
    all_of: list[Condition] = Field(default_factory=list, max_length=5)


class RuleConfig(Contract):
    rules: list[Rule] = Field(max_length=50)


class RuleEngine:
    def __init__(self, path: Path | None = None):
        path = path or Path(__file__).parent / "configs" / "rules.json"
        try:
            if path.stat().st_size > 65536:
                raise ValueError("Oversized rule configuration")
            self.rules = RuleConfig.model_validate(json.loads(path.read_text(encoding="utf-8"))).rules
            if len({r.rule_id for r in self.rules}) != len(self.rules):
                raise ValueError("Duplicate rule identifiers")
        except (OSError, ValueError, ValidationError):
            raise AppError("invalid_rules", "Rule configuration is invalid", 503) from None

    def evaluate(self, facts: dict[str, float | None]) -> list[dict]:
        records = []
        for rule in self.rules:
            conditions = [Condition(metric=rule.metric, operator=rule.operator, threshold=rule.threshold), *rule.all_of]
            missing = [condition.metric for condition in conditions if facts.get(condition.metric) is None]
            triggered = not missing and all(OPERATORS[c.operator](facts[c.metric], c.threshold) for c in conditions)
            records.append({"rule_id": rule.rule_id, "severity": rule.severity, "message": rule.message,
                            "status": "skipped" if missing else "triggered" if triggered else "not_triggered",
                            "triggered": triggered, "missing_metrics": missing,
                            "conditions": [{**c.model_dump(), "value": facts.get(c.metric)} for c in conditions]})
        return records

    def run(self, frame: pd.DataFrame, args: ToolArgs) -> tuple[list[dict], dict]:
        records = []
        numeric = list(frame.select_dtypes(include="number").columns)
        latest = frame[args.scope.date_column].max().to_period("M") if args.scope.date_column else None
        for dimension, group in groups(frame, args.dimensions):
            facts = {f"{name}_sum": metric_value(group, name, "sum")[0] for name in numeric}
            if {"profit", "sales_amount"}.issubset(numeric):
                facts["profit_margin"] = metric_value(group, "profit_margin", "sum")[0]
            if latest is not None:
                periods = group[args.scope.date_column].dt.to_period("M")
                current, previous = group.loc[periods == latest], group.loc[periods == latest - 1]
                for name in numeric:
                    now = metric_value(current, name, "sum")[0]
                    before = metric_value(previous, name, "sum")[0]
                    facts[f"{name}_growth"] = growth_rate(now, before)[0]
            for result in self.evaluate(facts):
                records.append({**result, "dimension": dimension,
                                "growth_period": str(latest) if latest is not None else None,
                                "margin_scope": "all selected records"})
                if len(records) > 2000:
                    raise AppError("result_too_large", "Narrow the dimensions for business rules")
        return records, {"triggered": sum(r["triggered"] for r in records),
                         "skipped": sum(r["status"] == "skipped" for r in records),
                         "rules_configured": len(self.rules)}
