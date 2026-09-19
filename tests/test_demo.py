from pathlib import Path

from app.core import Settings
from scripts.demo import run_demo
from scripts.generate_samples import sample_rows, write_samples


def test_sample_reproducible(tmp_path):
    generated = tmp_path / "sample.csv"
    write_samples(generated)
    source = Path(__file__).resolve().parents[1] / "data/samples/sample_sales.csv"
    assert generated.read_bytes() == source.read_bytes()
    assert len(sample_rows()) == 107


def test_demo_executes_real_tools(tmp_path):
    result = run_demo(Settings(_env_file=None, data_dir=tmp_path))
    assert result["dataset"]["profile"]["rows"] == 107
    assert result["dataset"]["profile"]["duplicates"] == 1
    assert all(item["status"] == "completed" for item in result["analyses"])
    tools = {tool["tool"] for item in result["analyses"] for tool in item["tool_results"]}
    assert {"trend_analysis", "ranking_analysis", "growth_analysis", "anomaly_detection", "business_rule_check"}.issubset(tools)
    anomaly = result["analyses"][2]["tool_results"][0]
    assert anomaly["statistics"]["outlier_count"] >= 1
