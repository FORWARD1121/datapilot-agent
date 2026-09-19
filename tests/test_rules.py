import json

import pandas as pd
import pytest

from app.core import AppError
from app.rules import RuleEngine
from app.tools import ToolRegistry


def test_threshold_and_missing_evidence():
    rules = RuleEngine()
    result = rules.evaluate({"profit_margin": .05})
    assert result[0]["triggered"] is True
    assert result[1]["status"] == "skipped"
    assert rules.evaluate({"profit_margin": .1})[0]["triggered"] is False


def test_composite_rule():
    result = RuleEngine().evaluate({"sales_amount_growth": .2, "profit_growth": -.2})
    assert result[2]["triggered"] is True
    result = RuleEngine().evaluate({"sales_amount_growth": .2, "profit_growth": .1})
    assert result[2]["triggered"] is False


@pytest.mark.parametrize("op,expected", [("<", False), ("<=", True), (">", False), (">=", True), ("==", True), ("!=", False)])
def test_configurable_operators(tmp_path, op, expected):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"rules": [{"rule_id": "TEST", "metric": "m", "operator": op, "threshold": 5, "message": "test"}]}))
    assert RuleEngine(path).evaluate({"m": 5})[0]["triggered"] == expected


def test_bad_operator(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text('{"rules": [{"rule_id":"x","metric":"m","operator":"eval","threshold":0,"message":"x"}]}')
    with pytest.raises(AppError):
        RuleEngine(path)


def test_rule_tool_integration():
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-02-01"]), "region": ["N", "N"],
                          "sales_amount": [100., 120.], "profit": [20., 10.]})
    registry = ToolRegistry(RuleEngine().run)
    result = registry.run("business_rule_check", {"dimensions": ["region"], "scope": {"date_column": "date"}}, frame)
    assert result.statistics["triggered"] == 1
    assert result.records[2]["rule_id"] == "GROWTH_PROFIT_DIVERGENCE"
    assert result.records[2]["triggered"] is True


def test_non_sales_dataset_skips_inapplicable_rules():
    frame = pd.DataFrame({"latency": [1., 2.]})
    result = ToolRegistry(RuleEngine().run).run("business_rule_check", {}, frame)
    assert result.statistics["skipped"] == 3
