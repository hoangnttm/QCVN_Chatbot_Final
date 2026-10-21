"""Admin token gate (until M9), request-id middleware and CORS methods."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import get_sessionmaker


def _client(db_settings, monkeypatch, token: str | None) -> TestClient:
    from app.api.deps import get_db
    from app.main import app

    if token is None:
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    else:
        monkeypatch.setenv("ADMIN_TOKEN", token)
    monkeypatch.setenv("ALERTS_ENABLED", "false")
    get_settings.cache_clear()
    maker = get_sessionmaker(db_settings)

    def _db():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_admin_routes_require_token(db_settings, monkeypatch):
    from app.main import app

    try:
        c = _client(db_settings, monkeypatch, "secret")
        assert c.get("/api/v1/admin/ping").status_code == 401
        assert c.get("/api/v1/admin/ping", headers={"X-Admin-Token": "wrong"}).status_code == 401
        r = c.get("/api/v1/admin/ping", headers={"X-Admin-Token": "secret"})
        assert r.status_code == 200 and r.json()["ok"] is True
        # the two routes that used to be open are now behind the token
        assert c.get("/api/v1/admin/query-logs").status_code == 401
        assert c.post("/api/v1/admin/index-versions/x/activate").status_code == 401
        assert c.get("/api/v1/admin/metrics").status_code == 401
        # public routes stay open
        assert c.get("/api/v1/health").status_code == 200
        assert c.get("/api/v1/index-versions").status_code == 200
        assert c.get("/api/v1/me/quota").status_code == 200
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_admin_unconfigured_is_503(db_settings, monkeypatch):
    from app.main import app

    try:
        c = _client(db_settings, monkeypatch, None)
        if get_settings().admin_token:  # backend/.env sets one on this machine
            return
        assert c.get("/api/v1/admin/ping", headers={"X-Admin-Token": "x"}).status_code == 503
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_request_id_and_cors(db_settings, monkeypatch):
    from app.main import app

    try:
        c = _client(db_settings, monkeypatch, "secret")
        r = c.get("/api/v1/health")
        assert r.headers.get("x-request-id")
        r = c.get("/api/v1/health", headers={"X-Request-Id": "abc-123"})
        assert r.headers["x-request-id"] == "abc-123"
        r = c.options(
            "/api/v1/admin/ping",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "x-admin-token",
            },
        )
        assert r.status_code == 200
        assert "DELETE" in r.headers.get("access-control-allow-methods", "")
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
