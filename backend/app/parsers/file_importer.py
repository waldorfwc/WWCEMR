"""
Multi-format file importer.
Detects and parses: ERA 835, CSV, XLS/XLSX, PDF
Returns a normalized ImportResult for each format.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict
import pandas as pd
import pdfplumber

from app.parsers.era_835 import Era835Parser, EraFile


@dataclass
class ImportResult:
    format: str
    filename: str
    success: bool
    era_data: Optional[EraFile] = None
    tabular_data: Optional[List[Dict]] = None
    text_content: Optional[str] = None
    detected_type: str = ""
    errors: List[str] = field(default_factory=list)
    row_count: int = 0


def detect_format(file_path: str, content_bytes: Optional[bytes] = None) -> str:
    """Detect file format by extension and content sniffing."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".835", ".x12", ".edi"):
        return "era835"
    if ext in (".xlsx", ".xls"):
        return "xlsx" if ext == ".xlsx" else "xls"
    if ext == ".csv":
        return "csv"
    if ext == ".pdf":
        return "pdf"
    if content_bytes:
        head = content_bytes[:200]
        if b"ISA*" in head or b"ISA~" in head or (b"ISA" in head and b"GS*" in head):
            return "era835"
        if head[:4] == b"%PDF":
            return "pdf"
        try:
            text = head.decode("utf-8", errors="ignore")
            if "," in text and "\n" in text:
                return "csv"
        except Exception:
            pass
    return "unknown"


def detect_content_type(df: pd.DataFrame) -> str:
    """Guess what kind of data a tabular file contains."""
    cols = [c.lower().replace(" ", "_") for c in df.columns]
    cols_str = " ".join(cols)
    if any(k in cols_str for k in ["claim_number", "claim_no", "claim_id", "clm"]):
        return "claims"
    if any(k in cols_str for k in ["payment", "check", "remit", "era"]):
        return "payments"
    if any(k in cols_str for k in ["eob", "explanation", "benefit"]):
        return "eob"
    if any(k in cols_str for k in ["patient", "mrn", "dob", "date_of_birth"]):
        return "patients"
    return "unknown"


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [
        re.sub(r"[^a-z0-9_]", "_", c.lower().strip().replace(" ", "_").replace("-", "_"))
        for c in df.columns
    ]
    return df


def import_file(file_path: str, content_bytes: Optional[bytes] = None) -> ImportResult:
    filename = os.path.basename(file_path)
    fmt = detect_format(file_path, content_bytes)

    if fmt == "era835":
        return _parse_era(file_path, filename, content_bytes)
    elif fmt == "csv":
        return _parse_csv(file_path, filename, content_bytes)
    elif fmt in ("xlsx", "xls"):
        return _parse_excel(file_path, filename, fmt, content_bytes)
    elif fmt == "pdf":
        return _parse_pdf(file_path, filename, content_bytes)
    else:
        return ImportResult(
            format="unknown", filename=filename, success=False,
            errors=["Unable to detect file format. Supported: ERA 835, CSV, XLS/XLSX, PDF"],
        )


def _parse_era(file_path: str, filename: str, content_bytes: Optional[bytes]) -> ImportResult:
    try:
        if content_bytes:
            content = content_bytes.decode("utf-8", errors="ignore")
        else:
            with open(file_path, "r", errors="ignore") as f:
                content = f.read()
        parser = Era835Parser()
        era = parser.parse(content, filename)
        return ImportResult(
            format="era835", filename=filename, success=True,
            era_data=era, detected_type="remittance", row_count=len(era.claims),
        )
    except Exception as e:
        return ImportResult(
            format="era835", filename=filename, success=False,
            errors=[f"ERA parse error: {str(e)}"],
        )


def _parse_csv(file_path: str, filename: str, content_bytes: Optional[bytes]) -> ImportResult:
    try:
        if content_bytes:
            from io import BytesIO
            df = pd.read_csv(BytesIO(content_bytes), encoding="utf-8", on_bad_lines="skip")
        else:
            df = pd.read_csv(file_path, encoding="utf-8", on_bad_lines="skip")
        df = _normalize_columns(df)
        content_type = detect_content_type(df)
        records = df.where(pd.notnull(df), None).to_dict("records")
        return ImportResult(
            format="csv", filename=filename, success=True,
            tabular_data=records, detected_type=content_type, row_count=len(records),
        )
    except Exception as e:
        return ImportResult(
            format="csv", filename=filename, success=False,
            errors=[f"CSV parse error: {str(e)}"],
        )


def _parse_excel(file_path: str, filename: str, fmt: str, content_bytes: Optional[bytes]) -> ImportResult:
    try:
        if content_bytes:
            from io import BytesIO
            xf = pd.ExcelFile(BytesIO(content_bytes))
        else:
            xf = pd.ExcelFile(file_path)

        all_records = []
        for sheet in xf.sheet_names:
            df = xf.parse(sheet)
            if df.empty:
                continue
            df = _normalize_columns(df)
            records = df.where(pd.notnull(df), None).to_dict("records")
            for r in records:
                r["__sheet__"] = sheet
            all_records.extend(records)

        content_type = detect_content_type(pd.DataFrame(all_records)) if all_records else "unknown"
        return ImportResult(
            format=fmt, filename=filename, success=True,
            tabular_data=all_records, detected_type=content_type, row_count=len(all_records),
        )
    except Exception as e:
        return ImportResult(
            format=fmt, filename=filename, success=False,
            errors=[f"Excel parse error: {str(e)}"],
        )


def _parse_pdf(file_path: str, filename: str, content_bytes: Optional[bytes]) -> ImportResult:
    try:
        from io import BytesIO
        source = BytesIO(content_bytes) if content_bytes else file_path

        text_parts = []
        tables = []
        with pdfplumber.open(source) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)
                for t in page.extract_tables():
                    tables.extend(t)

        full_text = "\n".join(text_parts)
        text_lower = full_text.lower()

        if "explanation of benefits" in text_lower or "eob" in text_lower:
            detected = "eob"
        elif "remittance" in text_lower or "era" in text_lower:
            detected = "remittance"
        elif "claim" in text_lower and ("paid" in text_lower or "denied" in text_lower):
            detected = "claims"
        else:
            detected = "unknown"

        tabular = None
        if tables and len(tables) > 1:
            headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(tables[0])]
            records = []
            for row in tables[1:]:
                if row:
                    records.append({headers[i]: str(v).strip() if v else "" for i, v in enumerate(row) if i < len(headers)})
            tabular = records if records else None

        return ImportResult(
            format="pdf", filename=filename, success=True,
            text_content=full_text, tabular_data=tabular,
            detected_type=detected, row_count=len(text_parts),
        )
    except Exception as e:
        return ImportResult(
            format="pdf", filename=filename, success=False,
            errors=[f"PDF parse error: {str(e)}"],
        )
