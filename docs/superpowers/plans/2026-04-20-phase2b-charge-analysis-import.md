# Phase 2b — Charge Analysis Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a two-step upload → preview → commit flow on `/imports` that ingests PrimeSuite Charge Analysis `.xls` exports, creates `Claim` + `ServiceLine` records (with adjustments, patient auto-create), skips voided rows and VisitID duplicates. Include a one-time wipe script for legacy claim data.

**Architecture:** Pure parser module (`charge_analysis_importer.py`) reads the Excel file into structured dataclasses. Session store (`import_sessions.py`) caches parsed payloads for 30 min between upload and commit. Router (`charge_imports.py`) has two endpoints both guarded by BILLING. Frontend adds a second card to `ImportFiles.jsx` with its own state machine.

**Tech Stack:** FastAPI + SQLAlchemy + pytest + pandas/xlrd (backend); React 18 + Vite + Tailwind + React Query v5 (frontend).

**Reference spec:** `docs/superpowers/specs/2026-04-20-phase2b-charge-analysis-import-design.md`

---

## Pre-flight notes

- Fixtures already in `backend/tests/conftest.py`: `db`, `client` (admin), `billing_client`, `clinical_client`. No fixture changes.
- `pandas 2.3.3` and `xlrd 2.0.2` are installed in the backend venv — `xlrd` handles `.xls` (legacy); `openpyxl` handles `.xlsx`.
- Source fixture file lives at `/Users/wwcclaudecode/Documents/Charge Analysis Test4.xls` (~1.1 MB, 45 columns, 1717 rows, 759 unique VisitIDs). We copy it into the repo at `backend/tests/fixtures/charge_analysis_test4.xls`.
- The `Patient` model has a SINGLE `address` text field (not Line 1 / City / State / Zip columns). The importer packs the four address columns from the file into one string: `"{line1}\n{line2}\n{city}, {state} {zip}"` with blank/None parts elided. The file's `Patient: Sex` column has no corresponding DB column — we capture it but drop it on patient-create.
- Phase 2a HIPAA audit pattern (`user_name` + `patient_id` on every `log_action` call) applies to every new audit row.
- Existing BILLING guard list in `main.py`: `BILLING = [Depends(auth.require_group("admin", "billing"))]`.
- `claim_math.recompute_balance(claim)` is imported from `app.services.claim_math` (Phase 2a).
- `log_action(db, action, resource_type, resource_id=..., patient_id=..., user_name=..., new_values=..., description=...)` from `app.services.audit_service`.
- Session store is a module-level dict (single-process only). Two workers would silently lose sessions — noted in its docstring with a Redis-swap TODO.

---

## Task 1: Backend — fixture file + smoke test

**Files:**
- Create: `backend/tests/fixtures/charge_analysis_test4.xls` (copied from `~/Documents/Charge Analysis Test4.xls`)
- Create: `backend/tests/fixtures/__init__.py` (empty)
- Create: `backend/tests/test_charge_analysis_fixture.py`

- [ ] **Step 1: Create fixture directory + copy the file**

```bash
mkdir -p /Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/fixtures
cp "/Users/wwcclaudecode/Documents/Charge Analysis Test4.xls" \
   /Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/fixtures/charge_analysis_test4.xls
touch /Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/fixtures/__init__.py
```

- [ ] **Step 2: Add a tiny smoke test that the fixture loads**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_charge_analysis_fixture.py`:

```python
"""Smoke test confirming the fixture file exists and loads as Excel."""
from pathlib import Path
import pandas as pd

FIXTURE = Path(__file__).parent / "fixtures" / "charge_analysis_test4.xls"


def test_fixture_exists():
    assert FIXTURE.exists(), f"missing fixture: {FIXTURE}"


def test_fixture_loads_with_pandas():
    df = pd.read_excel(FIXTURE, sheet_name=0)
    assert df.shape == (1717, 45)
    # Required anchor columns present
    assert "Visit: VisitID" in df.columns
    assert "Patient: Patient ID" in df.columns
    assert "Charge: Gross Charges" in df.columns
    assert "Charge: Void Indicator" in df.columns
```

- [ ] **Step 3: Run test to verify PASS**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_charge_analysis_fixture.py -v 2>&1 | tail -10
```
Expected: 2 tests PASS.

- [ ] **Step 4: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/tests/fixtures/ backend/tests/test_charge_analysis_fixture.py
git commit -m "test(backend): add Charge Analysis fixture file + smoke test"
```

---

## Task 2: Backend — `reset_claims_data.py` wipe script

**Files:**
- Create: `backend/app/scripts/__init__.py` (empty)
- Create: `backend/app/scripts/reset_claims_data.py`
- Create: `backend/tests/test_reset_claims_data.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_reset_claims_data.py`:

```python
"""Tests for the one-time claim-data wipe script."""
from decimal import Decimal
import pytest
from app.models.claim import Claim, ServiceLine, ClaimAdjustment, ServiceLineAdjustment, EraFile, ClaimStatus
from app.models.denial import Denial, DenialCategory, DenialStatus
from app.models.appeal import Appeal, AppealStatus
from app.models.audit import AuditLog
from app.models.patient import Patient
from app.models.user import User, UserGroup
from app.scripts.reset_claims_data import run


def _seed_all(db):
    # Claim-side rows that SHOULD be wiped
    c = Claim(claim_number="C1", status=ClaimStatus.PENDING, balance=Decimal("0"))
    db.add(c); db.commit(); db.refresh(c)

    sl = ServiceLine(claim_id=c.id, procedure_code="99213")
    db.add(sl); db.commit(); db.refresh(sl)

    db.add(ClaimAdjustment(claim_id=c.id, group_code="CO", reason_code="45", amount=Decimal("10")))
    db.add(ServiceLineAdjustment(service_line_id=sl.id, group_code="PR", reason_code="1", amount=Decimal("5")))

    d = Denial(claim_id=c.id, carc_code="16", category=DenialCategory.MISSING_INFORMATION, status=DenialStatus.OPEN, denied_amount=Decimal("10"))
    db.add(d); db.commit(); db.refresh(d)

    db.add(Appeal(denial_id=d.id, status=AppealStatus.DRAFT))
    db.add(EraFile(filename="x.835", file_path="/tmp/x.835"))

    # Audit rows: some that SHOULD be wiped, some that should survive
    db.add(AuditLog(action="UPDATE", resource_type="claim", resource_id=str(c.id)))
    db.add(AuditLog(action="UPDATE", resource_type="service_line", resource_id=str(sl.id)))
    db.add(AuditLog(action="DELETE", resource_type="denial", resource_id=str(d.id)))
    db.add(AuditLog(action="IMPORT", resource_type="charge_analysis_file", resource_id="abc"))
    db.add(AuditLog(action="USER_UPDATED", resource_type="user", resource_id="x@y.z"))  # survives
    db.add(AuditLog(action="VIEW", resource_type="document", resource_id="doc1"))        # survives

    # Non-claim rows that MUST survive
    db.add(Patient(patient_id="P001", first_name="Sur", last_name="Vive"))
    db.add(User(email="survivor@waldorfwomenscare.com", group=UserGroup.ADMIN))
    db.commit()


def test_wipe_deletes_claim_side_data_preserves_others(db):
    _seed_all(db)
    counts = run(confirm=True, session=db)

    assert db.query(Claim).count() == 0
    assert db.query(ServiceLine).count() == 0
    assert db.query(ClaimAdjustment).count() == 0
    assert db.query(ServiceLineAdjustment).count() == 0
    assert db.query(Denial).count() == 0
    assert db.query(Appeal).count() == 0
    assert db.query(EraFile).count() == 0

    # Audit wiped only for targeted resource_types
    surviving_types = {row.resource_type for row in db.query(AuditLog).all()}
    assert "claim" not in surviving_types
    assert "service_line" not in surviving_types
    assert "denial" not in surviving_types
    assert "charge_analysis_file" not in surviving_types
    assert "user" in surviving_types
    assert "document" in surviving_types

    # Non-claim tables untouched
    assert db.query(Patient).count() == 1
    assert db.query(User).count() == 1

    # Returned counts dict has the expected keys
    assert counts["claims"] >= 1
    assert counts["service_lines"] >= 1
    assert counts["audit_log"] >= 4
    assert "patients" not in counts  # proof the script never touched Patient


def test_wipe_refuses_without_confirm_flag(db):
    _seed_all(db)
    with pytest.raises(SystemExit):
        run(confirm=False, session=db)
    assert db.query(Claim).count() == 1


def test_wipe_is_idempotent(db):
    _seed_all(db)
    run(confirm=True, session=db)
    counts2 = run(confirm=True, session=db)
    assert all(v == 0 for v in counts2.values())
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_reset_claims_data.py -v 2>&1 | tail -15
```
Expected: all 3 tests FAIL with `ModuleNotFoundError: No module named 'app.scripts.reset_claims_data'`.

- [ ] **Step 3: Create empty package marker**

```bash
mkdir -p /Users/wwcclaudecode/Documents/wwc-era-project/backend/app/scripts
touch /Users/wwcclaudecode/Documents/wwc-era-project/backend/app/scripts/__init__.py
```

- [ ] **Step 4: Create `reset_claims_data.py`**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/scripts/reset_claims_data.py`:

```python
"""One-time claim data wipe — Phase 2b migration.

Deletes all legacy claim-side data so Charge Analysis imports can become the
new source of truth. Safe to keep in the repo after it's been used —
`--yes-i-am-sure` prevents accidental future runs. Touches data only, never
the schema.

Usage (from the backend/ directory):
    source venv/bin/activate
    python -m app.scripts.reset_claims_data --yes-i-am-sure
"""
import argparse
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.claim import (
    Claim, ServiceLine, ClaimAdjustment, ServiceLineAdjustment, EraFile,
)
from app.models.denial import Denial
from app.models.appeal import Appeal
from app.models.audit import AuditLog


WIPED_RESOURCE_TYPES = {
    "claim",
    "service_line",
    "claim_adjustment",
    "service_line_adjustment",
    "denial",
    "appeal",
    "era_file",
    "charge_analysis_file",
}


def run(confirm: bool, session: Optional[Session] = None) -> Dict[str, int]:
    """Wipe claim-side data. Returns a {table_name: rows_deleted} dict."""
    if not confirm:
        raise SystemExit("Refusing to run without --yes-i-am-sure flag.")

    db = session if session is not None else SessionLocal()
    owns_db = session is None
    counts: Dict[str, int] = {}
    try:
        # Leaf-first so child rows go before their parents.
        counts["service_line_adjustments"] = (
            db.query(ServiceLineAdjustment).delete(synchronize_session=False)
        )
        counts["claim_adjustments"] = (
            db.query(ClaimAdjustment).delete(synchronize_session=False)
        )
        counts["service_lines"] = db.query(ServiceLine).delete(synchronize_session=False)
        counts["appeals"] = db.query(Appeal).delete(synchronize_session=False)
        counts["denials"] = db.query(Denial).delete(synchronize_session=False)
        counts["claims"] = db.query(Claim).delete(synchronize_session=False)
        counts["era_files"] = db.query(EraFile).delete(synchronize_session=False)
        counts["audit_log"] = (
            db.query(AuditLog)
            .filter(AuditLog.resource_type.in_(WIPED_RESOURCE_TYPES))
            .delete(synchronize_session=False)
        )
        db.commit()
    finally:
        if owns_db:
            db.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes-i-am-sure", action="store_true")
    args = parser.parse_args()
    counts = run(confirm=args.yes_i_am_sure)
    print("Wipe complete. Rows deleted per table:")
    for table, n in counts.items():
        print(f"  {table:32s} {n:>8d}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_reset_claims_data.py tests/ -v 2>&1 | tail -20
```
Expected: 3 new tests PASS + all 125 prior tests PASS (123 from Phase 2a + 2 from Task 1) = 128 total.

- [ ] **Step 6: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/scripts/ backend/tests/test_reset_claims_data.py
git commit -m "feat(backend): reset_claims_data script — wipe legacy claim data"
```

---

## Task 3: Backend — parser module scaffolding + single-claim parsing

**Files:**
- Create: `backend/app/services/charge_analysis_importer.py`
- Create: `backend/tests/test_charge_analysis_parser.py`

- [ ] **Step 1: Write failing tests (scaffolding + single-row claim)**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_charge_analysis_parser.py`:

```python
"""Tests for the Charge Analysis parser (pure function, no DB)."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import pandas as pd
import pytest

from app.services.charge_analysis_importer import (
    parse, ChargeAnalysisImport, ParsedClaim, ParsedServiceLine, ParseIssue,
)

FIXTURE = Path(__file__).parent / "fixtures" / "charge_analysis_test4.xls"


def _build_df(rows):
    """Build a minimal DataFrame with every column the parser requires."""
    columns = [
        "Patient: Patient ID", "Patient: First Name", "Patient: Last Name",
        "Date: Service date of the Charge", "Procedure: Code",
        "Provider: Rendering", "Location: Service Location", "Visit: Visit Type",
        "Adjustment: Net Non-Primary Ins. Adjusted",
        "Adjustment: Net Patient/Other Adjusted",
        "Adjustment: Net Primary Ins. Adjusted",
        "Charge Balance: Collection", "Charge Balance: Insurance",
        "Charge Balance: Patient", "Charge Balance: Total",
        "Charge: Charge Amount", "Diagnosis: Primary Code",
        "Diagnosis: Primary ICD-10 Code",
        "Insurance: Charge Primary Ins. Class",
        "Insurance: Charge Primary Ins. Company",
        "Insurance: Charge Primary Ins. Plan",
        "Insurance: Charge Primary Policy Number",
        "Insurance: Charge Secondary Ins. Class",
        "Insurance: Charge Secondary Ins. Company",
        "Insurance: Charge Secondary Ins. Plan",
        "Insurance: Charge Secondary Policy Number",
        "Patient: Date Of Birth", "Patient: Phone Primary",
        "Visit: VisitID", "Charge: Co-Pay", "Charge: Net Units",
        "Patient: Address Line 1", "Patient: Address Line 2",
        "Patient: City", "Patient: State", "Patient: Zip Code",
        "Payment: Net Patient/Other Applied",
        "Payment: Net Primary Ins. Applied",
        "Procedure: Modifiers", "Provider: Rendering NPI",
        "Charge: Charge Voids", "Charge: Void Indicator", "Patient: Sex",
        "Provider: Billable NPI", "Charge: Gross Charges",
    ]
    # Pad every row dict with None for columns not set
    filled = []
    for r in rows:
        d = {c: None for c in columns}
        d.update(r)
        filled.append(d)
    return pd.DataFrame(filled, columns=columns)


BASE_ROW = {
    "Patient: Patient ID": "11175",
    "Patient: First Name": "SILVINA",
    "Patient: Last Name": "DELFIN-CRUZ",
    "Date: Service date of the Charge": "1/2/2026",
    "Procedure: Code": 76830,
    "Provider: Rendering": "Cooke, Aryian MD",
    "Adjustment: Net Non-Primary Ins. Adjusted": 0,
    "Adjustment: Net Patient/Other Adjusted": 0,
    "Adjustment: Net Primary Ins. Adjusted": -169.95,
    "Charge Balance: Collection": 0,
    "Charge Balance: Insurance": 0,
    "Charge Balance: Patient": 0,
    "Charge Balance: Total": 0,
    "Charge: Charge Amount": 289.70,
    "Diagnosis: Primary ICD-10 Code": "R10.20",
    "Insurance: Charge Primary Ins. Company": "BCBS -Carefirst FEP/DC Local- SB580",
    "Insurance: Charge Primary Policy Number": "F5E816281807",
    "Insurance: Charge Secondary Ins. Company": "No Secondary Insurance Company",
    "Patient: Date Of Birth": "9/12/1979",
    "Patient: Phone Primary": "240-416-4826",
    "Visit: VisitID": 262924,
    "Charge: Co-Pay": 0,
    "Charge: Net Units": 1,
    "Patient: Address Line 1": "12566 COUNCIL OAK DR",
    "Patient: City": "Waldorf",
    "Patient: State": "MD",
    "Patient: Zip Code": 20601,
    "Payment: Net Patient/Other Applied": 0,
    "Payment: Net Primary Ins. Applied": -119.75,
    "Provider: Rendering NPI": 1124225222,
    "Charge: Charge Voids": 0,
    "Charge: Void Indicator": "NO",
    "Patient: Sex": "Female",
    "Provider: Billable NPI": 1124225222,
    "Charge: Gross Charges": 289.70,
}


def test_parse_returns_dataclass(tmp_path):
    df = _build_df([BASE_ROW])
    path = tmp_path / "one_row.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert isinstance(result, ChargeAnalysisImport)
    assert result.total_rows == 1
    assert result.skipped_voids == 0
    assert len(result.claims) == 1
    assert len(result.issues) == 0
    assert result.source_filename == "one_row.xlsx"


def test_parse_single_line_claim_maps_all_fields(tmp_path):
    df = _build_df([BASE_ROW])
    path = tmp_path / "one.xlsx"
    df.to_excel(path, index=False)
    c = parse(str(path)).claims[0]

    assert c.visit_id == "262924"
    assert c.patient_external_id == "11175"
    assert c.date_of_service_from == date(2026, 1, 2)
    assert c.payer_name == "BCBS -Carefirst FEP/DC Local- SB580"
    assert c.subscriber_id == "F5E816281807"
    assert c.rendering_provider_name == "Cooke, Aryian MD"
    assert c.rendering_provider_npi == "1124225222"
    assert c.billing_provider_npi == "1124225222"
    # Rollups from a single service line
    assert c.billed_amount == Decimal("289.70")
    assert c.paid_amount == Decimal("119.75")          # abs(-119.75)
    assert c.contractual_adjustment == Decimal("169.95")  # abs(-169.95)
    assert c.other_adjustment == Decimal("0")
    assert c.patient_responsibility == Decimal("0")

    assert len(c.service_lines) == 1
    sl = c.service_lines[0]
    assert sl.procedure_code == "76830"
    assert sl.units == Decimal("1")
    assert sl.billed_amount == Decimal("289.70")
    assert sl.paid_amount == Decimal("119.75")
    assert sl.contractual_adjustment == Decimal("169.95")
    assert sl.date_of_service_from == date(2026, 1, 2)
    assert sl.diagnosis_codes == ["R10.20"]


def test_parse_missing_required_column_raises(tmp_path):
    df = _build_df([BASE_ROW]).drop(columns=["Visit: VisitID"])
    path = tmp_path / "bad.xlsx"
    df.to_excel(path, index=False)
    with pytest.raises(ValueError) as exc:
        parse(str(path))
    assert "Visit: VisitID" in str(exc.value)


def test_parse_real_fixture_file():
    """Full-size fixture parse — 758 non-voided claims, 104 voided rows, 0 errors."""
    result = parse(str(FIXTURE))
    assert result.total_rows == 1717
    assert result.skipped_voids == 104
    assert len(result.claims) == 758
    # Every claim has ≥1 service line
    assert all(len(c.service_lines) >= 1 for c in result.claims)
    # No parse errors on the clean fixture
    errors = [i for i in result.issues if i.severity == "error"]
    assert errors == [], f"unexpected errors: {errors[:5]}"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_charge_analysis_parser.py -v 2>&1 | tail -15
```
Expected: all 4 tests FAIL with `ModuleNotFoundError: No module named 'app.services.charge_analysis_importer'`.

- [ ] **Step 3: Create the parser module**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/services/charge_analysis_importer.py`:

```python
"""Charge Analysis importer — pure parser, no DB, no FastAPI.

Reads a PrimeSuite Charge Analysis .xls/.xlsx export and returns a
ChargeAnalysisImport dataclass. Does NOT perform patient matching or
deduplication — those happen at the endpoint / commit stage.
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
    "Patient: Patient ID",
    "Patient: First Name",
    "Patient: Last Name",
    "Date: Service date of the Charge",
    "Procedure: Code",
    "Provider: Rendering",
    "Provider: Rendering NPI",
    "Provider: Billable NPI",
    "Adjustment: Net Non-Primary Ins. Adjusted",
    "Adjustment: Net Patient/Other Adjusted",
    "Adjustment: Net Primary Ins. Adjusted",
    "Charge Balance: Patient",
    "Charge: Gross Charges",
    "Charge: Net Units",
    "Diagnosis: Primary ICD-10 Code",
    "Insurance: Charge Primary Ins. Company",
    "Insurance: Charge Primary Policy Number",
    "Insurance: Charge Secondary Ins. Company",
    "Insurance: Charge Secondary Policy Number",
    "Patient: Date Of Birth",
    "Patient: Phone Primary",
    "Patient: Address Line 1",
    "Patient: Address Line 2",
    "Patient: City",
    "Patient: State",
    "Patient: Zip Code",
    "Patient: Sex",
    "Payment: Net Patient/Other Applied",
    "Payment: Net Primary Ins. Applied",
    "Procedure: Modifiers",
    "Visit: VisitID",
    "Charge: Void Indicator",
]


@dataclass
class ParsedServiceLine:
    procedure_code: Optional[str]
    modifier_1: Optional[str]
    modifier_2: Optional[str]
    modifier_3: Optional[str]
    modifier_4: Optional[str]
    units: Decimal
    billed_amount: Decimal
    paid_amount: Decimal
    patient_responsibility: Decimal
    contractual_adjustment: Decimal
    other_adjustment: Decimal
    date_of_service_from: Optional[date]
    diagnosis_codes: List[str]


@dataclass
class ParsedClaim:
    visit_id: str
    patient_external_id: str
    patient_demographics: Dict[str, Any]
    date_of_service_from: Optional[date]
    payer_name: Optional[str]
    subscriber_id: Optional[str]
    secondary_payer_name: Optional[str]
    secondary_subscriber_id: Optional[str]
    rendering_provider_name: Optional[str]
    rendering_provider_npi: Optional[str]
    billing_provider_npi: Optional[str]
    billed_amount: Decimal
    paid_amount: Decimal
    patient_responsibility: Decimal
    contractual_adjustment: Decimal
    other_adjustment: Decimal
    service_lines: List[ParsedServiceLine] = field(default_factory=list)


@dataclass
class ParseIssue:
    severity: str  # "error" | "warning"
    row_index: int
    visit_id: Optional[str]
    message: str


@dataclass
class ChargeAnalysisImport:
    claims: List[ParsedClaim]
    skipped_voids: int
    issues: List[ParseIssue]
    source_filename: str
    total_rows: int


def _abs_decimal(v: Any) -> Decimal:
    """Coerce to Decimal and take absolute value. None/NaN → 0."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return Decimal("0")
    try:
        return abs(Decimal(str(v)))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _decimal(v: Any) -> Decimal:
    """Coerce to Decimal, preserving sign. None/NaN → 0."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return Decimal("0")
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _str_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    # pandas loves turning int-ish cells into "1124225222.0" floats
    if s.endswith(".0"):
        try:
            int_part = int(float(s))
            s = str(int_part)
        except ValueError:
            pass
    return s or None


def _parse_date(v: Any) -> Optional[date]:
    """Accept datetime, date, ISO string, or MM/DD/YYYY."""
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


def _pack_address(row: Dict[str, Any]) -> Optional[str]:
    line1 = _str_or_none(row.get("Patient: Address Line 1"))
    line2 = _str_or_none(row.get("Patient: Address Line 2"))
    city = _str_or_none(row.get("Patient: City"))
    state = _str_or_none(row.get("Patient: State"))
    zip_ = _str_or_none(row.get("Patient: Zip Code"))
    parts: List[str] = []
    if line1:
        parts.append(line1)
    if line2:
        parts.append(line2)
    city_state = ", ".join(p for p in [city, f"{state} {zip_}".strip() if (state or zip_) else ""] if p)
    if city_state:
        parts.append(city_state)
    return "\n".join(parts) or None


def _demographics_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "patient_id": _str_or_none(row.get("Patient: Patient ID")),
        "first_name": _str_or_none(row.get("Patient: First Name")),
        "last_name": _str_or_none(row.get("Patient: Last Name")),
        "date_of_birth": _parse_date(row.get("Patient: Date Of Birth")),
        "phone": _str_or_none(row.get("Patient: Phone Primary")),
        "address": _pack_address(row),
        # Captured but not persisted — Patient model has no `sex` column.
        "_sex": _str_or_none(row.get("Patient: Sex")),
    }


def _split_modifiers(v: Any) -> List[Optional[str]]:
    """Return [mod1, mod2, mod3, mod4] from a whitespace/comma separated string."""
    s = _str_or_none(v)
    if not s:
        return [None, None, None, None]
    parts = re.split(r"[\s,]+", s)
    parts = [p for p in parts if p]
    return (parts[:4] + [None] * 4)[:4]


def _build_service_line(row: Dict[str, Any], dx: Optional[str], dos: Optional[date]) -> ParsedServiceLine:
    mods = _split_modifiers(row.get("Procedure: Modifiers"))
    return ParsedServiceLine(
        procedure_code=_str_or_none(row.get("Procedure: Code")),
        modifier_1=mods[0],
        modifier_2=mods[1],
        modifier_3=mods[2],
        modifier_4=mods[3],
        units=_decimal(row.get("Charge: Net Units")),
        billed_amount=_decimal(row.get("Charge: Gross Charges")),
        paid_amount=_abs_decimal(row.get("Payment: Net Primary Ins. Applied")),
        patient_responsibility=_decimal(row.get("Charge Balance: Patient")),
        contractual_adjustment=_abs_decimal(row.get("Adjustment: Net Primary Ins. Adjusted")),
        other_adjustment=(
            _abs_decimal(row.get("Adjustment: Net Non-Primary Ins. Adjusted"))
            + _abs_decimal(row.get("Adjustment: Net Patient/Other Adjusted"))
        ),
        date_of_service_from=dos,
        diagnosis_codes=[dx] if dx else [],
    )


def parse(path: str) -> ChargeAnalysisImport:
    """Parse a Charge Analysis .xls/.xlsx file."""
    df = pd.read_excel(path, sheet_name=0)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    total_rows = int(len(df))
    issues: List[ParseIssue] = []
    skipped_voids = 0

    # Per-row parse into intermediate "(visit_id, row_dict, dx, dos, sl)" tuples,
    # then group by visit_id.
    groups: Dict[str, List[Dict[str, Any]]] = {}

    for row_index, raw in df.iterrows():
        row = raw.to_dict()

        # Void filter FIRST — skipped rows don't get further validation.
        void = _str_or_none(row.get("Charge: Void Indicator")) or ""
        if void.upper() == "YES":
            skipped_voids += 1
            continue

        visit_id = _str_or_none(row.get("Visit: VisitID"))
        if not visit_id:
            issues.append(ParseIssue("error", int(row_index), None,
                                     "missing Visit: VisitID — row dropped"))
            continue

        # Charge amount numeric check
        raw_charge = row.get("Charge: Gross Charges")
        if raw_charge is None or (isinstance(raw_charge, float) and pd.isna(raw_charge)):
            issues.append(ParseIssue("error", int(row_index), visit_id,
                                     "missing Charge: Gross Charges — row dropped"))
            continue
        try:
            Decimal(str(raw_charge))
        except (InvalidOperation, TypeError, ValueError):
            issues.append(ParseIssue("error", int(row_index), visit_id,
                                     f"non-numeric Charge: Gross Charges: {raw_charge!r} — row dropped"))
            continue

        # Modifier count warning
        raw_mod_str = _str_or_none(row.get("Procedure: Modifiers"))
        if raw_mod_str and len(re.split(r"[\s,]+", raw_mod_str)) > 4:
            issues.append(ParseIssue("warning", int(row_index), visit_id,
                                     f"more than 4 modifiers found in {raw_mod_str!r} — extras dropped"))

        # Negative charge warning
        if _decimal(raw_charge) < 0:
            issues.append(ParseIssue("warning", int(row_index), visit_id,
                                     f"negative Charge: Gross Charges: {raw_charge}"))

        groups.setdefault(visit_id, []).append({"__index__": int(row_index), **row})

    # Build one ParsedClaim per visit_id
    claims: List[ParsedClaim] = []
    for visit_id, rows in groups.items():
        first = rows[0]
        dos = _parse_date(first.get("Date: Service date of the Charge"))
        payer = _str_or_none(first.get("Insurance: Charge Primary Ins. Company"))
        sec_payer = _str_or_none(first.get("Insurance: Charge Secondary Ins. Company"))
        # Treat PrimeSuite's "No Secondary Insurance Company" placeholder as None
        if sec_payer and sec_payer.lower().startswith("no secondary"):
            sec_payer = None

        # Warn if payer differs across lines
        for r in rows[1:]:
            rp = _str_or_none(r.get("Insurance: Charge Primary Ins. Company"))
            if rp != payer:
                issues.append(ParseIssue("warning", r["__index__"], visit_id,
                                         f"payer name differs between lines; using first ({payer!r})"))
                break

        service_lines = []
        for r in rows:
            dx = _str_or_none(r.get("Diagnosis: Primary ICD-10 Code"))
            sl_dos = _parse_date(r.get("Date: Service date of the Charge"))
            service_lines.append(_build_service_line(r, dx, sl_dos))

        def _sum(attr: str) -> Decimal:
            return sum((getattr(sl, attr) for sl in service_lines), Decimal("0"))

        secondary_sub = _str_or_none(first.get("Insurance: Charge Secondary Policy Number"))

        claims.append(ParsedClaim(
            visit_id=visit_id,
            patient_external_id=_str_or_none(first.get("Patient: Patient ID")) or "",
            patient_demographics=_demographics_from_row(first),
            date_of_service_from=dos,
            payer_name=payer,
            subscriber_id=_str_or_none(first.get("Insurance: Charge Primary Policy Number")),
            secondary_payer_name=sec_payer,
            secondary_subscriber_id=secondary_sub if sec_payer else None,
            rendering_provider_name=_str_or_none(first.get("Provider: Rendering")),
            rendering_provider_npi=_str_or_none(first.get("Provider: Rendering NPI")),
            billing_provider_npi=_str_or_none(first.get("Provider: Billable NPI")),
            billed_amount=_sum("billed_amount"),
            paid_amount=_sum("paid_amount"),
            patient_responsibility=_sum("patient_responsibility"),
            contractual_adjustment=_sum("contractual_adjustment"),
            other_adjustment=_sum("other_adjustment"),
            service_lines=service_lines,
        ))

    return ChargeAnalysisImport(
        claims=claims,
        skipped_voids=skipped_voids,
        issues=issues,
        source_filename=os.path.basename(path),
        total_rows=total_rows,
    )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_charge_analysis_parser.py -v 2>&1 | tail -15
```
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/charge_analysis_importer.py backend/tests/test_charge_analysis_parser.py
git commit -m "feat(backend): charge_analysis_importer — single-row parse + real-fixture test"
```

---

## Task 4: Backend — parser multi-line grouping + rollups

**Files:**
- Modify: `backend/tests/test_charge_analysis_parser.py` (append tests)

(The parser from Task 3 already groups by VisitID; these tests confirm the grouping behavior and rollup math work end-to-end.)

- [ ] **Step 1: Append tests**

Append to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_charge_analysis_parser.py`:

```python
def test_parse_multi_line_claim_groups_and_rolls_up(tmp_path):
    row_a = {**BASE_ROW, "Visit: VisitID": 999, "Procedure: Code": "99213",
             "Charge: Gross Charges": 100.00, "Adjustment: Net Primary Ins. Adjusted": -30.00,
             "Payment: Net Primary Ins. Applied": -50.00, "Charge Balance: Patient": 20.00,
             "Diagnosis: Primary ICD-10 Code": "R10.20"}
    row_b = {**BASE_ROW, "Visit: VisitID": 999, "Procedure: Code": "76830",
             "Charge: Gross Charges": 200.00, "Adjustment: Net Primary Ins. Adjusted": -60.00,
             "Payment: Net Primary Ins. Applied": -140.00, "Charge Balance: Patient": 0,
             "Diagnosis: Primary ICD-10 Code": "N92.0"}
    df = _build_df([row_a, row_b])
    path = tmp_path / "two.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))

    assert len(result.claims) == 1
    c = result.claims[0]
    assert c.visit_id == "999"
    assert len(c.service_lines) == 2
    # Rollups are sums across lines
    assert c.billed_amount == Decimal("300.00")
    assert c.paid_amount == Decimal("190.00")          # 50 + 140
    assert c.contractual_adjustment == Decimal("90.00")  # 30 + 60
    assert c.patient_responsibility == Decimal("20.00")
    # Order preserved
    assert [sl.procedure_code for sl in c.service_lines] == ["99213", "76830"]
    assert [sl.diagnosis_codes for sl in c.service_lines] == [["R10.20"], ["N92.0"]]


def test_parse_payer_differs_across_lines_warns(tmp_path):
    row_a = {**BASE_ROW, "Visit: VisitID": 101, "Procedure: Code": "99213",
             "Insurance: Charge Primary Ins. Company": "Aetna"}
    row_b = {**BASE_ROW, "Visit: VisitID": 101, "Procedure: Code": "76830",
             "Insurance: Charge Primary Ins. Company": "BCBS"}
    df = _build_df([row_a, row_b])
    path = tmp_path / "diff.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))

    assert result.claims[0].payer_name == "Aetna"  # first wins
    warnings = [i for i in result.issues if i.severity == "warning" and "payer name" in i.message]
    assert len(warnings) == 1


def test_parse_secondary_placeholder_treated_as_none(tmp_path):
    row = {**BASE_ROW, "Insurance: Charge Secondary Ins. Company": "No Secondary Insurance Company"}
    df = _build_df([row])
    path = tmp_path / "no_sec.xlsx"
    df.to_excel(path, index=False)
    c = parse(str(path)).claims[0]
    assert c.secondary_payer_name is None
    assert c.secondary_subscriber_id is None
```

- [ ] **Step 2: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_charge_analysis_parser.py -v 2>&1 | tail -15
```
Expected: 7 tests PASS (4 from Task 3 + 3 new).

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/tests/test_charge_analysis_parser.py
git commit -m "test(backend): parser grouping + rollups + payer-divergence warning"
```

---

## Task 5: Backend — parser edge cases (voids, modifiers, sign normalization, validation)

**Files:**
- Modify: `backend/tests/test_charge_analysis_parser.py` (append tests)

All logic needed for these tests was already written in Task 3. This task locks the behavior in with tests.

- [ ] **Step 1: Append tests**

Append to `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_charge_analysis_parser.py`:

```python
def test_parse_voided_row_skipped(tmp_path):
    row_good = {**BASE_ROW, "Visit: VisitID": 1}
    row_void = {**BASE_ROW, "Visit: VisitID": 2, "Charge: Void Indicator": "YES"}
    df = _build_df([row_good, row_void])
    path = tmp_path / "voided.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert result.skipped_voids == 1
    assert len(result.claims) == 1
    assert result.claims[0].visit_id == "1"


def test_parse_multi_unit_service_line(tmp_path):
    row = {**BASE_ROW, "Procedure: Code": "J2003",
           "Charge: Net Units": 20, "Charge: Charge Amount": 1.50,
           "Charge: Gross Charges": 30.00,
           "Adjustment: Net Primary Ins. Adjusted": -29.80,
           "Payment: Net Primary Ins. Applied": -0.20}
    df = _build_df([row])
    path = tmp_path / "multi_unit.xlsx"
    df.to_excel(path, index=False)
    sl = parse(str(path)).claims[0].service_lines[0]
    assert sl.units == Decimal("20")
    assert sl.billed_amount == Decimal("30.00")
    assert sl.contractual_adjustment == Decimal("29.80")
    assert sl.paid_amount == Decimal("0.20")


def test_parse_payment_negative_sign_normalized(tmp_path):
    row = {**BASE_ROW, "Payment: Net Primary Ins. Applied": -119.75,
           "Adjustment: Net Primary Ins. Adjusted": -169.95,
           "Adjustment: Net Non-Primary Ins. Adjusted": -10.00}
    df = _build_df([row])
    path = tmp_path / "signs.xlsx"
    df.to_excel(path, index=False)
    c = parse(str(path)).claims[0]
    assert c.paid_amount == Decimal("119.75")
    assert c.contractual_adjustment == Decimal("169.95")
    assert c.other_adjustment == Decimal("10.00")


def test_parse_modifier_splitting_two(tmp_path):
    row = {**BASE_ROW, "Procedure: Modifiers": "25 59"}
    df = _build_df([row])
    path = tmp_path / "mods2.xlsx"
    df.to_excel(path, index=False)
    sl = parse(str(path)).claims[0].service_lines[0]
    assert sl.modifier_1 == "25"
    assert sl.modifier_2 == "59"
    assert sl.modifier_3 is None
    assert sl.modifier_4 is None


def test_parse_modifier_overflow_warns(tmp_path):
    row = {**BASE_ROW, "Procedure: Modifiers": "25 59 76 RT LT"}
    df = _build_df([row])
    path = tmp_path / "mods5.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    sl = result.claims[0].service_lines[0]
    assert (sl.modifier_1, sl.modifier_2, sl.modifier_3, sl.modifier_4) == ("25", "59", "76", "RT")
    warn = [i for i in result.issues if "more than 4 modifiers" in i.message]
    assert len(warn) == 1


def test_parse_missing_visit_id_row_dropped(tmp_path):
    row = {**BASE_ROW, "Visit: VisitID": None}
    df = _build_df([row])
    path = tmp_path / "no_visit.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert result.claims == []
    errs = [i for i in result.issues if i.severity == "error"]
    assert len(errs) == 1
    assert "Visit: VisitID" in errs[0].message


def test_parse_non_numeric_charge_dropped(tmp_path):
    row = {**BASE_ROW, "Charge: Gross Charges": "N/A"}
    df = _build_df([row])
    path = tmp_path / "bad_charge.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert result.claims == []
    errs = [i for i in result.issues if "non-numeric" in i.message]
    assert len(errs) == 1


def test_parse_negative_charge_warns_but_parses(tmp_path):
    row = {**BASE_ROW, "Charge: Gross Charges": -100.00}
    df = _build_df([row])
    path = tmp_path / "negative.xlsx"
    df.to_excel(path, index=False)
    result = parse(str(path))
    assert len(result.claims) == 1
    warn = [i for i in result.issues if "negative" in i.message]
    assert len(warn) == 1


def test_parse_demographics_packs_address(tmp_path):
    df = _build_df([BASE_ROW])
    path = tmp_path / "demo.xlsx"
    df.to_excel(path, index=False)
    d = parse(str(path)).claims[0].patient_demographics
    assert d["first_name"] == "SILVINA"
    assert d["last_name"] == "DELFIN-CRUZ"
    assert d["date_of_birth"] == date(1979, 9, 12)
    assert d["phone"] == "240-416-4826"
    assert "12566 COUNCIL OAK DR" in d["address"]
    assert "Waldorf, MD 20601" in d["address"]
    assert d["_sex"] == "Female"  # captured; dropped on persist
```

- [ ] **Step 2: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_charge_analysis_parser.py -v 2>&1 | tail -20
```
Expected: 16 tests PASS (7 prior + 9 new).

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/tests/test_charge_analysis_parser.py
git commit -m "test(backend): parser edge cases — voids, modifiers, signs, validation"
```

---

## Task 6: Backend — `import_sessions.py` session store

**Files:**
- Create: `backend/app/services/import_sessions.py`
- Create: `backend/tests/test_import_sessions.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_import_sessions.py`:

```python
"""Tests for the in-memory import session store."""
from datetime import datetime, timedelta, timezone
import pytest
from app.services import import_sessions as store


@pytest.fixture(autouse=True)
def _clear_store():
    store._sessions.clear()
    yield
    store._sessions.clear()


def _entry(id_: str, now=None):
    return store.SessionEntry(
        session_id=id_,
        payload={"hello": "world"},
        filename="test.xls",
        file_path="/tmp/test.xls",
        user_email="u@example.com",
        created_at=now or datetime.now(timezone.utc),
        expires_at=(now or datetime.now(timezone.utc)) + timedelta(minutes=30),
    )


def test_put_and_get_roundtrip():
    e = _entry("abc")
    store.put(e)
    got = store.get("abc")
    assert got is not None
    assert got.filename == "test.xls"
    assert got.payload == {"hello": "world"}


def test_get_returns_none_for_unknown_id():
    assert store.get("nope") is None


def test_get_returns_none_for_expired_entry_and_purges():
    past = datetime.now(timezone.utc) - timedelta(minutes=60)
    e = store.SessionEntry(
        session_id="old",
        payload={},
        filename="t",
        file_path="/tmp/t",
        user_email="u@x",
        created_at=past,
        expires_at=past + timedelta(minutes=30),  # still in the past
    )
    store.put(e)
    assert store.get("old") is None
    assert "old" not in store._sessions


def test_purge_removes_entry():
    store.put(_entry("zap"))
    store.purge("zap")
    assert "zap" not in store._sessions


def test_purge_missing_is_noop():
    store.purge("missing")  # should not raise
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_import_sessions.py -v 2>&1 | tail -10
```
Expected: all 5 tests FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `import_sessions.py`**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/services/import_sessions.py`:

```python
"""In-memory session store for two-step import flows.

Holds the parsed payload between the upload endpoint (which computes the
preview) and the commit endpoint (which persists it).

LIMITATION: This is a module-level dict. Safe for single-process uvicorn,
NOT safe across multiple workers — each worker would have its own dict and
a commit hitting the wrong worker would 404. If the app ever runs multi-
worker, swap this for Redis with the same interface. TODO.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class SessionEntry:
    session_id: str
    payload: Any                 # the parser's ChargeAnalysisImport result
    filename: str
    file_path: str
    user_email: Optional[str]
    created_at: datetime
    expires_at: datetime
    # Pre-computed per-claim flags for fast commit:
    # list of {visit_id, exists_in_db, patient_resolved_id, will_create_patient}
    claim_flags: List[Dict[str, Any]] = field(default_factory=list)


_sessions: Dict[str, SessionEntry] = {}


def put(entry: SessionEntry) -> None:
    _sessions[entry.session_id] = entry


def get(session_id: str) -> Optional[SessionEntry]:
    entry = _sessions.get(session_id)
    if entry is None:
        return None
    if datetime.now(timezone.utc) >= entry.expires_at:
        _sessions.pop(session_id, None)
        return None
    return entry


def purge(session_id: str) -> None:
    _sessions.pop(session_id, None)


def expire_old() -> int:
    """Drop all expired entries. Returns count removed. Called opportunistically."""
    now = datetime.now(timezone.utc)
    stale = [sid for sid, e in _sessions.items() if now >= e.expires_at]
    for sid in stale:
        _sessions.pop(sid, None)
    return len(stale)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_import_sessions.py tests/ -v 2>&1 | tail -10
```
Expected: 5 new tests PASS + all prior tests still PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/services/import_sessions.py backend/tests/test_import_sessions.py
git commit -m "feat(backend): import_sessions in-memory session store + tests"
```

---

## Task 7: Backend — `charge_imports` router: upload/preview endpoint

**Files:**
- Create: `backend/app/routers/charge_imports.py`
- Create: `backend/tests/test_charge_imports_upload.py`
- Modify: `backend/app/main.py` (include router)

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_charge_imports_upload.py`:

```python
"""Tests for POST /api/imports/charge-analysis (upload → preview)."""
from decimal import Decimal
from pathlib import Path
from app.models.claim import Claim, ClaimStatus
from app.models.patient import Patient
from app.services import import_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "charge_analysis_test4.xls"


def _upload(client):
    import_sessions._sessions.clear()
    with FIXTURE.open("rb") as f:
        return client.post(
            "/api/imports/charge-analysis",
            files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
        )


def test_upload_returns_preview(client, db):
    r = _upload(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_filename"] == "charge_analysis_test4.xls"
    assert body["total_rows"] == 1717
    assert body["skipped_voids"] == 104
    assert body["parsed_claims"] == 758
    assert body["will_create"] == 758
    assert body["will_skip_existing"] == 0
    assert body["will_create_patients"] + body["will_match_patients"] == 758
    assert body["errors"] == 0
    assert len(body["sample_claims"]) == 20
    assert "expires_at" in body
    assert "session_id" in body


def test_upload_detects_existing_claim_by_visit_id(client, db):
    db.add(Claim(claim_number="263259", status=ClaimStatus.PENDING, balance=Decimal("0")))
    db.commit()
    r = _upload(client)
    assert r.status_code == 200
    body = r.json()
    assert body["will_skip_existing"] == 1
    assert body["will_create"] == 757


def test_upload_detects_matching_patient(client, db):
    db.add(Patient(patient_id="11175", first_name="Silvina", last_name="Delfin-Cruz"))
    db.commit()
    r = _upload(client)
    assert r.status_code == 200
    body = r.json()
    assert body["will_match_patients"] >= 1


def test_upload_bad_file_422(client, db):
    r = client.post(
        "/api/imports/charge-analysis",
        files={"file": ("bogus.txt", b"not an excel file", "text/plain")},
    )
    assert r.status_code == 422


def test_upload_missing_column_422(client, db, tmp_path):
    import pandas as pd
    # Export a DataFrame missing the VisitID column
    df = pd.DataFrame([{"Patient: Patient ID": "1"}])
    path = tmp_path / "bad.xlsx"
    df.to_excel(path, index=False)
    with path.open("rb") as f:
        r = client.post(
            "/api/imports/charge-analysis",
            files={"file": (path.name, f, "application/vnd.ms-excel")},
        )
    assert r.status_code == 422
    assert "missing required columns" in r.json()["detail"].lower() or "missing" in r.json()["detail"].lower()


def test_upload_forbidden_for_clinical(clinical_client, db):
    r = _upload(clinical_client)
    assert r.status_code == 403
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_charge_imports_upload.py -v 2>&1 | tail -15
```
Expected: all 6 tests FAIL (most 404 — router not yet mounted; `_upload` fixture returns the response regardless).

- [ ] **Step 3: Create `charge_imports.py` with the upload endpoint**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/charge_imports.py`:

```python
"""Two-step Charge Analysis import: POST upload (preview), POST {id}/commit."""
from __future__ import annotations

import os
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.models.claim import Claim
from app.models.patient import Patient
from app.services.audit_service import log_action
from app.services import import_sessions
from app.services.charge_analysis_importer import (
    ChargeAnalysisImport, ParsedClaim, parse,
)
from app.routers.auth import get_current_user


router = APIRouter(prefix="/imports", tags=["charge-imports"])

SESSION_TTL_MIN = 30


def _claim_to_jsonable(c: ParsedClaim) -> Dict[str, Any]:
    """Convert a ParsedClaim dataclass tree to JSON-safe types."""
    def _j(v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        if hasattr(v, "isoformat"):
            return v.isoformat()
        if is_dataclass(v):
            return {k: _j(x) for k, x in asdict(v).items()}
        if isinstance(v, list):
            return [_j(x) for x in v]
        if isinstance(v, dict):
            return {k: _j(x) for k, x in v.items()}
        return v
    return _j(c)


def _compute_flags(
    parsed: ChargeAnalysisImport, db: Session
) -> List[Dict[str, Any]]:
    """For each parsed claim, resolve existing-claim + existing-patient flags."""
    visit_ids = [c.visit_id for c in parsed.claims]
    existing = {
        row.claim_number for row in db.query(Claim.claim_number)
        .filter(Claim.claim_number.in_(visit_ids)).all()
    } if visit_ids else set()

    patient_ids = [c.patient_external_id for c in parsed.claims if c.patient_external_id]
    existing_patients = {
        row.patient_id: str(row.id) for row in db.query(Patient.patient_id, Patient.id)
        .filter(Patient.patient_id.in_(patient_ids)).all()
    } if patient_ids else {}

    flags = []
    for c in parsed.claims:
        resolved = existing_patients.get(c.patient_external_id)
        flags.append({
            "visit_id": c.visit_id,
            "exists_in_db": c.visit_id in existing,
            "patient_resolved_id": resolved,
            "will_create_patient": resolved is None,
        })
    return flags


@router.post("/charge-analysis")
async def upload_charge_analysis(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    # Persist the upload under upload_dir/charge_analysis/<session_id><ext>
    session_id = str(uuid.uuid4())
    subdir = os.path.join(settings.upload_dir, "charge_analysis")
    os.makedirs(subdir, exist_ok=True)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".xls", ".xlsx"):
        raise HTTPException(status_code=422, detail="file must be .xls or .xlsx")
    save_path = os.path.join(subdir, f"{session_id}{ext}")
    content = await file.read()
    with open(save_path, "wb") as fh:
        fh.write(content)

    # Parse (pure function)
    try:
        parsed = parse(save_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422,
                            detail=f"could not read Excel file: {exc}")

    # Dedup / patient-resolution flags
    flags = _compute_flags(parsed, db)
    will_create = sum(1 for f in flags if not f["exists_in_db"])
    will_skip_existing = sum(1 for f in flags if f["exists_in_db"])
    will_match_patients = sum(1 for f in flags if not f["will_create_patient"])
    will_create_patients = sum(1 for f in flags if f["will_create_patient"])
    errors = sum(1 for i in parsed.issues if i.severity == "error")
    warnings = sum(1 for i in parsed.issues if i.severity == "warning")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=SESSION_TTL_MIN)

    import_sessions.put(import_sessions.SessionEntry(
        session_id=session_id,
        payload=parsed,
        filename=file.filename or "charge_analysis.xls",
        file_path=save_path,
        user_email=current_user.get("email"),
        created_at=now,
        expires_at=expires_at,
        claim_flags=flags,
    ))

    return {
        "session_id": session_id,
        "source_filename": parsed.source_filename,
        "total_rows": parsed.total_rows,
        "parsed_claims": len(parsed.claims),
        "skipped_voids": parsed.skipped_voids,
        "will_create": will_create,
        "will_skip_existing": will_skip_existing,
        "will_create_patients": will_create_patients,
        "will_match_patients": will_match_patients,
        "errors": errors,
        "warnings": warnings,
        "sample_claims": [_claim_to_jsonable(c) for c in parsed.claims[:20]],
        "issues": [
            {
                "severity": i.severity,
                "row_index": i.row_index,
                "visit_id": i.visit_id,
                "message": i.message,
            }
            for i in parsed.issues
        ],
        "expires_at": expires_at.isoformat(),
    }
```

- [ ] **Step 4: Wire router into `main.py`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/main.py`.

Update the imports line (second `from app.routers import …`):
```python
from app.routers import waystar, ar, documents, intake, chart, fax, auth, dashboard, fax_batch, admin_users, service_lines, claim_adjustments, service_line_adjustments, charge_imports
```

Add `include_router` call right below `service_line_adjustments`:
```python
app.include_router(service_line_adjustments.router, prefix="/api", dependencies=BILLING)
app.include_router(charge_imports.router, prefix="/api", dependencies=BILLING)
```

- [ ] **Step 5: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_charge_imports_upload.py tests/ -v 2>&1 | tail -20
```
Expected: 6 new tests PASS + all prior tests still PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/charge_imports.py backend/app/main.py backend/tests/test_charge_imports_upload.py
git commit -m "feat(backend): POST /imports/charge-analysis upload + preview"
```

---

## Task 8: Backend — commit endpoint (persist claims + lines + patients)

**Files:**
- Modify: `backend/app/routers/charge_imports.py` (append commit endpoint)
- Create: `backend/tests/test_charge_imports_commit.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/wwcclaudecode/Documents/wwc-era-project/backend/tests/test_charge_imports_commit.py`:

```python
"""Tests for POST /api/imports/charge-analysis/{session_id}/commit."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from app.models.claim import Claim, ServiceLine, ClaimStatus
from app.models.patient import Patient
from app.models.audit import AuditLog
from app.services import import_sessions

FIXTURE = Path(__file__).parent / "fixtures" / "charge_analysis_test4.xls"


def _upload(client):
    import_sessions._sessions.clear()
    with FIXTURE.open("rb") as f:
        r = client.post(
            "/api/imports/charge-analysis",
            files={"file": (FIXTURE.name, f, "application/vnd.ms-excel")},
        )
    return r.json()


def test_commit_creates_claims_and_service_lines(client, db):
    preview = _upload(client)
    r = client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["claims_created"] == 758
    assert body["claims_skipped_existing"] == 0
    assert body["service_lines_created"] == 1613  # 1717 rows - 104 voided
    assert body["errors"] == []

    assert db.query(Claim).count() == 758
    assert db.query(ServiceLine).count() == 1613


def test_commit_skips_existing_claim_by_visit_id(client, db):
    db.add(Claim(claim_number="263259", status=ClaimStatus.PENDING, balance=Decimal("0")))
    db.commit()
    preview = _upload(client)
    r = client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    body = r.json()
    assert body["claims_created"] == 757
    assert body["claims_skipped_existing"] == 1
    # The pre-seeded claim is still a single row, untouched
    existing = db.query(Claim).filter(Claim.claim_number == "263259").all()
    assert len(existing) == 1
    assert db.query(ServiceLine).filter(ServiceLine.claim_id == existing[0].id).count() == 0


def test_commit_creates_missing_patient(client, db):
    # Pre-seed one patient; commit should match it and create all others new
    db.add(Patient(patient_id="11175", first_name="Silvina", last_name="Delfin-Cruz"))
    db.commit()
    preview = _upload(client)
    r = client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    body = r.json()
    assert body["patients_matched"] >= 1
    assert body["patients_created"] >= 1
    # The seeded patient wasn't duplicated
    assert db.query(Patient).filter(Patient.patient_id == "11175").count() == 1


def test_commit_does_not_duplicate_existing_patient(client, db):
    db.add(Patient(patient_id="11175", first_name="Silvina", last_name="Delfin-Cruz"))
    db.commit()
    preview = _upload(client)
    client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    assert db.query(Patient).filter(Patient.patient_id == "11175").count() == 1


def test_commit_recomputes_claim_balance(client, db):
    preview = _upload(client)
    client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    # Pick any claim from the set; its balance must equal
    # billed - contractual - other - paid - pt_resp.
    claim = db.query(Claim).first()
    expected = (claim.billed_amount or 0) - (claim.contractual_adjustment or 0) \
               - (claim.other_adjustment or 0) - (claim.paid_amount or 0) \
               - (claim.patient_responsibility or 0)
    assert float(claim.balance) == float(expected)


def test_commit_writes_audit_row_per_claim(client, db):
    preview = _upload(client)
    client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    create_rows = db.query(AuditLog).filter(
        AuditLog.action == "CREATE",
        AuditLog.resource_type == "claim",
    ).all()
    assert len(create_rows) == 759
    assert all(r.user_name == "tester@waldorfwomenscare.com" for r in create_rows)
    # Each claim is linked to a patient (either matched or newly created)
    assert all(r.patient_id is not None for r in create_rows)


def test_commit_writes_single_import_audit_row(client, db):
    preview = _upload(client)
    sid = preview["session_id"]
    client.post(f"/api/imports/charge-analysis/{sid}/commit")
    import_rows = db.query(AuditLog).filter(
        AuditLog.resource_type == "charge_analysis_file",
    ).all()
    assert len(import_rows) == 1
    assert import_rows[0].action == "IMPORT"
    assert import_rows[0].resource_id == sid


def test_commit_404_on_unknown_session(client, db):
    r = client.post("/api/imports/charge-analysis/not-a-session/commit")
    assert r.status_code == 404


def test_commit_404_on_expired_session(client, db):
    # Manufacture an expired session in the store directly
    past = datetime.now(timezone.utc) - timedelta(minutes=45)
    entry = import_sessions.SessionEntry(
        session_id="expired", payload=None, filename="f", file_path="/tmp/f",
        user_email="u@x", created_at=past, expires_at=past + timedelta(minutes=30),
        claim_flags=[],
    )
    import_sessions._sessions["expired"] = entry  # bypass put() TTL
    r = client.post("/api/imports/charge-analysis/expired/commit")
    assert r.status_code == 404


def test_commit_session_is_purged_after_success(client, db):
    preview = _upload(client)
    sid = preview["session_id"]
    client.post(f"/api/imports/charge-analysis/{sid}/commit")
    assert import_sessions.get(sid) is None


def test_commit_forbidden_for_clinical(clinical_client, db, client):
    # Upload as admin so the session exists, then attempt commit as clinical
    preview = _upload(client)
    r = clinical_client.post(f"/api/imports/charge-analysis/{preview['session_id']}/commit")
    assert r.status_code == 403


def test_upload_re_run_shows_all_existing(client, db):
    preview1 = _upload(client)
    client.post(f"/api/imports/charge-analysis/{preview1['session_id']}/commit")
    # Second upload of the same file → all should be skip-existing
    preview2 = _upload(client)
    assert preview2["will_create"] == 0
    assert preview2["will_skip_existing"] == 758
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_charge_imports_commit.py -v 2>&1 | tail -20
```
Expected: all 12 tests FAIL with 404 (commit endpoint not defined).

- [ ] **Step 3: Append commit endpoint to `charge_imports.py`**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/backend/app/routers/charge_imports.py` and add at the bottom:

```python
from app.models.claim import ServiceLine, ClaimStatus, InsuranceOrder
from app.services.claim_math import recompute_balance


def _summary_for_audit(claim: Claim) -> Dict[str, Any]:
    return {
        "claim_number": claim.claim_number,
        "payer_name": claim.payer_name,
        "billed_amount": float(claim.billed_amount or 0),
        "paid_amount": float(claim.paid_amount or 0),
        "patient_responsibility": float(claim.patient_responsibility or 0),
    }


def _ensure_patient(db: Session, parsed_claim: ParsedClaim, resolved_id: Optional[str]) -> Optional[Patient]:
    """Return an existing or newly-created Patient row; None if no patient info."""
    if resolved_id:
        return db.query(Patient).filter(Patient.id == resolved_id).first()
    demo = parsed_claim.patient_demographics or {}
    external = parsed_claim.patient_external_id
    if not external:
        return None
    p = Patient(
        patient_id=external,
        first_name=demo.get("first_name"),
        last_name=demo.get("last_name"),
        date_of_birth=demo.get("date_of_birth"),
        phone=demo.get("phone"),
        address=demo.get("address"),
    )
    db.add(p)
    db.flush()  # assign p.id without committing the outer transaction
    return p


def _create_claim_with_lines(
    db: Session, parsed: ParsedClaim, patient: Optional[Patient]
) -> Claim:
    claim = Claim(
        claim_number=parsed.visit_id,
        patient_id=patient.id if patient else None,
        date_of_service_from=parsed.date_of_service_from,
        date_of_service_to=parsed.date_of_service_from,
        payer_name=parsed.payer_name,
        subscriber_id=parsed.subscriber_id,
        rendering_provider_name=parsed.rendering_provider_name,
        rendering_provider_npi=parsed.rendering_provider_npi,
        billing_provider_npi=parsed.billing_provider_npi,
        insurance_order=InsuranceOrder.PRIMARY,
        status=ClaimStatus.PENDING,
        billed_amount=parsed.billed_amount,
        paid_amount=parsed.paid_amount,
        patient_responsibility=parsed.patient_responsibility,
        contractual_adjustment=parsed.contractual_adjustment,
        other_adjustment=parsed.other_adjustment,
    )
    db.add(claim)
    db.flush()
    for sl in parsed.service_lines:
        db.add(ServiceLine(
            claim_id=claim.id,
            procedure_code=sl.procedure_code,
            modifier_1=sl.modifier_1,
            modifier_2=sl.modifier_2,
            modifier_3=sl.modifier_3,
            modifier_4=sl.modifier_4,
            units=sl.units,
            billed_amount=sl.billed_amount,
            paid_amount=sl.paid_amount,
            patient_responsibility=sl.patient_responsibility,
            contractual_adjustment=sl.contractual_adjustment,
            other_adjustment=sl.other_adjustment,
            date_of_service_from=sl.date_of_service_from,
            date_of_service_to=sl.date_of_service_from,
            diagnosis_codes=list(sl.diagnosis_codes),
        ))
    recompute_balance(claim)
    return claim


@router.post("/charge-analysis/{session_id}/commit")
def commit_charge_analysis(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    entry = import_sessions.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="session not found or expired")

    parsed: ChargeAnalysisImport = entry.payload
    flags_by_vid = {f["visit_id"]: f for f in entry.claim_flags}

    claims_created = 0
    claims_skipped_existing = 0
    patients_created = 0
    patients_matched = 0
    service_lines_created = 0
    errors: List[Dict[str, Any]] = []
    user_email = current_user.get("email")

    for parsed_claim in parsed.claims:
        flag = flags_by_vid.get(parsed_claim.visit_id, {})
        if flag.get("exists_in_db"):
            claims_skipped_existing += 1
            continue

        try:
            patient = _ensure_patient(db, parsed_claim, flag.get("patient_resolved_id"))
            if patient is not None:
                if flag.get("will_create_patient"):
                    patients_created += 1
                else:
                    patients_matched += 1

            claim = _create_claim_with_lines(db, parsed_claim, patient)
            db.commit()
            claims_created += 1
            service_lines_created += len(parsed_claim.service_lines)

            log_action(
                db, "CREATE", "claim",
                resource_id=str(claim.id),
                patient_id=str(claim.patient_id) if claim.patient_id else None,
                user_name=user_email,
                new_values=_summary_for_audit(claim),
                description=f"import: {entry.filename} VisitID {parsed_claim.visit_id}",
            )
        except Exception as exc:
            db.rollback()
            errors.append({
                "visit_id": parsed_claim.visit_id,
                "message": f"{type(exc).__name__}: {exc}",
            })

    log_action(
        db, "IMPORT", "charge_analysis_file",
        resource_id=session_id,
        user_name=user_email,
        description=(
            f"{entry.filename} — {claims_created} claims created, "
            f"{claims_skipped_existing} skipped, "
            f"{patients_created} patients created"
        ),
    )
    import_sessions.purge(session_id)

    return {
        "source_filename": entry.filename,
        "claims_created": claims_created,
        "claims_skipped_existing": claims_skipped_existing,
        "patients_created": patients_created,
        "patients_matched": patients_matched,
        "service_lines_created": service_lines_created,
        "errors": errors,
    }
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/test_charge_imports_commit.py tests/ -v 2>&1 | tail -25
```
Expected: 12 new commit tests PASS + all prior tests still PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/routers/charge_imports.py backend/tests/test_charge_imports_commit.py
git commit -m "feat(backend): POST /imports/charge-analysis/{id}/commit — persist claims + lines + patients"
```

---

## Task 9: Frontend — Charge Analysis card skeleton (dropzone + uploading state)

**Files:**
- Modify: `frontend/src/pages/ImportFiles.jsx` (add new card below existing drop zone)

- [ ] **Step 1: Add Charge Analysis upload card**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/ImportFiles.jsx`.

Near the top, replace the existing icon import line with:
```js
import { Upload, FileText, CheckCircle, AlertCircle, Clock, Database } from 'lucide-react'
```

Inside the `ImportFiles` component, after the existing `useRef()` line, add:
```js
  // Charge Analysis (Phase 2b) state machine:
  // null                                  → drop zone
  // { uploading: true, filename }         → uploading
  // { preview: {...} }                    → preview card
  // { preview, committing: true }         → preview + spinner
  // { success: {...} }                    → success card
  // { preview?, error: {...} }            → error card
  const [chargeState, setChargeState] = useState(null)
  const chargeInputRef = useRef()

  const handleChargeFile = async (file) => {
    setChargeState({ uploading: true, filename: file.name })
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await api.post('/imports/charge-analysis', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setChargeState({ preview: res.data })
    } catch (e) {
      setChargeState({ error: { message: e.response?.data?.detail || e.message } })
    }
  }
```

Inside the main `return (...)` block, find the closing `</div>` of the existing result/error blocks (before the "ERA File History" card at line ~168 `{/* ERA File History */}`). Just BEFORE that comment, insert the new Charge Analysis card:

```jsx
      {/* Charge Analysis Import (Phase 2b) */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-1">
          <Database size={16} className="text-primary-600" />
          <h2 className="text-sm font-semibold text-gray-800">Charge Analysis Import (PrimeSuite)</h2>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Upload the monthly or quarterly Charge Analysis <code>.xls</code> export.
          Voided charges skipped. Existing claims (by VisitID) skipped.
        </p>

        {!chargeState && (
          <div
            className="border-2 border-dashed rounded-lg p-6 text-center cursor-pointer border-gray-300 hover:border-primary-400 hover:bg-gray-50"
            onClick={() => chargeInputRef.current?.click()}
            onDragOver={e => e.preventDefault()}
            onDrop={e => {
              e.preventDefault()
              const f = e.dataTransfer.files[0]
              if (f) handleChargeFile(f)
            }}
          >
            <input
              ref={chargeInputRef}
              type="file"
              accept=".xls,.xlsx"
              className="hidden"
              onChange={e => e.target.files[0] && handleChargeFile(e.target.files[0])}
            />
            <p className="text-sm text-gray-700">📊 Drop <code>.xls</code> here or click to browse</p>
          </div>
        )}

        {chargeState?.uploading && (
          <div className="border-2 border-dashed rounded-lg p-6 text-center border-gray-300 text-gray-500">
            <div className="animate-spin inline-block text-lg mr-2">⟳</div>
            Parsing <code>{chargeState.filename}</code>…
          </div>
        )}

        {chargeState?.error && (
          <div className="card border border-red-200 bg-red-50">
            <div className="flex items-center gap-2">
              <AlertCircle size={16} className="text-red-600" />
              <span className="font-semibold text-red-700 text-sm">Upload failed</span>
            </div>
            <pre className="text-xs text-red-600 mt-2 whitespace-pre-wrap">{chargeState.error.message}</pre>
            <button className="btn-secondary text-xs mt-2" onClick={() => setChargeState(null)}>Try another file</button>
          </div>
        )}
      </div>
```

- [ ] **Step 2: Verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -8
```
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/ImportFiles.jsx
git commit -m "feat(frontend): Charge Analysis card — dropzone + uploading + error states"
```

---

## Task 10: Frontend — Preview card + issues disclosure

**Files:**
- Modify: `frontend/src/pages/ImportFiles.jsx` (append preview render block)

- [ ] **Step 1: Add preview render inside the Charge Analysis card**

Open `/Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/ImportFiles.jsx`.

Inside the Charge Analysis card JSX (from Task 9), immediately AFTER the `{chargeState?.error && (...)}` block and BEFORE the closing `</div>` of the card, insert:

```jsx
        {chargeState?.preview && !chargeState.success && (
          <ChargeAnalysisPreview
            preview={chargeState.preview}
            committing={chargeState.committing}
            onCancel={() => setChargeState(null)}
            onCommit={async () => {
              setChargeState(s => ({ ...s, committing: true }))
              try {
                const res = await api.post(`/imports/charge-analysis/${chargeState.preview.session_id}/commit`)
                setChargeState({ success: res.data })
              } catch (e) {
                setChargeState(s => ({
                  preview: s.preview,
                  error: { message: e.response?.data?.detail || e.message },
                }))
              }
            }}
          />
        )}
```

Below the `ImportFiles` component (outside its function body, at the bottom of the file above `export default`), add the new helper components:

```jsx
function ChargeAnalysisPreview({ preview, committing, onCancel, onCommit }) {
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
        {preview.parsed_claims} claims parsed · {preview.total_rows} rows
      </div>

      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Claims</div>
      <div className="text-sm space-y-0.5 mb-3">
        <div><span className="text-green-600 mr-1">✓</span>{preview.will_create} new claims will be created</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{preview.will_skip_existing} existing (by VisitID) skipped</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{preview.skipped_voids} voided rows skipped</div>
      </div>

      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Patients</div>
      <div className="text-sm space-y-0.5 mb-3">
        <div><span className="text-green-600 mr-1">✓</span>{preview.will_match_patients} matched to existing charts</div>
        <div><span className="text-primary-600 mr-1">+</span>{preview.will_create_patients} new patients will be created</div>
      </div>

      <div className="text-xs text-gray-600 mb-2">
        <strong>{preview.errors} errors · {preview.warnings} warnings</strong>
        {(preview.errors + preview.warnings) > 0 && (
          <button className="ml-2 text-primary-600 underline" onClick={() => setShowIssues(v => !v)}>
            {showIssues ? 'Hide details ▴' : 'Show details ▾'}
          </button>
        )}
      </div>

      {showIssues && (
        <div className="max-h-40 overflow-y-auto border border-gray-100 rounded p-2 bg-gray-50 text-xs mb-3">
          {preview.issues.map((i, idx) => (
            <div key={idx} className="py-0.5">
              <span className={i.severity === 'error' ? 'text-red-600 font-semibold' : 'text-amber-600 font-semibold'}>
                {i.severity.toUpperCase()}
              </span>
              {' · row '}{i.row_index}
              {i.visit_id && <> · VisitID <code>{i.visit_id}</code></>}
              {' · '}{i.message}
            </div>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-2 mt-2">
        <button className="btn-secondary text-xs" disabled={committing} onClick={onCancel}>Cancel</button>
        <button
          className="btn-primary text-xs"
          disabled={committing || expired}
          onClick={onCommit}
        >
          {committing ? 'Committing…' : expired ? 'Session expired' : 'Commit import'}
        </button>
      </div>
    </div>
  )
}

function secondsUntil(isoString) {
  const diffMs = new Date(isoString).getTime() - Date.now()
  return Math.max(0, Math.floor(diffMs / 1000))
}
function formatRemaining(seconds) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${String(s).padStart(2, '0')}`
}
```

Also update the top-of-file import:
```js
import { useState, useRef, useEffect } from 'react'
```

- [ ] **Step 2: Verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -8
```
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/ImportFiles.jsx
git commit -m "feat(frontend): Charge Analysis preview card + issues disclosure + expiry countdown"
```

---

## Task 11: Frontend — Success + partial-failure states

**Files:**
- Modify: `frontend/src/pages/ImportFiles.jsx` (add success render + partial-failure render)

- [ ] **Step 1: Add success + failure renders**

Inside the Charge Analysis card JSX, AFTER the preview block added in Task 10, insert:

```jsx
        {chargeState?.success && (
          <ChargeAnalysisSuccess result={chargeState.success} onAgain={() => setChargeState(null)} />
        )}
```

Below the `ChargeAnalysisPreview` helper, add:

```jsx
function ChargeAnalysisSuccess({ result, onAgain }) {
  const hasErrors = Array.isArray(result.errors) && result.errors.length > 0

  if (hasErrors) {
    return (
      <div className="card border border-amber-300 bg-amber-50">
        <div className="flex items-center gap-2 mb-2">
          <AlertCircle size={16} className="text-amber-700" />
          <span className="font-semibold text-amber-800 text-sm">Import completed with errors</span>
        </div>
        <div className="text-xs text-amber-900 mb-3">
          {result.source_filename} — {result.claims_created} of {result.claims_created + result.errors.length} claims committed
        </div>
        <div className="grid grid-cols-2 gap-1 text-xs mb-3">
          <div>Claims created: <span className="font-mono">{result.claims_created}</span></div>
          <div className="text-red-700">Claims failed: <span className="font-mono">{result.errors.length}</span></div>
          <div>Service lines created: <span className="font-mono">{result.service_lines_created}</span></div>
          <div>Patients created: <span className="font-mono">{result.patients_created}</span></div>
        </div>
        <div className="text-[11px] uppercase tracking-wide text-amber-800 mb-1">Failed claims</div>
        <div className="max-h-32 overflow-y-auto text-xs bg-white border border-amber-200 rounded p-2">
          {result.errors.map((err, idx) => (
            <div key={idx} className="py-0.5">
              VisitID <code>{err.visit_id}</code> · {err.message}
            </div>
          ))}
        </div>
        <div className="mt-3">
          <button className="btn-secondary text-xs" onClick={onAgain}>Dismiss</button>
        </div>
      </div>
    )
  }

  return (
    <div className="card border border-green-200 bg-green-50">
      <div className="flex items-center gap-2 mb-2">
        <CheckCircle size={16} className="text-green-700" />
        <span className="font-semibold text-green-800 text-sm">Import complete</span>
      </div>
      <div className="text-xs text-green-900 mb-3">{result.source_filename}</div>
      <div className="grid grid-cols-2 gap-1 text-xs mb-3">
        <div>Claims created: <span className="font-mono font-semibold">{result.claims_created}</span></div>
        <div>Service lines: <span className="font-mono font-semibold">{result.service_lines_created}</span></div>
        <div>Patients created: <span className="font-mono">{result.patients_created}</span></div>
        <div>Patients matched: <span className="font-mono">{result.patients_matched}</span></div>
        <div>Skipped (existing): <span className="font-mono">{result.claims_skipped_existing}</span></div>
      </div>
      <div className="flex gap-2">
        <a href="/claims" className="btn-primary text-xs">View claims →</a>
        <button className="btn-secondary text-xs" onClick={onAgain}>Import another file</button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify build**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npx vite build 2>&1 | tail -8
```
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add frontend/src/pages/ImportFiles.jsx
git commit -m "feat(frontend): Charge Analysis success + partial-failure cards"
```

---

## Task 12: Manual verification + run the wipe script

**Files:**
- No code changes; this is a runtime verification task.

- [ ] **Step 1: Run the wipe script against the dev database**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m app.scripts.reset_claims_data --yes-i-am-sure
```
Expected: prints a per-table deletion count summary. First run wipes whatever legacy claim data exists. Second run should return all zeros.

- [ ] **Step 2: Start the dev stack**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8000 &
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run dev &
```
Wait ~5 seconds. Check `http://localhost:8000/api/health` returns `{"status":"ok"...}` and `http://localhost:3000/` returns 200.

- [ ] **Step 3: Manual UI checklist**

Sign in as admin. Go to `/imports`.

- [ ] New "Charge Analysis Import (PrimeSuite)" card appears below the existing ERA drop zone.
- [ ] Drop `Charge Analysis Test4.xls` → spinner briefly → preview card shows `758 parsed · 758 new · 0 existing · 104 voided`.
- [ ] Click "Show details" → issues (if any) scroll. Click again → collapses.
- [ ] Expiry countdown ticks once per second.
- [ ] Click "Cancel" → drop zone returns.
- [ ] Drop the file again → same preview. Click "Commit import" → button shows "Committing…", card turns green with stats after a few seconds.
- [ ] Click "View claims →" → `/claims` shows the 758 new claims (may paginate).
- [ ] Open one claim → ClaimDetail renders service lines, computed balance, and the Phase 2a "Edit claim" button works.
- [ ] Back to `/imports`. Drop the SAME file again → preview shows `0 new · 758 existing skipped`. Commit → success card says `claims_created: 0, claims_skipped_existing: 758`.
- [ ] Drop a random PDF → 422 banner appears with the detail message.
- [ ] Flip your group in sqlite to `clinical` (`UPDATE users SET "group"='clinical' WHERE email='ocooke@waldorfwomenscare.com';`). Refresh. `/imports` route should redirect. Restore admin after: `UPDATE users SET "group"='admin' WHERE email='ocooke@waldorfwomenscare.com';`

- [ ] **Step 4: Run full backend test suite one final time**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && source venv/bin/activate && python -m pytest tests/ -v 2>&1 | tail -10
```
Expected: **all prior Phase 2a tests (123) + Task 1 (2) + Task 2 (3) + Tasks 3-5 (16 parser) + Task 6 (5 session) + Task 7 (6 upload) + Task 8 (12 commit) = 167 tests PASS**.

- [ ] **Step 5: Kill dev servers**

```bash
kill %1 %2 2>/dev/null
```

---

## Summary

**Total new tests:** 44 backend tests (2 fixture + 3 wipe + 16 parser + 5 session + 6 upload + 12 commit).

**Total commits:** 12 feature/test commits (one per task). All independently revertable.

**Files created:**
- Backend: 1 script, 2 services, 1 router, 4 test files, 1 fixture dir + file = 9 files.
- Frontend: 0 new files (modifications only to `ImportFiles.jsx`).

**Files modified:**
- `backend/app/main.py` — include `charge_imports` router (Task 7).
- `frontend/src/pages/ImportFiles.jsx` — three modifications (Tasks 9, 10, 11).

**After this plan:** The app has a working end-to-end Charge Analysis import. Phase 2c (ERA payment posting to these imported claims) and Phase 2d (Claims Analysis enrichment) are unblocked.
