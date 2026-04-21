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
