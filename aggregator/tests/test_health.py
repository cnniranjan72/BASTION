from bastion_aggregator.main import app
from fastapi.testclient import TestClient


def test_healthz_returns_ok():
    client = TestClient(app)
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "aggregator"}


def test_response_includes_request_id_header():
    client = TestClient(app)
    response = client.get("/healthz")

    assert "X-Request-Id" in response.headers
