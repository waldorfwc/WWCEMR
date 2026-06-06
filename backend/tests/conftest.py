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
from app.routers.patient_surgery import require_patient_token


TEST_USER = {"email": "tester@waldorfwomenscare.com", "name": "Test User", "group": "admin"}

# TRANSITIONAL: legacy tests rely on TEST_USER having every old permission.
# New tests use `client_factory` + a real User row with the new per-module
# tier model (see docs/superpowers/plans/2026-06-06-permissions-redesign.md).
# Remove this block in Phase 4 once all tests are migrated.
_TEST_USER_PERMS = [
    "claim:read", "claim:edit", "claim:writeoff",
    "payment:post", "payment:void",
    "user:manage",
    "audit:read",
    "bankrecon:read", "bankrecon:generate",
    "report:financial",
    "surgery:read", "surgery:work",
    "pellet:read", "pellet:work", "pellet:manage",
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

    def override_require_patient_token(surgery_id: str = None):
        return "test-token"

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[require_patient_token] = override_require_patient_token
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


@pytest.fixture
def client_factory(db):
    """Build a TestClient bound to a specific persisted User row.

    Use this for tier-aware tests that need a real user with specific group
    memberships or is_super_admin set, rather than the broad-permissions
    TEST_USER baked into `client`.

    Usage:
        def test_x(client_factory, db):
            u = User(email="x@waldorfwomenscare.com", ...)
            db.add(u); db.commit()
            client = client_factory(user=u)
            r = client.get("/some/path")
    """
    def _make(user):
        def override_get_db():
            try:
                yield db
            finally:
                pass

        def override_get_current_user():
            return {
                "email": user.email,
                "name": user.display_name or user.email,
                "picture": "",
                "group": (user.group.value
                          if hasattr(user.group, "value") else user.group),
            }

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_get_current_user
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()
