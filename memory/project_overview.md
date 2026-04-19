---
name: ERA 835 Payment Posting System Overview
description: Full-stack ERA 835 parser and payment posting system built for a Maryland medical practice
type: project
---

Built a complete ERA 835 payment posting system at `/Users/wwcclaudecode/Documents/wwc-era-project/`.

**Tech Stack:** Python FastAPI backend, React + Tailwind frontend, PostgreSQL, ReportLab PDFs, Claude API for appeal letters

**Key Features:**
- Full X12 ERA 835 parser (ISA/GS/ST/BPR/TRN/CLP/CAS/SVC/NM1/PLB)
- Multi-insurance COB: primary, secondary, tertiary
- CARC/RARC code library with 60+ codes categorized
- Maryland-specific timely filing rules by payer (CareFirst 180d, UHC/Cigna 90d, Medicare/Medicaid 365d)
- Appeal deadline tracking with urgency alerts
- AI appeal letter generation (Claude Opus 4.6) — especially timely filing with MD Insurance Article §15-1005
- CO-45 contractual adjustments ignored per user (no denial records created)
- EOB PDF generator (ReportLab, professional format)
- Patient financial ledger by DOS
- Multi-format import: ERA 835, CSV, XLS/XLSX, PDF
- HIPAA audit log on all PHI access
- Drag-and-drop file import UI

**To start:** Copy `.env.example` to `.env`, add `ANTHROPIC_API_KEY`, run `./start.sh`

**Why:** Internal billing system for Maryland medical practice. Files come from PrimeSuite or Waystar.
**How to apply:** When extending, maintain Maryland-specific payer rules in `maryland_rules.py` and keep CO-45 out of denial workflow.
