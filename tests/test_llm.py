import json

import httpx
import pytest
from pydantic import ValidationError

from app.core import AppError, Settings
from app.llm import MockProvider, OpenAICompatibleProvider, strict_json
from app.reports import Commentary, Evidence, InsightDraft
from app.schemas import Intent
from app.tools import ToolRegistry

CONTEXT = {"numeric_columns": ["sales_amount", "profit"], "categorical_columns": ["region", "product"], "date_columns": ["date"]}


@pytest.mark.parametrize("query,kind", [
    ("分析最近三个月销售额变化情况，并找出下降最明显的区域", "trend"),
    ("哪些产品利润率异常", "anomaly"), ("比较不同区域销售表现", "comparison"),
    ("找出销售增长但是利润下降的业务区域", "business_diagnosis"),
    ("sales growth by region", "growth"), ("top 3 products by sales", "ranking"),
    ("correlation of sales and profit", "correlation"), ("summary", "summary"),
])
def test_mock_intents(query, kind):
    assert MockProvider().intent(query, CONTEXT).analysis_type == kind


def test_mock_time_range():
    intent = MockProvider().intent("最近三个月销售额变化", CONTEXT)
    assert intent.scope.last_n_months == 3


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"v":NaN}', '```json\n{}\n```'])
def test_strict_json(text):
    with pytest.raises(ValueError):
        strict_json(text)


def configured(handler):
    settings = Settings(_env_file=None, llm_model="test-model", llm_api_key="unit-test-only", llm_base_url="https://example.invalid/v1")
    return OpenAICompatibleProvider(settings, httpx.MockTransport(handler))


def message(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_structured_repair():
    count = []
    def handler(request):
        count.append(1)
        return message("broken" if len(count) == 1 else '{"analysis_type":"summary"}')
    result = configured(handler).intent("summary", CONTEXT)
    assert result.analysis_type == "summary"
    assert len(count) == 2


def test_invalid_output_bounded():
    count = []
    def handler(request):
        count.append(1)
        return message("broken")
    with pytest.raises(AppError) as exc:
        configured(handler).intent("summary", CONTEXT)
    assert exc.value.code == "llm_invalid_output"
    assert len(count) == 2


def test_real_function_call_wire_contract():
    def handler(request):
        body = json.loads(request.content)
        assert body["tool_choice"] == "required"
        assert len(body["tools"]) == 8
        assert body["model"] == "test-model"
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
            {"id": "call_test", "type": "function", "function": {"name": "dataset_summary", "arguments": "{}"}}
        ]}}]})
    plan = configured(handler).plan(Intent(), ToolRegistry())
    assert plan.steps[0].tool == "dataset_summary"


def test_missing_key_is_clear():
    provider = OpenAICompatibleProvider(Settings(_env_file=None))
    with pytest.raises(AppError) as exc:
        provider.intent("summary", CONTEXT)
    assert exc.value.code == "llm_not_configured"


def test_untrusted_evidence_id():
    evidence = [Evidence(id="e0", tool="dataset_summary", path="/x", content={}, text="ok")]
    with pytest.raises(ValueError):
        InsightDraft(summary_evidence_ids=["fake"]).validate_evidence(evidence)


def test_hallucinated_numeric_commentary_rejected():
    with pytest.raises(ValidationError):
        Commentary(text="Revenue will grow 90%", evidence_ids=["e0"])


def test_safe_upstream_error():
    provider = configured(lambda request: httpx.Response(401, text="secret diagnostic"))
    with pytest.raises(AppError) as exc:
        provider.intent("summary", CONTEXT)
    assert "secret diagnostic" not in exc.value.message
