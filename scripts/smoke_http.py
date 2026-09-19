"""Verify an already-running DataPilot HTTP server with fictional data only."""

import argparse
import json
import os
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    headers = {"X-API-Key": os.getenv("API_TOKEN", "")}
    sample = Path(__file__).resolve().parents[1] / "data" / "samples" / "sample_sales.csv"
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=180, trust_env=False) as client:
        health = client.get("/health")
        health.raise_for_status()
        upload = client.post("/datasets/upload", files={"file": (sample.name, sample.read_bytes(), "text/csv")})
        upload.raise_for_status()
        dataset_id = upload.json()["dataset_id"]
        profile = client.get(f"/datasets/{dataset_id}/profile")
        profile.raise_for_status()
        checks = []
        for query in ("sales trend by region", "top 3 products by sales", "sales anomaly by region"):
            result = client.post("/analysis", json={"dataset_id": dataset_id, "query": query})
            result.raise_for_status()
            task = result.json()
            assert task["status"] == "completed"
            report = client.get(f"/analysis/{task['analysis_id']}/report")
            report.raise_for_status()
            assert report.json() == task["report"]
            checks.append({"query": query, "status": result.status_code,
                           "tools": [tool["tool"] for tool in task["tool_results"]]})
        print(json.dumps({"health": health.json(), "rows": profile.json()["profile"]["rows"], "checks": checks}, indent=2))


if __name__ == "__main__":
    main()
