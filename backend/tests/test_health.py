import pytest
from fastapi.testclient import TestClient

from jyj.main import create_app


@pytest.mark.parametrize("path", ["/healthz", "/api/healthz"])
def test_healthz_returns_ok(path: str) -> None:
    client = TestClient(create_app())

    response = client.get(path)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
