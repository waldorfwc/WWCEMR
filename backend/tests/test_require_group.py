"""Tests for require_group FastAPI dependency factory."""
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from app.routers.auth import get_current_user, require_group


def test_require_group_allows_matching_group():
    app = FastAPI()
    r = APIRouter()

    @r.get("/probe")
    def _probe(_: dict = Depends(require_group("admin", "billing"))):
        return {"ok": True}

    app.include_router(r)

    app.dependency_overrides[get_current_user] = lambda: {
        "email": "a@b.com", "group": "admin", "name": "A",
    }

    with TestClient(app) as client:
        assert client.get("/probe").status_code == 200


def test_require_group_blocks_wrong_group():
    app = FastAPI()
    r = APIRouter()

    @r.get("/probe")
    def _probe(_: dict = Depends(require_group("admin", "billing"))):
        return {"ok": True}

    app.include_router(r)

    app.dependency_overrides[get_current_user] = lambda: {
        "email": "c@b.com", "group": "clinical", "name": "C",
    }

    with TestClient(app) as client:
        r = client.get("/probe")
        assert r.status_code == 403
        assert "forbidden" in r.json()["detail"].lower()


def test_require_group_accepts_multiple_groups():
    app = FastAPI()
    r = APIRouter()

    @r.get("/probe")
    def _probe(_: dict = Depends(require_group("billing"))):
        return {"ok": True}

    app.include_router(r)

    app.dependency_overrides[get_current_user] = lambda: {
        "email": "b@b.com", "group": "billing", "name": "B",
    }

    with TestClient(app) as client:
        assert client.get("/probe").status_code == 200
