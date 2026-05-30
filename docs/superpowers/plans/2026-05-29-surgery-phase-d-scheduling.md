# Surgery Phase D — Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish patient self-scheduling (pre-highlight earliest slot) and give the coordinator override capability — pick a slot for the patient, override the allotted duration, adjust duration on an existing booked slot.

**Architecture:** Three new endpoints (patient slot select, coordinator schedule, slot duration patch). One new modal on `SurgeryDetail`. Procedure template lookups feed default durations.

**Tech Stack:** FastAPI + SQLAlchemy, pytest with sqlite-in-memory, React + TanStack Query.

**Depends on:** Phase B (procedure templates) for default durations. If B isn't done yet, fall back to the hardcoded `procedure_kind` → minutes map.

---

## File Structure

- **Create:** `backend/tests/test_patient_select_slot.py`
- **Create:** `backend/tests/test_coordinator_schedule.py`
- **Create:** `backend/tests/test_slot_duration_patch.py`
- **Modify:** `backend/app/routers/patient_surgery.py` — new `/select-slot` endpoint.
- **Modify:** `backend/app/routers/surgery.py` — new `/schedule` endpoint, new `/slots/{slot_id}` patch.
- **Modify:** `backend/app/services/surgery_date_picker.py` — read default duration from procedure template if available.
- **Modify:** `frontend/src/pages/PatientSurgery.jsx` — pre-highlight earliest slot, "Confirm this time" CTA.
- **Modify:** `frontend/src/pages/SurgeryDetail.jsx` — "Schedule for patient" modal + slot duration inline edit.

---

## Section 1 — Backend: patient self-scheduling

### Task D1: `POST /api/p/surgery/:id/select-slot`

**Files:**
- Modify: `backend/app/routers/patient_surgery.py`
- Create: `backend/tests/test_patient_select_slot.py`

- [ ] **Step 1: Write the failing test**

```python
"""Patient slot-select endpoint coverage (Phase D)."""
from datetime import date, time, timedelta

from app.models.surgery import (
    Surgery, BlockDay, SurgerySlot, SurgeryNote,
)


def _seed(db):
    s = Surgery(
        chart_number="1", patient_name="Pat",
        eligible_facilities=["medstar"], selected_facility="medstar",
        status="in_progress",
        procedures=[{"name": "Hysterectomy", "kind": "robotic_180"}],
    )
    db.add(s); db.flush()
    bd = BlockDay(
        facility="medstar",
        block_date=date.today() + timedelta(days=14),
        block_kind="robotic_180",
        start_time=time(7, 30), end_time=time(15, 0),
    )
    db.add(bd); db.flush()
    return s, bd


def test_select_slot_books_with_template_duration(client, db):
    s, bd = _seed(db)
    # Token-gated endpoint — the test client overrides auth.
    resp = client.post(f"/api/p/surgery/{s.id}/select-slot", json={
        "block_day_id": str(bd.id),
        "start_time": "07:30",
    })
    assert resp.status_code == 200, resp.text
    slot = db.query(SurgerySlot).filter_by(surgery_id=s.id).first()
    assert slot is not None
    assert slot.start_time == time(7, 30)
    assert slot.duration_minutes in (180, 240)  # robotic baseline


def test_select_slot_rejects_busy_time(client, db):
    s, bd = _seed(db)
    # Pre-existing slot at the requested time.
    db.add(SurgerySlot(block_day_id=bd.id, start_time=time(7, 30),
                        duration_minutes=180, procedure_kind="robotic_180"))
    db.commit()

    resp = client.post(f"/api/p/surgery/{s.id}/select-slot", json={
        "block_day_id": str(bd.id), "start_time": "07:30",
    })
    assert resp.status_code == 409


def test_select_slot_writes_audit_note(client, db):
    s, bd = _seed(db)
    client.post(f"/api/p/surgery/{s.id}/select-slot", json={
        "block_day_id": str(bd.id), "start_time": "07:30",
    })
    note = (db.query(SurgeryNote)
              .filter(SurgeryNote.surgery_id == s.id,
                       SurgeryNote.kind == "slot_scheduled").first())
    assert note is not None
```

Run:

```bash
cd backend && ./venv/bin/pytest tests/test_patient_select_slot.py -v
```

Expected: 3 FAILs (endpoint not implemented).

- [ ] **Step 2: Implement the endpoint**

In `backend/app/routers/patient_surgery.py`, add:

```python
from datetime import time as _time
from pydantic import BaseModel
from app.models.surgery import SurgerySlot, BlockDay, SurgeryNote
from app.models.surgery_config import SurgeryProcedureTemplate


class SelectSlotIn(BaseModel):
    block_day_id: str
    start_time: str          # "HH:MM"


def _parse_hhmm(s: str) -> _time:
    h, m = s.split(":")
    return _time(int(h), int(m))


def _default_duration_for(db, surgery, block_day) -> int:
    """Look up procedure-template duration; fall back to procedure_kind map."""
    kind = block_day.block_kind
    template = (db.query(SurgeryProcedureTemplate)
                  .filter(SurgeryProcedureTemplate.procedure_kind == kind,
                           SurgeryProcedureTemplate.is_active.is_(True))
                  .order_by(SurgeryProcedureTemplate.name.asc())
                  .first())
    if template:
        return template.default_duration_minutes
    fallback = {"office": 30, "minor": 60, "major": 120,
                 "robotic_180": 180, "robotic_240": 240}
    return fallback.get(kind, 60)


@router.post("/{surgery_id}/select-slot")
def patient_select_slot(
    surgery_id: str,
    payload: SelectSlotIn,
    db: Session = Depends(get_db),
):
    # NB: the existing module-level token check (see /pick) gates this. Replicate
    # the same auth pattern from /pick — extract the bearer-token check into a
    # helper if not already.
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")

    bd = db.query(BlockDay).filter(BlockDay.id == payload.block_day_id).first()
    if not bd:
        raise HTTPException(status_code=404, detail="block day not found")

    start = _parse_hhmm(payload.start_time)
    if any(slot.start_time == start for slot in (bd.slots or [])):
        raise HTTPException(status_code=409, detail="that start time is already booked")

    duration = _default_duration_for(db, s, bd)
    slot = SurgerySlot(
        block_day_id=bd.id, surgery_id=s.id,
        start_time=start, duration_minutes=duration,
        procedure_kind=bd.block_kind,
    )
    db.add(slot)
    s.scheduled_date = bd.block_date
    s.selected_facility = bd.facility
    db.add(SurgeryNote(
        surgery_id=s.id, created_by="patient:self-service",
        kind="slot_scheduled",
        body=f"Patient self-scheduled {bd.block_date} {start.strftime('%H:%M')} "
              f"({duration} min) at {bd.facility}.",
    ))
    db.commit()
    return {
        "ok": True, "slot_id": str(slot.id),
        "block_day_id": str(bd.id), "start_time": start.strftime("%H:%M"),
        "duration_minutes": duration,
    }
```

- [ ] **Step 3: Re-run tests**

```bash
cd backend && ./venv/bin/pytest tests/test_patient_select_slot.py -v
```

Expected: 3 PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/patient_surgery.py backend/tests/test_patient_select_slot.py
git commit -m "feat(surgery): patient slot-select endpoint with template-driven duration"
```

---

## Section 2 — Backend: coordinator schedule

### Task D2: `POST /api/surgery/:id/schedule`

**Files:**
- Modify: `backend/app/routers/surgery.py`
- Create: `backend/tests/test_coordinator_schedule.py`

- [ ] **Step 1: Write the failing test**

```python
"""Coordinator schedule-for-patient endpoint (Phase D)."""
from datetime import date, time, timedelta

from app.models.surgery import Surgery, BlockDay, SurgerySlot, SurgeryNote


def _seed(db):
    s = Surgery(chart_number="1", patient_name="Pat",
                 eligible_facilities=["medstar"], selected_facility="medstar",
                 status="in_progress",
                 procedures=[{"name": "Hyst", "kind": "robotic_180"}])
    db.add(s); db.flush()
    bd = BlockDay(facility="medstar",
                   block_date=date.today() + timedelta(days=14),
                   block_kind="robotic_180",
                   start_time=time(7, 0), end_time=time(17, 0))
    db.add(bd); db.flush()
    return s, bd


def test_coordinator_schedule_default_duration(client, db):
    s, bd = _seed(db)
    resp = client.post(f"/api/surgery/{s.id}/schedule", json={
        "block_day_id": str(bd.id), "start_time": "08:00",
    })
    assert resp.status_code == 200, resp.text
    slot = db.query(SurgerySlot).filter_by(surgery_id=s.id).first()
    assert slot.duration_minutes in (180, 240)


def test_coordinator_override_requires_reason_above_10pct(client, db):
    s, bd = _seed(db)
    # 180 min default; 220 is >10% above => reason required.
    resp = client.post(f"/api/surgery/{s.id}/schedule", json={
        "block_day_id": str(bd.id), "start_time": "08:00",
        "duration_minutes": 220,
    })
    assert resp.status_code == 422

    resp = client.post(f"/api/surgery/{s.id}/schedule", json={
        "block_day_id": str(bd.id), "start_time": "08:00",
        "duration_minutes": 220,
        "override_reason": "Extra complexity",
    })
    assert resp.status_code == 200


def test_coordinator_schedule_writes_note(client, db):
    s, bd = _seed(db)
    client.post(f"/api/surgery/{s.id}/schedule", json={
        "block_day_id": str(bd.id), "start_time": "08:00",
    })
    n = (db.query(SurgeryNote)
           .filter(SurgeryNote.surgery_id == s.id,
                    SurgeryNote.kind == "slot_scheduled_by_coordinator").first())
    assert n is not None
```

- [ ] **Step 2: Implement the endpoint**

In `backend/app/routers/surgery.py`:

```python
from app.routers.patient_surgery import _parse_hhmm, _default_duration_for


class CoordinatorScheduleIn(BaseModel):
    block_day_id: str
    start_time: str
    duration_minutes: Optional[int] = None
    override_reason: Optional[str] = None


@router.post("/{surgery_id}/schedule")
def coordinator_schedule(
    surgery_id: str,
    payload: CoordinatorScheduleIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("claim:edit")),
):
    from app.models.surgery import SurgeryNote
    s = db.query(Surgery).filter(Surgery.id == surgery_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="surgery not found")
    bd = db.query(BlockDay).filter(BlockDay.id == payload.block_day_id).first()
    if not bd:
        raise HTTPException(status_code=404, detail="block day not found")
    start = _parse_hhmm(payload.start_time)
    if any(slot.start_time == start for slot in (bd.slots or [])):
        raise HTTPException(status_code=409, detail="that start time is already booked")

    default = _default_duration_for(db, s, bd)
    duration = payload.duration_minutes or default
    # If >10% off the template default, require an override reason.
    threshold = default * 0.10
    if abs(duration - default) > threshold and not (payload.override_reason or "").strip():
        raise HTTPException(status_code=422,
                            detail="override_reason required: duration differs >10% from template default")

    actor = current_user.get("email") or "system"
    slot = SurgerySlot(
        block_day_id=bd.id, surgery_id=s.id,
        start_time=start, duration_minutes=duration,
        procedure_kind=bd.block_kind,
    )
    db.add(slot)
    s.scheduled_date = bd.block_date
    s.selected_facility = bd.facility
    db.add(SurgeryNote(
        surgery_id=s.id, created_by=actor,
        kind="slot_scheduled_by_coordinator",
        body=(f"Coordinator scheduled {bd.block_date} {start.strftime('%H:%M')} "
              f"({duration} min, template default {default} min) at {bd.facility}."
              + (f" Override reason: {payload.override_reason}" if payload.override_reason else "")),
    ))
    db.commit()
    return {"ok": True, "slot_id": str(slot.id),
            "start_time": start.strftime("%H:%M"),
            "duration_minutes": duration,
            "template_default": default}
```

- [ ] **Step 3: Run tests**

```bash
cd backend && ./venv/bin/pytest tests/test_coordinator_schedule.py -v
```

Expected: 3 PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/surgery.py backend/tests/test_coordinator_schedule.py
git commit -m "feat(surgery): coordinator schedule endpoint with override-reason gate"
```

---

## Section 3 — Backend: slot duration patch

### Task D3: `PATCH /api/surgery/slots/:slot_id`

**Files:**
- Modify: `backend/app/routers/surgery.py`
- Create: `backend/tests/test_slot_duration_patch.py`

- [ ] **Step 1: Write the failing test**

```python
"""Slot duration patch endpoint (Phase D)."""
from datetime import date, time, timedelta

from app.models.surgery import Surgery, BlockDay, SurgerySlot, SurgeryNote


def _seed_with_slot(db, dur=180):
    s = Surgery(chart_number="1", patient_name="Pat",
                 eligible_facilities=["medstar"], selected_facility="medstar",
                 status="confirmed",
                 procedures=[{"name": "Hyst", "kind": "robotic_180"}])
    db.add(s); db.flush()
    bd = BlockDay(facility="medstar",
                   block_date=date.today() + timedelta(days=14),
                   block_kind="robotic_180",
                   start_time=time(7, 0), end_time=time(17, 0))
    db.add(bd); db.flush()
    slot = SurgerySlot(block_day_id=bd.id, surgery_id=s.id,
                        start_time=time(8, 0), duration_minutes=dur,
                        procedure_kind="robotic_180")
    db.add(slot); db.commit()
    return s, slot


def test_patch_slot_duration(client, db):
    s, slot = _seed_with_slot(db)
    resp = client.patch(f"/api/surgery/slots/{slot.id}", json={
        "duration_minutes": 210,
        "override_reason": "Extended OR time approved",
    })
    assert resp.status_code == 200, resp.text
    db.refresh(slot)
    assert slot.duration_minutes == 210


def test_patch_slot_requires_reason(client, db):
    s, slot = _seed_with_slot(db)
    resp = client.patch(f"/api/surgery/slots/{slot.id}", json={
        "duration_minutes": 210,
    })
    assert resp.status_code == 422


def test_patch_slot_writes_note(client, db):
    s, slot = _seed_with_slot(db)
    client.patch(f"/api/surgery/slots/{slot.id}", json={
        "duration_minutes": 210, "override_reason": "Extra time"})
    n = (db.query(SurgeryNote)
           .filter(SurgeryNote.surgery_id == s.id,
                    SurgeryNote.kind == "slot_duration_changed").first())
    assert n is not None
    assert "180" in (n.body or "")
    assert "210" in (n.body or "")
```

- [ ] **Step 2: Implement**

In `backend/app/routers/surgery.py`:

```python
class SlotPatch(BaseModel):
    duration_minutes: int
    override_reason: Optional[str] = None


@router.patch("/slots/{slot_id}")
def patch_slot(
    slot_id: str,
    payload: SlotPatch,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("claim:edit")),
):
    from app.models.surgery import SurgeryNote
    slot = db.query(SurgerySlot).filter(SurgerySlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="slot not found")

    new_dur = payload.duration_minutes
    if new_dur <= 0:
        raise HTTPException(status_code=422, detail="duration must be > 0")
    if not (payload.override_reason or "").strip():
        raise HTTPException(status_code=422, detail="override_reason required")

    actor = current_user.get("email") or "system"
    old = slot.duration_minutes
    slot.duration_minutes = new_dur

    if slot.surgery_id:
        db.add(SurgeryNote(
            surgery_id=slot.surgery_id, created_by=actor,
            kind="slot_duration_changed",
            body=(f"Duration {old} → {new_dur} min. "
                  f"Reason: {payload.override_reason}"),
        ))
    db.commit()
    return {"ok": True, "slot_id": str(slot.id),
            "duration_minutes": slot.duration_minutes}
```

- [ ] **Step 3: Run tests**

```bash
cd backend && ./venv/bin/pytest tests/test_slot_duration_patch.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/surgery.py backend/tests/test_slot_duration_patch.py
git commit -m "feat(surgery): slot duration patch with required override_reason"
```

---

## Section 4 — Frontend: patient flow

### Task D4: Pre-highlight earliest slot on `PatientSurgery`

**Files:**
- Modify: `frontend/src/pages/PatientSurgery.jsx`

- [ ] **Step 1: Locate the slot-list render**

Open `frontend/src/pages/PatientSurgery.jsx`. Find the component that lists available slots after the patient picks a date (currently renders multiple slot buttons or block-day rows).

- [ ] **Step 2: Identify the earliest slot and pre-select it**

In that component:

```jsx
  const [selected, setSelected] = useState(null)

  // Earliest = first slot of the first block day in chronological order
  useEffect(() => {
    if (selected) return
    if (!blockDays?.length) return
    const earliest = (() => {
      for (const bd of blockDays) {
        for (const slot of (bd.available_slots || [])) {
          return { block_day_id: bd.id, start_time: slot.start_time }
        }
      }
      return null
    })()
    if (earliest) setSelected(earliest)
  }, [blockDays, selected])
```

- [ ] **Step 3: Decorate the earliest slot + the Confirm CTA**

Where each slot button renders:

```jsx
<button onClick={() => setSelected({ block_day_id: bd.id, start_time: slot.start_time })}
        className={`...existing classes... ${
          selected?.block_day_id === bd.id && selected?.start_time === slot.start_time
            ? 'border-plum-700 bg-plum-50'
            : 'border-gray-200 hover:border-plum-400'
        }`}>
  {slot.start_time}
  {/* badge only on the very first slot */}
  {bd === blockDays[0] && slot === bd.available_slots[0] && (
    <span className="ml-2 text-[10px] text-plum-700 font-semibold">Recommended</span>
  )}
</button>
```

And below the slot list, add the prominent Confirm button:

```jsx
{selected && (
  <button className="btn-primary w-full text-base py-3 mt-4"
          onClick={() => confirm.mutate(selected)}
          disabled={confirm.isPending}>
    {confirm.isPending ? 'Booking…' : `Confirm this time (${selected.start_time})`}
  </button>
)}
```

Where `confirm` is:

```jsx
const confirm = useMutation({
  mutationFn: ({ block_day_id, start_time }) =>
    publicApi.post(`/p/surgery/${id}/select-slot`,
                    { block_day_id, start_time },
                    { headers: { Authorization: `Bearer ${token}` } })
             .then(r => r.data),
  onSuccess: () => { /* refetch status, route to confirmation, etc. */ },
  onError: (e) => alert(e?.response?.data?.detail || 'Booking failed'),
})
```

- [ ] **Step 4: Verify build**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/PatientSurgery.jsx
git commit -m "feat(surgery): patient flow pre-highlights earliest slot + Confirm CTA"
```

---

## Section 5 — Frontend: coordinator modal + slot adjustment

### Task D5: "Schedule for patient" modal on `SurgeryDetail`

**Files:**
- Modify: `frontend/src/pages/SurgeryDetail.jsx`

- [ ] **Step 1: Add the modal component**

At the bottom of `SurgeryDetail.jsx`:

```jsx
function ScheduleForPatientModal({ surgery, templates, onClose, onSaved }) {
  const [selected, setSelected] = useState(null)  // {block_day_id, start_time}
  const [duration, setDuration] = useState(null)
  const [overrideReason, setOverrideReason] = useState('')

  const { data } = useQuery({
    queryKey: ['surgery-block-days-for-schedule', surgery.id],
    queryFn: () => api.get('/surgery/admin/block-days?days=60', {
      params: { facility: surgery.selected_facility },
    }).then(r => r.data),
  })

  // Default duration = matching template's default, or fall back to procedure_kind map
  const templateDefault = useMemo(() => {
    const kind = selected ? data?.block_days?.find(b => b.id === selected.block_day_id)?.block_kind : null
    const t = templates.find(t => t.procedure_kind === kind && t.is_active)
    if (t) return t.default_duration_minutes
    return ({office: 30, minor: 60, major: 120, robotic_180: 180, robotic_240: 240}[kind] || 60)
  }, [selected, data, templates])

  useEffect(() => { setDuration(templateDefault) }, [templateDefault])

  const schedule = useMutation({
    mutationFn: () =>
      api.post(`/surgery/${surgery.id}/schedule`, {
        block_day_id: selected.block_day_id,
        start_time:   selected.start_time,
        duration_minutes: duration,
        override_reason: overrideReason.trim() || undefined,
      }).then(r => r.data),
    onSuccess: () => { onSaved(); onClose() },
    onError: (e) => alert(e?.response?.data?.detail || 'Schedule failed'),
  })

  const durationOff = Math.abs(duration - templateDefault) > templateDefault * 0.10
  const needsReason = durationOff && !overrideReason.trim()

  return (
    <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center" onClick={onClose}>
      <div className="bg-white rounded-lg w-full max-w-2xl p-6" onClick={e => e.stopPropagation()}>
        <h2 className="text-lg font-semibold mb-3">Schedule for patient</h2>
        <div className="max-h-96 overflow-y-auto space-y-2 mb-3">
          {(data?.block_days || []).map(bd => (
            <div key={bd.id} className="border border-border-subtle rounded p-2">
              <div className="text-sm font-medium">{bd.block_date} · {bd.facility}</div>
              <div className="flex flex-wrap gap-1 mt-1">
                {bd.available_starts?.map(t => (
                  <button key={t}
                          onClick={() => setSelected({ block_day_id: bd.id, start_time: t })}
                          className={`text-[12px] px-2 py-1 rounded border ${
                            selected?.block_day_id === bd.id && selected?.start_time === t
                              ? 'border-plum-700 bg-plum-50' : 'border-gray-200 hover:bg-plum-50'
                          }`}>
                    {t}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
        {selected && (
          <div className="space-y-2 border-t border-border-subtle pt-3">
            <div className="flex items-center gap-2">
              <label className="text-[11px] uppercase text-gray-500 w-32">Duration (min)</label>
              <input type="number" className="input text-sm w-24"
                     value={duration ?? ''}
                     onChange={e => setDuration(Number(e.target.value))} />
              <span className="text-[11px] text-gray-400">(template default: {templateDefault})</span>
            </div>
            {durationOff && (
              <div className="flex items-center gap-2">
                <label className="text-[11px] uppercase text-gray-500 w-32">Override reason</label>
                <input className="input text-sm flex-1"
                       value={overrideReason}
                       onChange={e => setOverrideReason(e.target.value)}
                       placeholder="Required: duration ≠ template default by >10%" />
              </div>
            )}
            <div className="flex items-center gap-2 pt-2">
              <button className="btn-primary text-sm"
                      disabled={needsReason || schedule.isPending}
                      onClick={() => schedule.mutate()}>
                {schedule.isPending ? 'Scheduling…' : 'Confirm schedule'}
              </button>
              <button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Wire up the trigger button on `SurgeryDetail`**

In the main `SurgeryDetail` component:

```jsx
const [showSchedule, setShowSchedule] = useState(false)
const { data: tpl } = useQuery({
  queryKey: ['surgery-templates'],
  queryFn: () => api.get('/surgery/picklists/procedure-templates').then(r => r.data.templates),
  staleTime: 60_000,
})

// Above the existing detail body:
{!s.scheduled_date && (
  <button className="btn-primary text-sm" onClick={() => setShowSchedule(true)}>
    Schedule for patient
  </button>
)}

{showSchedule && (
  <ScheduleForPatientModal
    surgery={s}
    templates={tpl || []}
    onClose={() => setShowSchedule(false)}
    onSaved={() => qc.invalidateQueries({ queryKey: ['surgery', s.id] })}
  />
)}
```

- [ ] **Step 3: Verify build**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/SurgeryDetail.jsx
git commit -m "feat(surgery): coordinator Schedule-for-patient modal with duration override"
```

---

### Task D6: Slot duration inline edit on the booked slot

**Files:**
- Modify: `frontend/src/pages/SurgeryDetail.jsx`

- [ ] **Step 1: Find the booked-slot display**

In `SurgeryDetail.jsx`, find where the booked slot's date/time/duration is rendered (search for `scheduled_date` or `duration_minutes`).

- [ ] **Step 2: Add an inline "Adjust duration" affordance**

```jsx
function SlotDurationEdit({ slotId, currentMinutes, onSaved }) {
  const [editing, setEditing] = useState(false)
  const [draftMin, setDraftMin] = useState(currentMinutes)
  const [reason, setReason] = useState('')
  const save = useMutation({
    mutationFn: () => api.patch(`/surgery/slots/${slotId}`, {
      duration_minutes: draftMin,
      override_reason: reason.trim(),
    }).then(r => r.data),
    onSuccess: () => { setEditing(false); onSaved() },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  if (!editing) {
    return (
      <button className="text-[11px] text-plum-700 hover:underline"
              onClick={() => setEditing(true)}>
        Adjust duration ({currentMinutes}m)
      </button>
    )
  }
  return (
    <div className="flex items-center gap-1 text-[11px]">
      <input type="number" className="input text-[11px] w-16"
             value={draftMin} onChange={e => setDraftMin(Number(e.target.value))} />
      <input className="input text-[11px] flex-1"
             placeholder="Reason (required)"
             value={reason} onChange={e => setReason(e.target.value)} />
      <button className="text-plum-700 hover:underline"
              disabled={!reason.trim() || save.isPending}
              onClick={() => save.mutate()}>Save</button>
      <button className="text-gray-500 hover:underline"
              onClick={() => { setEditing(false); setReason(''); setDraftMin(currentMinutes) }}>
        Cancel
      </button>
    </div>
  )
}
```

Render it next to the existing duration display, gated on `isAdmin || isBilling || claim:edit`:

```jsx
<div className="text-[12px] text-gray-700">
  {s.scheduled_date} · {slot.start_time} · {slot.duration_minutes} min
  {canEdit && (
    <span className="ml-2">
      <SlotDurationEdit slotId={slot.id}
                         currentMinutes={slot.duration_minutes}
                         onSaved={() => qc.invalidateQueries({ queryKey: ['surgery', s.id] })} />
    </span>
  )}
</div>
```

- [ ] **Step 3: Verify build + commit**

```bash
cd frontend && npm run build 2>&1 | tail -5
git add frontend/src/pages/SurgeryDetail.jsx
git commit -m "feat(surgery): slot duration inline edit on SurgeryDetail"
```

---

## Section 6 — Verification

### Task D7: Deploy + smoke test

- [ ] **Step 1: Deploy backend + frontend**

```bash
cd backend && gcloud builds submit . --project=wwc-solutions --region=us-east4 \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v26
gcloud run deploy backend --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:v26 \
  --region=us-east4 --project=wwc-solutions
cd ../frontend && gcloud builds submit . --project=wwc-solutions --region=us-east4 \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:v24
gcloud run deploy frontend --image=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:v24 \
  --region=us-east4 --project=wwc-solutions
```

- [ ] **Step 2: Smoke test the coordinator flow**

1. Open a surgery in `in_progress` status with no scheduled_date at `/surgery/:id`.
2. Click **Schedule for patient** → modal opens with block days for the patient's facility.
3. Pick a slot → duration prefilled with the template default.
4. Change the duration by >10% — verify the override-reason input appears and the Confirm button is disabled until reason is filled.
5. Save → surgery now shows scheduled.

- [ ] **Step 3: Smoke test the patient flow**

1. Pull a patient-link from `/surgery/:id` (Klara link); open in incognito.
2. Authenticate as the patient, navigate to the slot picker.
3. The earliest slot should show a "Recommended" badge and be pre-highlighted; the **Confirm this time** button at the bottom is enabled by default.
4. Pick a different slot — verify highlight + button text update.
5. Confirm — verify the slot is booked with the template's default duration.

- [ ] **Step 4: Smoke test the duration edit**

1. On a surgery with a booked slot, click **Adjust duration (XXm)** next to the slot.
2. Type a new duration, type a reason, click Save.
3. Verify the duration updates and a `slot_duration_changed` note appears in the audit list.

- [ ] **Step 5: Push**

```bash
git push origin main
```
