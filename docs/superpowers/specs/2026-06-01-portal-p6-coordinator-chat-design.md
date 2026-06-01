# Patient Portal — P6: Coordinator Chat

**Status:** Draft for review
**Author:** Claude Code, 2026-06-01
**Builds on:** P1–P5, P5b. Reuses portal auth, send_sms, SurgeryDetail page, coordinator preview's read-only middleware.

## Goal

Give patient and coordinator a single text-based conversation per surgery so questions get answered without phone tag. v1 ships:

1. Patient sees a "Messages" page in their portal with full thread + compose
2. Staff sees a top-level "Messages" inbox page listing patients with unread messages + a per-surgery thread inline on `SurgeryDetail`
3. Staff has access to a CRUD-managed library of canned reply templates with `{{patient_name}}` and `{{surgery_date}}` substitutions
4. SMS notifies the patient when staff sends them a message
5. No attachments, no Slack integration, no real-time WebSocket — polling every 30 seconds is good enough for clinic pace

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Patient Portal — Messages                                       │
│                                                                 │
│  GET  /api/patient/portal/{sid}/messages                        │
│        Returns full thread + marks staff messages as read       │
│        (side effect SKIPPED when JWT viewer is staff:* —       │
│        from #154 preview mode)                                  │
│                                                                 │
│  POST /api/patient/portal/{sid}/messages                        │
│        Body: {body: "..."}                                      │
│        Persists, returns the new message row                    │
│        Already blocked for staff preview via #154 middleware    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Staff API                                                       │
│                                                                 │
│  GET  /api/staff/surgeries/{sid}/messages                       │
│        Returns thread + marks patient messages as read          │
│                                                                 │
│  POST /api/staff/surgeries/{sid}/messages                       │
│        Body: {body: "..."}                                      │
│        Persists with author_email=user.email                    │
│        Fires SMS to patient via send_sms                        │
│                                                                 │
│  GET  /api/staff/messages/inbox                                 │
│        Surgeries with author_kind='patient' AND                 │
│        read_by_staff_at IS NULL — newest first                  │
│                                                                 │
│  GET  /api/staff/message-templates                              │
│        List all templates                                       │
│  POST /api/staff/message-templates                              │
│        Create — body, name                                      │
│  PUT  /api/staff/message-templates/{tid}                        │
│        Update — body, name                                      │
│  DELETE /api/staff/message-templates/{tid}                      │
│        Hard delete (templates are non-critical)                 │
│  GET  /api/staff/message-templates/{tid}/render?surgery_id=...  │
│        Returns rendered body with substitutions applied         │
└─────────────────────────────────────────────────────────────────┘
```

## New schema

### `surgery_messages` table

```python
class SurgeryMessage(Base):
    __tablename__ = "surgery_messages"

    id                  = Column(GUID(), primary_key=True, default=new_uuid)
    surgery_id          = Column(GUID(),
                                    ForeignKey("surgeries.id", ondelete="CASCADE"),
                                    nullable=False, index=True)
    author_kind         = Column(String(20), nullable=False)
    # "patient" or "staff"
    author_email        = Column(String(200), nullable=True)
    # NULL when author_kind="patient"; staff email otherwise
    body                = Column(Text, nullable=False)
    sent_at             = Column(DateTime, default=datetime.utcnow,
                                    nullable=False, index=True)
    read_by_patient_at  = Column(DateTime, nullable=True)
    # NULL until the patient opens the thread after this msg was sent.
    # Only meaningful when author_kind="staff".
    read_by_staff_at    = Column(DateTime, nullable=True)
    # NULL until any staff opens the thread after this msg was sent.
    # Only meaningful when author_kind="patient".
```

Index on `(surgery_id, sent_at DESC)` for thread queries. The unread-inbox query benefits from a partial index but Postgres on Cloud SQL handles ~10k rows cheaply without it; defer.

### `message_templates` table

```python
class MessageTemplate(Base):
    __tablename__ = "message_templates"

    id          = Column(GUID(), primary_key=True, default=new_uuid)
    name        = Column(String(120), nullable=False)
    # short label shown in the staff dropdown
    body        = Column(Text, nullable=False)
    # supports {{patient_name}} and {{surgery_date}} substitutions
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow, nullable=False)
```

Templates are global (not per-staff). Any staff can edit any template — this is a tiny clinic and that scales fine. If contention emerges, add a `created_by` column later.

### Seed templates

Migration script inserts 5 starter rows:

| Name | Body |
|---|---|
| Eating/drinking before surgery | "Hi {{patient_name}}, you can have clear liquids up to 2 hours before your surgery on {{surgery_date}}. No solid food after midnight the night before." |
| Consent signing tips | "Hi {{patient_name}}, if you're having trouble with the consent form, please use a recent browser (Chrome/Safari) on a desktop or laptop instead of mobile. If it still won't work, call us at 240-252-2140." |
| FMLA processing timing | "Hi {{patient_name}}, we received your FMLA paperwork. We'll fill it out within 5 business days and post it to your portal." |
| Schedule reminder | "Hi {{patient_name}}, we've cleared your insurance — please log into your portal to pick a surgery date." |
| Post-op check-in | "Hi {{patient_name}}, how are you feeling after your surgery on {{surgery_date}}? Let us know if you have any concerns." |

## Template rendering

The render endpoint takes `surgery_id` and looks up `patient_name` + `scheduled_date`. Substitutions are a simple `.replace()` — no full templating engine. Empty values render as empty strings (no scary `{{patient_name}}` artifact left in the final message).

```python
def render_template(body: str, surgery: Surgery) -> str:
    sd = surgery.scheduled_date
    date_str = sd.strftime("%B %-d, %Y") if sd else ""
    return (body
              .replace("{{patient_name}}", surgery.patient_name or "")
              .replace("{{surgery_date}}", date_str))
```

Rendering happens on demand at the render endpoint, NOT at message creation. This means if a template is edited later, already-sent messages keep their original text (which is correct — they're historical).

## Notifications

### Staff → Patient: SMS

When staff posts a message, `send_sms(patient.cell_phone, body)` fires with:

> WWC has a new message for you. Sign in at https://gw.waldorfwomenscare.com to read it.

Hardcoded URL — keep it short to fit in 160 chars without segmentation. If the URL changes, update the literal.

Soft-fail: if SMS dispatch errors, log and continue. The message is still saved; the patient just won't get a ping. Subsequent polls in their portal will surface it.

### Patient → Staff: badge only

No SMS or email. Two surfaces signal unread:

1. **Inbox page badge** — top-level nav has "Messages (N)" where N = count of surgeries with `read_by_staff_at IS NULL` patient messages. Polled by the inbox query every 60s.
2. **SurgeryDetail badge** — same count but filtered to the specific surgery, shown as a small chip near the patient name header.

## Read-state semantics

| Action | Side effect |
|---|---|
| Patient calls GET /messages | `UPDATE surgery_messages SET read_by_patient_at=NOW() WHERE surgery_id=... AND author_kind='staff' AND read_by_patient_at IS NULL` |
| Patient calls GET /messages with JWT `viewer="staff:*"` | **Skip the update.** Coordinator previewing must not clear the patient's unread state. |
| Staff calls GET /api/staff/surgeries/{sid}/messages | `UPDATE surgery_messages SET read_by_staff_at=NOW() WHERE surgery_id=... AND author_kind='patient' AND read_by_staff_at IS NULL` |
| Patient sends message | Just persists. Stays "unread for staff" until any staff opens the thread. |
| Staff sends message | Persists + SMS dispatched. Stays "unread for patient" until they open. |

## Frontend changes

### Patient portal

Replace `frontend/src/pages/portal/stubs/MessagesStub.jsx` with `frontend/src/pages/portal/Messages.jsx`:

```
┌────────────────────────────────────────────┐
│  Messages                                   │
├────────────────────────────────────────────┤
│  ┌─[oldest]─────────────────────────────┐  │
│  │ WWC · 2026-05-29 at 10:14am          │  │
│  │ Hi Jane, you can have clear liquids… │  │
│  ├──────────────────────────────────────┤  │
│  │ You · 2026-05-29 at 11:02am          │  │
│  │ Thanks! Can I also have coffee?      │  │
│  ├──────────────────────────────────────┤  │
│  │ WWC · 2026-05-29 at 11:45am          │  │
│  │ Yes, black coffee is fine.           │  │
│  └─[newest]─────────────────────────────┘  │
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │ Type a message…                      │  │
│  │                                      │  │
│  └──────────────────────────────────────┘  │
│  [Send]                                    │
└────────────────────────────────────────────┘
```

- Polls every 30s via TanStack Query's `refetchInterval`
- Auto-scrolls to bottom on mount + on new message
- Staff messages show "WWC" as the author label (not their email; we don't want patients knowing individual staff names from the chat)
- Patient messages show "You"
- Compose textarea + Send button. Disabled while pending. After submit, optimistic update + invalidate query

### Staff inbox — new page

`frontend/src/pages/StaffInbox.jsx`, mounted at `/messages`:

```
┌────────────────────────────────────────────┐
│  Messages                                   │
├────────────────────────────────────────────┤
│  Patient                  Last message      │
│  Jane Doe (Chart 1234)    "Can I have…"  ●  │
│  John Smith (Chart 5678)  "OK thanks"    ●  │
└────────────────────────────────────────────┘
```

Rows ordered by most recent unread patient message. Click row → navigate to `/surgery/{sid}#messages` which opens that section in SurgeryDetail. The red dot drops once staff opens the thread.

### Staff per-surgery — section on SurgeryDetail

A new `<MessagesSection sid={s.id} />` component lives near the bottom of SurgeryDetail.jsx (after Payments, before Notes if those exist). The section shows the full thread + compose + template dropdown.

The compose area has:
- A textarea
- A "Insert template…" dropdown that lists templates by name
- When selected, fetches the rendered body for `surgery_id=current` and replaces the textarea content
- Staff can edit before clicking Send

### Top-level nav — new "Messages" item

Add an entry to the staff nav that links to `/messages` and shows a badge with the unread count. The badge is fetched via the inbox endpoint.

### Staff settings — Message Templates CRUD

New page `/staff/message-templates`. Standard list + edit form:

```
┌────────────────────────────────────────────┐
│  Message templates              [+ New]    │
├────────────────────────────────────────────┤
│  Eating/drinking before surgery   [Edit] [Delete]
│  Consent signing tips             [Edit] [Delete]
│  FMLA processing timing           [Edit] [Delete]
│  Schedule reminder                [Edit] [Delete]
│  Post-op check-in                 [Edit] [Delete]
└────────────────────────────────────────────┘
```

Edit form: Name + Body textarea. Body supports `{{patient_name}}` and `{{surgery_date}}` (mentioned in helper text below the textarea).

## Coordinator preview (#154) compatibility

The portal middleware already blocks staff-preview POSTs. The only adjustment for messages: patient's GET endpoint must check the JWT viewer claim and skip the read-mark-as-read side effect when viewer starts with `"staff:"`. This is a single 2-line check in the endpoint, not a middleware change.

```python
if (viewer or "").startswith("staff:"):
    pass  # don't mutate read state during preview
else:
    db.execute(update(SurgeryMessage)...)
```

## What's NOT in P6 (defer)

- **Attachments** (photos, PDFs) — separate flow, can reuse P5 upload infrastructure later
- **Slack pipe** for patient messages — Oliver said no
- **Email notifications** to a clinic inbox — SMS is enough; if staff want batch summaries, can add later
- **Per-patient staff assignment** — Oliver said any staff can reply
- **Triage routing** to billing/clinical/scheduling roles — Oliver said no
- **Real-time WebSocket** — polling 30s is fine for clinic pace
- **Message edit/delete** — chats are append-only; corrections happen by sending a follow-up
- **Read receipts shown to the other side** — the read timestamps are for inbox sorting, not displayed to either side. Less anxiety-inducing for both patient and staff.

## Open questions

1. **What's the portal URL we hardcode in the SMS?** — Plan to use `https://gw.waldorfwomenscare.com`. Confirm before shipping.
2. **Should the staff template editor allow other substitutions** (e.g., `{{surgery_facility}}`)? — Not in v1. Add when actually needed.
3. **Patient sending a message after surgery is completed** — Allowed. Some patients have post-op questions. The thread doesn't lock.

## Risks

- **SMS cost** — At ~$0.01 per outbound SMS, even an active patient could trigger 5-10 SMS over a surgery lifecycle = $0.05–0.10. Negligible at WWC's scale.
- **Patient flooding** — A patient could submit 100 messages in a row. The frontend Send button disables during the request so accidental double-clicks don't double-send. No server-side rate limit in v1; add if it becomes a problem.
- **Staff seeing patient PHI in the inbox page** — The inbox shows patient name + chart number + message preview. Staff already see this on SurgeryDetail; consistent privacy boundary.

## Tech stack

Same as everything else. Two new tables (`surgery_messages`, `message_templates`). Backend endpoints in `app/routers/surgery_messages.py` + `app/routers/portal_messages.py` (or fold the patient ones into the existing `patient_portal.py`). Frontend in patient portal + new staff pages.
