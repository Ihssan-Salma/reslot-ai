from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.main import create_app

client = TestClient(create_app())


def test_liveness() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_failure_hides_connection_details() -> None:
    with patch("app.api.routes.health.get_engine") as engine:
        engine.return_value.connect.side_effect = OperationalError("private", {}, Exception())
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_unknown_route() -> None:
    assert client.get("/api/v1/reservations").status_code == 404


def test_readiness_success() -> None:
    with patch("app.api.routes.health.get_engine") as engine:
        response = client.get("/health/ready")
        engine.return_value.connect.return_value.__enter__.return_value.execute.assert_called_once()
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
