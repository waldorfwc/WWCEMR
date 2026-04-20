"""Shared pytest fixtures: in-memory SQLite + FastAPI TestClient."""
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.database import Base, get_db
from app.main import app
from app.routers.auth import get_current_user


TEST_USER = {"email": "tester@waldorfwomenscare.com", "name": "Test User", "group": "admin"}


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass
    def override_get_current_user():
        return TEST_USER

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


CLINICAL_USER = {"email": "clinician@waldorfwomenscare.com", "name": "Clinician", "group": "clinical"}


@pytest.fixture
def clinical_client(db):
    """Same as `client` but the authenticated user has group=clinical."""
    def override_get_db():
        try:
            yield db
        finally:
            pass
    def override_get_current_user():
        return CLINICAL_USER

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


BILLING_USER = {"email": "biller@waldorfwomenscare.com", "name": "Biller", "group": "billing"}


@pytest.fixture
def billing_client(db):
    """Same as `client` but the authenticated user has group=billing."""
    def override_get_db():
        try:
            yield db
        finally:
            pass
    def override_get_current_user():
        return BILLING_USER

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
