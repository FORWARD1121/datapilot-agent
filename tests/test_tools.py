import pandas as pd
import pytest
from pydantic import ValidationError

from app.core import AppError
from app.profiling import profile
from app.schemas import Plan, Scope, ToolArgs
from app.tools import ToolRegistry, growth_rate


@pytest.fixture
def frame():
    return pd.DataFrame({"date": pd.to_datetime(["2025-01-01", "2025-02-01", "2026-01-01", "2026-02-01"]),
        "region": ["N", "N", "N", "N"], "sales_amount": [100., 120., 200., 180.],
        "profit": [20., 18., 30., 9.]})


def test_profile(frame):
    result = profile(frame)
    assert result["rows"] == 4
    assert result["numeric_statistics"]["sales_amount"]["max"] == 200
    assert result["date_columns"] == ["date"]


@pytest.mark.parametrize("aggregation,expected", [("sum", 600), ("mean", 150), ("count", 4), ("min", 100), ("max", 200)])
def test_aggregations(frame, aggregation, expected):
    result = ToolRegistry().run("group_aggregate", {"metrics": ["sales_amount"], "aggregation": aggregation}, frame)
    assert result.records[0]["value"] == expected


def test_weighted_margin(frame):
    result = ToolRegistry().run("group_aggregate", {"metrics": ["profit_margin"]}, frame)
    assert result.records[0]["value"] == pytest.approx(77 / 600)


@pytest.mark.parametrize("current,previous,expected", [(10, 0, None), (10, -5, None), (10, None, None), (None, 10, None), (15, 10, .5), (5, 10, -.5)])
def test_growth_edges(current, previous, expected):
    value, reason = growth_rate(current, previous)
    assert value == expected
    assert bool(reason) == (expected is None)


def test_growth_calendar_not_adjacent_rows(frame):
    result = ToolRegistry().run("growth_analysis", {"metrics": ["sales_amount"], "scope": {"date_column": "date"}}, frame)
    assert result.records[1]["growth_rate"] == .2
    assert result.records[2]["growth_rate"] is None
    assert result.records[3]["growth_rate"] == -.1


def test_yoy(frame):
    result = ToolRegistry().run("growth_analysis", {"metrics": ["sales_amount"], "comparison": "year_over_year", "scope": {"date_column": "date"}}, frame)
    assert result.records[2]["growth_rate"] == 1.0
    assert result.records[3]["growth_rate"] == .5


def test_ranking_bottom_k(frame):
    frame["region"] = ["N", "S", "N", "S"]
    result = ToolRegistry().run("ranking_analysis", {"metrics": ["profit"], "dimensions": ["region"], "top_k": 1, "ascending": True}, frame)
    assert result.records[0]["dimension"] == {"region": "S"}
    assert result.records[0]["value"] == 27


def test_anomaly():
    frame = pd.DataFrame({"value": [10] * 9 + [1000], "group": ["A"] * 10})
    result = ToolRegistry().run("anomaly_detection", {"metrics": ["value"]}, frame)
    assert result.statistics["outlier_count"] == 1
    assert result.records[0]["value"] == 1000


def test_correlation(frame):
    frame["double"] = frame.sales_amount * 2
    result = ToolRegistry().run("correlation_analysis", {"metrics": ["sales_amount", "double"]}, frame)
    assert result.records[0]["correlation"] == pytest.approx(1.0)
    assert result.records[0]["n"] == 4


def test_constant_correlation(frame):
    frame["constant"] = 1
    result = ToolRegistry().run("correlation_analysis", {"metrics": ["sales_amount", "constant"]}, frame)
    assert result.records[0]["correlation"] is None


def test_recent_months_anchored_to_data(frame):
    result = ToolRegistry().run("trend_analysis", {"metrics": ["sales_amount"], "scope": {"date_column": "date", "last_n_months": 3}}, frame)
    assert result.rows_considered == 2


@pytest.mark.parametrize("tool,args", [
    ("execute_python", {}), ("group_aggregate", {"metrics": ["absent"]}),
    ("trend_analysis", {"metrics": ["profit"]}),
    ("ranking_analysis", {"metrics": ["profit"]}),
    ("group_aggregate", {"metrics": ["region"]}),
    ("dataset_summary", {"scope": {"filters": {"region": "ABSENT"}}}),
])
def test_invalid_tools(frame, tool, args):
    with pytest.raises(AppError):
        ToolRegistry().run(tool, args, frame)


def test_schema_rejects_unregistered_tool():
    with pytest.raises(ValidationError):
        Plan.model_validate({"steps": [{"tool": "shell", "arguments": {}}]})


def test_scope_validation():
    with pytest.raises(ValidationError):
        Scope(last_n_months=3)
    with pytest.raises(ValidationError):
        ToolArgs(metrics=["profit", "profit"])


def test_missing_values_not_zero(frame):
    frame["profit"] = float("nan")
    result = ToolRegistry().run("group_aggregate", {"metrics": ["profit"]}, frame)
    assert result.records[0]["value"] is None
    assert result.records[0]["reason"] == "all_values_missing"
