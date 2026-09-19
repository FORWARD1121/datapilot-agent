import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import Settings
from app.main import create_app


def test_coze_facade(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        dataset = client.post("/datasets/upload", files={"file": ("s.csv", b"region,sales_amount\nN,100\nS,80")}).json()
        response = client.post("/integrations/coze/analyze", json={"dataset_id": dataset["dataset_id"], "query": "compare sales by region"})
        assert response.status_code == 201
        result = response.json()
        report = client.get(f"/analysis/{result['analysis_id']}/report").json()
        assert json.loads(result["report_json"]) == report
        assert result["summary"] == report["summary"]


def test_coze_early_auth(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path, app_env="public", api_token="k" * 32))) as client:
        response = client.post("/integrations/coze/analyze", content=b"x" * 70000)
        assert response.status_code == 401


def test_minimal_schema_matches_facade():
    path = Path(__file__).resolve().parents[1] / "docs/coze/openapi.json"
    schema = json.loads(path.read_text())
    full = create_app().openapi()
    operation = schema["paths"]["/integrations/coze/analyze"]["post"]
    actual = full["paths"]["/integrations/coze/analyze"]["post"]
    assert operation["operationId"] == actual["operationId"]
    properties = operation["responses"]["201"]["content"]["application/json"]["schema"]["properties"]
    assert set(properties) == set(full["components"]["schemas"]["CozeResponse"]["properties"])
    assert schema["components"]["securitySchemes"]["DataPilotKey"]["name"] == "X-API-Key"
