import pandas as pd
import pytest

from app.agent import Agent
from app.core import AppError, Settings
from app.llm import MockProvider
from app.reports import InsightDraft
from app.rules import RuleEngine
from app.schemas import AnalysisRequest, Intent, Plan, Scope, Step, ToolArgs
from app.service import DataPilot
from app.tools import ToolRegistry


@pytest.fixture
def frame():
    return pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"]),
        "region": ["North"] * 4, "sales_amount": [100., 120., 140., 160.], "profit": [20., 18., 16., 10.]})


def test_full_agent_flow(frame):
    state = Agent(ToolRegistry(RuleEngine().run), MockProvider()).run(frame, "business diagnosis by region")
    assert len(state.results) == 5
    assert state.report.provenance["configured_provider"] == "mock"
    assert state.trace[0]["stage"] == "intent"
    assert state.trace[-1]["stage"] == "report"
    assert any(result.statistics.get("triggered", 0) > 0 for result in state.results)
    assert state.report.key_findings


def test_planner_cannot_widen_scope(frame):
    class MaliciousPlanner(MockProvider):
        name = "test-provider"
        def plan(self, intent, registry):
            return Plan(steps=[Step(tool="trend_analysis", arguments=ToolArgs(metrics=["sales_amount"], scope=Scope(date_column="date")))])
    intent = Intent(analysis_type="trend", metrics=["sales_amount"], scope=Scope(date_column="date", last_n_months=2))
    state = Agent(ToolRegistry(), MaliciousPlanner()).run(frame, "trend", intent)
    assert "plan_changed_intent" in state.provenance["fallback_reasons"]
    assert all(result.rows_considered == 2 for result in state.results)


def test_fabricated_evidence_falls_back(frame):
    class BadInsights(MockProvider):
        def insights(self, query, evidence):
            return InsightDraft(summary_evidence_ids=["fabricated"])
    state = Agent(ToolRegistry(), BadInsights()).run(frame, "summary")
    assert state.provenance["insight_source"] == "mock_fallback"
    assert "fabricated" not in state.report.summary


def test_provider_outage_keeps_deterministic_tools(frame):
    class Outage(MockProvider):
        name = "unavailable-provider"
        def intent(self, query, context):
            raise AppError("llm_unavailable", "safe", 503)
    state = Agent(ToolRegistry(), Outage()).run(frame, "summary")
    assert state.provenance["intent_source"] == "mock_fallback"
    assert state.results[0].statistics["rows"] == 4


def test_invalid_explicit_intent_fails_before_tools(frame):
    with pytest.raises(AppError):
        Agent(ToolRegistry(), MockProvider()).run(frame, "summary", Intent(metrics=["made_up"]))


def test_service_roundtrip(tmp_path):
    service = DataPilot(Settings(_env_file=None, data_dir=tmp_path))
    dataset = service.upload("s.csv", b"region,sales_amount\nN,100\nS,120")
    result = service.analyze(AnalysisRequest(dataset_id=dataset["dataset_id"], query="compare sales by region"))
    assert result["status"] == "completed"
    assert service.store.task(result["analysis_id"])["report"] == result["report"]
    service.close()


def test_failed_task_persisted(tmp_path):
    service = DataPilot(Settings(_env_file=None, data_dir=tmp_path))
    dataset = service.upload("s.csv", b"region,value\nN,1")
    with pytest.raises(AppError) as exc:
        service.analyze(AnalysisRequest(dataset_id=dataset["dataset_id"], query="unsupported request"))
    task = service.store.task(exc.value.analysis_id)
    assert task["status"] == "failed"
    assert task["error_code"] == "intent_unrecognized"
    service.close()
