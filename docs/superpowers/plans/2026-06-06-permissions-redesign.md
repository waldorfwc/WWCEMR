# Permissions Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace today's `verb:resource` permissions with the per-module tier model defined in `docs/superpowers/specs/2026-06-06-permissions-redesign-design.md`.

**Architecture:** Additive rollout. Phase 1 ships the new model alongside the old without changing route gates. Phase 2 translates existing groups + seeds Default Staff. Phase 3 cuts modules over one at a time. Phase 4 deletes the legacy system. The app is fully functional at every checkpoint.

**Tech Stack:** FastAPI + SQLAlchemy + PostgreSQL (Cloud SQL) + pytest with in-memory SQLite + React/Vite frontend. No Alembic — schema additions go through SQLAlchemy models and one-shot scripts in `scripts/migrate/`.

---

## Phase 1 — Foundation

### Task 1: SQLAlchemy models for tier tables + super-admin flag

**Files:**
- Create: `backend/app/models/module_tier.py`
- Modify: `backend/app/models/user.py` (add `is_super_admin` column)
- Test: `backend/tests/test_module_tier_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_module_tier_models.py
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User


def test_group_module_tier_round_trip(db_session):
    row = GroupModuleTier(group_id="g_billing", module="active_ar", tier=20)
    db_session.add(row)
    db_session.commit()
    fetched = (db_session.query(GroupModuleTier)
                         .filter_by(group_id="g_billing", module="active_ar")
                         .one())
    assert fetched.tier == 20


def test_user_override_round_trip(db_session):
    row = UserModuleOverride(
        user_email="apetit@waldorfwomenscare.com",
        module="active_ar", tier=30,
        added_by="ocooke@waldorfwomenscare.com",
    )
    db_session.add(row)
    db_session.commit()
    fetched = (db_session.query(UserModuleOverride)
                         .filter_by(user_email="apetit@waldorfwomenscare.com",
                                    module="active_ar")
                         .one())
    assert fetched.tier == 30
    assert fetched.added_by == "ocooke@waldorfwomenscare.com"


def test_super_admin_column_default_false(db_session):
    u = User(email="x@waldorfwomenscare.com", display_name="X",
             group="CLINICAL", is_active=True)
    db_session.add(u); db_session.commit()
    assert u.is_super_admin is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_module_tier_models.py -v`
Expected: ImportError on `module_tier`.

- [ ] **Step 3: Create the models**

```python
# backend/app/models/module_tier.py
from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base


class GroupModuleTier(Base):
    """Per-group tier grant for a single module.
    Composes with other groups' grants via max() in the resolver."""
    __tablename__ = "group_module_tiers"

    group_id = Column(
        String, ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True, nullable=False,
    )
    module = Column(String(60), primary_key=True, nullable=False)
    tier   = Column(Integer, nullable=False)


class UserModuleOverride(Base):
    """Per-user tier override for a single module.
    Always wins over group grants. tier=0 means 'denied'."""
    __tablename__ = "user_module_overrides"

    user_email = Column(
        String, ForeignKey("users.email", ondelete="CASCADE"),
        primary_key=True, nullable=False,
    )
    module    = Column(String(60), primary_key=True, nullable=False)
    tier      = Column(Integer, nullable=False)
    added_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    added_by  = Column(String(120), nullable=False)
```

- [ ] **Step 4: Add the super_admin column to User**

In `backend/app/models/user.py`, add to the `User` class:

```python
is_super_admin = Column(Boolean, nullable=False, default=False)
```

- [ ] **Step 5: Register the new module so `Base.metadata.create_all` picks it up**

In `backend/app/database.py` (or wherever models are imported for metadata), add:

```python
from app.models import module_tier  # noqa: F401  — register tables
```

- [ ] **Step 6: Run tests and verify they pass**

Run: `cd backend && pytest tests/test_module_tier_models.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/module_tier.py backend/app/models/user.py backend/app/database.py backend/tests/test_module_tier_models.py
git commit -m "feat(perms): add GroupModuleTier, UserModuleOverride, users.is_super_admin"
```

---

### Task 2: Module + Tier catalog

**Files:**
- Create: `backend/app/permissions/__init__.py`
- Create: `backend/app/permissions/catalog.py`
- Test: `backend/tests/test_permissions_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_permissions_catalog.py
from app.permissions.catalog import Module, Tier, MODULE_REGISTRY


def test_module_enum_has_all_15_modules():
    expected = {
        "chart", "active_ar", "billing_bank_recon", "billing_missing_charges",
        "billing_insurance_docs", "billing_insurance_contacts", "recall",
        "surgery", "device_larc", "device_office_procedures", "pellets",
        "reputation", "training", "my_checklist", "audit_log",
    }
    assert {m.value for m in Module} == expected


def test_tier_ordinal_values():
    assert Tier.NONE < Tier.VIEW < Tier.WORK < Tier.MANAGE < Tier.ADMIN < Tier.SUPER_ADMIN
    assert Tier.NONE == 0
    assert Tier.VIEW == 10
    assert Tier.SUPER_ADMIN == 50


def test_module_registry_covers_every_module():
    for m in Module:
        spec = MODULE_REGISTRY[m]
        assert spec.label
        assert spec.manage_means
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_permissions_catalog.py -v`
Expected: ImportError on `app.permissions`.

- [ ] **Step 3: Create the catalog**

```python
# backend/app/permissions/__init__.py
# (empty)
```

```python
# backend/app/permissions/catalog.py
"""Module + Tier catalog. Single source of truth for permission structure.

Per the design: 15 modules, 5 tiers (NONE/VIEW/WORK/MANAGE/ADMIN/SUPER_ADMIN).
"""
from dataclasses import dataclass
from enum import Enum, IntEnum


class Module(str, Enum):
    CHART                       = "chart"
    ACTIVE_AR                   = "active_ar"
    BANK_RECON                  = "billing_bank_recon"
    MISSING_CHARGES             = "billing_missing_charges"
    INSURANCE_DOCS              = "billing_insurance_docs"
    INSURANCE_CONTACTS          = "billing_insurance_contacts"
    RECALL                      = "recall"
    SURGERY                     = "surgery"
    LARC                        = "device_larc"
    OFFICE_PROCEDURES           = "device_office_procedures"
    PELLETS                     = "pellets"
    REPUTATION                  = "reputation"
    TRAINING                    = "training"
    MY_CHECKLIST                = "my_checklist"
    AUDIT_LOG                   = "audit_log"


class Tier(IntEnum):
    NONE         = 0
    VIEW         = 10
    WORK         = 20
    MANAGE       = 30
    ADMIN        = 40
    SUPER_ADMIN  = 50


@dataclass(frozen=True)
class ModuleSpec:
    label: str
    description: str
    manage_means: str


MODULE_REGISTRY: dict[Module, ModuleSpec] = {
    Module.CHART: ModuleSpec(
        label="Chart",
        description="Patient demographics, clinical history, encounters, recalls.",
        manage_means=(
            "Merge duplicate charts; configure problem-list templates; "
            "delete chart entries."
        ),
    ),
    Module.ACTIVE_AR: ModuleSpec(
        label="Active AR",
        description="Claim queue, payments, denials, appeals, ERA posting.",
        manage_means=(
            "Bulk write-off; configure denial codes & workflow states; "
            "delete claims."
        ),
    ),
    Module.BANK_RECON: ModuleSpec(
        label="Billing – Bank Recon",
        description="Bank reconciliation workflow.",
        manage_means=(
            "Configure recon rules; resolve overlap exceptions; "
            "delete reconciled records."
        ),
    ),
    Module.MISSING_CHARGES: ModuleSpec(
        label="Billing – Missing Charges",
        description="Provider charge-capture review.",
        manage_means=(
            "Issue provider portal tokens; bulk-mark complete; delete charges."
        ),
    ),
    Module.INSURANCE_DOCS: ModuleSpec(
        label="Billing – Insurance Documents",
        description="Insurance correspondence and billing documents.",
        manage_means=(
            "Hard-delete documents; bulk-assign; configure classifications."
        ),
    ),
    Module.INSURANCE_CONTACTS: ModuleSpec(
        label="Billing – Insurance Contacts",
        description="Payer contact directory.",
        manage_means=(
            "Bulk import; delete contacts; configure source rules."
        ),
    ),
    Module.RECALL: ModuleSpec(
        label="Recall",
        description="Patient recall lists and outreach.",
        manage_means=(
            "Configure recall rules; bulk-schedule; delete recall lists."
        ),
    ),
    Module.SURGERY: ModuleSpec(
        label="Surgery",
        description="Surgery scheduling, consent, fee schedule, block calendar.",
        manage_means=(
            "Configure block schedules / fee schedule / consent templates / "
            "surgery types; delete surgeries."
        ),
    ),
    Module.LARC: ModuleSpec(
        label="Device Tracking – LARC",
        description="IUD/implant pharmacy and checkout workflow.",
        manage_means=(
            "Bulk-import devices; configure inventory rules; delete devices."
        ),
    ),
    Module.OFFICE_PROCEDURES: ModuleSpec(
        label="Device Tracking – Office Procedures",
        description="Office-procedure device tracking.",
        manage_means=(
            "Bulk-import devices; configure inventory rules; delete devices."
        ),
    ),
    Module.PELLETS: ModuleSpec(
        label="Pellets",
        description="Pellet inventory + visits (DEA Schedule III).",
        manage_means=(
            "Configure lots & dose schedules; configure Smartsheet sync; "
            "delete adjustments (within DEA constraints)."
        ),
    ),
    Module.REPUTATION: ModuleSpec(
        label="Reputation Management",
        description="Review portal and patient feedback.",
        manage_means=(
            "Configure review portal; configure response templates; delete reviews."
        ),
    ),
    Module.TRAINING: ModuleSpec(
        label="Training",
        description="Training modules and completion tracking.",
        manage_means=(
            "Author training modules; assign training paths; "
            "mark complete on behalf of others."
        ),
    ),
    Module.MY_CHECKLIST: ModuleSpec(
        label="My Checklist",
        description="Personal task lists. Own-list access is implicit.",
        manage_means=(
            "Assign tasks to other users; configure recurring tasks."
        ),
    ),
    Module.AUDIT_LOG: ModuleSpec(
        label="Audit Log",
        description="HIPAA audit trail. Append-only.",
        manage_means=(
            "Export audit data; configure retention policy. "
            "Rows are append-only — no deletion ever."
        ),
    ),
}
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `pytest tests/test_permissions_catalog.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/permissions/
git add backend/tests/test_permissions_catalog.py
git commit -m "feat(perms): Module + Tier catalog (15 modules, 5 tiers)"
```

---

### Task 3: Effective-tier resolver

**Files:**
- Create: `backend/app/permissions/resolver.py`
- Test: `backend/tests/test_permissions_resolver.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_permissions_resolver.py
import pytest
from app.models.group import Group, UserGroupMembership
from app.models.user import User
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.permissions.catalog import Module, Tier
from app.permissions.resolver import effective_tier, effective_tier_with_source


@pytest.fixture
def user_with_groups(db_session):
    u = User(email="apetit@waldorfwomenscare.com", display_name="Apetit",
             group="BILLING", is_active=True)
    g1 = Group(id="g_billing", name="Billing Coders")
    g2 = Group(id="g_frontdesk", name="Front Desk")
    db_session.add_all([u, g1, g2])
    db_session.add_all([
        UserGroupMembership(user_email=u.email, group_id=g1.id),
        UserGroupMembership(user_email=u.email, group_id=g2.id),
        GroupModuleTier(group_id="g_billing", module="active_ar", tier=Tier.WORK),
        GroupModuleTier(group_id="g_frontdesk", module="active_ar", tier=Tier.VIEW),
    ])
    db_session.commit()
    return u


def test_max_of_groups_wins(db_session, user_with_groups):
    # billing=WORK, frontdesk=VIEW → effective WORK
    t = effective_tier(db_session, user_with_groups.email, Module.ACTIVE_AR)
    assert t == Tier.WORK


def test_override_beats_group(db_session, user_with_groups):
    db_session.add(UserModuleOverride(
        user_email=user_with_groups.email,
        module="active_ar", tier=Tier.MANAGE,
        added_by="ocooke@waldorfwomenscare.com",
    ))
    db_session.commit()
    t = effective_tier(db_session, user_with_groups.email, Module.ACTIVE_AR)
    assert t == Tier.MANAGE


def test_denied_override_blocks_group_grant(db_session, user_with_groups):
    db_session.add(UserModuleOverride(
        user_email=user_with_groups.email,
        module="active_ar", tier=Tier.NONE,
        added_by="ocooke@waldorfwomenscare.com",
    ))
    db_session.commit()
    t = effective_tier(db_session, user_with_groups.email, Module.ACTIVE_AR)
    assert t == Tier.NONE


def test_super_admin_short_circuits_to_max(db_session, user_with_groups):
    user_with_groups.is_super_admin = True
    db_session.commit()
    t = effective_tier(db_session, user_with_groups.email, Module.SURGERY)
    assert t == Tier.SUPER_ADMIN


def test_no_grant_returns_none(db_session):
    u = User(email="nobody@waldorfwomenscare.com", display_name="N",
             group="CLINICAL", is_active=True)
    db_session.add(u); db_session.commit()
    t = effective_tier(db_session, u.email, Module.ACTIVE_AR)
    assert t == Tier.NONE


def test_source_reports_specific_group_name(db_session, user_with_groups):
    result = effective_tier_with_source(
        db_session, user_with_groups.email, Module.ACTIVE_AR,
    )
    assert result.tier == Tier.WORK
    assert result.source_kind == "group"
    assert result.source_label == "Billing Coders"


def test_source_reports_override(db_session, user_with_groups):
    db_session.add(UserModuleOverride(
        user_email=user_with_groups.email,
        module="active_ar", tier=Tier.MANAGE,
        added_by="ocooke@waldorfwomenscare.com",
    ))
    db_session.commit()
    result = effective_tier_with_source(
        db_session, user_with_groups.email, Module.ACTIVE_AR,
    )
    assert result.tier == Tier.MANAGE
    assert result.source_kind == "override"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_permissions_resolver.py -v`
Expected: ImportError on `app.permissions.resolver`.

- [ ] **Step 3: Implement the resolver**

```python
# backend/app/permissions/resolver.py
"""Resolve effective tier for (user, module).

Algorithm (per spec §Resolution algorithm):
    1. If user.is_super_admin              → SUPER_ADMIN
    2. If override exists for (user, mod)  → override.tier
    3. Else max(group.tier) across groups  → that
    4. Else                                → NONE
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.group import Group, UserGroupMembership
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User
from app.permissions.catalog import Module, Tier


@dataclass
class TierWithSource:
    tier: Tier
    source_kind: str          # "super_admin" | "override" | "group" | "none"
    source_label: Optional[str]  # group name when source_kind == "group"


def effective_tier(db: Session, user_email: str, module: Module) -> Tier:
    return effective_tier_with_source(db, user_email, module).tier


def effective_tier_with_source(
    db: Session, user_email: str, module: Module,
) -> TierWithSource:
    user = db.query(User).filter(User.email == user_email).first()
    if user is None:
        return TierWithSource(Tier.NONE, "none", None)

    if user.is_super_admin:
        return TierWithSource(Tier.SUPER_ADMIN, "super_admin", None)

    override = (db.query(UserModuleOverride)
                  .filter(UserModuleOverride.user_email == user_email,
                          UserModuleOverride.module == module.value)
                  .first())
    if override is not None:
        return TierWithSource(Tier(override.tier), "override", None)

    # Max of group grants
    rows = (db.query(GroupModuleTier, Group)
              .join(Group, Group.id == GroupModuleTier.group_id)
              .join(UserGroupMembership,
                    UserGroupMembership.group_id == GroupModuleTier.group_id)
              .filter(UserGroupMembership.user_email == user_email,
                      GroupModuleTier.module == module.value)
              .all())
    if not rows:
        return TierWithSource(Tier.NONE, "none", None)
    best_row, best_group = max(rows, key=lambda pair: pair[0].tier)
    return TierWithSource(Tier(best_row.tier), "group", best_group.name)
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `pytest tests/test_permissions_resolver.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/permissions/resolver.py backend/tests/test_permissions_resolver.py
git commit -m "feat(perms): effective_tier resolver (override > max-group > none)"
```

---

### Task 4: `requires_tier` FastAPI dependency

**Files:**
- Create: `backend/app/permissions/dependencies.py`
- Test: `backend/tests/test_requires_tier.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_requires_tier.py
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
from app.models.module_tier import GroupModuleTier
from app.models.user import User
from app.models.group import Group, UserGroupMembership


def _build_app(db_session_factory):
    app = FastAPI()

    router = APIRouter()

    @router.get(
        "/protected",
        dependencies=[Depends(requires_tier(Module.SURGERY, Tier.WORK))],
    )
    def protected():
        return {"ok": True}

    app.include_router(router)
    return app


def test_403_when_below_required_tier(client_factory, db_session):
    u = User(email="x@waldorfwomenscare.com", display_name="X",
             group="CLINICAL", is_active=True)
    g = Group(id="g_fd", name="Front Desk")
    db_session.add_all([u, g, UserGroupMembership(user_email=u.email, group_id=g.id),
                         GroupModuleTier(group_id=g.id, module="surgery",
                                         tier=Tier.VIEW)])
    db_session.commit()
    client = client_factory(user=u)
    r = client.get("/protected")
    assert r.status_code == 403
    body = r.json()
    assert "Surgery" in body["detail"]  # human-readable module label
    assert "Work" in body["detail"]


def test_200_when_at_required_tier(client_factory, db_session):
    u = User(email="x@waldorfwomenscare.com", display_name="X",
             group="CLINICAL", is_active=True)
    g = Group(id="g_sc", name="Surgery Coordinators")
    db_session.add_all([u, g, UserGroupMembership(user_email=u.email, group_id=g.id),
                         GroupModuleTier(group_id=g.id, module="surgery",
                                         tier=Tier.WORK)])
    db_session.commit()
    client = client_factory(user=u)
    r = client.get("/protected")
    assert r.status_code == 200


def test_200_when_super_admin(client_factory, db_session):
    u = User(email="root@waldorfwomenscare.com", display_name="R",
             group="CLINICAL", is_active=True, is_super_admin=True)
    db_session.add(u); db_session.commit()
    client = client_factory(user=u)
    r = client.get("/protected")
    assert r.status_code == 200
```

(`client_factory` is a new pytest fixture in `conftest.py` that builds a TestClient with the given user as `get_current_user`'s return; described in Task 9.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_requires_tier.py -v`
Expected: ImportError on `app.permissions.dependencies`.

- [ ] **Step 3: Implement the dependency**

```python
# backend/app/permissions/dependencies.py
"""FastAPI dependency factory: 403 if user's effective tier is below `min_tier`."""
from typing import Callable

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.permissions.catalog import MODULE_REGISTRY, Module, Tier
from app.permissions.resolver import effective_tier
from app.routers.auth import get_current_user


def requires_tier(module: Module, min_tier: Tier) -> Callable:
    """Return a FastAPI dependency that 403s if the current user's
    effective tier on `module` is less than `min_tier`."""

    def _dep(
        current_user: dict = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        email = (current_user.get("email") or "").lower().strip()
        actual = effective_tier(db, email, module)
        if actual < min_tier:
            spec = MODULE_REGISTRY[module]
            tier_name = min_tier.name.replace("_", " ").title()
            raise HTTPException(
                status_code=403,
                detail=(f"forbidden — needs {tier_name} on {spec.label} "
                        f"(you have {actual.name.title()})"),
            )
        # Inject the resolved tier so handlers can branch on it without
        # re-querying.
        out = dict(current_user)
        out["module_tier"] = {module.value: int(actual)}
        return out

    return _dep
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `pytest tests/test_requires_tier.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/permissions/dependencies.py backend/tests/test_requires_tier.py
git commit -m "feat(perms): requires_tier FastAPI dependency"
```

---

### Task 5: Tier-grant service (group + user override) with audit

**Files:**
- Create: `backend/app/services/permission_grants.py`
- Modify: `backend/app/services/audit_service.py` (no schema change; just new action constants if needed)
- Test: `backend/tests/test_permission_grants.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_permission_grants.py
import pytest
from app.models.audit_log import AuditLog
from app.models.group import Group, UserGroupMembership
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User
from app.permissions.catalog import Module, Tier
from app.services.permission_grants import (
    set_group_tier, clear_group_tier,
    set_user_override, clear_user_override,
    set_super_admin,
    SuperAdminProtected,
)


def _seed_user(db, email, super_admin=False):
    u = User(email=email, display_name=email, group="CLINICAL",
             is_active=True, is_super_admin=super_admin)
    db.add(u); db.commit()
    return u


def test_set_group_tier_creates_and_audits(db_session):
    db_session.add(Group(id="g_b", name="Billing Coders")); db_session.commit()
    set_group_tier(
        db_session, group_id="g_b", module=Module.ACTIVE_AR, tier=Tier.WORK,
        actor_email="ocooke@waldorfwomenscare.com",
    )
    row = (db_session.query(GroupModuleTier)
                     .filter_by(group_id="g_b", module="active_ar").one())
    assert row.tier == Tier.WORK
    audit = (db_session.query(AuditLog)
                       .filter_by(action="GROUP_PERMS_UPDATED").first())
    assert audit is not None
    assert "Billing Coders" in audit.description
    assert "Active AR" in audit.description


def test_set_user_override_creates_and_audits(db_session):
    _seed_user(db_session, "apetit@waldorfwomenscare.com")
    set_user_override(
        db_session, user_email="apetit@waldorfwomenscare.com",
        module=Module.ACTIVE_AR, tier=Tier.MANAGE,
        actor_email="ocooke@waldorfwomenscare.com",
    )
    row = (db_session.query(UserModuleOverride)
                     .filter_by(user_email="apetit@waldorfwomenscare.com",
                                module="active_ar").one())
    assert row.tier == Tier.MANAGE
    assert row.added_by == "ocooke@waldorfwomenscare.com"
    audit = (db_session.query(AuditLog)
                       .filter_by(action="USER_PERMS_OVERRIDE").first())
    assert audit is not None


def test_clear_user_override_audits(db_session):
    _seed_user(db_session, "apetit@waldorfwomenscare.com")
    set_user_override(
        db_session, user_email="apetit@waldorfwomenscare.com",
        module=Module.ACTIVE_AR, tier=Tier.MANAGE,
        actor_email="ocooke@waldorfwomenscare.com",
    )
    clear_user_override(
        db_session, user_email="apetit@waldorfwomenscare.com",
        module=Module.ACTIVE_AR,
        actor_email="ocooke@waldorfwomenscare.com",
    )
    assert (db_session.query(UserModuleOverride)
                      .filter_by(user_email="apetit@waldorfwomenscare.com",
                                 module="active_ar").first()) is None


def test_set_super_admin_grants(db_session):
    _seed_user(db_session, "x@waldorfwomenscare.com")
    set_super_admin(
        db_session, target_email="x@waldorfwomenscare.com",
        is_super_admin=True, actor_email="root@waldorfwomenscare.com",
    )
    u = db_session.query(User).filter_by(email="x@waldorfwomenscare.com").one()
    assert u.is_super_admin is True
    audit = (db_session.query(AuditLog)
                       .filter_by(action="SUPER_ADMIN_GRANTED").first())
    assert audit is not None


def test_last_super_admin_cannot_be_demoted(db_session):
    _seed_user(db_session, "root@waldorfwomenscare.com", super_admin=True)
    with pytest.raises(SuperAdminProtected):
        set_super_admin(
            db_session, target_email="root@waldorfwomenscare.com",
            is_super_admin=False, actor_email="root@waldorfwomenscare.com",
        )


def test_demoting_one_of_two_super_admins_succeeds(db_session):
    _seed_user(db_session, "root@waldorfwomenscare.com", super_admin=True)
    _seed_user(db_session, "ocooke@waldorfwomenscare.com", super_admin=True)
    set_super_admin(
        db_session, target_email="ocooke@waldorfwomenscare.com",
        is_super_admin=False, actor_email="root@waldorfwomenscare.com",
    )
    u = db_session.query(User).filter_by(email="ocooke@waldorfwomenscare.com").one()
    assert u.is_super_admin is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_permission_grants.py -v`
Expected: ImportError on `app.services.permission_grants`.

- [ ] **Step 3: Implement the service**

```python
# backend/app/services/permission_grants.py
"""Grant + override + super-admin management with audit + safety."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog  # adjust import to actual model path
from app.models.group import Group
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User
from app.permissions.catalog import MODULE_REGISTRY, Module, Tier
from app.services.audit_service import log_action


class SuperAdminProtected(Exception):
    """Raised when an action would leave zero Super Admins."""


def set_group_tier(db: Session, *, group_id: str, module: Module,
                    tier: Tier, actor_email: str) -> None:
    group = db.query(Group).filter(Group.id == group_id).one()
    row = (db.query(GroupModuleTier)
             .filter_by(group_id=group_id, module=module.value)
             .first())
    before = Tier(row.tier).name if row else "NONE"
    if row is None:
        row = GroupModuleTier(group_id=group_id, module=module.value,
                              tier=int(tier))
        db.add(row)
    else:
        row.tier = int(tier)
    spec = MODULE_REGISTRY[module]
    log_action(
        db, action="GROUP_PERMS_UPDATED", resource_type="group",
        resource_id=group_id,
        user_id=actor_email, user_name=actor_email,
        description=(f"Set {group.name} → {spec.label} = "
                     f"{tier.name.title()} (was {before.title()})"),
    )
    db.commit()


def clear_group_tier(db: Session, *, group_id: str, module: Module,
                      actor_email: str) -> None:
    group = db.query(Group).filter(Group.id == group_id).one()
    row = (db.query(GroupModuleTier)
             .filter_by(group_id=group_id, module=module.value)
             .first())
    if row is None:
        return
    db.delete(row)
    spec = MODULE_REGISTRY[module]
    log_action(
        db, action="GROUP_PERMS_UPDATED", resource_type="group",
        resource_id=group_id,
        user_id=actor_email, user_name=actor_email,
        description=f"Cleared {group.name} → {spec.label}",
    )
    db.commit()


def set_user_override(db: Session, *, user_email: str, module: Module,
                       tier: Tier, actor_email: str) -> None:
    row = (db.query(UserModuleOverride)
             .filter_by(user_email=user_email, module=module.value)
             .first())
    before = Tier(row.tier).name if row else "(none)"
    if row is None:
        row = UserModuleOverride(
            user_email=user_email, module=module.value, tier=int(tier),
            added_by=actor_email, added_at=datetime.utcnow(),
        )
        db.add(row)
    else:
        row.tier = int(tier)
        row.added_by = actor_email
        row.added_at = datetime.utcnow()
    spec = MODULE_REGISTRY[module]
    log_action(
        db, action="USER_PERMS_OVERRIDE", resource_type="user",
        resource_id=user_email,
        user_id=actor_email, user_name=actor_email,
        description=(f"Override {user_email} → {spec.label} = "
                     f"{tier.name.title()} (was {before.title()})"),
    )
    db.commit()


def clear_user_override(db: Session, *, user_email: str, module: Module,
                         actor_email: str) -> None:
    row = (db.query(UserModuleOverride)
             .filter_by(user_email=user_email, module=module.value)
             .first())
    if row is None:
        return
    db.delete(row)
    spec = MODULE_REGISTRY[module]
    log_action(
        db, action="USER_PERMS_OVERRIDE", resource_type="user",
        resource_id=user_email,
        user_id=actor_email, user_name=actor_email,
        description=f"Cleared override {user_email} → {spec.label}",
    )
    db.commit()


def set_super_admin(db: Session, *, target_email: str,
                     is_super_admin: bool, actor_email: str) -> None:
    target = db.query(User).filter(User.email == target_email).one()
    if target.is_super_admin and not is_super_admin:
        remaining = (db.query(User)
                       .filter(User.is_super_admin.is_(True),
                               User.email != target_email)
                       .count())
        if remaining == 0:
            raise SuperAdminProtected(
                "Refusing to leave zero Super Admins. "
                "Grant another user Super Admin first.",
            )
    target.is_super_admin = is_super_admin
    action = "SUPER_ADMIN_GRANTED" if is_super_admin else "SUPER_ADMIN_REVOKED"
    log_action(
        db, action=action, resource_type="user", resource_id=target_email,
        user_id=actor_email, user_name=actor_email,
        description=(f"{'Granted' if is_super_admin else 'Revoked'} Super Admin "
                     f"for {target_email}"),
    )
    db.commit()
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `pytest tests/test_permission_grants.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/permission_grants.py backend/tests/test_permission_grants.py
git commit -m "feat(perms): grant + override + super-admin services with audit"
```

---

### Task 6: Admin API endpoints

**Files:**
- Create: `backend/app/routers/admin_tiers.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/test_admin_tiers_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_admin_tiers_api.py
from app.models.group import Group, UserGroupMembership
from app.models.module_tier import GroupModuleTier
from app.models.user import User
from app.permissions.catalog import Tier


def _seed_super_admin(db, email="root@waldorfwomenscare.com"):
    u = User(email=email, display_name="Root", group="CLINICAL",
             is_active=True, is_super_admin=True)
    db.add(u); db.commit()
    return u


def test_get_user_tiers_returns_resolved_grid(client_factory, db_session):
    u = User(email="apetit@waldorfwomenscare.com", display_name="A",
             group="BILLING", is_active=True)
    g = Group(id="g_b", name="Billing Coders")
    db_session.add_all([u, g, UserGroupMembership(user_email=u.email, group_id=g.id),
                         GroupModuleTier(group_id=g.id, module="active_ar",
                                         tier=Tier.WORK)])
    db_session.commit()
    root = _seed_super_admin(db_session)
    client = client_factory(user=root)
    r = client.get(f"/api/admin/users/{u.email}/tiers")
    assert r.status_code == 200
    body = r.json()
    entries = {e["module"]: e for e in body["tiers"]}
    assert entries["active_ar"]["tier"] == "work"
    assert entries["active_ar"]["source_kind"] == "group"
    assert entries["active_ar"]["source_label"] == "Billing Coders"


def test_put_user_override_creates(client_factory, db_session):
    u = User(email="apetit@waldorfwomenscare.com", display_name="A",
             group="BILLING", is_active=True)
    db_session.add(u); db_session.commit()
    root = _seed_super_admin(db_session)
    client = client_factory(user=root)
    r = client.put(
        f"/api/admin/users/{u.email}/overrides/active_ar",
        json={"tier": "manage"},
    )
    assert r.status_code == 200


def test_put_user_override_with_null_clears(client_factory, db_session):
    from app.models.module_tier import UserModuleOverride
    u = User(email="apetit@waldorfwomenscare.com", display_name="A",
             group="BILLING", is_active=True)
    db_session.add(u); db_session.commit()
    db_session.add(UserModuleOverride(
        user_email=u.email, module="active_ar", tier=Tier.MANAGE,
        added_by="root@waldorfwomenscare.com",
    ))
    db_session.commit()
    root = _seed_super_admin(db_session)
    client = client_factory(user=root)
    r = client.put(
        f"/api/admin/users/{u.email}/overrides/active_ar",
        json={"tier": None},
    )
    assert r.status_code == 200
    assert (db_session.query(UserModuleOverride)
                      .filter_by(user_email=u.email, module="active_ar")
                      .first()) is None


def test_put_super_admin_requires_super_admin(client_factory, db_session):
    target = User(email="t@waldorfwomenscare.com", display_name="T",
                  group="CLINICAL", is_active=True)
    non_root = User(email="x@waldorfwomenscare.com", display_name="X",
                    group="CLINICAL", is_active=True, is_super_admin=False)
    db_session.add_all([target, non_root]); db_session.commit()
    client = client_factory(user=non_root)
    r = client.put(
        f"/api/admin/users/{target.email}/super_admin",
        json={"is_super_admin": True},
    )
    assert r.status_code == 403


def test_put_super_admin_works_for_super_admin(client_factory, db_session):
    target = User(email="t@waldorfwomenscare.com", display_name="T",
                  group="CLINICAL", is_active=True)
    db_session.add(target); db_session.commit()
    root = _seed_super_admin(db_session)
    client = client_factory(user=root)
    r = client.put(
        f"/api/admin/users/{target.email}/super_admin",
        json={"is_super_admin": True},
    )
    assert r.status_code == 200
    db_session.refresh(target)
    assert target.is_super_admin is True


def test_last_super_admin_demote_returns_409(client_factory, db_session):
    root = _seed_super_admin(db_session)
    client = client_factory(user=root)
    r = client.put(
        f"/api/admin/users/{root.email}/super_admin",
        json={"is_super_admin": False},
    )
    assert r.status_code == 409
    body = r.json()
    assert "Super Admin" in body["detail"]


def test_put_group_tier_requires_admin_on_module(client_factory, db_session):
    g = Group(id="g_b", name="Billing Coders")
    actor = User(email="actor@waldorfwomenscare.com", display_name="Act",
                 group="BILLING", is_active=True)
    db_session.add_all([g, actor]); db_session.commit()
    client = client_factory(user=actor)
    r = client.put(
        f"/api/admin/groups/{g.id}/tiers/active_ar",
        json={"tier": "manage"},
    )
    assert r.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_admin_tiers_api.py -v`
Expected: 404 (router not registered).

- [ ] **Step 3: Implement the router**

```python
# backend/app/routers/admin_tiers.py
"""Admin endpoints for tier grants and Super Admin management.

Auth model:
  - PUT /users/{email}/overrides/{module}  → caller needs ADMIN on `module`
                                             (Super Admin always passes)
  - PUT /users/{email}/super_admin         → caller must be Super Admin
  - PUT /groups/{group}/tiers/{module}     → caller needs ADMIN on `module`
  - GET endpoints                          → caller must be Super Admin
                                             (matrix views show every user's
                                              tiers across all modules)
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.permissions.catalog import MODULE_REGISTRY, Module, Tier
from app.permissions.resolver import effective_tier, effective_tier_with_source
from app.routers.auth import get_current_user
from app.services.permission_grants import (
    SuperAdminProtected,
    clear_group_tier, clear_user_override,
    set_group_tier, set_super_admin, set_user_override,
)


router = APIRouter(prefix="/admin", tags=["admin-tiers"])


# ─── Helpers ────────────────────────────────────────────────────────

def _module_or_404(slug: str) -> Module:
    try:
        return Module(slug)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"unknown module: {slug}")


def _tier_or_422(name: Optional[str]) -> Optional[Tier]:
    if name is None:
        return None
    try:
        return Tier[name.upper()]
    except KeyError:
        raise HTTPException(status_code=422, detail=f"unknown tier: {name}")


def _require_super_admin(current_user: dict, db: Session) -> User:
    email = (current_user.get("email") or "").lower().strip()
    u = db.query(User).filter(User.email == email).first()
    if u is None or not u.is_super_admin:
        raise HTTPException(status_code=403,
                             detail="Super Admin required")
    return u


def _require_admin_on_module(current_user: dict, db: Session,
                              module: Module) -> User:
    email = (current_user.get("email") or "").lower().strip()
    u = db.query(User).filter(User.email == email).first()
    if u is None:
        raise HTTPException(status_code=403, detail="forbidden")
    if u.is_super_admin:
        return u
    if effective_tier(db, email, module) < Tier.ADMIN:
        raise HTTPException(
            status_code=403,
            detail=f"forbidden — needs Admin on {MODULE_REGISTRY[module].label}",
        )
    return u


# ─── GET /users/{email}/tiers ───────────────────────────────────────

@router.get("/users/{email}/tiers")
def get_user_tiers(email: str, db: Session = Depends(get_db),
                    current_user: dict = Depends(get_current_user)):
    _require_super_admin(current_user, db)
    tiers = []
    for module in Module:
        result = effective_tier_with_source(db, email, module)
        tiers.append({
            "module": module.value,
            "label": MODULE_REGISTRY[module].label,
            "tier": result.tier.name.lower(),
            "source_kind": result.source_kind,
            "source_label": result.source_label,
        })
    return {"email": email, "tiers": tiers}


# ─── PUT /users/{email}/overrides/{module} ──────────────────────────

class OverrideIn(BaseModel):
    tier: Optional[str]   # "view" | "work" | "manage" | "admin" | "denied" | None


@router.put("/users/{email}/overrides/{module_slug}")
def put_user_override(email: str, module_slug: str, payload: OverrideIn,
                      db: Session = Depends(get_db),
                      current_user: dict = Depends(get_current_user)):
    module = _module_or_404(module_slug)
    actor = _require_admin_on_module(current_user, db, module)
    if payload.tier is None:
        clear_user_override(db, user_email=email, module=module,
                            actor_email=actor.email)
        return {"ok": True, "cleared": True}
    tier_name = "NONE" if payload.tier.lower() == "denied" else payload.tier.upper()
    tier = _tier_or_422(tier_name)
    # ADMIN tier may only be granted by Super Admin
    if tier == Tier.ADMIN and not actor.is_super_admin:
        raise HTTPException(
            status_code=403,
            detail="Only Super Admin can grant the Admin tier",
        )
    set_user_override(db, user_email=email, module=module, tier=tier,
                      actor_email=actor.email)
    return {"ok": True, "tier": tier.name.lower()}


# ─── PUT /users/{email}/super_admin ─────────────────────────────────

class SuperAdminIn(BaseModel):
    is_super_admin: bool


@router.put("/users/{email}/super_admin")
def put_super_admin(email: str, payload: SuperAdminIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(get_current_user)):
    actor = _require_super_admin(current_user, db)
    try:
        set_super_admin(db, target_email=email,
                        is_super_admin=payload.is_super_admin,
                        actor_email=actor.email)
    except SuperAdminProtected as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, "is_super_admin": payload.is_super_admin}


# ─── GET /groups/{group_id}/tiers ───────────────────────────────────

@router.get("/groups/{group_id}/tiers")
def get_group_tiers(group_id: str, db: Session = Depends(get_db),
                     current_user: dict = Depends(get_current_user)):
    _require_super_admin(current_user, db)
    from app.models.module_tier import GroupModuleTier
    rows = (db.query(GroupModuleTier)
              .filter(GroupModuleTier.group_id == group_id)
              .all())
    by_module = {r.module: r.tier for r in rows}
    out = []
    for m in Module:
        tier = by_module.get(m.value)
        out.append({
            "module": m.value,
            "label": MODULE_REGISTRY[m].label,
            "tier": Tier(tier).name.lower() if tier is not None else None,
        })
    return {"group_id": group_id, "tiers": out}


# ─── PUT /groups/{group_id}/tiers/{module} ──────────────────────────

class GroupTierIn(BaseModel):
    tier: Optional[str]


@router.put("/groups/{group_id}/tiers/{module_slug}")
def put_group_tier(group_id: str, module_slug: str, payload: GroupTierIn,
                    db: Session = Depends(get_db),
                    current_user: dict = Depends(get_current_user)):
    module = _module_or_404(module_slug)
    actor = _require_admin_on_module(current_user, db, module)
    if payload.tier is None:
        clear_group_tier(db, group_id=group_id, module=module,
                         actor_email=actor.email)
        return {"ok": True, "cleared": True}
    tier_name = "NONE" if payload.tier.lower() == "denied" else payload.tier.upper()
    tier = _tier_or_422(tier_name)
    if tier == Tier.ADMIN and not actor.is_super_admin:
        raise HTTPException(
            status_code=403,
            detail="Only Super Admin can grant the Admin tier to a group",
        )
    set_group_tier(db, group_id=group_id, module=module, tier=tier,
                   actor_email=actor.email)
    return {"ok": True, "tier": tier.name.lower()}
```

- [ ] **Step 4: Register the router**

In `backend/app/main.py`, after the existing `admin_users`/`admin_groups` includes:

```python
from app.routers import admin_tiers
app.include_router(admin_tiers.router, prefix="/api")
```

- [ ] **Step 5: Run tests and verify they pass**

Run: `pytest tests/test_admin_tiers_api.py -v`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/admin_tiers.py backend/app/main.py backend/tests/test_admin_tiers_api.py
git commit -m "feat(perms): admin API for tier grants + super-admin"
```

---

### Task 7: Update test conftest for new permissions model

**Files:**
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: Read the current conftest**

Open `backend/tests/conftest.py`. The current `TEST_USER` block uses `_TEST_USER_PERMS` (a list of `verb:resource` strings) injected as `permissions_extra`. This is going away in Phase 4 but other tests still rely on `TEST_USER` having broad access.

- [ ] **Step 2: Add a `client_factory` fixture that accepts a user**

Append to `conftest.py`:

```python
@pytest.fixture
def client_factory(db_session):
    """Returns a callable that builds a TestClient bound to a given User row.

    Usage:
        def test_x(client_factory, db_session):
            u = User(email="x@waldorfwomenscare.com", ...)
            db_session.add(u); db_session.commit()
            client = client_factory(user=u)
            r = client.get("/some/path")
    """
    def _make(user):
        def _override():
            return {
                "email": user.email,
                "name": user.display_name or user.email,
                "picture": "",
                "group": user.group.value if hasattr(user.group, "value") else user.group,
            }
        app.dependency_overrides[get_current_user] = _override
        return TestClient(app)
    yield _make
    app.dependency_overrides.pop(get_current_user, None)
```

- [ ] **Step 3: Mark the existing `TEST_USER` block as transitional**

Above the `_TEST_USER_PERMS` list, add a comment:

```python
# TRANSITIONAL: legacy tests rely on TEST_USER having every old permission.
# New tests use client_factory + the per-module tier model directly.
# Remove this block in Phase 4 once all tests are migrated.
```

(No behavior change yet — keeps existing tests green.)

- [ ] **Step 4: Run the full backend test suite to confirm nothing broke**

Run: `cd backend && pytest -x -q`
Expected: All tests pass. New `client_factory` fixture is available for Tasks 4 and 6.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "test(perms): add client_factory fixture for tier-aware tests"
```

---

### Task 8: Default Staff group seed + auto-join hook

**Files:**
- Create: `backend/app/services/default_staff_group.py`
- Modify: `backend/app/routers/auth.py` (auto-join in `get_current_user`'s user-creation branch)
- Test: `backend/tests/test_default_staff.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_default_staff.py
from app.models.group import Group, UserGroupMembership
from app.models.module_tier import GroupModuleTier
from app.permissions.catalog import Module, Tier
from app.services.default_staff_group import (
    ensure_default_staff_group,
    auto_join_default_staff,
    DEFAULT_STAFF_GROUP_ID,
)


def test_ensure_creates_group_with_baseline_grants(db_session):
    ensure_default_staff_group(db_session)
    g = db_session.query(Group).filter_by(id=DEFAULT_STAFF_GROUP_ID).one()
    assert g.name == "Default Staff"
    tiers = {r.module: r.tier for r in
             db_session.query(GroupModuleTier).filter_by(group_id=g.id).all()}
    assert tiers == {
        Module.CHART.value: int(Tier.VIEW),
        Module.MY_CHECKLIST.value: int(Tier.WORK),
    }


def test_ensure_is_idempotent(db_session):
    ensure_default_staff_group(db_session)
    ensure_default_staff_group(db_session)
    g_count = db_session.query(Group).filter_by(id=DEFAULT_STAFF_GROUP_ID).count()
    tier_count = (db_session.query(GroupModuleTier)
                            .filter_by(group_id=DEFAULT_STAFF_GROUP_ID).count())
    assert g_count == 1
    assert tier_count == 2


def test_auto_join_adds_membership(db_session):
    ensure_default_staff_group(db_session)
    auto_join_default_staff(db_session, "newhire@waldorfwomenscare.com")
    m = (db_session.query(UserGroupMembership)
                   .filter_by(user_email="newhire@waldorfwomenscare.com",
                              group_id=DEFAULT_STAFF_GROUP_ID).first())
    assert m is not None


def test_auto_join_is_idempotent(db_session):
    ensure_default_staff_group(db_session)
    auto_join_default_staff(db_session, "newhire@waldorfwomenscare.com")
    auto_join_default_staff(db_session, "newhire@waldorfwomenscare.com")
    count = (db_session.query(UserGroupMembership)
                       .filter_by(user_email="newhire@waldorfwomenscare.com",
                                  group_id=DEFAULT_STAFF_GROUP_ID).count())
    assert count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_default_staff.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement the seed + join helper**

```python
# backend/app/services/default_staff_group.py
"""Default Staff group: a system-managed group auto-joined by every new user.

Baseline grants (per the design spec):
  - Chart        → View
  - My Checklist → Work
"""
from sqlalchemy.orm import Session

from app.models.group import Group, UserGroupMembership
from app.models.module_tier import GroupModuleTier
from app.permissions.catalog import Module, Tier


DEFAULT_STAFF_GROUP_ID   = "default_staff"
DEFAULT_STAFF_GROUP_NAME = "Default Staff"

DEFAULT_STAFF_GRANTS: dict[Module, Tier] = {
    Module.CHART:        Tier.VIEW,
    Module.MY_CHECKLIST: Tier.WORK,
}


def ensure_default_staff_group(db: Session) -> None:
    """Idempotently create the Default Staff group + its baseline grants."""
    g = db.query(Group).filter_by(id=DEFAULT_STAFF_GROUP_ID).first()
    if g is None:
        g = Group(id=DEFAULT_STAFF_GROUP_ID, name=DEFAULT_STAFF_GROUP_NAME)
        db.add(g)
        db.flush()
    for module, tier in DEFAULT_STAFF_GRANTS.items():
        existing = (db.query(GroupModuleTier)
                      .filter_by(group_id=g.id, module=module.value)
                      .first())
        if existing is None:
            db.add(GroupModuleTier(group_id=g.id, module=module.value,
                                    tier=int(tier)))
        elif existing.tier != int(tier):
            existing.tier = int(tier)
    db.commit()


def auto_join_default_staff(db: Session, user_email: str) -> None:
    """Add user to Default Staff group (idempotent)."""
    existing = (db.query(UserGroupMembership)
                  .filter_by(user_email=user_email,
                             group_id=DEFAULT_STAFF_GROUP_ID)
                  .first())
    if existing is None:
        db.add(UserGroupMembership(
            user_email=user_email, group_id=DEFAULT_STAFF_GROUP_ID,
        ))
        db.commit()
```

- [ ] **Step 4: Wire auto-join into the auth flow**

In `backend/app/routers/auth.py`, inside `get_current_user`'s user-creation branch (where the new `User` row is added), after `db.commit()`:

```python
from app.services.default_staff_group import (
    ensure_default_staff_group, auto_join_default_staff,
)
ensure_default_staff_group(db)
auto_join_default_staff(db, email)
```

- [ ] **Step 5: Run tests and verify they pass**

Run: `pytest tests/test_default_staff.py -v && pytest tests/test_auth_user_upsert.py -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/default_staff_group.py backend/app/routers/auth.py backend/tests/test_default_staff.py
git commit -m "feat(perms): Default Staff group + auto-join on first login"
```

---

## Phase 2 — Translate existing groups + seed live data

### Task 9: One-shot translation script

**Files:**
- Create: `backend/scripts/migrate/translate_perms_to_tiers.py`
- Create: `backend/scripts/migrate/perm_to_tier_map.py`

- [ ] **Step 1: Define the canonical permission → tier map**

```python
# backend/scripts/migrate/perm_to_tier_map.py
"""Single source of truth for the Phase 2 translation.

Each old PERMISSIONS string maps to one or more (Module, Tier) targets.
Listed in the design spec §Migration / Translation cheat-sheet.
"""
from app.permissions.catalog import Module, Tier


# Old permission string → list of (Module, Tier) it implies.
# A user that holds the old string ends up granted at least the listed tier
# on each module (max wins via the resolver).
PERM_TO_TIER: dict[str, list[tuple[Module, Tier]]] = {
    "patient:read":          [(Module.CHART, Tier.VIEW)],
    "patient:create":        [(Module.CHART, Tier.WORK)],
    "patient:edit":          [(Module.CHART, Tier.WORK)],
    "chart:read":            [(Module.CHART, Tier.VIEW)],
    "chart:edit":            [(Module.CHART, Tier.WORK)],
    "chart:sign":            [(Module.CHART, Tier.WORK)],
    "document:read":         [(Module.CHART, Tier.VIEW),
                              (Module.INSURANCE_DOCS, Tier.VIEW)],
    "document:upload":       [(Module.CHART, Tier.WORK),
                              (Module.INSURANCE_DOCS, Tier.WORK)],
    "document:delete":       [(Module.CHART, Tier.MANAGE),
                              (Module.INSURANCE_DOCS, Tier.MANAGE)],
    "intake:read":           [(Module.CHART, Tier.VIEW)],
    "intake:edit":           [(Module.CHART, Tier.WORK)],
    "fax:read":              [],   # collapsed — gated by owning module
    "fax:send":              [],   # collapsed — gated by owning module
    "claim:read":            [(Module.ACTIVE_AR, Tier.VIEW)],
    "claim:edit":            [(Module.ACTIVE_AR, Tier.WORK)],
    "claim:writeoff":        [(Module.ACTIVE_AR, Tier.MANAGE)],
    "payment:post":          [(Module.ACTIVE_AR, Tier.WORK)],
    "payment:void":          [(Module.ACTIVE_AR, Tier.MANAGE)],
    "bankrecon:read":        [(Module.BANK_RECON, Tier.VIEW)],
    "bankrecon:generate":    [(Module.BANK_RECON, Tier.WORK)],
    "audit:read":            [(Module.AUDIT_LOG, Tier.VIEW)],
    "report:financial":      [(Module.ACTIVE_AR, Tier.MANAGE)],
    "surgery:read":          [(Module.SURGERY, Tier.VIEW)],
    "surgery:work":          [(Module.SURGERY, Tier.WORK)],
    "larc:read":             [(Module.LARC, Tier.VIEW)],
    "larc:edit":             [(Module.LARC, Tier.WORK)],
    "larc:manage":           [(Module.LARC, Tier.MANAGE)],
    "pellet:read":           [(Module.PELLETS, Tier.VIEW)],
    "pellet:work":           [(Module.PELLETS, Tier.WORK)],
    "pellet:manage":         [(Module.PELLETS, Tier.MANAGE)],
    "user:manage":           [],   # see ADMIN_PERMS below
}

# Old permission strings whose holders should become per-module Admins.
# user:manage is the closest analog; translates to ADMIN on every module
# (the system-admin role).
ADMIN_PERMS: set[str] = {"user:manage"}
```

- [ ] **Step 2: Write the translation script**

```python
# backend/scripts/migrate/translate_perms_to_tiers.py
"""Phase 2 one-shot.

For every existing Group:
  1. Read the union of perms held by the group (group_permissions).
  2. Translate each perm via PERM_TO_TIER → set of (Module, Tier).
  3. For each Module, write the MAX tier into group_module_tiers.
  4. If the group holds any ADMIN_PERMS, additionally grant ADMIN on every
     module (and flag the group as the system-admin group — log only,
     no enum needed).

For every active User with permissions_extra entries:
  1. Translate each extra perm → list of (Module, Tier).
  2. For each Module, write the MAX tier as a user override IFF it would
     exceed what their group memberships already grant.

Also:
  - Ensures the Default Staff group exists with its baseline grants.
  - Auto-joins every active user to Default Staff.

Idempotent. Run via:
    cd backend && python -m scripts.migrate.translate_perms_to_tiers
"""
from collections import defaultdict

from app.database import SessionLocal
from app.models.group import Group, GroupPermission
from app.models.module_tier import GroupModuleTier, UserModuleOverride
from app.models.user import User
from app.permissions.catalog import Module, Tier
from app.permissions.resolver import effective_tier
from app.services.default_staff_group import (
    auto_join_default_staff, ensure_default_staff_group,
)

from .perm_to_tier_map import ADMIN_PERMS, PERM_TO_TIER


def translate_group(db, group: Group) -> None:
    perms = {gp.permission for gp in
             db.query(GroupPermission).filter_by(group_id=group.id).all()}
    by_module: dict[Module, int] = defaultdict(int)
    for perm in perms:
        for module, tier in PERM_TO_TIER.get(perm, []):
            by_module[module] = max(by_module[module], int(tier))
        if perm in ADMIN_PERMS:
            for m in Module:
                by_module[m] = max(by_module[m], int(Tier.ADMIN))
    for module, tier in by_module.items():
        existing = (db.query(GroupModuleTier)
                      .filter_by(group_id=group.id, module=module.value)
                      .first())
        if existing is None:
            db.add(GroupModuleTier(group_id=group.id, module=module.value,
                                    tier=tier))
        elif existing.tier < tier:
            existing.tier = tier
    db.commit()


def translate_user_extras(db, user: User) -> None:
    extras = user.permissions_extra or []
    if not extras:
        return
    by_module: dict[Module, int] = defaultdict(int)
    for perm in extras:
        for module, tier in PERM_TO_TIER.get(perm, []):
            by_module[module] = max(by_module[module], int(tier))
        if perm in ADMIN_PERMS:
            for m in Module:
                by_module[m] = max(by_module[m], int(Tier.ADMIN))
    for module, tier in by_module.items():
        # Only override if this would grant more than groups already give.
        if int(effective_tier(db, user.email, module)) >= tier:
            continue
        existing = (db.query(UserModuleOverride)
                      .filter_by(user_email=user.email, module=module.value)
                      .first())
        if existing is None:
            db.add(UserModuleOverride(
                user_email=user.email, module=module.value, tier=tier,
                added_by="system:phase2_migration",
            ))
        elif existing.tier < tier:
            existing.tier = tier
    db.commit()


def main():
    db = SessionLocal()
    try:
        ensure_default_staff_group(db)
        for group in db.query(Group).all():
            translate_group(db, group)
        for user in db.query(User).filter_by(is_active=True).all():
            auto_join_default_staff(db, user.email)
            translate_user_extras(db, user)
        print("Phase 2 translation complete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Dry-run the script locally against a Cloud SQL snapshot**

This step is run manually after the script is committed and ready. Process:

```
1. Take a SQL dump of production app-db (read-only, public IP closed after).
2. Restore into a local Postgres database named wwc_app_phase2_dryrun.
3. Set DATABASE_URL to that local DB.
4. Run:  cd backend && python -m scripts.migrate.translate_perms_to_tiers
5. Inspect group_module_tiers + user_module_overrides for plausibility.
6. Show the diff to Oliver for sign-off before running against production.
```

(This step has no automated test — it's a human review checkpoint. Document any surprises before proceeding to Step 4.)

- [ ] **Step 4: Run on production once approved**

When sign-off received:

```
gcloud sql instances patch app-db --assign-ip --project=wwc-solutions --quiet
# (wait for IP)
cd backend && python -m scripts.migrate.translate_perms_to_tiers
gcloud sql instances patch app-db --no-assign-ip --project=wwc-solutions --quiet
```

- [ ] **Step 5: Commit (script only — the run is an operational step)**

```bash
git add backend/scripts/migrate/perm_to_tier_map.py backend/scripts/migrate/translate_perms_to_tiers.py
git commit -m "migrate(perms): Phase 2 — translate verbs → tiers + seed Default Staff"
```

---

## Phase 3 — Per-module cutovers (in this order)

Each cutover task replaces `require_permission("verb:resource")` calls in one module's router with `requires_tier(Module.X, Tier.Y)` and replaces the router-level dependency in `main.py`. Deploy after each task and smoke-test the module before moving on.

### Task 10: Cutover — Audit Log

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/routers/audit.py`

- [ ] **Step 1: Replace router-level gate in main.py**

Find: `app.include_router(audit.router, prefix="/api", dependencies=AUDIT_READ)`

Replace with:
```python
app.include_router(audit.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.AUDIT_LOG, Tier.VIEW))])
```

Add the imports near the top of `main.py`:
```python
from app.permissions.catalog import Module, Tier
from app.permissions.dependencies import requires_tier
```

- [ ] **Step 2: Smoke-test locally**

Run: `cd backend && pytest tests/test_admin_audit.py -v` (or whatever test covers /api/audit)
Expected: passes.

- [ ] **Step 3: Deploy + verify**

```bash
gcloud builds submit --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:perms-cutover-audit --project=wwc-solutions
gcloud run deploy backend --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:perms-cutover-audit --region=us-east4 --project=wwc-solutions --quiet
```

After deploy, verify with a Super Admin token: `GET /api/audit?per_page=5` → 200. With a no-grant token: 403.

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "cutover(perms): Audit Log → Tier.VIEW gate"
```

---

### Task 11: Cutover — Reputation Management

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/routers/reputation*.py` (whichever file owns the staff side)

- [ ] **Step 1: Inventory the existing require_permission calls in the reputation routers**

```bash
grep -n require_permission backend/app/routers/reputation*.py
```

- [ ] **Step 2: For each match, replace with the appropriate Tier**

Pattern:
- Read endpoints (`GET /api/reputation/...`) → `requires_tier(Module.REPUTATION, Tier.VIEW)`
- Write endpoints (`POST/PATCH/DELETE`) → `requires_tier(Module.REPUTATION, Tier.WORK)`
- Config endpoints (template edits, portal settings) → `requires_tier(Module.REPUTATION, Tier.MANAGE)`

- [ ] **Step 3: Update the router-level include in main.py**

Replace any existing `dependencies=[...]` for the reputation staff router with:
```python
dependencies=[Depends(requires_tier(Module.REPUTATION, Tier.VIEW))]
```

(The token-gated reputation public endpoints under `/api/r/...` are already allowlisted in `route_perm_catalog`; no change.)

- [ ] **Step 4: Run reputation-specific tests + smoke test in browser**

```bash
pytest tests/test_reputation_*.py -v
```

- [ ] **Step 5: Deploy + verify; commit**

```bash
gcloud builds submit --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:perms-cutover-reputation --project=wwc-solutions
gcloud run deploy backend --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:perms-cutover-reputation --region=us-east4 --project=wwc-solutions --quiet
git add backend/app/main.py backend/app/routers/reputation*.py
git commit -m "cutover(perms): Reputation Management → tier gates"
```

---

### Task 12: Cutover — Training

Same pattern as Task 11 but for the training router.

**Files:** `backend/app/main.py`, `backend/app/routers/training.py`

- [ ] **Step 1: Replace each `require_permission(...)` in `training.py`**

- Reads → `requires_tier(Module.TRAINING, Tier.VIEW)`
- "I'm taking this training" actions → `requires_tier(Module.TRAINING, Tier.WORK)`
- Author/assign actions → `requires_tier(Module.TRAINING, Tier.MANAGE)`

- [ ] **Step 2: Update router-level include in main.py**

```python
app.include_router(training.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.TRAINING, Tier.VIEW))])
```

- [ ] **Step 3: Tests, deploy, commit**

```bash
pytest tests/test_training*.py -v
gcloud builds submit --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:perms-cutover-training --project=wwc-solutions
gcloud run deploy backend --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:perms-cutover-training --region=us-east4 --project=wwc-solutions --quiet
git add backend/app/main.py backend/app/routers/training.py
git commit -m "cutover(perms): Training → tier gates"
```

---

### Task 13: Cutover — My Checklist

**Files:** `backend/app/main.py`, `backend/app/routers/personal_tasks.py`

- [ ] **Step 1: Read each endpoint and decide whether "own" or "others"**

For My Checklist, the tier semantics in the spec say own-list access is implicit. So:
- Endpoints scoped to the current user's own checklist → no `requires_tier` (auth alone is enough)
- Endpoints that read/touch other users' checklists → `requires_tier(Module.MY_CHECKLIST, Tier.VIEW)` for reads, `Tier.WORK` for assign-to-others
- Recurring-task / template config endpoints → `requires_tier(Module.MY_CHECKLIST, Tier.MANAGE)`

- [ ] **Step 2: Update router-level include — `AUTH_ONLY` is fine here**

The router-level dependency stays `AUTH_ONLY` because own-checklist access is implicit for any authenticated user. Per-endpoint `requires_tier` calls handle cross-user actions.

- [ ] **Step 3: Tests, deploy, commit**

```bash
pytest tests/test_personal_tasks*.py -v
# build + deploy as above
git add backend/app/main.py backend/app/routers/personal_tasks.py
git commit -m "cutover(perms): My Checklist → tier gates (cross-user only)"
```

---

### Task 14: Cutover — Recall

**Files:** `backend/app/main.py`, `backend/app/routers/recalls.py`, `backend/app/routers/recall_filter_presets.py`

- [ ] **Step 1: Replace each require_permission**

- Reads (`GET /api/recalls...`) → `requires_tier(Module.RECALL, Tier.VIEW)`
- Marking calls, status updates → `requires_tier(Module.RECALL, Tier.WORK)`
- Recall-rule config, bulk-schedule → `requires_tier(Module.RECALL, Tier.MANAGE)`

- [ ] **Step 2: Update both router-level includes**

```python
app.include_router(recalls.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.RECALL, Tier.VIEW))])
app.include_router(recall_filter_presets.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.RECALL, Tier.VIEW))])
```

- [ ] **Step 3: Tests, deploy, commit**

```bash
pytest tests/test_recall*.py -v
git add backend/app/main.py backend/app/routers/recalls.py backend/app/routers/recall_filter_presets.py
git commit -m "cutover(perms): Recall → tier gates"
```

---

### Task 15: Cutover — Bank Recon

**Files:** `backend/app/main.py`, `backend/app/routers/bank_recon.py`

- [ ] **Step 1: Replace each require_permission**

Today's `bankrecon:read` → `requires_tier(Module.BANK_RECON, Tier.VIEW)`
Today's `bankrecon:generate` → `requires_tier(Module.BANK_RECON, Tier.WORK)`
Any config/admin endpoints → `requires_tier(Module.BANK_RECON, Tier.MANAGE)`

- [ ] **Step 2: Update router include**

Replace `dependencies=BANKRECON_READ` with:
```python
dependencies=[Depends(requires_tier(Module.BANK_RECON, Tier.VIEW))]
```

- [ ] **Step 3: Tests, deploy, commit**

```bash
pytest tests/test_bank_recon*.py -v
git add backend/app/main.py backend/app/routers/bank_recon.py
git commit -m "cutover(perms): Bank Recon → tier gates"
```

---

### Task 16: Cutover — Missing Charges

**Files:** `backend/app/main.py`, `backend/app/routers/missing_charges.py`

- [ ] **Step 1: Replace each require_permission**

- Reads → `requires_tier(Module.MISSING_CHARGES, Tier.VIEW)`
- Add charge, mark complete → `requires_tier(Module.MISSING_CHARGES, Tier.WORK)`
- Issue provider portal token, delete, bulk-complete → `requires_tier(Module.MISSING_CHARGES, Tier.MANAGE)`

Token-gated provider endpoints (`/api/billing/missing-charges/provider/{token}/...`) stay in the public allowlist — no change.

- [ ] **Step 2: Update router include**

```python
app.include_router(missing_charges.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.MISSING_CHARGES, Tier.VIEW))])
```

- [ ] **Step 3: Tests, deploy, commit**

```bash
pytest tests/test_missing_charges*.py -v
git add backend/app/main.py backend/app/routers/missing_charges.py
git commit -m "cutover(perms): Missing Charges → tier gates"
```

---

### Task 17: Cutover — Insurance Contacts

**Files:** `backend/app/main.py`, `backend/app/routers/insurance_contacts.py`

- [ ] **Step 1: Replace each require_permission**

- Reads → `requires_tier(Module.INSURANCE_CONTACTS, Tier.VIEW)`
- Add/edit contact → `requires_tier(Module.INSURANCE_CONTACTS, Tier.WORK)`
- Bulk import, delete, source-rule config → `requires_tier(Module.INSURANCE_CONTACTS, Tier.MANAGE)`

- [ ] **Step 2: Update router include**

```python
app.include_router(insurance_contacts.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.INSURANCE_CONTACTS, Tier.VIEW))])
```

- [ ] **Step 3: Tests, deploy, commit**

```bash
pytest tests/test_insurance_contact*.py -v
git add backend/app/main.py backend/app/routers/insurance_contacts.py
git commit -m "cutover(perms): Insurance Contacts → tier gates"
```

---

### Task 18: Cutover — Insurance Documents

**Files:** `backend/app/main.py`, `backend/app/routers/billing_documents.py`

- [ ] **Step 1: Replace each require_permission**

- Reads, list/detail/download → `requires_tier(Module.INSURANCE_DOCS, Tier.VIEW)`
- Upload, classify, assign, work, note → `requires_tier(Module.INSURANCE_DOCS, Tier.WORK)`
- DELETE `/{doc_id}`, bulk operations → `requires_tier(Module.INSURANCE_DOCS, Tier.MANAGE)`

- [ ] **Step 2: Update router include**

Replace `dependencies=[]` (or whatever's there now) with:
```python
app.include_router(billing_documents.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.INSURANCE_DOCS, Tier.VIEW))])
```

- [ ] **Step 3: Tests, deploy, commit**

```bash
pytest tests/test_billing_doc*.py -v
git add backend/app/main.py backend/app/routers/billing_documents.py
git commit -m "cutover(perms): Insurance Documents → tier gates"
```

---

### Task 19: Cutover — Device Tracking (LARC + Office Procedures)

**Files:** `backend/app/main.py`, `backend/app/routers/larc.py`

- [ ] **Step 1: Replace each require_permission in larc.py**

- `larc:read` → `requires_tier(Module.LARC, Tier.VIEW)`
- `larc:edit` → `requires_tier(Module.LARC, Tier.WORK)`
- `larc:manage` → `requires_tier(Module.LARC, Tier.MANAGE)`

Office Procedures endpoints (in the same router) use `Module.OFFICE_PROCEDURES`:
- Same tier mapping — `:read` → VIEW, `:edit` → WORK, `:manage` → MANAGE.

(If LARC + Office Procedures share endpoints today, leave a TODO comment in the route header noting that these two modules currently share a router; future task may split them.)

- [ ] **Step 2: Update router include**

```python
app.include_router(larc.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.LARC, Tier.VIEW))])
```

(The Office Procedures router-level gate is per-endpoint inside the same router today.)

- [ ] **Step 3: Tests, deploy, commit**

```bash
pytest tests/test_larc*.py -v
git add backend/app/main.py backend/app/routers/larc.py
git commit -m "cutover(perms): Device Tracking - LARC + Office Procedures → tier gates"
```

---

### Task 20: Cutover — Pellets

**Files:** `backend/app/main.py`, `backend/app/routers/pellet.py`

- [ ] **Step 1: Replace each require_permission in pellet.py**

- `pellet:read` → `requires_tier(Module.PELLETS, Tier.VIEW)`
- `pellet:work` → `requires_tier(Module.PELLETS, Tier.WORK)`
- `pellet:manage` → `requires_tier(Module.PELLETS, Tier.MANAGE)`

- [ ] **Step 2: Update router include**

```python
app.include_router(pellet.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.PELLETS, Tier.VIEW))])
```

- [ ] **Step 3: Tests, deploy, commit**

```bash
pytest tests/test_pellet*.py -v
git add backend/app/main.py backend/app/routers/pellet.py
git commit -m "cutover(perms): Pellets → tier gates"
```

---

### Task 21: Cutover — Chart

**Files:** `backend/app/main.py`, `backend/app/routers/chart.py`, `backend/app/routers/patients.py`, `backend/app/routers/documents.py`, `backend/app/routers/intake.py`

This module spans multiple routers (Chart bundle).

- [ ] **Step 1: Replace each require_permission across the four routers**

In `patients.py`:
- `require_permission("patient:read")` → `requires_tier(Module.CHART, Tier.VIEW)` (was already router-level; just swap the include)
- `require_permission("patient:create")` → `requires_tier(Module.CHART, Tier.WORK)`
- `require_permission("patient:edit")` → `requires_tier(Module.CHART, Tier.WORK)`

In `chart.py`:
- All endpoint-level guards swap from `chart:*` to the corresponding Tier on `Module.CHART`.
- `chart:sign` stays at `Tier.WORK` but keep the additional provider-role check (see spec — implement as a separate `Depends` if not already present).

In `documents.py`:
- `document:read` → `requires_tier(Module.CHART, Tier.VIEW)`
- `document:upload` → `requires_tier(Module.CHART, Tier.WORK)`
- `document:delete` → `requires_tier(Module.CHART, Tier.MANAGE)`
- `user:manage` admin endpoints → see Task 23

In `intake.py`:
- `intake:read` → `requires_tier(Module.CHART, Tier.VIEW)`
- `intake:edit` → `requires_tier(Module.CHART, Tier.WORK)`
- `user:manage` admin endpoints → see Task 23

- [ ] **Step 2: Update all four router-level includes in main.py**

```python
app.include_router(patients.router,  prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(chart.router,     prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(documents.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
app.include_router(intake.router,    prefix="/api",
                   dependencies=[Depends(requires_tier(Module.CHART, Tier.VIEW))])
```

- [ ] **Step 3: Tests, deploy, smoke-test all 4 routers**

```bash
pytest tests/test_chart*.py tests/test_patient*.py tests/test_document*.py tests/test_intake*.py -v
```

Smoke-test via the staff UI: log in as a user with Chart View; verify chart loads, can't edit; log in as Chart Work; verify can edit but can't delete.

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py backend/app/routers/chart.py backend/app/routers/patients.py backend/app/routers/documents.py backend/app/routers/intake.py
git commit -m "cutover(perms): Chart bundle → Module.CHART tier gates"
```

---

### Task 22: Cutover — Surgery

**Files:** `backend/app/main.py`, `backend/app/routers/surgery.py`, `backend/app/routers/surgery_config.py`, `backend/app/routers/surgery_filter_presets.py`, `backend/app/routers/consent_templates.py`, `backend/app/routers/boldsign.py`, `backend/app/routers/checklist.py`

The largest router family. Take it slow — one PR can be split into two if needed.

- [ ] **Step 1: For each router, replace `require_permission("surgery:read")` → `requires_tier(Module.SURGERY, Tier.VIEW)`**

- [ ] **Step 2: For each router, replace `require_permission("surgery:work")` → `requires_tier(Module.SURGERY, Tier.WORK)`**

- [ ] **Step 3: Identify Manage endpoints**

These belong on `Tier.MANAGE`:
- `surgery_config.router` — fee schedule, surgery types, procedure templates (entire router)
- `consent_templates.router` — consent template CRUD (entire router)
- `surgery.router` block schedule admin endpoints (`/admin/block-schedules*`)
- `surgery.router` consent admin (`/admin/consent/boldsign-sync/{surgery_id}`)
- DELETE endpoints anywhere in surgery.router

For each of these, add an explicit `Depends(requires_tier(Module.SURGERY, Tier.MANAGE))` on the endpoint.

- [ ] **Step 4: Update all router-level includes in main.py**

```python
app.include_router(surgery.router,                prefix="/api",
                   dependencies=[Depends(requires_tier(Module.SURGERY, Tier.VIEW))])
app.include_router(surgery_config.router,         prefix="/api",
                   dependencies=[Depends(requires_tier(Module.SURGERY, Tier.VIEW))])
app.include_router(surgery_filter_presets.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.SURGERY, Tier.VIEW))])
app.include_router(consent_templates.router,      prefix="/api",
                   dependencies=[Depends(requires_tier(Module.SURGERY, Tier.VIEW))])
app.include_router(boldsign.router,               prefix="/api",
                   dependencies=[Depends(requires_tier(Module.SURGERY, Tier.VIEW))])
app.include_router(checklist.router,              prefix="/api",
                   dependencies=[Depends(requires_tier(Module.SURGERY, Tier.VIEW))])
```

Patient-facing routers (`patient_surgery.router`, `patient_portal.router`) stay token-gated — no change.

- [ ] **Step 5: Tests, deploy, smoke-test all surgery surfaces**

```bash
pytest tests/test_surgery*.py tests/test_consent*.py tests/test_boldsign*.py -v
```

Test against staging or production with three users: Surgery View, Surgery Work, Surgery Manage. Verify the right buttons appear / 403 fires correctly.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/app/routers/surgery.py backend/app/routers/surgery_config.py backend/app/routers/surgery_filter_presets.py backend/app/routers/consent_templates.py backend/app/routers/boldsign.py backend/app/routers/checklist.py
git commit -m "cutover(perms): Surgery family → Module.SURGERY tier gates"
```

---

### Task 23: Cutover — Active AR

**Files:** `backend/app/main.py`, `backend/app/routers/active_ar.py`, `backend/app/routers/active_ar_filter_presets.py`, `backend/app/routers/claims.py`, `backend/app/routers/denials.py`, `backend/app/routers/appeals.py`, `backend/app/routers/eob.py`, `backend/app/routers/era_posting.py`, `backend/app/routers/waystar.py`, `backend/app/routers/imports.py`, `backend/app/routers/charge_imports.py`, `backend/app/routers/claim_id_bootstrap.py`, `backend/app/routers/service_lines.py`, `backend/app/routers/claim_adjustments.py`, `backend/app/routers/service_line_adjustments.py`, `backend/app/routers/adjustment_codes.py`, `backend/app/routers/transaction_detail_imports.py`, `backend/app/routers/dashboard.py`, `backend/app/routers/stripe_payments.py` (partial — surgery payments stay under Surgery), `backend/app/routers/ar.py`

The biggest cutover. Money-sensitive. Suggested approach: split into 2 sub-tasks if the diff is too large to review.

- [ ] **Step 1: Across all listed routers, replace `claim:read` / `claim:edit` / `claim:writeoff` / `payment:post` / `payment:void`**

- `claim:read`     → `requires_tier(Module.ACTIVE_AR, Tier.VIEW)`
- `claim:edit`     → `requires_tier(Module.ACTIVE_AR, Tier.WORK)`
- `claim:writeoff` → `requires_tier(Module.ACTIVE_AR, Tier.MANAGE)`
- `payment:post`   → `requires_tier(Module.ACTIVE_AR, Tier.WORK)`
- `payment:void`   → `requires_tier(Module.ACTIVE_AR, Tier.MANAGE)`
- `report:financial` (in `dashboard.py`) → `requires_tier(Module.ACTIVE_AR, Tier.MANAGE)`

Stripe payments split:
- Surgery-flow payments (`POST /surgery/{id}/request-payment`, `POST /surgery/payments/{id}/refund`, `GET /surgery/{id}/payments`) → `requires_tier(Module.SURGERY, Tier.WORK)` for request, `Tier.MANAGE` for refund
- Webhook stays signature-gated — no tier change

- [ ] **Step 2: Update all router-level includes in main.py (huge block — be methodical)**

Replace every `dependencies=BILLING_READ` with:
```python
dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.VIEW))]
```

Specifically: imports, claims, service_lines, claim_adjustments, service_line_adjustments, charge_imports, claim_id_bootstrap, era_posting, ar, denials, appeals, eob, waystar, active_ar, active_ar_filter_presets, adjustment_codes, transaction_detail_imports.

The dashboard router moves to MANAGE tier:
```python
app.include_router(dashboard.router, prefix="/api",
                   dependencies=[Depends(requires_tier(Module.ACTIVE_AR, Tier.MANAGE))])
```

- [ ] **Step 3: Admin endpoints in `documents.py` and `intake.py`**

In Task 21 we deferred `user:manage` admin endpoints. Update now:
- `POST /documents/index` → `requires_tier(Module.CHART, Tier.MANAGE)`
- `POST /chart/import-clinical` → `requires_tier(Module.CHART, Tier.MANAGE)`
- `POST /intake/build-directory`, `/intake/index` → `requires_tier(Module.CHART, Tier.MANAGE)`

- [ ] **Step 4: Tests, deploy, smoke-test**

```bash
pytest tests/test_active_ar*.py tests/test_claim*.py tests/test_denial*.py tests/test_appeal*.py tests/test_era*.py -v
```

Smoke-test:
- A Billing Coders user (Active AR Work) can view + post payments, can't write off
- An Active AR Manage user can write off + see dashboard
- A non-billing user 403s on `/api/claims`, `/api/denials`, `/api/dashboard/summary`

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/app/routers/active_ar.py backend/app/routers/claims.py backend/app/routers/denials.py backend/app/routers/appeals.py backend/app/routers/eob.py backend/app/routers/era_posting.py backend/app/routers/waystar.py backend/app/routers/imports.py backend/app/routers/charge_imports.py backend/app/routers/claim_id_bootstrap.py backend/app/routers/service_lines.py backend/app/routers/claim_adjustments.py backend/app/routers/service_line_adjustments.py backend/app/routers/adjustment_codes.py backend/app/routers/transaction_detail_imports.py backend/app/routers/dashboard.py backend/app/routers/stripe_payments.py backend/app/routers/ar.py backend/app/routers/active_ar_filter_presets.py backend/app/routers/documents.py backend/app/routers/intake.py
git commit -m "cutover(perms): Active AR family → Module.ACTIVE_AR tier gates"
```

---

## Frontend (admin grid)

### Task 24: Frontend admin tier grid component

**Files:**
- Create: `frontend/src/pages/admin/UserTierGrid.jsx`
- Create: `frontend/src/pages/admin/GroupTierGrid.jsx`
- Modify: `frontend/src/pages/AdminUsers.jsx` (add link/route to grid)
- Modify: `frontend/src/pages/AdminGroups.jsx` (add link/route to grid)

- [ ] **Step 1: Create the per-user grid page**

```jsx
// frontend/src/pages/admin/UserTierGrid.jsx
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../../utils/api'

const TIERS = [
  { value: 'view',   label: 'View'   },
  { value: 'work',   label: 'Work'   },
  { value: 'manage', label: 'Manage' },
  { value: 'admin',  label: 'Admin'  },
  { value: 'denied', label: 'Denied' },
]

export default function UserTierGrid() {
  const { email } = useParams()
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['user-tiers', email],
    queryFn: () => api.get(`/admin/users/${email}/tiers`).then(r => r.data),
  })

  const set = useMutation({
    mutationFn: ({ module, tier }) =>
      api.put(`/admin/users/${email}/overrides/${module}`, { tier }),
    onSuccess: () => qc.invalidateQueries(['user-tiers', email]),
  })

  if (isLoading) return <div>Loading…</div>

  const onClick = (entry, choice) => {
    if (entry.tier === choice && entry.source_kind === 'override') {
      // Clicking the currently-active override clears it
      set.mutate({ module: entry.module, tier: null })
    } else {
      set.mutate({ module: entry.module, tier: choice })
    }
  }

  return (
    <div className="p-4">
      <h1 className="text-xl font-bold mb-3">Permissions — {email}</h1>
      <table className="w-full text-sm border-collapse">
        <thead className="bg-gray-50">
          <tr>
            <th className="text-left p-2">Module</th>
            {TIERS.map(t => (
              <th key={t.value} className="p-2 text-center">{t.label}</th>
            ))}
            <th className="p-2 text-left">Source</th>
          </tr>
        </thead>
        <tbody>
          {data?.tiers?.map(entry => (
            <tr key={entry.module} className="border-t">
              <td className="p-2">{entry.label}</td>
              {TIERS.map(t => (
                <td key={t.value} className="text-center p-2">
                  <button
                    onClick={() => onClick(entry, t.value)}
                    aria-label={`Set ${entry.label} to ${t.label}`}
                    className={
                      entry.tier === t.value
                        ? 'inline-block w-3 h-3 rounded-full bg-plum-700'
                        : 'inline-block w-3 h-3 rounded-full border border-gray-300 hover:bg-plum-100'
                    }
                  />
                </td>
              ))}
              <td className="p-2 text-xs text-gray-600">
                {entry.source_kind === 'override' && 'Override'}
                {entry.source_kind === 'group' && entry.source_label}
                {entry.source_kind === 'super_admin' && 'Super Admin'}
                {entry.source_kind === 'none' && '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 2: Create the per-group grid page**

(Mostly identical, but without the "Source" column and pointing at `/admin/groups/{group_id}/tiers`.)

```jsx
// frontend/src/pages/admin/GroupTierGrid.jsx
// Same structure as UserTierGrid but:
// - Read: GET /admin/groups/{group_id}/tiers
// - Write: PUT /admin/groups/{group_id}/tiers/{module}
// - No source column (the group IS the source)
// - Same "click active to clear" behavior
```

- [ ] **Step 3: Route wiring**

In `frontend/src/router.jsx` (or wherever the routes are defined), add:
```jsx
<Route path="/admin/users/:email/tiers" element={<UserTierGrid />} />
<Route path="/admin/groups/:groupId/tiers" element={<GroupTierGrid />} />
```

In `AdminUsers.jsx`, for each user row, add a "Permissions" link → `/admin/users/{email}/tiers`.
In `AdminGroups.jsx`, for each group row, add a "Permissions" link → `/admin/groups/{group_id}/tiers`.

- [ ] **Step 4: Test in browser**

- As Super Admin, navigate to `/admin/users/apetit@waldorfwomenscare.com/tiers`. Verify the grid loads with the expected tier per module + Source column.
- Click a different tier on a row → row updates, Source shows "Override".
- Click the same override marker again → falls back to group default.
- Verify Audit Log has rows for each grant change.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/admin/UserTierGrid.jsx frontend/src/pages/admin/GroupTierGrid.jsx frontend/src/pages/AdminUsers.jsx frontend/src/pages/AdminGroups.jsx frontend/src/router.jsx
git commit -m "feat(perms): admin tier grid (per-user + per-group)"
```

---

## Phase 4 — Cleanup

### Task 25: Remove legacy PERMISSIONS catalog + verb-based gates

**Files:**
- Modify: `backend/app/services/permissions.py` (delete most of it)
- Modify: `backend/app/routers/auth.py` (delete `require_permission` + `effective_permissions`)
- Modify: `backend/app/main.py` (delete `BILLING_READ`, `AUDIT_READ`, etc. — already unused)
- Modify: `backend/tests/conftest.py` (drop `_TEST_USER_PERMS`)

- [ ] **Step 1: Grep for remaining references**

```bash
grep -rn "require_permission\|PERMISSIONS\[" backend/app/ backend/tests/
```

Expected: only references inside `permissions.py` itself + `auth.py`'s definition + the transitional conftest block. If any router still uses `require_permission(...)`, that module's cutover (Phase 3) was incomplete — go back and finish it.

- [ ] **Step 2: Delete the verb-based catalog and helper**

Delete from `backend/app/services/permissions.py`:
- `PERMISSIONS` dict
- `ALL_PERMISSIONS`
- `effective_permissions()` function
- Group-permission seed helpers

Delete from `backend/app/routers/auth.py`:
- `require_permission()` factory

Delete from `backend/app/main.py`:
- `BILLING_READ`, `AUDIT_READ`, `BANKRECON_READ`, `REPORT_FINANCIAL`, `AUTH_ONLY`, `USER_MANAGE` constants (they reference `require_permission` and are unused after Phase 3)
- `PATIENT_READ`, `DOCUMENT_READ`, `CHART_READ`, `INTAKE_READ`, `FAX_READ` constants (also unused — every `include_router` now uses `requires_tier(...)`)

Delete the transitional `_TEST_USER_PERMS` block from `backend/tests/conftest.py`.

- [ ] **Step 3: Run the full backend test suite**

Run: `cd backend && pytest -q`
Expected: all green. Any test that previously relied on `_TEST_USER_PERMS` needs to be updated to use `client_factory` + a User with appropriate group/super_admin tier; failures here are scope leaks from earlier cutover tasks.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/permissions.py backend/app/routers/auth.py backend/app/main.py backend/tests/conftest.py
git commit -m "chore(perms): remove legacy PERMISSIONS + require_permission"
```

---

### Task 26: Drop legacy tables + columns

**Files:**
- Create: `backend/scripts/migrate/drop_legacy_perms_schema.py`
- Modify: `backend/app/models/group.py` (remove the `GroupPermission` relationship)
- Modify: `backend/app/models/user.py` (drop `permissions_extra`, `permissions_revoked` columns)

- [ ] **Step 1: Write a one-shot SQL script**

```python
# backend/scripts/migrate/drop_legacy_perms_schema.py
"""Phase 4 — drop the legacy permissions schema.

Run once after Task 25 ships and is verified in production.
After this runs, downgrade is no longer possible without restoring from
a backup, so take a snapshot first.

Run via:
    cd backend && python -m scripts.migrate.drop_legacy_perms_schema
"""
from sqlalchemy import text

from app.database import engine


def main():
    statements = [
        "DROP TABLE IF EXISTS group_permissions",
        "ALTER TABLE users DROP COLUMN IF EXISTS permissions_extra",
        "ALTER TABLE users DROP COLUMN IF EXISTS permissions_revoked",
    ]
    with engine.begin() as conn:
        for stmt in statements:
            print(f"-- {stmt}")
            conn.execute(text(stmt))
    print("Legacy permissions schema dropped.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Take a snapshot of production app-db**

```
gcloud sql backups create --instance=app-db --project=wwc-solutions
```

Wait for the backup to complete and record the backup ID in case rollback is needed.

- [ ] **Step 3: Run the schema-drop script against production**

```
gcloud sql instances patch app-db --assign-ip --project=wwc-solutions --quiet
cd backend && python -m scripts.migrate.drop_legacy_perms_schema
gcloud sql instances patch app-db --no-assign-ip --project=wwc-solutions --quiet
```

- [ ] **Step 4: Remove the now-orphaned model classes/columns**

In `backend/app/models/user.py`, delete:
```python
permissions_extra   = Column(JSON, default=list)
permissions_revoked = Column(JSON, default=list)
```

In `backend/app/models/group.py`, delete the `GroupPermission` class and its relationship from `Group`.

- [ ] **Step 5: Run tests, deploy, commit**

```bash
pytest -q
gcloud builds submit --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:perms-phase4 --project=wwc-solutions
gcloud run deploy backend --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:perms-phase4 --region=us-east4 --project=wwc-solutions --quiet
git add backend/scripts/migrate/drop_legacy_perms_schema.py backend/app/models/user.py backend/app/models/group.py
git commit -m "chore(perms): Phase 4 — drop legacy schema"
```

---

## Done

After Task 26 ships and verifies clean:

- The system runs entirely on the new tier model
- One catalog (`MODULE_REGISTRY`), one resolver (`effective_tier`), one helper (`requires_tier`)
- Admins manage permissions through a single grid UI showing exactly where each grant comes from
- New module onboarding = add an entry to `MODULE_REGISTRY` + use `requires_tier(...)` in the router

The legacy 60+ permission strings + `permissions_extra`/`permissions_revoked` set algebra is gone.
