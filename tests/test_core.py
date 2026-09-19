import pytest
from pydantic import ValidationError

from app.core import Settings, ensure_json


def test_default_offline(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.llm_provider == "mock"
    assert settings.db_url.startswith("sqlite:///")


def test_public_requires_token():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="public", api_token="short")


def test_no_secret_repr():
    settings = Settings(_env_file=None, llm_api_key="DO_NOT_EXPOSE")
    assert "DO_NOT_EXPOSE" not in repr(settings)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_json_rejects_nonfinite(value):
    with pytest.raises(ValueError):
        ensure_json({"value": value})
