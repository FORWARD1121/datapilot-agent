"""Regressions found in the final independent logic and input-boundary audit."""

import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent import Agent
from app.core import Settings
from app.ingestion import normalize
from app.llm import MockProvider, strict_json
from app.main import create_app
from app.middleware import RequestGuard
from app.reports import Commentary, evidence_from
from app.schemas import ToolResult
from app.tools import ToolRegistry, growth_rate, metric_value


def test_integer_sum_does_not_wrap_int64():
    frame = pd.DataFrame({"value": [2**63 - 1, 2**63 - 1]})
    value, reason = metric_value(frame, "value", "sum")
    assert value == 2**64 - 2
    assert reason is None


def test_large_integer_margin_uses_safe_sums():
    frame = pd.DataFrame({"sales_amount": [2**63 - 1] * 2, "profit": [100, 100]})
    value, reason = metric_value(frame, "profit_margin", "sum")
    assert value == pytest.approx(200 / (2**64 - 2))
    assert reason is None


def test_float_overflow_has_an_explicit_reason():
    frame = pd.DataFrame({"value": [1e308, 1e308]})
    value, reason = metric_value(frame, "value", "sum")
    assert value is None
    assert reason == "numeric_overflow"


def test_mean_does_not_overflow_when_mean_is_finite():
    frame = pd.DataFrame({"value": [1e308, 1e308]})
    value, reason = metric_value(frame, "value", "mean")
    assert value == pytest.approx(1e308)
    assert reason is None


def test_growth_overflow_has_reason():
    value, reason = growth_rate(1e308, 1e-308)
    assert value is None
    assert reason == "numeric_overflow"


@pytest.mark.parametrize("dates", [[20260101, 20260201], [20260101.0, 20260201.0]])
def test_compact_numeric_dates_are_calendar_dates(dates):
    parsed = normalize(pd.DataFrame({"date": dates, "value": [1, 2]}))
    assert parsed.frame.date.dt.year.tolist() == [2026, 2026]
    assert parsed.frame.date.dt.month.tolist() == [1, 2]


def test_numeric_date_without_unit_is_not_epoch_nanoseconds():
    parsed = normalize(pd.DataFrame({"date": [45000, 45001], "value": [1, 2]}))
    assert not pd.api.types.is_datetime64_any_dtype(parsed.frame.date)
    assert parsed.frame.date.tolist() == [45000, 45001]


def test_summary_keeps_records_with_missing_dates():
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-01-01", None]), "sales_amount": [10, 99]})
    state = Agent(ToolRegistry(), MockProvider()).run(frame, "summary")
    assert state.results[0].statistics["rows"] == 2


@pytest.mark.parametrize("query", ["", " ", "\t\n"])
def test_coze_blank_query_is_422_not_500(tmp_path, query):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path)), raise_server_exceptions=False) as client:
        response = client.post("/integrations/coze/analyze", json={
            "dataset_id": "00000000-0000-0000-0000-000000000000", "query": query})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"


def test_bounded_evidence_keeps_largest_decline():
    records = [{"metric": "sales_amount", "value": 100, "dimension": {"region": str(i)},
                "period": "2026-09", "previous_period": "2026-08",
                "growth_rate": -0.9 if i == 25 else 0.1} for i in range(50)]
    result = ToolResult(tool="growth_analysis", rows_considered=100, records=records)
    evidence = evidence_from([result])
    assert len(evidence) == 30
    assert any(e.content["dimension"]["region"] == "25" for e in evidence)


def test_bounded_anomaly_evidence_keeps_largest_deviation():
    records = [{"metric": "sales_amount", "value": i, "dimension": {},
                "deviation": 9999 if i == 25 else 1} for i in range(50)]
    result = ToolResult(tool="anomaly_detection", rows_considered=100, records=records)
    assert any(e.content["deviation"] == 9999 for e in evidence_from([result]))


def test_nested_model_json_is_a_validation_error():
    with pytest.raises(ValueError):
        strict_json("[" * 1500 + "0" + "]" * 1500)


def test_unicode_digits_not_allowed_in_model_commentary():
    with pytest.raises(ValidationError):
        Commentary(text="销售将增长９０个百分点", evidence_ids=["e0"])


def test_long_content_length_is_a_client_error():
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        raise AssertionError("Invalid header reached the application")

    guard = RequestGuard(downstream, Settings(_env_file=None))
    asyncio.run(guard({"type": "http", "path": "/analysis", "method": "POST",
                       "headers": [(b"content-length", b"9" * 5000)]}, receive, send))
    assert sent[0]["status"] == 400


def test_offline_demo_does_not_write_configured_database(tmp_path, monkeypatch, capsys):
    from scripts.demo import main

    external_database = tmp_path / "must-not-touch.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{external_database}")
    monkeypatch.setattr("sys.argv", ["demo.py"])
    assert main() == 0
    assert not external_database.exists()
    assert '"mode": "mock"' in capsys.readouterr().out


def test_coze_documented_query_contract_matches_runtime():
    from app.coze import CozeRequest

    document = json.loads((Path(__file__).resolve().parents[1] / "docs/coze/openapi.json").read_text())
    documented = document["paths"]["/integrations/coze/analyze"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["query"]
    runtime = CozeRequest.model_json_schema()["properties"]["query"]
    for key in ("type", "minLength", "maxLength", "pattern"):
        assert documented[key] == runtime[key]
