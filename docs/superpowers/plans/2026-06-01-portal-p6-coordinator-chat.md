# Patient Portal P6 — Coordinator Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Wire a single text-based conversation per surgery so patient and coordinator can exchange messages without phone tag. SMS pings the patient when staff replies; badge-only signal for staff.

**Architecture:** Two new tables (`surgery_messages`, `message_templates`). Two new patient endpoints, six new staff endpoints. Replace `MessagesStub.jsx` with real Messages page; add `/messages` staff inbox + `/staff/message-templates` CRUD page + a thread section on `SurgeryDetail.jsx`. Reuses existing portal middleware (read-only for staff preview from #154) and `send_sms` infrastructure.

**Spec:** `docs/superpowers/specs/2026-06-01-portal-p6-coordinator-chat-design.md`

**Key facts (don't relitigate):**
- Patient portal middleware `require_portal_token` at `backend/app/routers/patient_portal.py:167` already blocks non-GET when JWT viewer is `staff:*` (from #154). Patient POST messages are automatically blocked for staff preview without any new code.
- `send_sms(to_phone, body)` lives in `backend/app/services/checklist_notifications.py:170` and is the right helper for staff→patient pings.
- `decode_portal_token(token)` at `backend/app/services/patient_portal_auth.py:148` returns full JWT payload incl. `viewer` claim. Patient GET handler will use it to detect preview mode.
- `get_current_user` at `backend/app/routers/auth.py:44` returns an enriched dict with `email`. Use `user["email"]` for `author_email`.
- `Surgery` has `cell_phone`, `patient_name`, `scheduled_date` — all the fields the SMS body + template substitutions need.
- Staff admin frontend uses `api` from `frontend/src/utils/api.js` with `baseURL: '/api'`.

---

## Task 1: Backend — `surgery_messages` + `message_templates` schema + migration

**Files:**
- Create: `backend/app/models/surgery_message.py` (both models — they're small)
- Modify: `backend/app/models/__init__.py` or equivalent if models are aggregated there (verify by grepping)
- Create: `backend/scripts/migrate_portal_p6.py`
- Test: `backend/tests/test_portal_p6_schema.py`

- [ ] **Step 1: Failing tests** at `backend/tests/test_portal_p6_schema.py`:

```python
"""Portal P6 schema — surgery_messages + message_templates."""
from datetime import datetime
from app.models.surgery import Surgery
from app.models.surgery_message import SurgeryMessage, MessageTemplate


def test_surgery_message_round_trip(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    m = SurgeryMessage(
        surgery_id=s.id,
        author_kind="patient",
        body="Hi, when can I eat before surgery?",
    )
    db.add(m); db.commit(); db.refresh(m)
    assert m.surgery_id == s.id
    assert m.author_kind == "patient"
    assert m.author_email is None
    assert m.read_by_patient_at is None
    assert m.read_by_staff_at is None
    assert m.sent_at is not None


def test_surgery_message_staff_author_records_email(db):
    s = Surgery(chart_number="1", patient_name="Pat", status="new")
    db.add(s); db.commit(); db.refresh(s)
    m = SurgeryMessage(
        surgery_id=s.id,
        author_kind="staff",
        author_email="ocooke@example.com",
        body="Clear liquids OK until 2 hours before.",
    )
    db.add(m); db.commit(); db.refresh(m)
    assert m.author_email == "ocooke@example.com"


def test_message_template_round_trip(db):
    t = MessageTemplate(
        name="Eating/drinking",
        body="Hi {{patient_name}}, you can have clear liquids until "
             "2 hours before your {{surgery_date}} surgery.",
    )
    db.add(t); db.commit(); db.refresh(t)
    assert t.id is not None
    assert "{{patient_name}}" in t.body
    assert t.created_at is not None
    assert t.updated_at is not None
```

- [ ] **Step 2: Run, confirm fail.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_portal_p6_schema.py -v
```

- [ ] **Step 3: Create the models** at `backend/app/models/surgery_message.py`:

```python
"""Patient<->staff messages per surgery + reusable message templates."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.guid import GUID, new_uuid


class SurgeryMessage(Base):
    __tablename__ = "surgery_messages"
    __table_args__ = (
        Index("ix_surgery_messages_thread", "surgery_id", "sent_at"),
    )

    id                  = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id          = Column(GUID(),
                                    ForeignKey("surgeries.id", ondelete="CASCADE"),
                                    nullable=False)
    author_kind         = Column(String(20), nullable=False)
    author_email        = Column(String(200), nullable=True)
    body                = Column(Text, nullable=False)
    sent_at             = Column(DateTime, default=datetime.utcnow,
                                    nullable=False)
    read_by_patient_at  = Column(DateTime, nullable=True)
    read_by_staff_at    = Column(DateTime, nullable=True)


class MessageTemplate(Base):
    __tablename__ = "message_templates"

    id          = Column(GUID(), primary_key=True, default=new_uuid)
    name        = Column(String(120), nullable=False)
    body        = Column(Text, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow, nullable=False)
```

- [ ] **Step 4: Ensure models load.** If `backend/app/models/__init__.py` aggregates models, add `from app.models.surgery_message import ...`. Otherwise, ensure any router/test importing `SurgeryMessage` will trigger model registration. Verify with:

```bash
/usr/bin/grep -n "import\|surgery_message" backend/app/models/__init__.py 2>&1 | /usr/bin/head -5
```

If the package's `__init__.py` doesn't aggregate, you can skip — tests import the classes directly.

- [ ] **Step 5: Run, confirm pass.**

- [ ] **Step 6: Create the migration** at `backend/scripts/migrate_portal_p6.py`:

```python
"""Idempotent P6 migration: surgery_messages + message_templates + seeds."""
import os
import sys
from sqlalchemy import create_engine, text

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS surgery_messages (
        id CHAR(36) PRIMARY KEY,
        surgery_id CHAR(36) NOT NULL REFERENCES surgeries(id) ON DELETE CASCADE,
        author_kind VARCHAR(20) NOT NULL,
        author_email VARCHAR(200),
        body TEXT NOT NULL,
        sent_at TIMESTAMP NOT NULL DEFAULT NOW(),
        read_by_patient_at TIMESTAMP,
        read_by_staff_at TIMESTAMP
    )""",
    """CREATE INDEX IF NOT EXISTS ix_surgery_messages_thread
       ON surgery_messages (surgery_id, sent_at)""",
    """CREATE TABLE IF NOT EXISTS message_templates (
        id CHAR(36) PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        body TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
]

SEED = [
    ("Eating/drinking before surgery",
     "Hi {{patient_name}}, you can have clear liquids up to 2 hours before "
     "your surgery on {{surgery_date}}. No solid food after midnight the "
     "night before."),
    ("Consent signing tips",
     "Hi {{patient_name}}, if you're having trouble with the consent form, "
     "please use a recent browser (Chrome/Safari) on a desktop or laptop "
     "instead of mobile. If it still won't work, call us at 240-252-2140."),
    ("FMLA processing timing",
     "Hi {{patient_name}}, we received your FMLA paperwork. We'll fill it "
     "out within 5 business days and post it to your portal."),
    ("Schedule reminder",
     "Hi {{patient_name}}, we've cleared your insurance — please log into "
     "your portal to pick a surgery date."),
    ("Post-op check-in",
     "Hi {{patient_name}}, how are you feeling after your surgery on "
     "{{surgery_date}}? Let us know if you have any concerns."),
]


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr); sys.exit(2)
    eng = create_engine(db_url)
    with eng.begin() as conn:
        for ddl in SCHEMA:
            conn.execute(text(ddl))
            print(f"  ✓ {ddl.split(chr(10))[0][:80]}")
        # Seed only if templates table is empty (re-running shouldn't dupe)
        count = conn.execute(text(
            "SELECT COUNT(*) FROM message_templates"
        )).scalar()
        if count == 0:
            import uuid
            for name, body in SEED:
                conn.execute(text(
                    "INSERT INTO message_templates (id, name, body) "
                    "VALUES (:id, :name, :body)"
                ), {"id": str(uuid.uuid4()), "name": name, "body": body})
            print(f"  ✓ seeded {len(SEED)} templates")
        else:
            print(f"  ✓ {count} templates already present — skipping seed")
    print("\nDone.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run full backend regression** to confirm no model-import issues:

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/backend && \
  ./venv/bin/pytest tests/test_portal_p6_schema.py -v 2>&1 | tail -10
./venv/bin/pytest tests/test_patient_portal_endpoints.py 2>&1 | tail -3
```

- [ ] **Step 8: Commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project
git add backend/app/models/surgery_message.py \
        backend/scripts/migrate_portal_p6.py \
        backend/tests/test_portal_p6_schema.py
git commit -m "feat(portal-p6): surgery_messages + message_templates schema + migration"
```

---

## Task 2: Backend — patient endpoints (GET + POST /portal/{sid}/messages)

**Files:**
- Modify: `backend/app/routers/patient_portal.py` — append two endpoints
- Modify: `backend/tests/test_patient_portal_endpoints.py` — append tests

- [ ] **Step 1: Failing tests** — append:

```python
def test_portal_messages_get_empty(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.get(f"/api/patient/portal/{s.id}/messages",
                      headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"messages": []}


def test_portal_messages_get_marks_staff_as_read_for_patient(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery_message import SurgeryMessage
    s = _seed_surgery(db)
    db.add(SurgeryMessage(surgery_id=s.id, author_kind="staff",
                              author_email="ocooke@example.com",
                              body="Hi Jane, you can have liquids…"))
    db.commit()
    token = issue_portal_token(s)
    client.get(f"/api/patient/portal/{s.id}/messages",
                  headers={"Authorization": f"Bearer {token}"})
    msgs = db.query(SurgeryMessage).filter(
        SurgeryMessage.surgery_id == s.id).all()
    assert all(m.read_by_patient_at is not None for m in msgs
                  if m.author_kind == "staff")


def test_portal_messages_get_in_staff_preview_skips_mark_read(client, db):
    """Preview mode (#154) must NOT mutate patient's unread state."""
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery_message import SurgeryMessage
    s = _seed_surgery(db)
    db.add(SurgeryMessage(surgery_id=s.id, author_kind="staff",
                              author_email="staff@x", body="hello"))
    db.commit()
    staff_tok = issue_portal_token(s, viewer="staff:ocooke@example.com",
                                       ttl_minutes=60)
    client.get(f"/api/patient/portal/{s.id}/messages",
                  headers={"Authorization": f"Bearer {staff_tok}"})
    msgs = db.query(SurgeryMessage).filter(
        SurgeryMessage.surgery_id == s.id).all()
    assert all(m.read_by_patient_at is None for m in msgs)


def test_portal_messages_post_persists_with_patient_author(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    from app.models.surgery_message import SurgeryMessage
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.post(
        f"/api/patient/portal/{s.id}/messages",
        json={"body": "Can I have coffee?"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["author_kind"] == "patient"
    rows = db.query(SurgeryMessage).filter(
        SurgeryMessage.surgery_id == s.id).all()
    assert len(rows) == 1
    assert rows[0].body == "Can I have coffee?"
    assert rows[0].author_email is None


def test_portal_messages_post_rejects_empty(client, db):
    from app.services.patient_portal_auth import issue_portal_token
    s = _seed_surgery(db)
    db.commit()
    token = issue_portal_token(s)
    r = client.post(
        f"/api/patient/portal/{s.id}/messages",
        json={"body": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Append the endpoints** to `backend/app/routers/patient_portal.py`. At the end of the file:

```python
# ─── /{surgery_id}/messages ────────────────────────────────────────

from datetime import datetime as _dt
from app.models.surgery_message import SurgeryMessage


class MessagePostPayload(BaseModel):
    body: str


@router.get("/{surgery_id}/messages")
def portal_messages_get(
    surgery_id: str,
    request: Request,
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Return the patient's full thread, oldest→newest. Marks staff-authored
    messages as read by the patient unless the active JWT is a staff
    preview token — preview must not clear unread state."""
    # Re-decode to read the viewer claim. require_portal_token validates
    # auth + surgery-match; this is just for the preview-skip side effect.
    token = authorization.split(" ", 1)[1].strip() if " " in authorization else ""
    payload = auth.decode_portal_token(token) or {}
    is_preview = (payload.get("viewer") or "").startswith("staff:")

    # Run require_portal_token semantics first
    require_portal_token(request, surgery_id, authorization)

    msgs = (db.query(SurgeryMessage)
              .filter(SurgeryMessage.surgery_id == surgery_id)
              .order_by(SurgeryMessage.sent_at.asc())
              .all())
    if not is_preview:
        for m in msgs:
            if m.author_kind == "staff" and m.read_by_patient_at is None:
                m.read_by_patient_at = _dt.utcnow()
        db.commit()
    return {"messages": [_msg_dict(m) for m in msgs]}


@router.post("/{surgery_id}/messages")
def portal_messages_post(
    surgery_id: str,
    payload: MessagePostPayload,
    db: Session = Depends(get_db),
    _: str = Depends(require_portal_token),
):
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    m = SurgeryMessage(
        surgery_id=s.id,
        author_kind="patient",
        body=body,
    )
    db.add(m); db.commit(); db.refresh(m)
    return _msg_dict(m)


def _msg_dict(m: "SurgeryMessage") -> dict:
    return {
        "id":           str(m.id),
        "author_kind":  m.author_kind,
        "author_label": "You" if m.author_kind == "patient" else "WWC",
        "body":         m.body,
        "sent_at":      m.sent_at.isoformat() if m.sent_at else None,
    }
```

Note: the GET endpoint can't use `Depends(require_portal_token)` directly because we need to inspect the viewer claim BEFORE deciding whether to mark-as-read. The endpoint calls `require_portal_token(...)` manually for validation. An alternative refactor (returning the payload from the dependency) is cleaner but out of scope.

- [ ] **Step 4: Run, confirm pass.**

- [ ] **Step 5: Commit.**

```bash
git add backend/app/routers/patient_portal.py \
        backend/tests/test_patient_portal_endpoints.py
git commit -m "feat(portal-p6): patient GET + POST /portal/{sid}/messages"
```

---

## Task 3: Backend — staff endpoints (thread + inbox + SMS notification)

**Files:**
- Create: `backend/app/routers/surgery_messages.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/test_surgery_messages.py`

- [ ] **Step 1: Failing tests** at `backend/tests/test_surgery_messages.py`:

```python
"""Staff-side messaging endpoints + SMS notification."""
from unittest.mock import patch

from app.models.surgery import Surgery
from app.models.surgery_message import SurgeryMessage


def _seed_surgery(db, **kw):
    s = Surgery(chart_number=kw.get("chart","S1"),
                  patient_name=kw.get("name","Pat"),
                  status="new",
                  cell_phone=kw.get("phone","+12405551234"))
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_staff_messages_get_returns_thread_and_marks_read(client, db):
    s = _seed_surgery(db)
    db.add(SurgeryMessage(surgery_id=s.id, author_kind="patient",
                              body="When can I eat?"))
    db.commit()
    r = client.get(f"/api/staff/surgeries/{s.id}/messages")
    assert r.status_code == 200, r.text
    assert len(r.json()["messages"]) == 1
    # patient message now marked read by staff
    rows = db.query(SurgeryMessage).filter(
        SurgeryMessage.surgery_id == s.id).all()
    assert all(m.read_by_staff_at is not None for m in rows
                  if m.author_kind == "patient")


def test_staff_messages_post_persists_and_sends_sms(client, db):
    s = _seed_surgery(db)
    db.commit()
    with patch("app.routers.surgery_messages.send_sms",
                return_value=True) as mock_sms:
        r = client.post(
            f"/api/staff/surgeries/{s.id}/messages",
            json={"body": "Clear liquids OK"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["author_kind"] == "staff"
    rows = db.query(SurgeryMessage).filter(
        SurgeryMessage.surgery_id == s.id).all()
    assert len(rows) == 1
    assert rows[0].body == "Clear liquids OK"
    assert rows[0].author_email == "tester@waldorfwomenscare.com"
    assert mock_sms.called
    sms_to, sms_body = mock_sms.call_args[0]
    assert sms_to == "+12405551234"
    assert "WWC" in sms_body or "wwc" in sms_body.lower()
    assert "gw.waldorfwomenscare.com" in sms_body


def test_staff_messages_post_soft_fails_on_sms_error(client, db):
    """If send_sms raises, the message should still be persisted."""
    s = _seed_surgery(db)
    db.commit()
    with patch("app.routers.surgery_messages.send_sms",
                side_effect=Exception("twilio down")):
        r = client.post(
            f"/api/staff/surgeries/{s.id}/messages",
            json={"body": "Hi"},
        )
    assert r.status_code == 200
    assert db.query(SurgeryMessage).count() == 1


def test_staff_messages_inbox_lists_surgeries_with_unread_patient_msgs(
        client, db):
    s1 = _seed_surgery(db, chart="A", name="Alice")
    s2 = _seed_surgery(db, chart="B", name="Bob")
    db.add(SurgeryMessage(surgery_id=s1.id, author_kind="patient",
                              body="hi"))
    # s2 has staff message — should NOT show up in inbox
    db.add(SurgeryMessage(surgery_id=s2.id, author_kind="staff",
                              author_email="x@y", body="hi back"))
    db.commit()
    r = client.get("/api/staff/messages/inbox")
    assert r.status_code == 200
    rows = r.json()["rows"]
    sids = [r["surgery_id"] for r in rows]
    assert str(s1.id) in sids
    assert str(s2.id) not in sids


def test_staff_messages_inbox_drops_once_staff_views(client, db):
    s = _seed_surgery(db)
    db.add(SurgeryMessage(surgery_id=s.id, author_kind="patient",
                              body="?"))
    db.commit()
    # Inbox shows it
    assert any(r["surgery_id"] == str(s.id)
                  for r in client.get("/api/staff/messages/inbox").json()["rows"])
    # Staff views the thread
    client.get(f"/api/staff/surgeries/{s.id}/messages")
    # Inbox no longer shows it
    assert not any(r["surgery_id"] == str(s.id)
                      for r in client.get("/api/staff/messages/inbox").json()["rows"])
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Create the router** at `backend/app/routers/surgery_messages.py`:

```python
"""Staff endpoints: per-surgery thread + global unread inbox.

The patient-facing endpoints live in patient_portal.py (gated by the
portal JWT). These endpoints are gated by the staff session via
get_current_user.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery import Surgery
from app.models.surgery_message import SurgeryMessage
from app.routers.auth import get_current_user
from app.services.checklist_notifications import send_sms

router = APIRouter(prefix="/api/staff", tags=["staff-messages"])

PORTAL_URL = "https://gw.waldorfwomenscare.com"


class MessagePayload(BaseModel):
    body: str


def _to_dict(m: SurgeryMessage) -> dict:
    return {
        "id":           str(m.id),
        "author_kind":  m.author_kind,
        "author_email": m.author_email,
        "body":         m.body,
        "sent_at":      m.sent_at.isoformat() if m.sent_at else None,
    }


@router.get("/surgeries/{surgery_id}/messages")
def staff_thread(
    surgery_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    msgs = (db.query(SurgeryMessage)
              .filter(SurgeryMessage.surgery_id == surgery_id)
              .order_by(SurgeryMessage.sent_at.asc())
              .all())
    for m in msgs:
        if m.author_kind == "patient" and m.read_by_staff_at is None:
            m.read_by_staff_at = datetime.utcnow()
    db.commit()
    return {"messages": [_to_dict(m) for m in msgs]}


@router.post("/surgeries/{surgery_id}/messages")
def staff_send(
    surgery_id: str,
    payload: MessagePayload,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="Message cannot be empty.")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    m = SurgeryMessage(
        surgery_id=s.id,
        author_kind="staff",
        author_email=user["email"],
        body=body,
    )
    db.add(m); db.commit(); db.refresh(m)
    # SMS the patient — soft-fail
    phone = (s.cell_phone or s.phone or "").strip()
    if phone:
        try:
            send_sms(phone,
                       f"WWC has a new message for you. Sign in at "
                       f"{PORTAL_URL} to read it.")
        except Exception:
            import logging
            logging.getLogger(__name__).exception("portal P6 SMS notify failed")
    return _to_dict(m)


@router.get("/messages/inbox")
def staff_inbox(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Surgeries with at least one unread patient message, newest first."""
    rows = (db.query(SurgeryMessage, Surgery)
              .join(Surgery, Surgery.id == SurgeryMessage.surgery_id)
              .filter(SurgeryMessage.author_kind == "patient",
                       SurgeryMessage.read_by_staff_at.is_(None))
              .order_by(SurgeryMessage.sent_at.desc())
              .all())
    # Collapse to one row per surgery (most recent unread per surgery)
    seen: set = set()
    out = []
    for m, s in rows:
        if s.id in seen: continue
        seen.add(s.id)
        out.append({
            "surgery_id":    str(s.id),
            "chart_number":  s.chart_number,
            "patient_name":  s.patient_name,
            "last_body":     m.body[:80],
            "last_sent_at":  m.sent_at.isoformat() if m.sent_at else None,
        })
    return {"rows": out, "count": len(out)}
```

- [ ] **Step 4: Register the router** in `backend/app/main.py`. Add to the existing import block + an `include_router` call. The router carries its full `/api/staff` prefix, so register without an additional `prefix=`:

```python
from app.routers import surgery_messages
app.include_router(surgery_messages.router)
```

- [ ] **Step 5: Run, confirm pass.**

- [ ] **Step 6: Commit.**

```bash
git add backend/app/routers/surgery_messages.py backend/app/main.py \
        backend/tests/test_surgery_messages.py
git commit -m "feat(portal-p6): staff thread + inbox endpoints + SMS notification"
```

---

## Task 4: Backend — message templates CRUD + render

**Files:**
- Create: `backend/app/routers/message_templates.py`
- Modify: `backend/app/main.py` (register)
- Test: `backend/tests/test_message_templates.py`

- [ ] **Step 1: Failing tests:**

```python
"""Message templates — CRUD + rendered output."""
from app.models.surgery import Surgery
from app.models.surgery_message import MessageTemplate


def test_message_templates_list_empty(client, db):
    r = client.get("/api/staff/message-templates")
    assert r.status_code == 200
    assert r.json() == {"templates": []}


def test_message_templates_crud_round_trip(client, db):
    # Create
    r = client.post("/api/staff/message-templates",
                       json={"name": "Test", "body": "Hi {{patient_name}}"})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    # List
    r = client.get("/api/staff/message-templates")
    assert len(r.json()["templates"]) == 1
    # Update
    r = client.put(f"/api/staff/message-templates/{tid}",
                      json={"name": "Test edited", "body": "Hi {{patient_name}}!"})
    assert r.status_code == 200
    assert r.json()["name"] == "Test edited"
    # Delete
    r = client.delete(f"/api/staff/message-templates/{tid}")
    assert r.status_code == 200
    assert db.query(MessageTemplate).count() == 0


def test_message_templates_render_substitutes_patient_and_date(client, db):
    from datetime import date
    s = Surgery(chart_number="1", patient_name="Jane Doe", status="new",
                  scheduled_date=date(2026, 6, 15))
    db.add(s); db.commit(); db.refresh(s)
    t = MessageTemplate(name="Hi", body="Hello {{patient_name}}, "
                                          "your date is {{surgery_date}}.")
    db.add(t); db.commit(); db.refresh(t)
    r = client.get(
        f"/api/staff/message-templates/{t.id}/render",
        params={"surgery_id": str(s.id)},
    )
    assert r.status_code == 200
    rendered = r.json()["body"]
    assert "Jane Doe" in rendered
    assert "June 15, 2026" in rendered


def test_message_templates_render_handles_missing_scheduled_date(client, db):
    s = Surgery(chart_number="2", patient_name="Pat", status="new",
                  scheduled_date=None)
    db.add(s); db.commit(); db.refresh(s)
    t = MessageTemplate(name="X", body="Date: {{surgery_date}}")
    db.add(t); db.commit(); db.refresh(t)
    r = client.get(
        f"/api/staff/message-templates/{t.id}/render",
        params={"surgery_id": str(s.id)},
    )
    assert r.status_code == 200
    assert r.json()["body"] == "Date: "
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Create the router** at `backend/app/routers/message_templates.py`:

```python
"""Message template CRUD + render endpoint. Staff-managed canned replies."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.surgery import Surgery
from app.models.surgery_message import MessageTemplate
from app.routers.auth import get_current_user

router = APIRouter(prefix="/api/staff/message-templates", tags=["staff-messages"])


class TemplatePayload(BaseModel):
    name: str
    body: str


def _to_dict(t: MessageTemplate) -> dict:
    return {"id": str(t.id), "name": t.name, "body": t.body}


def _render(body: str, surgery: Surgery) -> str:
    sd = surgery.scheduled_date
    date_str = sd.strftime("%B %-d, %Y") if sd else ""
    return (body
              .replace("{{patient_name}}", surgery.patient_name or "")
              .replace("{{surgery_date}}", date_str))


@router.get("")
def list_templates(db: Session = Depends(get_db),
                      user: dict = Depends(get_current_user)):
    rows = (db.query(MessageTemplate)
              .order_by(MessageTemplate.name.asc()).all())
    return {"templates": [_to_dict(t) for t in rows]}


@router.post("")
def create_template(payload: TemplatePayload,
                       db: Session = Depends(get_db),
                       user: dict = Depends(get_current_user)):
    t = MessageTemplate(name=payload.name.strip(),
                              body=payload.body)
    db.add(t); db.commit(); db.refresh(t)
    return _to_dict(t)


@router.put("/{tid}")
def update_template(tid: str, payload: TemplatePayload,
                       db: Session = Depends(get_db),
                       user: dict = Depends(get_current_user)):
    t = db.query(MessageTemplate).filter(MessageTemplate.id == tid).first()
    if t is None:
        raise HTTPException(status_code=404, detail="template not found")
    t.name = payload.name.strip()
    t.body = payload.body
    db.commit(); db.refresh(t)
    return _to_dict(t)


@router.delete("/{tid}")
def delete_template(tid: str, db: Session = Depends(get_db),
                       user: dict = Depends(get_current_user)):
    t = db.query(MessageTemplate).filter(MessageTemplate.id == tid).first()
    if t is None:
        raise HTTPException(status_code=404, detail="template not found")
    db.delete(t); db.commit()
    return {"ok": True}


@router.get("/{tid}/render")
def render_template(tid: str, surgery_id: str,
                       db: Session = Depends(get_db),
                       user: dict = Depends(get_current_user)):
    t = db.query(MessageTemplate).filter(MessageTemplate.id == tid).first()
    if t is None:
        raise HTTPException(status_code=404, detail="template not found")
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="surgery not found")
    return {"body": _render(t.body, s)}
```

- [ ] **Step 4: Register the router** in `backend/app/main.py`:

```python
from app.routers import message_templates
app.include_router(message_templates.router)
```

- [ ] **Step 5: Run, confirm pass + commit.**

```bash
git add backend/app/routers/message_templates.py backend/app/main.py \
        backend/tests/test_message_templates.py
git commit -m "feat(portal-p6): message templates CRUD + render endpoint"
```

---

## Task 5: Frontend — patient Messages page

**Files:**
- Modify: `frontend/src/App.jsx` — swap MessagesStub → Messages
- Create: `frontend/src/pages/portal/Messages.jsx`

- [ ] **Step 1: Inspect** App.jsx to find where MessagesStub is wired so the swap is targeted:

```bash
/usr/bin/grep -n "MessagesStub\|messages.*element" \
  /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/App.jsx
```

- [ ] **Step 2: Create** `frontend/src/pages/portal/Messages.jsx`:

```jsx
import { useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { portalApi, isStaffPreview } from '../../lib/portal-api'

export default function Messages() {
  const { sid } = useParams()
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')
  const scrollRef = useRef(null)

  const { data } = useQuery({
    queryKey: ['portal-messages', sid],
    queryFn: () => portalApi.get(`/${sid}/messages`).then(r => r.data),
    refetchInterval: 30_000,
    staleTime: 10_000,
  })

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [data?.messages?.length])

  const send = useMutation({
    mutationFn: (body) =>
      portalApi.post(`/${sid}/messages`, { body }).then(r => r.data),
    onSuccess: () => {
      setDraft('')
      qc.invalidateQueries({ queryKey: ['portal-messages', sid] })
    },
  })

  const messages = data?.messages || []
  return (
    <div className="space-y-3">
      <h1 className="text-2xl font-semibold text-gray-900">Messages</h1>
      <section className="bg-white rounded-lg shadow p-4 max-h-[60vh]
                            overflow-y-auto space-y-3">
        {messages.length === 0 && (
          <div className="text-sm text-gray-500 text-center py-8">
            No messages yet. Send us a message below to start the conversation.
          </div>
        )}
        {messages.map(m => (
          <div key={m.id}
                className={`flex ${m.author_kind === 'patient'
                                       ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] rounded-lg px-3 py-2 text-sm
                                ${m.author_kind === 'patient'
                                    ? 'bg-plum-100 text-gray-900'
                                    : 'bg-gray-100 text-gray-900'}`}>
              <div className="text-[10px] text-gray-500 mb-1">
                {m.author_label} · {m.sent_at?.slice(0, 16).replace('T', ' ')}
              </div>
              <div className="whitespace-pre-wrap">{m.body}</div>
            </div>
          </div>
        ))}
        <div ref={scrollRef} />
      </section>

      {!isStaffPreview() && (
        <form
          onSubmit={e => { e.preventDefault();
                              if (draft.trim()) send.mutate(draft.trim()) }}
          className="bg-white rounded-lg shadow p-4 space-y-2">
          <textarea
            value={draft}
            onChange={e => setDraft(e.target.value)}
            disabled={send.isPending}
            placeholder="Type a message…"
            rows={3}
            className="w-full rounded border-gray-300 text-sm" />
          <div className="flex justify-end">
            <button type="submit"
                     disabled={!draft.trim() || send.isPending}
                     className="btn-primary text-sm">
              {send.isPending ? 'Sending…' : 'Send'}
            </button>
          </div>
        </form>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Swap the route** in `frontend/src/App.jsx`. Replace the `MessagesStub` import + usage with `Messages`:

```jsx
import Messages from './pages/portal/Messages'
// ...
<Route path="messages" element={<Messages />} />
```

- [ ] **Step 4: Build check + commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -6
cd ..
git add frontend/src/App.jsx frontend/src/pages/portal/Messages.jsx
git commit -m "feat(portal-p6): patient Messages page (replaces MessagesStub)"
```

---

## Task 6: Frontend — staff inbox + nav badge

**Files:**
- Create: `frontend/src/pages/StaffInbox.jsx`
- Modify: `frontend/src/App.jsx` (add `/messages` route)
- Modify: the staff navigation (look for an existing nav component — likely a `<NavBar>` or sidebar — and add a "Messages" entry with badge)

- [ ] **Step 1: Locate the staff nav component:**

```bash
/usr/bin/grep -rn "NavLink\|nav.*items\|MENU\|to=\"/surgery" \
  /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/components/ \
  /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/App.jsx \
  2>&1 | /usr/bin/head -10
```

Pick the nav file most likely to be the persistent top/side bar. Read it to understand the array shape and add a `Messages` entry pointing to `/messages`.

- [ ] **Step 2: StaffInbox page** at `frontend/src/pages/StaffInbox.jsx`:

```jsx
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import api from '../utils/api'

export default function StaffInbox() {
  const nav = useNavigate()
  const { data, isLoading } = useQuery({
    queryKey: ['staff-inbox'],
    queryFn: () => api.get('/staff/messages/inbox').then(r => r.data),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
  if (isLoading) return <div className="p-4 text-sm">Loading…</div>
  const rows = data?.rows || []
  return (
    <div className="p-4 max-w-4xl">
      <h1 className="text-2xl font-semibold text-gray-900 mb-4">Messages</h1>
      {rows.length === 0 ? (
        <div className="text-sm text-gray-500">
          No unread patient messages.
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow divide-y divide-gray-100">
          {rows.map(r => (
            <button key={r.surgery_id}
                     onClick={() => nav(`/surgery/${r.surgery_id}#messages`)}
                     className="w-full text-left px-4 py-3 hover:bg-gray-50
                                  flex items-center justify-between">
              <div>
                <div className="font-medium text-gray-900">{r.patient_name}</div>
                <div className="text-xs text-gray-500">
                  Chart #{r.chart_number}
                </div>
                <div className="text-sm text-gray-700 mt-1 truncate">
                  {r.last_body}
                </div>
              </div>
              <div className="text-xs text-gray-500">
                {r.last_sent_at?.slice(0, 16).replace('T', ' ')}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Add `/messages` route** in `App.jsx` inside the `ProtectedApp`/main router. Place it alongside other top-level staff routes.

- [ ] **Step 4: Nav badge.** In the staff nav component identified in Step 1, fetch the inbox count via the same query and render a small red badge:

```jsx
import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

function MessagesNavLink() {
  const { data } = useQuery({
    queryKey: ['staff-inbox'],   // SHARED key so it dedupes
    queryFn: () => api.get('/staff/messages/inbox').then(r => r.data),
    refetchInterval: 60_000,
  })
  const count = data?.count || 0
  return (
    <NavLink to="/messages" className="...">
      Messages
      {count > 0 && (
        <span className="ml-1 bg-red-500 text-white text-[10px]
                            rounded-full px-1.5 py-0.5">
          {count}
        </span>
      )}
    </NavLink>
  )
}
```

Use whatever NavLink/className pattern the existing nav uses; don't copy this literal style if it clashes.

- [ ] **Step 5: Build check + commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -6
cd ..
git add frontend/src/pages/StaffInbox.jsx frontend/src/App.jsx \
        frontend/src/<nav-component>.jsx
git commit -m "feat(portal-p6): staff /messages inbox + nav badge"
```

---

## Task 7: Frontend — SurgeryDetail Messages section + template dropdown

**Files:**
- Modify: `frontend/src/pages/SurgeryDetail.jsx` — add a `<MessagesSection>` component near the bottom
- Create: `frontend/src/components/MessagesSection.jsx`

- [ ] **Step 1: Read the current SurgeryDetail layout** to find a good insertion point (after Payments section is good — search for `<h2.*Payments`):

```bash
/usr/bin/grep -n "<h2\|<section\|className=\"card" \
  /Users/wwcclaudecode/Documents/wwc-era-project/frontend/src/pages/SurgeryDetail.jsx \
  2>&1 | /usr/bin/head -20
```

- [ ] **Step 2: Create the component** at `frontend/src/components/MessagesSection.jsx`:

```jsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'

export default function MessagesSection({ sid }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')
  const [picked, setPicked] = useState('')

  const { data: thread } = useQuery({
    queryKey: ['staff-thread', sid],
    queryFn: () => api.get(`/staff/surgeries/${sid}/messages`).then(r => r.data),
    refetchInterval: 30_000,
  })

  const { data: templates } = useQuery({
    queryKey: ['message-templates'],
    queryFn: () => api.get('/staff/message-templates').then(r => r.data),
    staleTime: 300_000,
  })

  async function insertTemplate(tid) {
    if (!tid) return
    setPicked(tid)
    const { data } = await api.get(
      `/staff/message-templates/${tid}/render?surgery_id=${sid}`)
    setDraft(data.body)
    setPicked('')
  }

  const send = useMutation({
    mutationFn: (body) =>
      api.post(`/staff/surgeries/${sid}/messages`, { body }).then(r => r.data),
    onSuccess: () => {
      setDraft('')
      qc.invalidateQueries({ queryKey: ['staff-thread', sid] })
      qc.invalidateQueries({ queryKey: ['staff-inbox'] })
    },
  })

  const messages = thread?.messages || []
  return (
    <section id="messages" className="card mt-4">
      <h2 className="text-lg font-semibold mb-3">Messages</h2>
      <div className="max-h-80 overflow-y-auto space-y-2 mb-3">
        {messages.length === 0 && (
          <div className="text-sm text-gray-500 text-center py-4">
            No messages yet.
          </div>
        )}
        {messages.map(m => (
          <div key={m.id} className="text-sm border-l-2 pl-2"
                style={{ borderColor: m.author_kind === 'staff'
                                          ? '#7c3aed' : '#6b7280' }}>
            <div className="text-xs text-gray-500">
              {m.author_kind === 'staff' ? (m.author_email || 'WWC') : 'Patient'}
              {' · '}{m.sent_at?.slice(0, 16).replace('T', ' ')}
            </div>
            <div className="whitespace-pre-wrap">{m.body}</div>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 mb-2">
        <select value={picked}
                  onChange={e => insertTemplate(e.target.value)}
                  className="text-xs rounded border-gray-300">
          <option value="">Insert template…</option>
          {(templates?.templates || []).map(t => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
      </div>
      <textarea value={draft}
                  onChange={e => setDraft(e.target.value)}
                  disabled={send.isPending}
                  rows={3}
                  placeholder="Reply to patient…"
                  className="w-full text-sm rounded border-gray-300" />
      <div className="flex justify-end mt-2">
        <button onClick={() => draft.trim() && send.mutate(draft.trim())}
                 disabled={!draft.trim() || send.isPending}
                 className="btn-primary text-sm">
          {send.isPending ? 'Sending…' : 'Send'}
        </button>
      </div>
    </section>
  )
}
```

- [ ] **Step 3: Render `<MessagesSection sid={s.id} />`** in `SurgeryDetail.jsx`. Insert after the Payments section, before any Notes/footer sections. Match existing import patterns.

- [ ] **Step 4: Build check + commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -6
cd ..
git add frontend/src/components/MessagesSection.jsx frontend/src/pages/SurgeryDetail.jsx
git commit -m "feat(portal-p6): MessagesSection on SurgeryDetail + template dropdown"
```

---

## Task 8: Frontend — staff Message Templates CRUD page

**Files:**
- Create: `frontend/src/pages/StaffMessageTemplates.jsx`
- Modify: `frontend/src/App.jsx` (add `/staff/message-templates` route)
- Modify: staff settings or admin nav (add link to the new page — likely in the same nav identified in T6)

- [ ] **Step 1: CRUD page** at `frontend/src/pages/StaffMessageTemplates.jsx`. Standard list + edit-modal pattern:

```jsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'

export default function StaffMessageTemplates() {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(null)  // null | {id?, name, body}
  const { data } = useQuery({
    queryKey: ['message-templates'],
    queryFn: () => api.get('/staff/message-templates').then(r => r.data),
  })
  const save = useMutation({
    mutationFn: async (t) => {
      if (t.id) return api.put(`/staff/message-templates/${t.id}`,
                                     { name: t.name, body: t.body }).then(r => r.data)
      return api.post('/staff/message-templates',
                          { name: t.name, body: t.body }).then(r => r.data)
    },
    onSuccess: () => {
      setEditing(null)
      qc.invalidateQueries({ queryKey: ['message-templates'] })
    },
  })
  const del = useMutation({
    mutationFn: (id) => api.delete(`/staff/message-templates/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['message-templates'] }),
  })

  const rows = data?.templates || []
  return (
    <div className="p-4 max-w-4xl">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold">Message templates</h1>
        <button onClick={() => setEditing({ name: '', body: '' })}
                 className="btn-primary text-sm">+ New</button>
      </div>
      <div className="bg-white rounded-lg shadow divide-y">
        {rows.map(t => (
          <div key={t.id} className="px-4 py-3 flex items-center justify-between">
            <div>
              <div className="font-medium">{t.name}</div>
              <div className="text-xs text-gray-500 truncate max-w-xl">
                {t.body}
              </div>
            </div>
            <div className="flex gap-2">
              <button className="btn-secondary text-xs"
                       onClick={() => setEditing({ ...t })}>Edit</button>
              <button className="btn-danger text-xs"
                       onClick={() => confirm('Delete this template?')
                                          && del.mutate(t.id)}>Delete</button>
            </div>
          </div>
        ))}
      </div>

      {editing && (
        <div className="fixed inset-0 bg-black/40 flex items-center
                          justify-center z-50">
          <div className="bg-white rounded-lg shadow-lg p-4 max-w-2xl w-full">
            <h2 className="text-lg font-semibold mb-3">
              {editing.id ? 'Edit template' : 'New template'}
            </h2>
            <label className="text-sm font-medium block mt-2">Name</label>
            <input value={editing.name}
                    onChange={e => setEditing({...editing, name: e.target.value})}
                    className="w-full text-sm rounded border-gray-300 mb-3" />
            <label className="text-sm font-medium block">Body</label>
            <textarea value={editing.body}
                       onChange={e => setEditing({...editing, body: e.target.value})}
                       rows={6}
                       className="w-full text-sm rounded border-gray-300" />
            <div className="text-xs text-gray-500 mt-1">
              Supports <code>{'{{patient_name}}'}</code> and
              <code>{'{{surgery_date}}'}</code> substitutions.
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setEditing(null)}
                       className="btn-secondary text-sm">Cancel</button>
              <button onClick={() => save.mutate(editing)}
                       disabled={!editing.name.trim() || !editing.body.trim()
                                    || save.isPending}
                       className="btn-primary text-sm">
                {save.isPending ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Add route + nav link.** In `App.jsx`, register the route. In the nav component, add a "Message templates" link under whatever Settings/Admin grouping makes sense (or near the existing message templates / config links).

- [ ] **Step 3: Build check + commit.**

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project/frontend && npm run build 2>&1 | tail -6
cd ..
git add frontend/src/pages/StaffMessageTemplates.jsx frontend/src/App.jsx \
        frontend/src/<nav-file>.jsx
git commit -m "feat(portal-p6): /staff/message-templates CRUD page"
```

---

## Task 9: Smoke test in prod (manual)

I drive this after T1–T8 are merged + deployed.

- [ ] **Step 1:** Build backend `v48` + frontend `v_portal_p6`. Deploy. Apply migration:

```bash
DATABASE_URL='postgresql+psycopg2://...' \
  ./venv/bin/python scripts/migrate_portal_p6.py
```

- [ ] **Step 2:** Use the existing P5b test phone (`+12405653594`) to sign in as a test patient OR reuse a real surgery (cleaner for end-to-end). For a real surgery, click "View as patient" (#154) to get a portal token quickly without SMS.

- [ ] **Step 3:** Send a message from the patient side. Verify it lands in the Cloud SQL `surgery_messages` table.

- [ ] **Step 4:** Open SurgeryDetail in the staff UI. Verify the MessagesSection shows the new patient message with red highlight + the inbox badge shows count=1.

- [ ] **Step 5:** Send a staff reply using a template (Insert template → modify slightly → Send). Verify:
  - Patient receives the SMS at `+12405653594`
  - Reload the patient view: the staff message appears
  - The inbox count drops back to 0 (staff viewing thread marks patient msgs as read)

- [ ] **Step 6:** Open the portal in "View as patient" mode (#154). Click into Messages. Verify the thread renders BUT the compose box is hidden (`isStaffPreview()` check) and the patient's unread state is NOT cleared by the preview GET.

- [ ] **Step 7:** Visit `/staff/message-templates`. Verify the 5 seeded templates show. Create a 6th, edit it, delete it. Verify everything works.

- [ ] **Step 8:** Cleanup: delete the test messages from `surgery_messages` (if a real surgery was used) or delete the whole test surgery (if seeded).

- [ ] **Step 9:** Mark Task #154 → #160 follow-up Task complete.
