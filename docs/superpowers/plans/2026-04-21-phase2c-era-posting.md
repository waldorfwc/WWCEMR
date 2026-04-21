# Phase 2c — ERA 835 Payment Posting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two independent two-step upload flows on `/imports` — (1) Claims Analysis bootstrap that links `patient_control_number` on Charge-Analysis-imported claims and creates secondary/tertiary Claim rows, (2) multi-file ERA 835 payment posting with strict `patient_control_number` matching, Payment-row accumulation, denial creation, and reversal flagging. Retire the legacy ERA auto-create path.

**Architecture:** Pure-function services (`claims_analysis_matcher`, `era_poster`) produce structured preview results with no DB writes. Routers (`claim_id_bootstrap`, `era_posting`) own the HTTP layer, session caching, and commit-time writes. Reuses Phase 2b session store (`import_sessions`), HIPAA audit pattern, and frontend state-machine UI.

**Tech Stack:** FastAPI + SQLAlchemy + pytest + pandas/xlrd (backend); React 18 + Vite + Tailwind + React Query v5 (frontend).

**Reference spec:** `docs/superpowers/specs/2026-04-21-phase2c-era-posting-design.md`

---

## Pre-flight notes

- Fixtures already in `backend/tests/conftest.py`: `db`, `client` (admin), `billing_client`, `clinical_client`.
- Existing `era_835.py` parser: `Era835Parser().parse(content: str, filename: str)` → `EraFile` (has `claims`, `check_number`, `check_date`, `check_amount`, `payer_name`, `parse_errors`).
- Existing `import_sessions.py` — `SessionEntry` dataclass with `payload: Any`, `filename`, `file_path`, `user_email`, `created_at`, `expires_at`, `claim_flags: List[Dict]`. Reuse as-is.
- Existing legacy `era_import_service.py` — we reuse `_determine_claim_status`, `_has_real_denials`, `_create_denials`, `CONTRACTUAL_CODES`, `SKIP_DENIAL_CODES` via direct imports in `era_poster.py`.
- Existing `Claim.patient_control_number` — `Column(String(100), nullable=True)`. No migration.
- Existing `Payment` model — `payment_type: PaymentType` enum (use `INSURANCE_PAYMENT`), links to `claim_id` + `era_file_id`.
- `Claim.insurance_order` SAEnum — values `PRIMARY`, `SECONDARY`, `TERTIARY`, `PATIENT`.
- The committer identity on this machine currently resolves to `.(none)` — if `git commit` fails with "Author identity unknown", prefix with `git -c user.email=... -c user.name=...` or have the user set `git config --global`. The user has been told.
- Real fixtures to copy in (Task 1):
  - `/Users/wwcclaudecode/Documents/Claims Analysis Files/Claim Analysis 2026.01.xls` → `backend/tests/fixtures/claim_analysis_2026_01.xls` (1262 rows, 937 unique claims, 911 primary-only + 26 secondary-only claim IDs).
  - `/Users/wwcclaudecode/Documents/JOHNSHOPKINSHEALTHPLANS_354193286_1-2-2025_1993-17020835_4202026101309PM.835` → `backend/tests/fixtures/johns_hopkins_era.835` (18 CLP segments, all status=1 paid, check #355174145).
- Baseline test count before Phase 2c: **167**. After: **206** (39 new tests).

---

## Task 1: Backend — fixture files + smoke tests

**Files:**
- Create: `backend/tests/fixtures/claim_analysis_2026_01.xls`
- Create: `backend/tests/fixtures/johns_hopkins_era.835`
- Create: `backend/tests/test_phase2c_fixtures.py`

- [ ] **Step 1: Copy fixtures**

```bash
cp "/Users/wwcclaudecode/Documents/Claims Analysis Files/Claim Analysis 2026.01.xls" \
   /Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/fixtures/claim_analysis_2026_01.xls

cp "/Users/wwcclaudecode/Documents/JOHNSHOPKINSHEALTHPLANS_354193286_1-2-2025_1993-17020835_4202026101309PM.835" \
   /Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/fixtures/johns_hopkins_era.835
```

- [ ] **Step 2: Write smoke tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_phase2c_fixtures.py`:

```python
"""Smoke tests confirming Phase 2c fixtures load."""
from pathlib import Path
import pandas as pd
from app.parsers.era_835 import Era835Parser

FIXTURES = Path(__file__).parent / "fixtures"
CLAIMS_ANALYSIS = FIXTURES / "claim_analysis_2026_01.xls"
ERA_FILE = FIXTURES / "johns_hopkins_era.835"


def test_claims_analysis_fixture_shape():
    df = pd.read_excel(CLAIMS_ANALYSIS, sheet_name=0)
    assert df.shape == (1262, 49)
    for col in ("Patient ID", "Claim ID", "Date of Service",
                "Claim Amount", "Insurance Priority"):
        assert col in df.columns
    priorities = set(df["Insurance Priority"].dropna().unique())
    assert priorities == {"Primary", "Secondary"}
    assert df["Claim ID"].nunique() == 937


def test_era_fixture_parses():
    content = ERA_FILE.read_text()
    era = Era835Parser().parse(content, filename=ERA_FILE.name)
    assert era.payer_name == "JOHNS HOPKINS HEALTH PLANS"
    assert era.check_number == "355174145"
    assert len(era.claims) == 18
    assert era.parse_errors == []
    first = era.claims[0]
    assert first.patient_control_number == "216059P45740"
    assert first.claim_status_code == "1"
```

- [ ] **Step 3: Run tests**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_phase2c_fixtures.py tests/ -v 2>&1 | tail -10
```
Expected: 2 new tests PASS + 167 prior tests PASS = **169 total**.

- [ ] **Step 4: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add backend/tests/fixtures/claim_analysis_2026_01.xls backend/tests/fixtures/johns_hopkins_era.835 backend/tests/test_phase2c_fixtures.py
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "test(backend): add Phase 2c Claims Analysis + ERA fixtures + smoke tests"
```

---

## Task 2: Backend — `claims_analysis_matcher.py` parser

**Files:**
- Create: `backend/app/services/claims_analysis_matcher.py`
- Create: `backend/tests/test_claims_analysis_parser.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_claims_analysis_parser.py`:

```python
"""Parser tests for Claims Analysis (Part 1 of Phase 2c)."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import pandas as pd
import pytest
from app.services.claims_analysis_matcher import (
    parse, ClaimsAnalysisImport, ClaimsAnalysisGroup,
)

FIXTURE = Path(__file__).parent / "fixtures" / "claim_analysis_2026_01.xls"


def _build_df(rows):
    cols = [
        "Patient ID", "Patient Name", "Care Provider", "Insurance Class",
        "Claim Amount", "Claim ID", "Claim State", "Claim Status",
        "Date of Service", "Filing Method", "Insurance Company",
        "Insurance Priority", "Line Balance", "Payor ID",
    ]
    filled = []
    for r in rows:
        d = {c: None for c in cols}
        d.update(r)
        filled.append(d)
    return pd.DataFrame(filled, columns=cols)


BASE = {
    "Patient ID": "11175", "Patient Name": "DOE, JANE",
    "Claim ID": 241786, "Claim Amount": 254.32,
    "Date of Service": "1/2/2026", "Insurance Priority": "Primary",
    "Insurance Company": "BCBS", "Payor ID": "00580",
}


def test_parse_real_fixture():
    result = parse(str(FIXTURE))
    assert isinstance(result, ClaimsAnalysisImport)
    assert result.total_rows == 1262
    assert len(result.groups) == 937
    assert all(isinstance(g, ClaimsAnalysisGroup) for g in result.groups)
    # Matches the 911 primary + 26 secondary in real data
    primary = sum(1 for g in result.groups if g.insurance_priority == "primary")
    secondary = sum(1 for g in result.groups if g.insurance_priority == "secondary")
    assert primary == 911
    assert secondary == 26


def test_parse_missing_required_column_raises(tmp_path):
    df = _build_df([BASE]).drop(columns=["Claim ID"])
    path = tmp_path / "bad.xlsx"
    df.to_excel(path, index=False)
    with pytest.raises(ValueError) as exc:
        parse(str(path))
    assert "Claim ID" in str(exc.value)


def test_parse_drops_rows_with_null_patient_id(tmp_path):
    df = _build_df([BASE, {**BASE, "Patient ID": None}])
    path = tmp_path / "null_pid.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert len(result.groups) == 1
    assert result.skipped_rows == 1


def test_parse_groups_by_claim_id_and_sums(tmp_path):
    row_a = {**BASE, "Claim ID": 999, "Claim Amount": 100.00}
    row_b = {**BASE, "Claim ID": 999, "Claim Amount": 150.00}
    df = _build_df([row_a, row_b])
    path = tmp_path / "group.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert len(result.groups) == 1
    g = result.groups[0]
    assert g.claim_id == "999"
    assert g.total_amount == Decimal("250.00")
    assert g.row_count == 2
    assert g.internal_claim_id == "999P11175"


def test_parse_normalizes_priority_lowercase(tmp_path):
    df = _build_df([BASE])
    path = tmp_path / "p.xlsx"
    df.to_excel(path, index=False)
    g = parse(str(path)).groups[0]
    assert g.insurance_priority == "primary"


def test_parse_unknown_priority_warns_and_defaults(tmp_path):
    row = {**BASE, "Insurance Priority": "Weirdness"}
    df = _build_df([row])
    path = tmp_path / "u.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert result.groups[0].insurance_priority == "primary"
    warns = [i for i in result.issues if "unknown priority" in i.message.lower()]
    assert len(warns) == 1
```

- [ ] **Step 2: Run to verify RED**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claims_analysis_parser.py -v 2>&1 | tail -10
```
Expected: 6 tests FAIL with `ModuleNotFoundError: No module named 'app.services.claims_analysis_matcher'`.

- [ ] **Step 3: Create parser module**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/services/claims_analysis_matcher.py`:

```python
"""Claims Analysis bootstrap — parser + matcher (pure, no DB writes here).

Reads the PrimeSuite Claims Analysis .xls export and produces
ClaimsAnalysisGroup records (one per unique Claim ID) plus match plans
against existing Claims in the DB. The router handles DB writes.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import pandas as pd


REQUIRED_COLUMNS = [
    "Patient ID", "Claim ID", "Date of Service", "Claim Amount",
    "Insurance Priority",
]

VALID_PRIORITIES = {"primary", "secondary", "tertiary", "patient"}


@dataclass
class ClaimsAnalysisGroup:
    patient_external_id: str
    claim_id: str
    dos: Optional[date]
    total_amount: Decimal
    row_count: int
    insurance_priority: str             # "primary" | "secondary" | "tertiary" | "patient"
    internal_claim_id: str              # f"{claim_id}P{patient_external_id}"


@dataclass
class ParseIssue:
    severity: str                       # "error" | "warning"
    row_index: int
    claim_id: Optional[str]
    message: str


@dataclass
class ClaimsAnalysisImport:
    groups: List[ClaimsAnalysisGroup]
    source_filename: str
    total_rows: int
    skipped_rows: int
    issues: List[ParseIssue] = field(default_factory=list)


def _str_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    if s.endswith(".0"):
        try:
            s = str(int(float(s)))
        except ValueError:
            pass
    return s or None


def _decimal(v: Any) -> Decimal:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return Decimal("0")
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _parse_date(v: Any) -> Optional[date]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse(path: str) -> ClaimsAnalysisImport:
    df = pd.read_excel(path, sheet_name=0)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    total_rows = int(len(df))
    issues: List[ParseIssue] = []
    skipped_rows = 0

    # Row-level validation + grouping
    groups_map: Dict[str, List[Dict[str, Any]]] = {}
    for row_index, raw in df.iterrows():
        row = raw.to_dict()
        pid = _str_or_none(row.get("Patient ID"))
        cid = _str_or_none(row.get("Claim ID"))
        if not pid or not cid:
            skipped_rows += 1
            issues.append(ParseIssue(
                "error", int(row_index), cid,
                f"missing Patient ID or Claim ID — row dropped",
            ))
            continue
        groups_map.setdefault(cid, []).append({"__index__": int(row_index), **row, "__pid__": pid})

    groups: List[ClaimsAnalysisGroup] = []
    for cid, rows in groups_map.items():
        first = rows[0]
        pid = first["__pid__"]
        # Priority: first-row wins; warn if mixed
        priorities = {(_str_or_none(r.get("Insurance Priority")) or "").lower() for r in rows}
        if len(priorities) > 1:
            issues.append(ParseIssue(
                "warning", first["__index__"], cid,
                f"mixed Insurance Priority values across rows: {priorities}; using first",
            ))
        priority = (_str_or_none(first.get("Insurance Priority")) or "primary").lower()
        if priority not in VALID_PRIORITIES:
            issues.append(ParseIssue(
                "warning", first["__index__"], cid,
                f"unknown priority {priority!r}; defaulting to 'primary'",
            ))
            priority = "primary"

        total = sum((_decimal(r.get("Claim Amount")) for r in rows), Decimal("0"))
        dos = _parse_date(first.get("Date of Service"))
        groups.append(ClaimsAnalysisGroup(
            patient_external_id=pid,
            claim_id=cid,
            dos=dos,
            total_amount=total,
            row_count=len(rows),
            insurance_priority=priority,
            internal_claim_id=f"{cid}P{pid}",
        ))

    return ClaimsAnalysisImport(
        groups=groups,
        source_filename=os.path.basename(path),
        total_rows=total_rows,
        skipped_rows=skipped_rows,
        issues=issues,
    )
```

- [ ] **Step 4: Run to verify GREEN**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claims_analysis_parser.py tests/ -v 2>&1 | tail -10
```
Expected: 6 new tests PASS + all 169 prior PASS = **175 total**.

- [ ] **Step 5: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add backend/app/services/claims_analysis_matcher.py backend/tests/test_claims_analysis_parser.py
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "feat(backend): claims_analysis_matcher — parser + real-fixture test"
```

---

## Task 3: Backend — `claims_analysis_matcher` match logic

**Files:**
- Modify: `backend/app/services/claims_analysis_matcher.py` (append match function + MatchResult)
- Create: `backend/tests/test_claims_analysis_matcher.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_claims_analysis_matcher.py`:

```python
"""Match tests for Claims Analysis bootstrap."""
from datetime import date
from decimal import Decimal
from app.models.claim import Claim, ClaimStatus, InsuranceOrder
from app.models.patient import Patient
from app.services.claims_analysis_matcher import (
    ClaimsAnalysisGroup, MatchResult, match_groups,
)


def _group(priority="primary", cid="C1", pid="P1", dos=date(2026, 1, 2), amount="100"):
    return ClaimsAnalysisGroup(
        patient_external_id=pid, claim_id=cid, dos=dos,
        total_amount=Decimal(amount), row_count=1,
        insurance_priority=priority, internal_claim_id=f"{cid}P{pid}",
    )


def _seed_patient(db, pid="P1"):
    p = Patient(patient_id=pid, first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    return p


def _seed_claim(db, patient, order=InsuranceOrder.PRIMARY, dos=date(2026, 1, 2),
                amount="100", pcn=None):
    c = Claim(
        claim_number="V1", patient_id=patient.id,
        date_of_service_from=dos, billed_amount=Decimal(amount),
        status=ClaimStatus.PENDING, insurance_order=order,
        balance=Decimal("0"), patient_control_number=pcn,
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_match_primary_will_patch_when_pcn_null(db):
    p = _seed_patient(db)
    c = _seed_claim(db, p)
    results = match_groups(db, [_group()])
    assert len(results) == 1
    r = results[0]
    assert r.status == "will_patch"
    assert r.matched_claim_id == str(c.id)


def test_match_primary_already_set_when_equal(db):
    p = _seed_patient(db)
    _seed_claim(db, p, pcn="C1P1")
    r = match_groups(db, [_group()])[0]
    assert r.status == "already_set"


def test_match_primary_conflict_when_pcn_differs(db):
    p = _seed_patient(db)
    _seed_claim(db, p, pcn="OTHER999")
    r = match_groups(db, [_group()])[0]
    assert r.status == "conflict"
    assert r.conflict_existing_value == "OTHER999"


def test_match_primary_no_patient(db):
    r = match_groups(db, [_group(pid="GHOST")])[0]
    assert r.status == "no_patient"


def test_match_primary_no_claim(db):
    _seed_patient(db)
    r = match_groups(db, [_group(dos=date(2026, 2, 1))])[0]
    assert r.status == "no_claim"


def test_match_primary_ambiguous(db):
    p = _seed_patient(db)
    _seed_claim(db, p)
    _seed_claim(db, p)  # second claim, same patient+DOS+billed
    r = match_groups(db, [_group()])[0]
    assert r.status == "ambiguous"


def test_match_secondary_will_create_when_no_existing_secondary(db):
    p = _seed_patient(db)
    _seed_claim(db, p)  # primary exists
    r = match_groups(db, [_group(priority="secondary", cid="C2")])[0]
    assert r.status == "will_create_secondary"
    # matched_claim_id points at the PRIMARY (we copy from it on create)
    assert r.matched_claim_id is not None


def test_match_secondary_no_primary_means_no_claim(db):
    _seed_patient(db)  # patient exists but no primary claim
    r = match_groups(db, [_group(priority="secondary")])[0]
    assert r.status == "no_claim"


def test_match_secondary_already_set(db):
    p = _seed_patient(db)
    _seed_claim(db, p)  # primary
    _seed_claim(db, p, order=InsuranceOrder.SECONDARY, pcn="C2P1")
    r = match_groups(db, [_group(priority="secondary", cid="C2")])[0]
    assert r.status == "already_set"
```

- [ ] **Step 2: Run to verify RED**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claims_analysis_matcher.py -v 2>&1 | tail -10
```
Expected: 9 FAIL with `ImportError: cannot import name 'MatchResult'`.

- [ ] **Step 3: Append to `claims_analysis_matcher.py`**

Append this to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/services/claims_analysis_matcher.py`:

```python
from typing import Literal
from sqlalchemy.orm import Session
from app.models.claim import Claim, InsuranceOrder
from app.models.patient import Patient


MatchStatus = Literal[
    "will_patch", "will_create_secondary", "already_set",
    "no_patient", "no_claim", "ambiguous", "conflict",
]


@dataclass
class MatchResult:
    group: ClaimsAnalysisGroup
    status: MatchStatus
    matched_claim_id: Optional[str] = None   # our internal UUID as str
    conflict_existing_value: Optional[str] = None


_PRIORITY_TO_ORDER = {
    "primary": InsuranceOrder.PRIMARY,
    "secondary": InsuranceOrder.SECONDARY,
    "tertiary": InsuranceOrder.TERTIARY,
    "patient": InsuranceOrder.PATIENT,
}


def _candidate_claims(db: Session, patient_id: str, dos: Optional[date],
                      billed: Decimal, order: InsuranceOrder) -> List[Claim]:
    q = db.query(Claim).filter(
        Claim.patient_id == patient_id,
        Claim.insurance_order == order,
        Claim.billed_amount == billed,
    )
    if dos is not None:
        q = q.filter(Claim.date_of_service_from == dos)
    return q.all()


def match_groups(db: Session, groups: List[ClaimsAnalysisGroup]) -> List[MatchResult]:
    results: List[MatchResult] = []
    for g in groups:
        patient = db.query(Patient).filter(Patient.patient_id == g.patient_external_id).first()
        if patient is None:
            results.append(MatchResult(group=g, status="no_patient"))
            continue

        order = _PRIORITY_TO_ORDER.get(g.insurance_priority, InsuranceOrder.PRIMARY)

        if g.insurance_priority == "primary":
            candidates = _candidate_claims(db, patient.id, g.dos, g.total_amount, order)
            if not candidates:
                results.append(MatchResult(group=g, status="no_claim"))
                continue
            if len(candidates) > 1:
                results.append(MatchResult(group=g, status="ambiguous"))
                continue
            claim = candidates[0]
            if claim.patient_control_number is None:
                results.append(MatchResult(group=g, status="will_patch",
                                           matched_claim_id=str(claim.id)))
            elif claim.patient_control_number == g.internal_claim_id:
                results.append(MatchResult(group=g, status="already_set",
                                           matched_claim_id=str(claim.id)))
            else:
                results.append(MatchResult(group=g, status="conflict",
                                           matched_claim_id=str(claim.id),
                                           conflict_existing_value=claim.patient_control_number))
            continue

        # Secondary / tertiary / patient
        existing = _candidate_claims(db, patient.id, g.dos, g.total_amount, order)
        if existing:
            # Existing higher-COB claim. Check PCN state.
            if len(existing) > 1:
                results.append(MatchResult(group=g, status="ambiguous"))
                continue
            claim = existing[0]
            if claim.patient_control_number is None:
                results.append(MatchResult(group=g, status="will_patch",
                                           matched_claim_id=str(claim.id)))
            elif claim.patient_control_number == g.internal_claim_id:
                results.append(MatchResult(group=g, status="already_set",
                                           matched_claim_id=str(claim.id)))
            else:
                results.append(MatchResult(group=g, status="conflict",
                                           matched_claim_id=str(claim.id),
                                           conflict_existing_value=claim.patient_control_number))
            continue

        # No existing higher-COB claim — we need a primary to copy from
        primary = _candidate_claims(db, patient.id, g.dos, g.total_amount, InsuranceOrder.PRIMARY)
        if not primary:
            results.append(MatchResult(group=g, status="no_claim"))
            continue
        if len(primary) > 1:
            results.append(MatchResult(group=g, status="ambiguous"))
            continue
        results.append(MatchResult(group=g, status="will_create_secondary",
                                   matched_claim_id=str(primary[0].id)))

    return results
```

- [ ] **Step 4: Run to verify GREEN**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claims_analysis_matcher.py tests/ -v 2>&1 | tail -10
```
Expected: 9 new PASS + all 175 prior PASS = **184 total**.

- [ ] **Step 5: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add backend/app/services/claims_analysis_matcher.py backend/tests/test_claims_analysis_matcher.py
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "feat(backend): claims_analysis_matcher — match logic (primary + secondary + statuses)"
```

---

## Task 4: Backend — `claim_id_bootstrap.py` upload/preview endpoint

**Files:**
- Create: `backend/app/routers/claim_id_bootstrap.py`
- Create: `backend/tests/test_claim_id_bootstrap_upload.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_claim_id_bootstrap_upload.py`:

```python
"""Tests for POST /api/imports/claim-id-bootstrap (upload/preview)."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from app.models.claim import Claim, ClaimStatus, InsuranceOrder
from app.models.patient import Patient
from app.services import import_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "claim_analysis_2026_01.xls"


def _upload(client):
    import_sessions._sessions.clear()
    with FIXTURE.open("rb") as f:
        return client.post(
            "/api/imports/claim-id-bootstrap",
            files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
        )


def test_upload_returns_preview(client, db):
    r = _upload(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_rows"] == 1262
    assert body["unique_claims"] == 937
    # No seeded patients/claims → everything is "no_patient"
    assert body["no_patient"] == 937
    assert body["will_patch"] == 0
    assert "session_id" in body
    assert "expires_at" in body


def test_upload_bad_file_422(client, db):
    r = client.post(
        "/api/imports/claim-id-bootstrap",
        files={"file": ("x.txt", b"not excel", "text/plain")},
    )
    assert r.status_code == 422


def test_upload_forbidden_for_clinical(clinical_client, db):
    r = _upload(clinical_client)
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify RED**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claim_id_bootstrap_upload.py -v 2>&1 | tail -10
```
Expected: 3 FAIL (404 — endpoint not mounted).

- [ ] **Step 3: Create router**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/claim_id_bootstrap.py`:

```python
"""POST /imports/claim-id-bootstrap (upload/preview + commit).

Two-step flow: upload a Claims Analysis .xls, preview matches, commit.
Commit patches patient_control_number on primary matches and creates
new secondary/tertiary Claim rows where Claims Analysis shows them.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.routers.auth import get_current_user
from app.services import import_sessions
from app.services.claims_analysis_matcher import (
    ClaimsAnalysisImport, MatchResult, match_groups, parse,
)


router = APIRouter(prefix="/imports", tags=["claim-id-bootstrap"])
SESSION_TTL_MIN = 30


def _to_jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if is_dataclass(v):
        return {k: _to_jsonable(x) for k, x in asdict(v).items()}
    if isinstance(v, list):
        return [_to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _to_jsonable(x) for k, x in v.items()}
    return v


def _summarize(results: list) -> dict:
    out = {
        "will_patch": 0, "will_create_secondary": 0, "already_set": 0,
        "no_patient": 0, "no_claim": 0, "ambiguous": 0, "conflicts": 0,
    }
    for r in results:
        if r.status == "conflict":
            out["conflicts"] += 1
        else:
            out[r.status] = out.get(r.status, 0) + 1
    return out


@router.post("/claim-id-bootstrap")
async def upload_claims_analysis(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xls", ".xlsx"):
        raise HTTPException(status_code=422, detail="file must be .xls or .xlsx")
    session_id = str(uuid.uuid4())
    subdir = os.path.join(settings.upload_dir, "claim_id_bootstrap")
    os.makedirs(subdir, exist_ok=True)
    save_path = os.path.join(subdir, f"{session_id}{ext}")
    content = await file.read()
    with open(save_path, "wb") as fh:
        fh.write(content)

    try:
        parsed: ClaimsAnalysisImport = parse(save_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"could not read Excel file: {exc}")

    parsed.source_filename = file.filename or parsed.source_filename
    results = match_groups(db, parsed.groups)
    summary = _summarize(results)

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=SESSION_TTL_MIN)
    import_sessions.put(import_sessions.SessionEntry(
        session_id=session_id,
        payload={"parsed": parsed, "results": results},
        filename=parsed.source_filename,
        file_path=save_path,
        user_email=current_user.get("email"),
        created_at=now,
        expires_at=expires_at,
    ))

    return {
        "session_id": session_id,
        "source_filename": parsed.source_filename,
        "total_rows": parsed.total_rows,
        "skipped_rows": parsed.skipped_rows,
        "unique_claims": len(parsed.groups),
        **summary,
        "sample_matches": [_to_jsonable(r) for r in results[:20]],
        "issues": [_to_jsonable(i) for i in parsed.issues],
        "expires_at": expires_at.isoformat(),
    }
```

- [ ] **Step 4: Wire router into `main.py`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/main.py`.

Update the router imports line to include `claim_id_bootstrap`:
```python
from app.routers import waystar, ar, documents, intake, chart, fax, auth, dashboard, fax_batch, admin_users, service_lines, claim_adjustments, service_line_adjustments, charge_imports, claim_id_bootstrap
```

Add `include_router` below `charge_imports`:
```python
app.include_router(charge_imports.router, prefix="/api", dependencies=BILLING)
app.include_router(claim_id_bootstrap.router, prefix="/api", dependencies=BILLING)
```

- [ ] **Step 5: Run to verify GREEN**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claim_id_bootstrap_upload.py tests/ -v 2>&1 | tail -10
```
Expected: 3 new PASS + 184 prior PASS = **187 total**.

- [ ] **Step 6: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add backend/app/routers/claim_id_bootstrap.py backend/app/main.py backend/tests/test_claim_id_bootstrap_upload.py
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "feat(backend): POST /imports/claim-id-bootstrap upload + preview"
```

---

## Task 5: Backend — `claim_id_bootstrap` commit endpoint

**Files:**
- Modify: `backend/app/routers/claim_id_bootstrap.py` (append commit endpoint + helpers)
- Create: `backend/tests/test_claim_id_bootstrap_commit.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_claim_id_bootstrap_commit.py`:

```python
"""Tests for the claim-id-bootstrap commit endpoint."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from app.models.claim import Claim, ServiceLine, ClaimStatus, InsuranceOrder
from app.models.patient import Patient
from app.models.audit import AuditLog
from app.services import import_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "claim_analysis_2026_01.xls"


def _upload(client):
    import_sessions._sessions.clear()
    with FIXTURE.open("rb") as f:
        return client.post(
            "/api/imports/claim-id-bootstrap",
            files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
        ).json()


def test_commit_patches_matching_primary_claim(client, db):
    # Seed: one patient + one claim that should match Claim ID 241786
    # (from the first row of real fixture).
    p = Patient(patient_id="11175", first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    # Fixture row 0 has Claim Amount 254.32 / DOS 1/2/2026 / Patient 11175
    c = Claim(
        claim_number="V1", patient_id=p.id,
        date_of_service_from=date(2026, 1, 2),
        billed_amount=Decimal("254.32"),
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING, balance=Decimal("0"),
    )
    db.add(c); db.commit(); db.refresh(c)

    preview = _upload(client)
    assert preview["will_patch"] == 1

    r = client.post(f"/api/imports/claim-id-bootstrap/{preview['session_id']}/commit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["claims_patched"] == 1
    assert body["secondary_claims_created"] == 0

    db.refresh(c)
    assert c.patient_control_number == "241786P11175"


def test_commit_creates_secondary_claim_with_service_lines(client, db):
    # Build a synthetic scenario: patient + primary claim w/ 1 service line.
    # We craft a session directly to avoid depending on a secondary row
    # being present in the real fixture's first 20 rows.
    from app.services.claims_analysis_matcher import (
        ClaimsAnalysisGroup, MatchResult, ClaimsAnalysisImport,
    )
    from datetime import datetime, timezone, timedelta

    p = Patient(patient_id="99999", first_name="S", last_name="T")
    db.add(p); db.commit(); db.refresh(p)
    primary = Claim(
        claim_number="VSEC", patient_id=p.id,
        date_of_service_from=date(2026, 3, 1),
        billed_amount=Decimal("500.00"),
        payer_name="Primary BCBS",
        rendering_provider_name="Dr X", rendering_provider_npi="1111111111",
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING, balance=Decimal("0"),
    )
    db.add(primary); db.commit(); db.refresh(primary)
    db.add(ServiceLine(claim_id=primary.id, procedure_code="99213",
                       units=Decimal("1"), billed_amount=Decimal("500.00")))
    db.commit()

    # Inject a secondary group that matches
    group = ClaimsAnalysisGroup(
        patient_external_id="99999", claim_id="888888",
        dos=date(2026, 3, 1), total_amount=Decimal("500.00"),
        row_count=1, insurance_priority="secondary",
        internal_claim_id="888888P99999",
    )
    match = MatchResult(group=group, status="will_create_secondary",
                        matched_claim_id=str(primary.id))
    parsed = ClaimsAnalysisImport(
        groups=[group], source_filename="synthetic.xls",
        total_rows=1, skipped_rows=0,
    )
    now = datetime.now(timezone.utc)
    import_sessions._sessions.clear()
    import_sessions._sessions["syn"] = import_sessions.SessionEntry(
        session_id="syn", payload={"parsed": parsed, "results": [match]},
        filename="synthetic.xls", file_path="/tmp/synthetic.xls",
        user_email="tester@waldorfwomenscare.com",
        created_at=now, expires_at=now + timedelta(minutes=30),
    )

    r = client.post("/api/imports/claim-id-bootstrap/syn/commit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["secondary_claims_created"] == 1

    secondary = db.query(Claim).filter(
        Claim.patient_id == p.id,
        Claim.insurance_order == InsuranceOrder.SECONDARY,
    ).first()
    assert secondary is not None
    assert secondary.patient_control_number == "888888P99999"
    assert secondary.billed_amount == Decimal("500.00")
    assert secondary.rendering_provider_npi == "1111111111"
    lines = db.query(ServiceLine).filter(ServiceLine.claim_id == secondary.id).all()
    assert len(lines) == 1
    assert lines[0].procedure_code == "99213"
    assert lines[0].paid_amount == 0  # Secondary starts at zero


def test_commit_404_on_unknown_session(client, db):
    r = client.post("/api/imports/claim-id-bootstrap/nope/commit")
    assert r.status_code == 404


def test_commit_forbidden_for_clinical(clinical_client, db, client):
    preview = _upload(client)
    r = clinical_client.post(
        f"/api/imports/claim-id-bootstrap/{preview['session_id']}/commit")
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify RED**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claim_id_bootstrap_commit.py -v 2>&1 | tail -10
```
Expected: 4 FAIL (404 — commit endpoint not defined).

- [ ] **Step 3: Append commit endpoint to `claim_id_bootstrap.py`**

Append this to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/claim_id_bootstrap.py`:

```python
from app.models.claim import Claim, ServiceLine, ClaimStatus, InsuranceOrder
from app.services.audit_service import log_action
from app.services.claim_math import recompute_balance


def _patch_claim(db: Session, claim_id: str, internal_claim_id: str,
                 user_email: str) -> Claim:
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    old = {"patient_control_number": claim.patient_control_number}
    claim.patient_control_number = internal_claim_id
    db.commit()
    log_action(
        db, "UPDATE", "claim",
        resource_id=str(claim.id),
        patient_id=str(claim.patient_id) if claim.patient_id else None,
        user_name=user_email,
        old_values=old,
        new_values={"patient_control_number": internal_claim_id},
        description="claim-id-bootstrap: patched patient_control_number",
    )
    return claim


def _create_secondary(db: Session, primary_id: str, group, user_email: str) -> Claim:
    primary = db.query(Claim).filter(Claim.id == primary_id).first()
    order_map = {"secondary": InsuranceOrder.SECONDARY,
                 "tertiary": InsuranceOrder.TERTIARY}
    new_order = order_map.get(group.insurance_priority, InsuranceOrder.SECONDARY)
    secondary = Claim(
        claim_number=primary.claim_number,
        patient_id=primary.patient_id,
        date_of_service_from=primary.date_of_service_from,
        date_of_service_to=primary.date_of_service_to,
        payer_name=primary.payer_name,
        payer_id=primary.payer_id,
        subscriber_id=primary.subscriber_id,
        rendering_provider_name=primary.rendering_provider_name,
        rendering_provider_npi=primary.rendering_provider_npi,
        billing_provider_npi=primary.billing_provider_npi,
        insurance_order=new_order,
        status=ClaimStatus.PENDING,
        billed_amount=primary.billed_amount,
        patient_control_number=group.internal_claim_id,
    )
    db.add(secondary); db.flush()
    # Copy service lines — zero out paid/adjustment fields
    primary_lines = db.query(ServiceLine).filter(ServiceLine.claim_id == primary.id).all()
    for sl in primary_lines:
        db.add(ServiceLine(
            claim_id=secondary.id,
            procedure_code=sl.procedure_code,
            modifier_1=sl.modifier_1, modifier_2=sl.modifier_2,
            modifier_3=sl.modifier_3, modifier_4=sl.modifier_4,
            units=sl.units,
            billed_amount=sl.billed_amount,
            date_of_service_from=sl.date_of_service_from,
            date_of_service_to=sl.date_of_service_to,
            diagnosis_codes=list(sl.diagnosis_codes or []),
        ))
    recompute_balance(secondary)
    db.commit()
    db.refresh(secondary)
    log_action(
        db, "CREATE", "claim",
        resource_id=str(secondary.id),
        patient_id=str(secondary.patient_id) if secondary.patient_id else None,
        user_name=user_email,
        new_values={"claim_number": secondary.claim_number,
                    "insurance_order": new_order.value,
                    "patient_control_number": group.internal_claim_id},
        description=f"claim-id-bootstrap: created {new_order.value} claim from primary",
    )
    return secondary


@router.post("/claim-id-bootstrap/{session_id}/commit")
def commit_claim_id_bootstrap(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    entry = import_sessions.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="session not found or expired")

    results: list[MatchResult] = entry.payload["results"]
    user_email = current_user.get("email")

    claims_patched = 0
    secondary_claims_created = 0
    already_set = 0
    unmatched = 0
    ambiguous = 0
    conflicts = 0
    errors: list[dict] = []

    for r in results:
        try:
            if r.status == "will_patch":
                _patch_claim(db, r.matched_claim_id, r.group.internal_claim_id, user_email)
                claims_patched += 1
            elif r.status == "will_create_secondary":
                _create_secondary(db, r.matched_claim_id, r.group, user_email)
                secondary_claims_created += 1
            elif r.status == "already_set":
                already_set += 1
            elif r.status in ("no_patient", "no_claim"):
                unmatched += 1
            elif r.status == "ambiguous":
                ambiguous += 1
            elif r.status == "conflict":
                conflicts += 1
        except Exception as exc:
            db.rollback()
            errors.append({"claim_id": r.group.claim_id,
                           "message": f"{type(exc).__name__}: {exc}"})

    log_action(
        db, "IMPORT", "claim_id_bootstrap",
        resource_id=session_id, user_name=user_email,
        description=(f"{entry.filename} — {claims_patched} patched, "
                     f"{secondary_claims_created} secondary created, "
                     f"{already_set} already set, {unmatched} unmatched"),
    )
    import_sessions.purge(session_id)

    return {
        "source_filename": entry.filename,
        "claims_patched": claims_patched,
        "secondary_claims_created": secondary_claims_created,
        "already_set": already_set,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "conflicts": conflicts,
        "errors": errors,
    }
```

- [ ] **Step 4: Run to verify GREEN**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_claim_id_bootstrap_commit.py tests/ -v 2>&1 | tail -10
```
Expected: 4 new PASS + 187 prior PASS = **191 total**.

- [ ] **Step 5: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add backend/app/routers/claim_id_bootstrap.py backend/tests/test_claim_id_bootstrap_commit.py
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "feat(backend): POST /imports/claim-id-bootstrap/{id}/commit — patch + create secondary"
```

---

## Task 6: Backend — `era_poster.py` match logic

**Files:**
- Create: `backend/app/services/era_poster.py`
- Create: `backend/tests/test_era_poster_match.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_era_poster_match.py`:

```python
"""Matcher tests for era_poster."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from app.models.claim import Claim, ClaimStatus, InsuranceOrder
from app.models.patient import Patient
from app.models.payment import Payment, PaymentType
from app.parsers.era_835 import Era835Parser, EraClaim, EraAdjustment
from app.services.era_poster import build_preview


FIXTURE = Path(__file__).parent / "fixtures" / "johns_hopkins_era.835"


def _era_from_fixture():
    return Era835Parser().parse(FIXTURE.read_text(), filename=FIXTURE.name)


def _claim(db, pcn: str, billed="253.76"):
    p = Patient(patient_id=pcn.split("P")[1], first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    c = Claim(
        claim_number="V", patient_id=p.id, patient_control_number=pcn,
        billed_amount=Decimal(billed),
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING, balance=Decimal("0"),
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_match_strict_by_patient_control_number(db):
    _claim(db, "216059P45740")
    era = _era_from_fixture()
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    matched = [m for m in preview.matches if m.status == "matched"]
    assert len(matched) == 1
    assert matched[0].internal_claim_id == "216059P45740"


def test_match_unmatched_when_no_link(db):
    era = _era_from_fixture()
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    assert preview.n_matched == 0
    assert preview.n_unmatched == len(era.claims)


def test_match_malformed_clp01_skipped(db):
    era = _era_from_fixture()
    era.claims[0].patient_control_number = "NOTFORMATTED"
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    statuses = [m.status for m in preview.matches]
    assert "malformed_clp01" in statuses


def test_match_cb_prefix_in_clp07_skipped(db):
    era = _era_from_fixture()
    era.claims[0].payer_claim_number = "CBABC123"
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    skipped = [m for m in preview.matches if m.status == "cb_prefix_skipped"]
    assert len(skipped) == 1


def test_reversal_flagged_on_clp02_22(db):
    _claim(db, "216059P45740")
    era = _era_from_fixture()
    era.claims[0].claim_status_code = "22"
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    flagged = [m for m in preview.matches if m.status == "reversal_flagged"]
    assert any(m.internal_claim_id == "216059P45740" for m in flagged)
    assert "CLP02=22" in flagged[0].reversal_reason


def test_reversal_flagged_on_negative_cas(db):
    _claim(db, "216059P45740")
    era = _era_from_fixture()
    era.claims[0].adjustments.append(
        EraAdjustment(group_code="CO", reason_code="45", amount=Decimal("-50")))
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    flagged = [m for m in preview.matches if m.status == "reversal_flagged"]
    assert any(m.internal_claim_id == "216059P45740" for m in flagged)
    assert "negative" in flagged[0].reversal_reason.lower()


def test_already_posted_when_payment_exists(db):
    c = _claim(db, "216059P45740")
    era = _era_from_fixture()
    # Pre-seed a Payment that would look like the ERA's posting
    era_claim = [x for x in era.claims if x.patient_control_number == "216059P45740"][0]
    db.add(Payment(
        claim_id=c.id, payment_type=PaymentType.INSURANCE_PAYMENT,
        amount=era_claim.paid_amount, payment_date=era.check_date,
        check_number=era.check_number, payer_name=era.payer_name,
    ))
    db.commit()
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    already = [m for m in preview.matches if m.status == "already_posted"]
    assert any(m.internal_claim_id == "216059P45740" for m in already)
```

- [ ] **Step 2: Run to verify RED**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_era_poster_match.py -v 2>&1 | tail -10
```
Expected: 7 FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `era_poster.py` with match logic**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/services/era_poster.py`:

```python
"""ERA 835 payment-posting service.

build_preview() is pure (no DB writes) — it classifies each EraClaim into a
status and returns the plan. The commit step in the router does all writes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Literal, Optional

from sqlalchemy.orm import Session

from app.models.claim import Claim
from app.models.payment import Payment
from app.parsers.era_835 import EraClaim, EraFile


CLP01_PATTERN = re.compile(r"^\d+P\d+$")


MatchStatus = Literal[
    "matched", "unmatched", "cb_prefix_skipped",
    "reversal_flagged", "malformed_clp01", "already_posted",
]


@dataclass
class EraClaimMatch:
    era_claim: EraClaim
    status: MatchStatus
    internal_claim_id: Optional[str] = None
    matched_claim_id: Optional[str] = None   # our UUID as str
    reversal_reason: Optional[str] = None


@dataclass
class EraFilePreview:
    era: EraFile
    source_filename: str
    matches: List[EraClaimMatch] = field(default_factory=list)
    n_matched: int = 0
    n_unmatched: int = 0
    n_already_posted: int = 0
    n_cb_skipped: int = 0
    n_reversals: int = 0
    n_malformed: int = 0


def _has_negative_cas(era_claim: EraClaim) -> bool:
    for a in era_claim.adjustments:
        if a.amount < Decimal("0"):
            return True
    for svc in era_claim.service_lines:
        for a in svc.adjustments:
            if a.amount < Decimal("0"):
                return True
    return False


def _already_posted(db: Session, claim_id: str, era: EraFile,
                    era_claim: EraClaim) -> bool:
    """Return True iff a Payment already exists matching this (claim, ERA) tuple."""
    q = db.query(Payment).filter(
        Payment.claim_id == claim_id,
        Payment.check_number == era.check_number,
        Payment.amount == era_claim.paid_amount,
    )
    if era.check_date is not None:
        q = q.filter(Payment.payment_date == era.check_date)
    return q.first() is not None


def build_preview(db: Session, era: EraFile, source_filename: str) -> EraFilePreview:
    preview = EraFilePreview(era=era, source_filename=source_filename)
    for era_claim in era.claims:
        clp01 = era_claim.patient_control_number or ""
        clp07 = era_claim.payer_claim_number or ""

        if not CLP01_PATTERN.match(clp01):
            preview.matches.append(EraClaimMatch(
                era_claim=era_claim, status="malformed_clp01",
                internal_claim_id=clp01 or None,
            ))
            preview.n_malformed += 1
            continue

        if clp07.startswith("CB"):
            preview.matches.append(EraClaimMatch(
                era_claim=era_claim, status="cb_prefix_skipped",
                internal_claim_id=clp01,
            ))
            preview.n_cb_skipped += 1
            continue

        reversal_reason = None
        if era_claim.claim_status_code == "22":
            reversal_reason = "CLP02=22 (reversal of prior payment)"
        elif _has_negative_cas(era_claim):
            reversal_reason = "negative CAS adjustment amount"
        if reversal_reason:
            preview.matches.append(EraClaimMatch(
                era_claim=era_claim, status="reversal_flagged",
                internal_claim_id=clp01, reversal_reason=reversal_reason,
            ))
            preview.n_reversals += 1
            continue

        claim = db.query(Claim).filter(Claim.patient_control_number == clp01).first()
        if claim is None:
            preview.matches.append(EraClaimMatch(
                era_claim=era_claim, status="unmatched",
                internal_claim_id=clp01,
            ))
            preview.n_unmatched += 1
            continue

        if _already_posted(db, str(claim.id), era, era_claim):
            preview.matches.append(EraClaimMatch(
                era_claim=era_claim, status="already_posted",
                internal_claim_id=clp01, matched_claim_id=str(claim.id),
            ))
            preview.n_already_posted += 1
            continue

        preview.matches.append(EraClaimMatch(
            era_claim=era_claim, status="matched",
            internal_claim_id=clp01, matched_claim_id=str(claim.id),
        ))
        preview.n_matched += 1

    return preview
```

- [ ] **Step 4: Run to verify GREEN**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_era_poster_match.py tests/ -v 2>&1 | tail -10
```
Expected: 7 new PASS + 191 prior PASS = **198 total**.

- [ ] **Step 5: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add backend/app/services/era_poster.py backend/tests/test_era_poster_match.py
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "feat(backend): era_poster — match logic (strict PCN + reversal + dedup)"
```

---

## Task 7: Backend — `era_poster` posting logic

**Files:**
- Modify: `backend/app/services/era_poster.py` (append post_claim)
- Create: `backend/tests/test_era_poster_post.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_era_poster_post.py`:

```python
"""Posting tests for era_poster.post_claim()."""
from datetime import date
from decimal import Decimal
from pathlib import Path
from app.models.claim import Claim, ClaimStatus, InsuranceOrder, EraFile as EraFileModel, ClaimAdjustment
from app.models.denial import Denial
from app.models.patient import Patient
from app.models.payment import Payment
from app.parsers.era_835 import Era835Parser, EraAdjustment
from app.services.era_poster import build_preview, post_claim

FIXTURE = Path(__file__).parent / "fixtures" / "johns_hopkins_era.835"


def _setup(db):
    p = Patient(patient_id="45740", first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    c = Claim(
        claim_number="V1", patient_id=p.id,
        patient_control_number="216059P45740",
        billed_amount=Decimal("253.76"),
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING, balance=Decimal("253.76"),
    )
    db.add(c); db.commit(); db.refresh(c)
    era = Era835Parser().parse(FIXTURE.read_text(), filename=FIXTURE.name)
    era_file = EraFileModel(
        filename=FIXTURE.name, file_path=str(FIXTURE),
        payer_name=era.payer_name, check_number=era.check_number,
        check_date=era.check_date, check_amount=era.check_amount,
        transaction_count=len(era.claims), status="processed",
    )
    db.add(era_file); db.commit(); db.refresh(era_file)
    preview = build_preview(db, era, source_filename=FIXTURE.name)
    match = [m for m in preview.matches if m.status == "matched"][0]
    return c, era, era_file, match


def test_post_creates_payment_row(db):
    c, era, era_file, match = _setup(db)
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    payments = db.query(Payment).filter(Payment.claim_id == c.id).all()
    assert len(payments) == 1
    assert payments[0].amount == match.era_claim.paid_amount
    assert payments[0].check_number == era.check_number


def test_post_updates_claim_paid_amount_from_payment_sum(db):
    c, era, era_file, match = _setup(db)
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    db.refresh(c)
    assert c.paid_amount == match.era_claim.paid_amount


def test_post_recomputes_balance(db):
    c, era, era_file, match = _setup(db)
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    db.refresh(c)
    # balance = billed - contractual - other - paid - pt_resp
    expected = (c.billed_amount - (c.contractual_adjustment or 0)
                - (c.other_adjustment or 0) - (c.paid_amount or 0)
                - (c.patient_responsibility or 0))
    assert c.balance == expected


def test_post_sets_status_from_clp02_1_paid(db):
    c, era, era_file, match = _setup(db)
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    db.refresh(c)
    assert c.status == ClaimStatus.PAID


def test_post_sets_payer_claim_number_when_null(db):
    c, era, era_file, match = _setup(db)
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    db.refresh(c)
    assert c.payer_claim_number == match.era_claim.payer_claim_number


def test_post_creates_claim_adjustments(db):
    c, era, era_file, match = _setup(db)
    match.era_claim.adjustments = [
        EraAdjustment(group_code="CO", reason_code="45", amount=Decimal("100")),
        EraAdjustment(group_code="PR", reason_code="1", amount=Decimal("20")),
    ]
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    adjs = db.query(ClaimAdjustment).filter(ClaimAdjustment.claim_id == c.id).all()
    assert len(adjs) == 2
    codes = {(a.group_code, a.reason_code) for a in adjs}
    assert codes == {("CO", "45"), ("PR", "1")}


def test_post_creates_denial_for_real_denial(db):
    c, era, era_file, match = _setup(db)
    # CO-16 is a real denial (missing information)
    match.era_claim.adjustments = [
        EraAdjustment(group_code="CO", reason_code="16", amount=Decimal("50")),
    ]
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    denials = db.query(Denial).filter(Denial.claim_id == c.id).all()
    assert len(denials) == 1
    assert denials[0].carc_code == "16"


def test_post_skips_denial_for_co_45(db):
    c, era, era_file, match = _setup(db)
    match.era_claim.adjustments = [
        EraAdjustment(group_code="CO", reason_code="45", amount=Decimal("50")),
    ]
    post_claim(db, match, era, era_file, user_email="tester@x.com")
    denials = db.query(Denial).filter(Denial.claim_id == c.id).all()
    assert len(denials) == 0
```

- [ ] **Step 2: Run to verify RED**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_era_poster_post.py -v 2>&1 | tail -15
```
Expected: 8 FAIL with `ImportError: cannot import name 'post_claim'`.

- [ ] **Step 3: Append `post_claim` to `era_poster.py`**

Append this to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/services/era_poster.py`:

```python
from datetime import date as date_cls
from app.models.audit import AuditLog
from app.models.claim import ServiceLine, ClaimAdjustment, ServiceLineAdjustment
from app.models.denial import Denial
from app.models.payment import PaymentType
from app.services.audit_service import log_action
from app.services.claim_math import recompute_balance
from app.services.era_import_service import (
    _determine_claim_status, _has_real_denials, _create_denials,
    SKIP_DENIAL_CODES,
)
from app.utils.carc_codes import get_carc_info


def _update_claim_money(claim: Claim, era_claim: EraClaim) -> None:
    co45 = sum(
        a.amount for a in era_claim.adjustments
        if a.group_code == "CO" and a.reason_code == "45"
    )
    other = sum(
        a.amount for a in era_claim.adjustments
        if a.group_code not in ("CO", "PR")
    )
    claim.contractual_adjustment = (claim.contractual_adjustment or Decimal("0")) + co45
    claim.other_adjustment = (claim.other_adjustment or Decimal("0")) + other
    claim.patient_responsibility = era_claim.patient_responsibility
    claim.allowed_amount = era_claim.billed_amount - co45


def _post_claim_adjustments(db: Session, claim_id: str, era_file_id: str,
                            era_claim: EraClaim) -> None:
    """Create ClaimAdjustment rows. Dedup on (claim, era_file, group, reason)."""
    for adj in era_claim.adjustments:
        exists = db.query(ClaimAdjustment).filter(
            ClaimAdjustment.claim_id == claim_id,
            ClaimAdjustment.group_code == adj.group_code,
            ClaimAdjustment.reason_code == adj.reason_code,
        ).first()
        if exists:
            continue
        carc = get_carc_info(adj.reason_code)
        db.add(ClaimAdjustment(
            claim_id=claim_id,
            group_code=adj.group_code,
            reason_code=adj.reason_code,
            amount=adj.amount,
            quantity=adj.quantity,
            reason_description=carc.description,
        ))


def _post_service_lines(db: Session, claim_id: str, era_claim: EraClaim,
                        warnings: list) -> None:
    """Best-effort match by procedure_code + first modifier."""
    for svc in era_claim.service_lines:
        candidates = db.query(ServiceLine).filter(
            ServiceLine.claim_id == claim_id,
            ServiceLine.procedure_code == svc.procedure_code,
        ).all()
        chosen = None
        if len(candidates) == 1:
            chosen = candidates[0]
        elif len(candidates) > 1 and svc.modifier_1:
            mod_match = [c for c in candidates if c.modifier_1 == svc.modifier_1]
            if len(mod_match) == 1:
                chosen = mod_match[0]
        if chosen is None:
            warnings.append(f"service line {svc.procedure_code} not uniquely matched on claim")
            continue
        chosen.paid_amount = (chosen.paid_amount or Decimal("0")) + svc.paid_amount
        co45 = sum(a.amount for a in svc.adjustments
                   if a.group_code == "CO" and a.reason_code == "45")
        contractual = sum(a.amount for a in svc.adjustments if a.group_code == "CO")
        pr_sum = sum(a.amount for a in svc.adjustments if a.group_code == "PR")
        chosen.contractual_adjustment = (chosen.contractual_adjustment or Decimal("0")) + contractual
        chosen.patient_responsibility = pr_sum
        chosen.allowed_amount = svc.billed_amount - co45
        # Adjustments at line level
        for adj in svc.adjustments:
            carc = get_carc_info(adj.reason_code)
            db.add(ServiceLineAdjustment(
                service_line_id=chosen.id,
                group_code=adj.group_code,
                reason_code=adj.reason_code,
                amount=adj.amount,
                quantity=adj.quantity,
                reason_description=carc.description,
            ))


def post_claim(db: Session, match: EraClaimMatch, era: EraFile,
               era_file_row: "EraFileModel", user_email: Optional[str]) -> dict:
    """Post an ERA claim onto an existing Claim row.

    Assumes caller has already created the EraFile DB row (era_file_row).
    Writes Payment, updates Claim, creates ClaimAdjustment + Denial rows.
    """
    from app.models.claim import EraFile as EraFileModel  # noqa

    claim = db.query(Claim).filter(Claim.id == match.matched_claim_id).first()
    era_claim = match.era_claim

    # 1. Create Payment row
    pmt = Payment(
        claim_id=claim.id,
        patient_id=claim.patient_id,
        payment_type=PaymentType.INSURANCE_PAYMENT,
        amount=era_claim.paid_amount,
        payment_date=era.check_date or date_cls.today(),
        date_of_service=claim.date_of_service_from,
        payer_name=era.payer_name,
        check_number=era.check_number,
        era_file_id=era_file_row.id,
        posted_by=user_email or "era-poster",
    )
    db.add(pmt)
    db.flush()

    # 2. Update claim money + status
    _update_claim_money(claim, era_claim)
    if claim.payer_claim_number is None:
        claim.payer_claim_number = era_claim.payer_claim_number
    claim.check_number = era.check_number
    claim.check_date = era.check_date
    claim.era_file_id = era_file_row.id
    claim.status = _determine_claim_status(era_claim)
    # Sum all payments → claim.paid_amount
    total_paid = db.query(Payment).filter(Payment.claim_id == claim.id).all()
    claim.paid_amount = sum((p.amount for p in total_paid), Decimal("0"))

    # 3. Adjustments
    _post_claim_adjustments(db, str(claim.id), str(era_file_row.id), era_claim)

    # 4. Service lines
    warnings: list = []
    _post_service_lines(db, str(claim.id), era_claim, warnings)

    # 5. Denials (reuse legacy helper)
    if era_claim.is_denied or _has_real_denials(era_claim):
        _create_denials(db, claim, era_claim, era)

    # 6. Recompute balance
    recompute_balance(claim)

    db.commit()
    db.refresh(claim)

    log_action(
        db, "POST_PAYMENT", "claim",
        resource_id=str(claim.id),
        patient_id=str(claim.patient_id) if claim.patient_id else None,
        user_name=user_email,
        new_values={
            "paid_amount": float(claim.paid_amount or 0),
            "status": claim.status.value if claim.status else None,
            "check_number": claim.check_number,
        },
        description=f"ERA {match.internal_claim_id} check {era.check_number}",
    )
    return {"warnings": warnings}
```

- [ ] **Step 4: Run to verify GREEN**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_era_poster_post.py tests/ -v 2>&1 | tail -15
```
Expected: 8 new PASS + 198 prior PASS = **206 total**.

- [ ] **Step 5: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add backend/app/services/era_poster.py backend/tests/test_era_poster_post.py
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "feat(backend): era_poster.post_claim — Payment + adjustments + denials + balance"
```

---

## Task 8: Backend — `era_posting.py` multi-file upload/preview endpoint

**Files:**
- Create: `backend/app/routers/era_posting.py`
- Create: `backend/tests/test_era_posting_upload.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_era_posting_upload.py`:

```python
"""Upload/preview tests for ERA posting endpoint (supports multi-file)."""
from pathlib import Path
from app.services import import_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "johns_hopkins_era.835"


def _upload_one(client):
    import_sessions._sessions.clear()
    with FIXTURE.open("rb") as f:
        return client.post(
            "/api/imports/era-posting",
            files=[("file", (FIXTURE.name, f, "application/octet-stream"))],
        )


def _upload_multi(client, count=3):
    import_sessions._sessions.clear()
    data = FIXTURE.read_bytes()
    return client.post(
        "/api/imports/era-posting",
        files=[("file", (f"era{i}.835", data, "application/octet-stream"))
               for i in range(count)],
    )


def test_upload_single_era_returns_preview(client, db):
    r = _upload_one(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["n_files"] == 1
    assert body["totals"]["n_unmatched"] == 18  # no claims seeded
    assert len(body["files"]) == 1
    assert body["files"][0]["check_number"] == "355174145"


def test_upload_multiple_eras_combined_preview(client, db):
    r = _upload_multi(client, count=3)
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["n_files"] == 3
    assert body["totals"]["n_unmatched"] == 54  # 18 * 3


def test_upload_rejects_non_era(client, db):
    r = client.post(
        "/api/imports/era-posting",
        files=[("file", ("x.pdf", b"%PDF-1.4\n%noteraly", "application/pdf"))],
    )
    assert r.status_code == 422


def test_upload_forbidden_for_clinical(clinical_client, db):
    r = _upload_one(clinical_client)
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify RED**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_era_posting_upload.py -v 2>&1 | tail -10
```
Expected: 4 FAIL (404 — endpoint not mounted).

- [ ] **Step 3: Create router**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/era_posting.py`:

```python
"""POST /imports/era-posting (multi-file ERA upload/preview + commit)."""
from __future__ import annotations

import os
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.parsers.era_835 import Era835Parser
from app.routers.auth import get_current_user
from app.services import import_sessions
from app.services.era_poster import EraFilePreview, build_preview


router = APIRouter(prefix="/imports", tags=["era-posting"])
SESSION_TTL_MIN = 30


def _to_jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if is_dataclass(v):
        return {k: _to_jsonable(x) for k, x in asdict(v).items()}
    if isinstance(v, list):
        return [_to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _to_jsonable(x) for k, x in v.items()}
    return v


def _file_summary(p: EraFilePreview) -> dict:
    return {
        "source_filename": p.source_filename,
        "check_number": p.era.check_number,
        "check_amount": float(p.era.check_amount or 0),
        "check_date": p.era.check_date.isoformat() if p.era.check_date else None,
        "payer_name": p.era.payer_name,
        "n_claims": len(p.era.claims),
        "n_matched": p.n_matched,
        "n_unmatched": p.n_unmatched,
        "n_already_posted": p.n_already_posted,
        "n_cb_skipped": p.n_cb_skipped,
        "n_reversals": p.n_reversals,
        "n_malformed": p.n_malformed,
        "parse_errors": list(p.era.parse_errors or []),
    }


@router.post("/era-posting")
async def upload_eras(
    file: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not file:
        raise HTTPException(status_code=422, detail="at least one file required")
    for f in file:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in (".835", ".x12", ".edi", ""):
            raise HTTPException(status_code=422,
                                detail=f"file {f.filename!r} not a supported ERA format")

    session_id = str(uuid.uuid4())
    subdir = os.path.join(settings.upload_dir, "era_posting", session_id)
    os.makedirs(subdir, exist_ok=True)

    previews: List[EraFilePreview] = []
    for idx, f in enumerate(file):
        content_bytes = await f.read()
        save_path = os.path.join(subdir, f"{idx}-{f.filename or 'era.835'}")
        with open(save_path, "wb") as fh:
            fh.write(content_bytes)
        try:
            content = content_bytes.decode("utf-8", errors="ignore")
            if "ISA" not in content[:500]:
                raise HTTPException(status_code=422,
                                    detail=f"{f.filename!r} does not look like an ERA 835")
            era = Era835Parser().parse(content, filename=f.filename or f"era{idx}.835")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422,
                                detail=f"could not parse {f.filename!r}: {exc}")
        prev = build_preview(db, era, source_filename=f.filename or f"era{idx}.835")
        prev.era.filename = f.filename or prev.era.filename
        prev.__dict__["_file_path"] = save_path
        previews.append(prev)

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=SESSION_TTL_MIN)
    import_sessions.put(import_sessions.SessionEntry(
        session_id=session_id,
        payload={"previews": previews},
        filename=f"era_batch_{len(previews)}_files",
        file_path=subdir,
        user_email=current_user.get("email"),
        created_at=now, expires_at=expires_at,
    ))

    totals = {
        "n_files": len(previews),
        "combined_check_amount": sum(float(p.era.check_amount or 0) for p in previews),
        "n_matched": sum(p.n_matched for p in previews),
        "n_unmatched": sum(p.n_unmatched for p in previews),
        "n_already_posted": sum(p.n_already_posted for p in previews),
        "n_cb_skipped": sum(p.n_cb_skipped for p in previews),
        "n_reversals": sum(p.n_reversals for p in previews),
        "n_malformed": sum(p.n_malformed for p in previews),
    }

    sample = []
    for p in previews:
        for m in p.matches[:5]:
            sample.append({
                "source_filename": p.source_filename,
                "status": m.status,
                "internal_claim_id": m.internal_claim_id,
                "billed_amount": float(m.era_claim.billed_amount or 0),
                "paid_amount": float(m.era_claim.paid_amount or 0),
                "reversal_reason": m.reversal_reason,
            })

    issues = []
    for p in previews:
        for m in p.matches:
            if m.status in ("unmatched", "reversal_flagged", "malformed_clp01"):
                issues.append({
                    "source_filename": p.source_filename,
                    "status": m.status,
                    "internal_claim_id": m.internal_claim_id,
                    "billed_amount": float(m.era_claim.billed_amount or 0),
                    "reason": m.reversal_reason or None,
                })

    return {
        "session_id": session_id,
        "files": [_file_summary(p) for p in previews],
        "totals": totals,
        "sample_matches": sample,
        "issues": issues,
        "expires_at": expires_at.isoformat(),
    }
```

- [ ] **Step 4: Wire router into `main.py`**

Update imports line in `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/main.py` to include `era_posting`:
```python
from app.routers import ... (existing list) ..., era_posting
```

Add `include_router` below `claim_id_bootstrap`:
```python
app.include_router(claim_id_bootstrap.router, prefix="/api", dependencies=BILLING)
app.include_router(era_posting.router, prefix="/api", dependencies=BILLING)
```

- [ ] **Step 5: Run to verify GREEN**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_era_posting_upload.py tests/ -v 2>&1 | tail -10
```
Expected: 4 new PASS + 206 prior PASS = **210 total**.

- [ ] **Step 6: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add backend/app/routers/era_posting.py backend/app/main.py backend/tests/test_era_posting_upload.py
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "feat(backend): POST /imports/era-posting upload + multi-file preview"
```

---

## Task 9: Backend — `era_posting` commit endpoint

**Files:**
- Modify: `backend/app/routers/era_posting.py` (append commit)
- Create: `backend/tests/test_era_posting_commit.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_era_posting_commit.py`:

```python
"""Commit tests for ERA posting."""
from decimal import Decimal
from pathlib import Path
from app.models.audit import AuditLog
from app.models.claim import Claim, ClaimStatus, InsuranceOrder, EraFile as EraFileModel
from app.models.patient import Patient
from app.models.payment import Payment
from app.services import import_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "johns_hopkins_era.835"


def _link_one_claim(db):
    p = Patient(patient_id="45740", first_name="A", last_name="B")
    db.add(p); db.commit(); db.refresh(p)
    c = Claim(
        claim_number="V1", patient_id=p.id,
        patient_control_number="216059P45740",
        billed_amount=Decimal("253.76"),
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING, balance=Decimal("253.76"),
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


def _upload(client):
    import_sessions._sessions.clear()
    with FIXTURE.open("rb") as f:
        return client.post(
            "/api/imports/era-posting",
            files=[("file", (FIXTURE.name, f, "application/octet-stream"))],
        ).json()


def test_commit_posts_matched_claims(client, db):
    c = _link_one_claim(db)
    preview = _upload(client)
    assert preview["totals"]["n_matched"] == 1

    r = client.post(f"/api/imports/era-posting/{preview['session_id']}/commit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["claims_posted"] == 1
    assert body["payments_created"] == 1
    # One EraFile row created
    era_files = db.query(EraFileModel).all()
    assert len(era_files) == 1


def test_commit_writes_per_claim_audit(client, db):
    c = _link_one_claim(db)
    preview = _upload(client)
    client.post(f"/api/imports/era-posting/{preview['session_id']}/commit")
    audit = db.query(AuditLog).filter(
        AuditLog.action == "POST_PAYMENT",
        AuditLog.resource_type == "claim",
    ).all()
    assert len(audit) == 1
    assert audit[0].user_name == "tester@waldorfwomenscare.com"
    assert audit[0].patient_id == str(c.patient_id)


def test_commit_writes_top_level_import_audit(client, db):
    _link_one_claim(db)
    preview = _upload(client)
    client.post(f"/api/imports/era-posting/{preview['session_id']}/commit")
    audit = db.query(AuditLog).filter(
        AuditLog.resource_type == "era_file",
        AuditLog.action == "IMPORT",
    ).all()
    assert len(audit) == 1


def test_commit_404_on_unknown_session(client, db):
    r = client.post("/api/imports/era-posting/nope/commit")
    assert r.status_code == 404


def test_commit_forbidden_for_clinical(clinical_client, db, client):
    _link_one_claim(db)
    preview = _upload(client)
    r = clinical_client.post(
        f"/api/imports/era-posting/{preview['session_id']}/commit")
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify RED**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_era_posting_commit.py -v 2>&1 | tail -10
```
Expected: 5 FAIL (404 — commit not defined).

- [ ] **Step 3: Append commit to `era_posting.py`**

Append to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/era_posting.py`:

```python
from app.models.claim import EraFile as EraFileModel
from app.models.denial import Denial
from app.models.payment import Payment
from app.services.audit_service import log_action
from app.services.era_poster import post_claim


@router.post("/era-posting/{session_id}/commit")
def commit_eras(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    entry = import_sessions.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="session not found or expired")

    previews: List[EraFilePreview] = entry.payload["previews"]
    user_email = current_user.get("email")

    claims_posted = 0
    payments_created = 0
    claims_already_posted = 0
    claims_unmatched = 0
    claims_reversal_flagged = 0
    claims_cb_skipped = 0
    claims_malformed = 0
    denials_before = db.query(Denial).count()
    errors: list = []

    for p in previews:
        era = p.era
        file_path = p.__dict__.get("_file_path", "")
        era_file_row = EraFileModel(
            filename=p.source_filename,
            file_path=file_path,
            payer_name=era.payer_name,
            payer_id=era.payer_id,
            check_number=era.check_number,
            check_date=era.check_date,
            check_amount=era.check_amount,
            transaction_count=len(era.claims),
            status="processed" if not era.parse_errors else "partial",
            error_log="\n".join(era.parse_errors) if era.parse_errors else None,
            imported_by=user_email or "era-poster",
        )
        db.add(era_file_row); db.commit(); db.refresh(era_file_row)

        for m in p.matches:
            if m.status == "matched":
                try:
                    post_claim(db, m, era, era_file_row, user_email=user_email)
                    claims_posted += 1
                    payments_created += 1
                except Exception as exc:
                    db.rollback()
                    errors.append({"internal_claim_id": m.internal_claim_id,
                                   "message": f"{type(exc).__name__}: {exc}"})
            elif m.status == "already_posted":
                claims_already_posted += 1
            elif m.status == "unmatched":
                claims_unmatched += 1
            elif m.status == "reversal_flagged":
                claims_reversal_flagged += 1
            elif m.status == "cb_prefix_skipped":
                claims_cb_skipped += 1
            elif m.status == "malformed_clp01":
                claims_malformed += 1

        log_action(
            db, "IMPORT", "era_file",
            resource_id=str(era_file_row.id), user_name=user_email,
            description=(f"{p.source_filename} — {claims_posted} posted, "
                         f"{claims_unmatched} unmatched"),
        )

    denials_created = db.query(Denial).count() - denials_before

    import_sessions.purge(session_id)

    return {
        "files_processed": len(previews),
        "claims_posted": claims_posted,
        "claims_already_posted": claims_already_posted,
        "claims_unmatched": claims_unmatched,
        "claims_reversal_flagged": claims_reversal_flagged,
        "claims_cb_skipped": claims_cb_skipped,
        "claims_malformed": claims_malformed,
        "payments_created": payments_created,
        "denials_created": denials_created,
        "errors": errors,
    }
```

- [ ] **Step 4: Run to verify GREEN**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_era_posting_commit.py tests/ -v 2>&1 | tail -10
```
Expected: 5 new PASS + 210 prior PASS = **215 total**.

- [ ] **Step 5: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add backend/app/routers/era_posting.py backend/tests/test_era_posting_commit.py
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "feat(backend): POST /imports/era-posting/{id}/commit — post payments + adjustments + denials"
```

---

## Task 10: Backend — legacy cleanup

**Files:**
- Modify: `backend/app/services/era_import_service.py` (short-circuit `import_era_file`)
- Modify: `backend/app/routers/imports.py` (catch NotImplementedError → 410)
- Create: `backend/tests/test_legacy_era_disabled.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_legacy_era_disabled.py`:

```python
"""Tests that the legacy ERA auto-import path is disabled in favor of Phase 2c."""
from pathlib import Path
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "johns_hopkins_era.835"


def test_legacy_import_era_file_raises_not_implemented():
    from app.services.era_import_service import import_era_file
    with pytest.raises(NotImplementedError) as exc:
        import_era_file(None, None, "/tmp/x.835")
    assert "era-posting" in str(exc.value).lower()


def test_legacy_helpers_still_importable():
    """Phase 2c reuses _determine_claim_status, _create_denials, etc."""
    from app.services.era_import_service import (
        _determine_claim_status, _has_real_denials, _create_denials,
        SKIP_DENIAL_CODES, CONTRACTUAL_CODES,
    )
    assert callable(_determine_claim_status)
    assert callable(_create_denials)
    assert "45" in SKIP_DENIAL_CODES


def test_legacy_imports_upload_era_returns_410(client, db):
    with FIXTURE.open("rb") as f:
        r = client.post(
            "/api/imports/upload",
            files={"file": (FIXTURE.name, f, "application/octet-stream")},
        )
    assert r.status_code == 410, r.text
    detail = r.json()["detail"]
    # Works whether detail is a dict or plain str
    s = detail if isinstance(detail, str) else (detail.get("message") or str(detail))
    assert "era-posting" in s.lower()
```

- [ ] **Step 2: Run to verify RED**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_legacy_era_disabled.py -v 2>&1 | tail -10
```
Expected: 3 FAIL (legacy still creates Claims, no 410).

- [ ] **Step 3: Short-circuit `import_era_file`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/services/era_import_service.py`.

Replace ONLY the body of `import_era_file(...)` (the function defined around line 43) with:

```python
def import_era_file(
    db,
    era,
    file_path,
    imported_by: str = "system",
):
    raise NotImplementedError(
        "Legacy ERA auto-import was retired in Phase 2c. "
        "Use POST /api/imports/era-posting for payment posting."
    )
```

Keep `_determine_claim_status`, `_map_insurance_order`, `_import_claim`, `_has_real_denials`, `_create_denials`, and the constants untouched (era_poster imports them).

- [ ] **Step 4: Catch in `imports.py`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/imports.py`.

Find the block around line 60 where `import_era_file(...)` is called. Wrap it:

```python
    # For ERA files, persist to DB automatically
    if result.format == "era835" and result.era_data:
        try:
            era_file = import_era_file(db, result.era_data, save_path)
        except NotImplementedError as e:
            raise HTTPException(status_code=410, detail={
                "message": str(e),
                "migration_endpoint": "/api/imports/era-posting",
            })
        response["era_file_id"] = str(era_file.id)
        ...
```

- [ ] **Step 5: Run to verify GREEN**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_legacy_era_disabled.py tests/ -v 2>&1 | tail -15
```
Expected: 3 new PASS + 215 prior PASS = **218 total**.

- [ ] **Step 6: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add backend/app/services/era_import_service.py backend/app/routers/imports.py backend/tests/test_legacy_era_disabled.py
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "chore(backend): retire legacy ERA auto-import path (410 Gone with migration note)"
```

---

## Task 11: Frontend — banner + Card 1 skeleton (dropzone + uploading + error)

**Files:**
- Modify: `frontend/src/pages/ImportFiles.jsx`

- [ ] **Step 1: Add banner + Card 1 skeleton**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/ImportFiles.jsx`.

Add `Link2` to the lucide-react import:
```js
import { Upload, FileText, CheckCircle, AlertCircle, Clock, Database, Link2 } from 'lucide-react'
```

Inside the `ImportFiles` component, after the existing `chargeState` / `chargeInputRef` lines, add:

```js
  const [bootstrapState, setBootstrapState] = useState(null)
  const bootstrapInputRef = useRef()

  const handleBootstrapFile = async (file) => {
    setBootstrapState({ uploading: true, filename: file.name })
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await api.post('/imports/claim-id-bootstrap', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setBootstrapState({ preview: res.data })
    } catch (e) {
      setBootstrapState({ error: { message: e.response?.data?.detail || e.message } })
    }
  }
```

In the JSX return, before the existing Charge Analysis card (so ordering is `banner → bootstrap → charge analysis → era posting → era history`), insert:

```jsx
      {/* Phase 2c banner */}
      <div className="card border border-amber-300 bg-amber-50 mb-6">
        <div className="flex items-center gap-2 text-amber-800 text-sm">
          <AlertCircle size={16} />
          <strong>Legacy ERA auto-posting is disabled.</strong>
          <span>Use the ERA 835 Payment Posting card below.</span>
        </div>
      </div>

      {/* Claim ID Bootstrap (Phase 2c Part 1) */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-1">
          <Link2 size={16} className="text-primary-600" />
          <h2 className="text-sm font-semibold text-gray-800">Link Claim IDs (PrimeSuite Claims Analysis)</h2>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Upload the Claims Analysis <code>.xls</code> export to link each claim to its PrimeSuite Claim ID.
          Enables ERA payment posting. Secondary/tertiary claim records are created when Claims Analysis shows them.
        </p>

        {!bootstrapState && (
          <div
            className="border-2 border-dashed rounded-lg p-6 text-center cursor-pointer border-gray-300 hover:border-primary-400 hover:bg-gray-50"
            onClick={() => bootstrapInputRef.current?.click()}
            onDragOver={e => e.preventDefault()}
            onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) handleBootstrapFile(f) }}
          >
            <input ref={bootstrapInputRef} type="file" accept=".xls,.xlsx" className="hidden"
                   onChange={e => e.target.files[0] && handleBootstrapFile(e.target.files[0])} />
            <p className="text-sm text-gray-700">📄 Drop <code>.xls</code> here or click to browse</p>
          </div>
        )}

        {bootstrapState?.uploading && (
          <div className="border-2 border-dashed rounded-lg p-6 text-center border-gray-300 text-gray-500">
            <div className="animate-spin inline-block text-lg mr-2">⟳</div>
            Parsing <code>{bootstrapState.filename}</code>…
          </div>
        )}

        {bootstrapState?.error && (
          <div className="card border border-red-200 bg-red-50">
            <div className="flex items-center gap-2">
              <AlertCircle size={16} className="text-red-600" />
              <span className="font-semibold text-red-700 text-sm">Upload failed</span>
            </div>
            <pre className="text-xs text-red-600 mt-2 whitespace-pre-wrap">{typeof bootstrapState.error.message === 'string' ? bootstrapState.error.message : JSON.stringify(bootstrapState.error.message)}</pre>
            <button className="btn-secondary text-xs mt-2" onClick={() => setBootstrapState(null)}>Try another file</button>
          </div>
        )}
      </div>
```

- [ ] **Step 2: Verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -8
```
Expected: success.

- [ ] **Step 3: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add frontend/src/pages/ImportFiles.jsx
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "feat(frontend): Phase 2c banner + Claim ID bootstrap dropzone + uploading + error"
```

---

## Task 12: Frontend — Card 1 preview + commit + success

**Files:**
- Modify: `frontend/src/pages/ImportFiles.jsx`

- [ ] **Step 1: Add preview + success render**

Inside the Bootstrap card (after the `bootstrapState?.error` block and before its closing `</div>`), insert:

```jsx
        {bootstrapState?.preview && !bootstrapState.success && (
          <BootstrapPreview
            preview={bootstrapState.preview}
            committing={bootstrapState.committing}
            onCancel={() => setBootstrapState(null)}
            onCommit={async () => {
              setBootstrapState(s => ({ ...s, committing: true }))
              try {
                const res = await api.post(`/imports/claim-id-bootstrap/${bootstrapState.preview.session_id}/commit`)
                setBootstrapState({ success: res.data })
              } catch (e) {
                setBootstrapState(s => ({
                  preview: s.preview,
                  error: { message: e.response?.data?.detail || e.message },
                }))
              }
            }}
          />
        )}
        {bootstrapState?.success && (
          <BootstrapSuccess result={bootstrapState.success}
                            onAgain={() => setBootstrapState(null)} />
        )}
```

Below the existing `ChargeAnalysisSuccess` helper (outside the component function, above `secondsUntil`), add:

```jsx
function BootstrapPreview({ preview, committing, onCancel, onCommit }) {
  const [showIssues, setShowIssues] = useState(false)
  const [remaining, setRemaining] = useState(() => secondsUntil(preview.expires_at))
  useEffect(() => {
    const id = setInterval(() => setRemaining(secondsUntil(preview.expires_at)), 1000)
    return () => clearInterval(id)
  }, [preview.expires_at])
  const expired = remaining <= 0

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-semibold text-gray-800">Preview · {preview.source_filename}</div>
        <div className="text-xs text-gray-500 font-mono">
          {expired ? 'Session expired' : `Expires in ${formatRemaining(remaining)}`}
        </div>
      </div>
      <div className="text-xs text-gray-500 mb-3">
        {preview.unique_claims} unique claims · {preview.total_rows} rows
      </div>

      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Claim IDs</div>
      <div className="text-sm space-y-0.5 mb-3">
        <div><span className="text-green-600 mr-1">✓</span>{preview.will_patch} will be linked</div>
        <div><span className="text-primary-600 mr-1">+</span>{preview.will_create_secondary} secondary claims will be created</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{preview.already_set} already linked</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{preview.no_patient + preview.no_claim} not found in system</div>
        <div><span className="text-amber-600 mr-1">⚠</span>{preview.ambiguous} ambiguous</div>
        <div><span className="text-amber-600 mr-1">⚠</span>{preview.conflicts} conflicts</div>
      </div>

      {preview.issues && preview.issues.length > 0 && (
        <div className="text-xs text-gray-600 mb-2">
          <strong>{preview.issues.length} issues</strong>
          <button className="ml-2 text-primary-600 underline" onClick={() => setShowIssues(v => !v)}>
            {showIssues ? 'Hide ▴' : 'Show ▾'}
          </button>
        </div>
      )}
      {showIssues && (
        <div className="max-h-40 overflow-y-auto border border-gray-100 rounded p-2 bg-gray-50 text-xs mb-3">
          {preview.issues.map((i, idx) => (
            <div key={idx} className="py-0.5">
              <span className={i.severity === 'error' ? 'text-red-600 font-semibold' : 'text-amber-600 font-semibold'}>
                {i.severity.toUpperCase()}
              </span>
              {i.claim_id && <> · Claim <code>{i.claim_id}</code></>}
              {' · '}{i.message}
            </div>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-2 mt-2">
        <button className="btn-secondary text-xs" disabled={committing} onClick={onCancel}>Cancel</button>
        <button className="btn-primary text-xs" disabled={committing || expired} onClick={onCommit}>
          {committing ? 'Committing…' : expired ? 'Session expired' : 'Commit'}
        </button>
      </div>
    </div>
  )
}

function BootstrapSuccess({ result, onAgain }) {
  return (
    <div className="card border border-green-200 bg-green-50">
      <div className="flex items-center gap-2 mb-2">
        <CheckCircle size={16} className="text-green-700" />
        <span className="font-semibold text-green-800 text-sm">Claim IDs linked</span>
      </div>
      <div className="text-xs text-green-900 mb-3">{result.source_filename}</div>
      <div className="grid grid-cols-2 gap-1 text-xs mb-3">
        <div>Claims patched: <span className="font-mono font-semibold">{result.claims_patched}</span></div>
        <div>Secondary created: <span className="font-mono font-semibold">{result.secondary_claims_created}</span></div>
        <div>Already set: <span className="font-mono">{result.already_set}</span></div>
        <div>Unmatched: <span className="font-mono">{result.unmatched}</span></div>
        <div>Ambiguous: <span className="font-mono">{result.ambiguous}</span></div>
        <div>Conflicts: <span className="font-mono">{result.conflicts}</span></div>
      </div>
      <div className="flex gap-2">
        <a href="/claims" className="btn-primary text-xs">View claims →</a>
        <button className="btn-secondary text-xs" onClick={onAgain}>Upload another file</button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -8
```
Expected: success.

- [ ] **Step 3: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add frontend/src/pages/ImportFiles.jsx
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "feat(frontend): Claim ID bootstrap preview + success cards"
```

---

## Task 13: Frontend — Card 2 (ERA) skeleton (multi-file)

**Files:**
- Modify: `frontend/src/pages/ImportFiles.jsx`

- [ ] **Step 1: Add Card 2 skeleton**

Inside the component, after the bootstrap state block, add:

```js
  const [eraState, setEraState] = useState(null)
  const eraInputRef = useRef()

  const handleEraFiles = async (fileList) => {
    const files = Array.from(fileList).filter(Boolean)
    if (!files.length) return
    setEraState({ uploading: true, filenames: files.map(f => f.name) })
    const form = new FormData()
    for (const f of files) form.append('file', f)
    try {
      const res = await api.post('/imports/era-posting', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setEraState({ preview: res.data })
    } catch (e) {
      setEraState({ error: { message: e.response?.data?.detail || e.message } })
    }
  }
```

In the JSX, directly after the Bootstrap card closing `</div>` (before Charge Analysis card), insert:

```jsx
      {/* ERA 835 Payment Posting (Phase 2c Part 2) */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-1">
          <FileText size={16} className="text-primary-600" />
          <h2 className="text-sm font-semibold text-gray-800">ERA 835 Payment Posting</h2>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Upload one or more ERA <code>.835</code> files to post payments to existing claims.
          Strict match on linked Claim ID. Reversals and unmatched claims are flagged.
        </p>

        {!eraState && (
          <div
            className="border-2 border-dashed rounded-lg p-6 text-center cursor-pointer border-gray-300 hover:border-primary-400 hover:bg-gray-50"
            onClick={() => eraInputRef.current?.click()}
            onDragOver={e => e.preventDefault()}
            onDrop={e => { e.preventDefault(); handleEraFiles(e.dataTransfer.files) }}
          >
            <input ref={eraInputRef} type="file" accept=".835,.x12,.edi" multiple className="hidden"
                   onChange={e => handleEraFiles(e.target.files)} />
            <p className="text-sm text-gray-700">📋 Drop one or more <code>.835</code> files here or click to browse</p>
          </div>
        )}

        {eraState?.uploading && (
          <div className="border-2 border-dashed rounded-lg p-6 text-center border-gray-300 text-gray-500">
            <div className="animate-spin inline-block text-lg mr-2">⟳</div>
            Parsing {eraState.filenames.length} file{eraState.filenames.length > 1 ? 's' : ''}…
          </div>
        )}

        {eraState?.error && (
          <div className="card border border-red-200 bg-red-50">
            <div className="flex items-center gap-2">
              <AlertCircle size={16} className="text-red-600" />
              <span className="font-semibold text-red-700 text-sm">Upload failed</span>
            </div>
            <pre className="text-xs text-red-600 mt-2 whitespace-pre-wrap">{typeof eraState.error.message === 'string' ? eraState.error.message : JSON.stringify(eraState.error.message)}</pre>
            <button className="btn-secondary text-xs mt-2" onClick={() => setEraState(null)}>Try again</button>
          </div>
        )}
      </div>
```

- [ ] **Step 2: Verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -8
```
Expected: success.

- [ ] **Step 3: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add frontend/src/pages/ImportFiles.jsx
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "feat(frontend): ERA Payment Posting card — multi-file dropzone + uploading + error"
```

---

## Task 14: Frontend — Card 2 preview + commit + success

**Files:**
- Modify: `frontend/src/pages/ImportFiles.jsx`

- [ ] **Step 1: Add preview + success render**

Inside the ERA card, after `eraState?.error` block and before its closing `</div>`, insert:

```jsx
        {eraState?.preview && !eraState.success && (
          <EraPreview
            preview={eraState.preview}
            committing={eraState.committing}
            onCancel={() => setEraState(null)}
            onCommit={async () => {
              setEraState(s => ({ ...s, committing: true }))
              try {
                const res = await api.post(`/imports/era-posting/${eraState.preview.session_id}/commit`)
                setEraState({ success: res.data })
              } catch (e) {
                setEraState(s => ({
                  preview: s.preview,
                  error: { message: e.response?.data?.detail || e.message },
                }))
              }
            }}
          />
        )}
        {eraState?.success && (
          <EraSuccess result={eraState.success} onAgain={() => setEraState(null)} />
        )}
```

After `BootstrapSuccess`, add:

```jsx
function EraPreview({ preview, committing, onCancel, onCommit }) {
  const [showIssues, setShowIssues] = useState(false)
  const [remaining, setRemaining] = useState(() => secondsUntil(preview.expires_at))
  useEffect(() => {
    const id = setInterval(() => setRemaining(secondsUntil(preview.expires_at)), 1000)
    return () => clearInterval(id)
  }, [preview.expires_at])
  const expired = remaining <= 0
  const t = preview.totals

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-semibold text-gray-800">
          Preview · {t.n_files} ERA file{t.n_files > 1 ? 's' : ''}
        </div>
        <div className="text-xs text-gray-500 font-mono">
          {expired ? 'Session expired' : `Expires in ${formatRemaining(remaining)}`}
        </div>
      </div>
      <div className="text-xs text-gray-500 mb-3">
        Combined check total: ${t.combined_check_amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </div>

      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Totals</div>
      <div className="text-sm space-y-0.5 mb-3">
        <div><span className="text-green-600 mr-1">✓</span>{t.n_matched} will be posted</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{t.n_already_posted} already posted (skipped)</div>
        <div><span className="text-amber-600 mr-1">⚠</span>{t.n_unmatched} unmatched (no linked Claim ID)</div>
        <div><span className="text-amber-600 mr-1">⚠</span>{t.n_reversals} reversals flagged</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{t.n_cb_skipped} CB-prefix ModMed claims</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{t.n_malformed} malformed CLP01</div>
      </div>

      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Per file</div>
      <div className="text-xs space-y-1 mb-3">
        {preview.files.map((f, idx) => (
          <div key={idx} className="border border-gray-100 rounded p-2">
            <div className="font-mono truncate">{f.source_filename}</div>
            <div className="text-gray-500">
              Check #{f.check_number} · ${f.check_amount.toLocaleString(undefined, { minimumFractionDigits: 2 })} ·
              {' '}{f.n_matched} matched / {f.n_unmatched} unmatched / {f.n_reversals} reversals
            </div>
          </div>
        ))}
      </div>

      {preview.issues && preview.issues.length > 0 && (
        <div className="text-xs text-gray-600 mb-2">
          <strong>{preview.issues.length} flagged</strong>
          <button className="ml-2 text-primary-600 underline" onClick={() => setShowIssues(v => !v)}>
            {showIssues ? 'Hide ▴' : 'Show ▾'}
          </button>
        </div>
      )}
      {showIssues && (
        <div className="max-h-40 overflow-y-auto border border-gray-100 rounded p-2 bg-gray-50 text-xs mb-3">
          {preview.issues.map((i, idx) => (
            <div key={idx} className="py-0.5">
              <span className="text-amber-700 font-semibold">{i.status.toUpperCase()}</span>
              {' · '}<code>{i.internal_claim_id || '—'}</code>
              {i.billed_amount > 0 && <> · billed ${i.billed_amount.toFixed(2)}</>}
              {i.reason && <> · {i.reason}</>}
              <span className="text-gray-400"> ({i.source_filename})</span>
            </div>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-2 mt-2">
        <button className="btn-secondary text-xs" disabled={committing} onClick={onCancel}>Cancel</button>
        <button className="btn-primary text-xs" disabled={committing || expired || t.n_matched === 0} onClick={onCommit}>
          {committing ? 'Committing…' : expired ? 'Session expired' : 'Post payments'}
        </button>
      </div>
    </div>
  )
}

function EraSuccess({ result, onAgain }) {
  return (
    <div className="card border border-green-200 bg-green-50">
      <div className="flex items-center gap-2 mb-2">
        <CheckCircle size={16} className="text-green-700" />
        <span className="font-semibold text-green-800 text-sm">Payments posted</span>
      </div>
      <div className="grid grid-cols-2 gap-1 text-xs mb-3">
        <div>Files processed: <span className="font-mono">{result.files_processed}</span></div>
        <div>Claims posted: <span className="font-mono font-semibold">{result.claims_posted}</span></div>
        <div>Payments created: <span className="font-mono">{result.payments_created}</span></div>
        <div>Denials created: <span className="font-mono">{result.denials_created}</span></div>
        <div>Unmatched: <span className="font-mono">{result.claims_unmatched}</span></div>
        <div>Reversals flagged: <span className="font-mono">{result.claims_reversal_flagged}</span></div>
      </div>
      {result.errors && result.errors.length > 0 && (
        <div className="text-xs text-red-700 border-t border-green-200 pt-2 mt-2">
          {result.errors.length} errors: {result.errors.map(e => e.internal_claim_id).join(', ')}
        </div>
      )}
      <div className="flex gap-2 mt-2">
        <a href="/claims" className="btn-primary text-xs">View claims →</a>
        <button className="btn-secondary text-xs" onClick={onAgain}>Upload more ERAs</button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -8
```
Expected: success.

- [ ] **Step 3: Commit**

```bash
git -C /Users/wwcclaudecode/Documents/wwc-era-project add frontend/src/pages/ImportFiles.jsx
git -C /Users/wwcclaudecode/Documents/wwc-era-project commit -m "feat(frontend): ERA Payment Posting preview + success (with per-file breakdown)"
```

---

## Task 15: Manual verification + final test run

**Files:** none — runtime verification.

- [ ] **Step 1: Full backend test suite**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ 2>&1 | tail -5
```
Expected: **218 passed** (167 prior + 51 new from Tasks 1-10). Note: the plan says "~39 new" in the spec; this plan delivered 51 (richer coverage). All green is the goal.

- [ ] **Step 2: Start dev stack**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8000 &
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run dev &
sleep 5 && curl -sS http://localhost:8000/api/health
```

- [ ] **Step 3: Manual UI checklist**

Sign in as admin. Navigate to `/imports`.

- [ ] Amber banner visible at top.
- [ ] Card 1 "Link Claim IDs" visible above "Charge Analysis Import".
- [ ] Card 2 "ERA 835 Payment Posting" visible below Card 1.
- [ ] Card 1: drop `Claim Analysis 2026.01.xls` → preview shows counts. Some will be `will_patch` (based on the 758 claims Phase 2b imported).
- [ ] Card 1 commit → success card shows counts. Open a claim on `/claims/:id` → `patient_control_number` visible in edit drawer.
- [ ] Card 2: drop one ERA → preview shows matched (should be > 0 if Card 1 linked some IDs). Commit → success.
- [ ] Open a posted claim → Phase 2a drawer shows updated status, a Payment row, and any Denial rows.
- [ ] Card 2: drop 2-3 ERA files simultaneously → preview shows per-file breakdown + combined totals.
- [ ] Re-upload same ERA → preview shows `already_posted` count.
- [ ] Legacy drop zone (top of page): drop a `.835` → 410 error with migration message.
- [ ] Flip your group to `clinical` via sqlite → `/imports` redirects. Flip back to `admin`.

- [ ] **Step 4: Kill dev servers**

```bash
kill %1 %2 2>/dev/null
```

No commit needed for this task.

---

## Summary

**Total new tests:** 51 backend (2 fixture + 6 parser + 9 matcher + 3 bootstrap upload + 4 bootstrap commit + 7 poster match + 8 poster post + 4 era posting upload + 5 era posting commit + 3 legacy disabled).

**Total commits:** 14 (tasks 1-14 each a commit; task 15 is verification only).

**Files created:**
- Backend: 4 routers/services, 2 fixture files, 9 test files = 15 files.
- Frontend: none new (modifications only).

**Files modified:**
- `backend/app/main.py` (2 new include_router calls).
- `backend/app/services/era_import_service.py` (short-circuit `import_era_file`).
- `backend/app/routers/imports.py` (catch NotImplementedError → 410).
- `frontend/src/pages/ImportFiles.jsx` (banner + 2 cards + 4 helper components).

**After this plan:** Charge-Analysis-imported claims can be linked to PrimeSuite Claim IDs; ERA 835 files post payments to those linked claims; secondary/tertiary claims are created where Claims Analysis shows them; the legacy auto-create path is retired.
