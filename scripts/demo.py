"""Run a complete offline portfolio demo without a server or paid API key."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core import AppError, Settings
from app.schemas import AnalysisRequest
from app.service import DataPilot


SAMPLE = Path(__file__).resolve().parents[1] / "data" / "samples" / "sample_sales.csv"
QUERIES = ["分析最近三个月销售额变化情况，并找出下降最明显的区域",
           "top 3 products by sales", "sales anomaly by region",
           "找出销售增长但是利润下降的业务区域"]


def run_demo(settings: Settings, dataset_path: Path = SAMPLE) -> dict:
    service = DataPilot(settings)
    try:
        dataset = service.upload(dataset_path.name, dataset_path.read_bytes())
        analyses = [service.analyze(AnalysisRequest(dataset_id=dataset["dataset_id"], query=query)) for query in QUERIES]
        return {"dataset": dataset, "analyses": analyses}
    finally:
        service.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="Retain the demo database and dataset here")
    parser.add_argument("--output", type=Path, help="Write the full JSON result to this file")
    parser.add_argument("--live", action="store_true", help="Use the explicitly configured LLM provider instead of mock")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="datapilot-demo-") as temporary:
        directory = args.data_dir or Path(temporary)
        settings = Settings(data_dir=directory) if args.live else Settings(_env_file=None, data_dir=directory, llm_provider="mock")
        if args.live and settings.llm_provider != "openai_compatible":
            parser.error("--live requires LLM_PROVIDER=openai_compatible and your API configuration")
        try:
            result = run_demo(settings)
        except AppError as exc:
            print(json.dumps({"error": {"code": exc.code, "message": exc.message}}, ensure_ascii=False))
            return 1
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        summary = {"mode": settings.llm_provider, "rows": result["dataset"]["profile"]["rows"],
                   "duplicates": result["dataset"]["profile"]["duplicates"],
                   "analyses": [{"query": item["query"], "status": item["status"],
                        "tools": [tool["tool"] for tool in item["tool_results"]],
                        "summary": item["report"]["summary"], "provenance": item["report"]["provenance"]}
                        for item in result["analyses"]]}
        print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
