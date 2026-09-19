from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as test_client:
        yield test_client


def uploaded(client):
    response = client.post("/datasets/upload", files={"file": ("s.csv", b"region,sales_amount\nN,100\nS,80", "text/csv")})
    assert response.status_code == 201
    return response.json()["dataset_id"]


def test_health_and_openapi(client):
    assert client.get("/health").json()["status"] == "ok"
    schema = client.get("/openapi.json").json()
    assert len(schema["paths"]) == 6
    assert "APIKeyHeader" in schema["components"]["securitySchemes"]


def test_complete_api_workflow(client):
    dataset_id = uploaded(client)
    assert client.get(f"/datasets/{dataset_id}/profile").json()["profile"]["rows"] == 2
    response = client.post("/analysis", json={"dataset_id": dataset_id, "query": "compare sales by region"})
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["status"] == "completed"
    assert client.get(f"/analysis/{task['analysis_id']}").json()["plan"] == task["plan"]
    assert client.get(f"/analysis/{task['analysis_id']}/report").json() == task["report"]
    assert response.headers["X-Request-ID"]


@pytest.mark.parametrize("path", ["/datasets/not-a-uuid/profile", "/analysis/not-a-uuid"])
def test_invalid_uuid(client, path):
    response = client.get(path)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_nonexistent(client):
    response = client.get(f"/analysis/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "analysis_not_found"


def test_unsafe_upload(client):
    response = client.post("/datasets/upload", files={"file": ("../s.csv", b"a,b\n1,2")})
    assert response.status_code == 422


def test_validation_does_not_echo_input(client):
    response = client.post("/analysis", json={"dataset_id": "SECRET_INVALID_ID", "query": "x"})
    assert "SECRET_INVALID_ID" not in response.text


def test_oversized_json_body(client):
    response = client.post("/analysis", content=b"x" * 65537, headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_public_authentication(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, app_env="public", api_token="t" * 32)
    with TestClient(create_app(settings)) as client:
        assert client.post("/analysis", json={}).status_code == 401
        assert client.post("/analysis", json={}, headers={"X-API-Key": "t" * 32}).status_code == 422
        assert client.get("/health").status_code == 200


def test_failed_task_has_retrievable_id(client):
    dataset_id = uploaded(client)
    response = client.post("/analysis", json={"dataset_id": dataset_id, "query": "unrecognized"})
    assert response.status_code == 422
    task_id = response.json()["error"]["analysis_id"]
    assert client.get(f"/analysis/{task_id}").json()["status"] == "failed"
    assert client.get(f"/analysis/{task_id}/report").status_code == 409


def test_explicit_intent(client):
    dataset_id = uploaded(client)
    response = client.post("/analysis", json={"dataset_id": dataset_id, "query": "custom query",
        "intent": {"analysis_type": "ranking", "metrics": ["sales_amount"], "dimensions": ["region"], "ascending": True}})
    assert response.status_code == 201
    assert response.json()["tool_results"][0]["records"][0]["dimension"]["region"] == "S"
