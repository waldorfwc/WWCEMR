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
from app.models.user import User, UserGroup
from app.routers.auth import get_current_user


TEST_USER = {"email": "tester@waldorfwomenscare.com", "name": "Test User", "group": "admin"}

# All permissions needed by any endpoint exercised in the test suite.
# Using permissions_extra so no Group rows are needed.
_TEST_USER_PERMS = [
    "claim:read", "claim:edit", "claim:writeoff",
    "payment:post", "payment:void",
    "user:manage",
    "audit:read",
    "bankrecon:read", "bankrecon:generate",
    "report:financial",
    "surgery:read", "surgery:work",
]


def _seed_test_user(db):
    """Insert (or upsert) the TEST_USER row so require_permission lookups pass."""
    existing = db.query(User).filter(User.email == TEST_USER["email"]).first()
    if existing is None:
        db.add(User(
            email=TEST_USER["email"],
            display_name=TEST_USER["name"],
            group=UserGroup.ADMIN,
            permissions_extra=_TEST_USER_PERMS,
        ))
        db.commit()


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
    _seed_test_user(db)

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
