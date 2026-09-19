"""Unit/integration tests must never make real network connections."""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network access is forbidden in tests; use an injected mock transport")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
