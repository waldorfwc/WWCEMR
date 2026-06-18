# Permanent-Outcome Confirm — Recall Modals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a caller logs a permanent recall outcome (Declined / Do Not Call / Patient deceased / Left practice) in either recall detail modal, show an inline styled confirmation and, on confirm, resend with `confirm_permanent=true`.

**Architecture:** Backend already returns HTTP 409 + supports `confirm_permanent` (the pellet endpoint reuses `recalls.OutcomePayload`), so no backend code changes — only a verification test. Both modals (`Recalls.jsx`, `PelletRecallDetail.jsx`) catch the 409, show an inline amber confirm block in the Log Call Outcome card, and resend the same `{outcome, notes}` with `confirm_permanent: true` on confirm.

**Tech Stack:** React + react-query (frontend); pytest (backend verification only).

**Spec:** `docs/superpowers/specs/2026-06-18-permanent-outcome-confirm-design.md`

**Conventions:** Title Case button labels; amber warning-card styling; backend's 409 `detail` is the message shown. Backend pytest via `./venv/bin/python -m pytest ...` from `backend/`; frontend `npm run build` from `frontend/`.

**Grounding facts (verified):**
- `log_outcome` (`backend/app/routers/recalls.py`) raises `409` with a detail message when `payload.outcome in permanent and not payload.confirm_permanent`. Permanent labels include `"Declined recall"`, `"Do not call"`, `"Patient deceased"`, `"Left practice"`. `OutcomePayload.confirm_permanent: bool = False`.
- The pellet `POST /pellets/recall/{id}/outcome` endpoint takes `payload: OutcomePayload` and delegates to `log_outcome`, so it already relays `confirm_permanent`.
- Pellet modal `frontend/src/pages/PelletRecallDetail.jsx`: `logOutcome` mutation (lines ~115-125) posts `{ outcome, notes }`, `onSuccess` invalidates `['pellet-recall', recallId]` + clears form, `onError` alerts. State `outcome`/`notes`. The Log Call Outcome card (lines ~252-281) has the select (flat string `outcomes`), the notes textarea, and a "Log Outcome" button calling `logOutcome.mutate()`.
- WWE modal `frontend/src/pages/Recalls.jsx`: `submit` mutation (lines ~601-611) posts `{ outcome, notes: notes || null }`, `onSuccess` invalidates `['recalls']`/`['recalls-dash']`/`['recalls', recallId]` + `onClose()`, and has NO `onError`. `isPermanent` is computed (line ~632). The Log Call Outcome card (lines ~708-742) has the select, textarea, an `isPermanent` ⚠ warning (lines ~729-733), and a "Log outcome" button calling `submit.mutate()`.
- The app uses `btn-primary` and `btn-secondary` button classes.

---

## File Structure
- Modify `frontend/src/pages/PelletRecallDetail.jsx` — pellet modal outcome mutation + inline confirm.
- Modify `frontend/src/pages/Recalls.jsx` — WWE modal outcome mutation + inline confirm.
- Modify `backend/tests/test_pellet_recall_router.py` — one verification test. No backend code change.

---

### Task 1: Backend verification test (permanent outcome → confirm)

**Files:**
- Test: `backend/tests/test_pellet_recall_router.py` (append)

This confirms the pellet `/outcome` endpoint already relays `confirm_permanent` to `log_outcome` (no code change — TDD safety net before the frontend work).

- [ ] **Step 1: Append the test**

```python
def test_outcome_permanent_requires_confirm(client, db):
    _due(db, "PERM1")
    client.post("/api/pellets/recall/sync")
    rid = client.get("/api/pellets/recall").json()["items"][0]["id"]
    # A permanent outcome without confirmation is rejected with 409 + a message.
    r = client.post(f"/api/pellets/recall/{rid}/outcome",
                    json={"outcome": "Declined recall"})
    assert r.status_code == 409, r.text
    assert "confirm" in r.json()["detail"].lower() or "permanent" in r.json()["detail"].lower()
    # Resending with confirm_permanent=true applies it.
    r2 = client.post(f"/api/pellets/recall/{rid}/outcome",
                     json={"outcome": "Declined recall", "confirm_permanent": True})
    assert r2.status_code == 200, r2.text
```

- [ ] **Step 2: Run the test**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_pellet_recall_router.py::test_outcome_permanent_requires_confirm -v`
Expected: PASS (the endpoint already supports this). If it FAILS at the 409 step because `"Declined recall"` is not the exact permanent label in the default taxonomy, run a quick check — `GET /api/pellets/recall/{rid}` returns `outcomes`; pick a label whose submission yields 409 (a permanent one) and use it (keep the test's intent: one permanent label, 409 without confirm, 200 with). If it fails because `log_outcome` requires a claim first, add `client.post(f"/api/pellets/recall/{rid}/claim")` before the outcome posts.

- [ ] **Step 3: Commit**

```bash
cd backend && git add tests/test_pellet_recall_router.py
git commit -m "test(pellet-recall): permanent outcome requires confirm_permanent"
```

---

### Task 2: Pellet modal — inline permanent-outcome confirm

**Files:**
- Modify: `frontend/src/pages/PelletRecallDetail.jsx`

- [ ] **Step 1: Read the current code**

Run: `cd frontend && grep -n "const logOutcome\|const \[outcome\|const \[notes\|Log Outcome\|logOutcome.mutate\|setNotes" src/pages/PelletRecallDetail.jsx`
Read the `logOutcome` mutation (~115-125) and the Log Call Outcome card (~252-281) to land the edits precisely.

- [ ] **Step 2: Add a `permanentConfirm` state**

Next to the existing `outcome`/`notes` `useState` declarations, add:

```javascript
  const [permanentConfirm, setPermanentConfirm] = useState(null)
```

- [ ] **Step 3: Update the `logOutcome` mutation**

Replace the `logOutcome` mutation with:

```javascript
  const logOutcome = useMutation({
    mutationFn: (vars) => api.post(`/pellets/recall/${recallId}/outcome`,
      { outcome, notes, confirm_permanent: vars?.confirmPermanent || false }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-recall', recallId] })
      setOutcome('')
      setNotes('')
      setPermanentConfirm(null)
    },
    onError: (err) => {
      if (err?.response?.status === 409) {
        setPermanentConfirm(err.response.data?.detail
          || 'This outcome permanently removes the patient from the recall list.')
      } else {
        alert(err?.response?.data?.detail || 'Failed to log outcome.')
      }
    },
  })
```

- [ ] **Step 4: Add the inline confirm block + reset on outcome change**

In the Log Call Outcome card, change the outcome `<select>`'s `onChange` to also clear a pending confirm when the picked outcome changes:

```jsx
                    onChange={e => { setOutcome(e.target.value); setPermanentConfirm(null) }}
```

Then, between the notes `<textarea>` and the button row (`<div className="flex justify-end">`), insert:

```jsx
                  {permanentConfirm && (
                    <div className="text-[12px] text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 space-y-2">
                      <div>⚠ {permanentConfirm}</div>
                      <div className="flex gap-2 justify-end">
                        <button className="btn-secondary text-xs"
                                onClick={() => setPermanentConfirm(null)}>Cancel</button>
                        <button className="btn-primary text-xs"
                                disabled={logOutcome.isPending}
                                onClick={() => logOutcome.mutate({ confirmPermanent: true })}>
                          {logOutcome.isPending ? 'Removing…' : 'Confirm & Remove'}
                        </button>
                      </div>
                    </div>
                  )}
```

(The existing "Log Outcome" button keeps calling `logOutcome.mutate()` — no args → `confirm_permanent` false → a permanent outcome 409s and reveals this block.)

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: succeeds, no errors referencing `PelletRecallDetail.jsx`.

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/pages/PelletRecallDetail.jsx
git commit -m "feat(pellet-recall): inline confirm for permanent outcomes"
```

---

### Task 3: WWE modal — inline permanent-outcome confirm

**Files:**
- Modify: `frontend/src/pages/Recalls.jsx`

- [ ] **Step 1: Read the current code**

Run: `cd frontend && grep -n "const submit = useMutation\|isPermanent\|Log Call Outcome\|submit.mutate\|const \[notes\|const \[outcome" src/pages/Recalls.jsx`
Read the `submit` mutation (~601-611) and the Log Call Outcome card (~708-742) so the edits land precisely.

- [ ] **Step 2: Add a `permanentConfirm` state**

Next to the existing `outcome`/`notes` `useState` in the detail-drawer component, add:

```javascript
  const [permanentConfirm, setPermanentConfirm] = useState(null)
```

- [ ] **Step 3: Update the `submit` mutation**

Replace the `submit` mutation with:

```javascript
  const submit = useMutation({
    mutationFn: (vars) => api.post(`/recalls/${recallId}/outcome`, {
      outcome, notes: notes || null, confirm_permanent: vars?.confirmPermanent || false,
    }).then(r => r.data),
    onSuccess: () => {
      setPermanentConfirm(null)
      qc.invalidateQueries({ queryKey: ['recalls'] })
      qc.invalidateQueries({ queryKey: ['recalls-dash'] })
      qc.invalidateQueries({ queryKey: ['recalls', recallId] })
      onClose()
    },
    onError: (err) => {
      if (err?.response?.status === 409) {
        setPermanentConfirm(err.response.data?.detail
          || 'This outcome permanently suppresses this patient.')
      } else {
        alert(err?.response?.data?.detail || 'Failed to log outcome.')
      }
    },
  })
```

- [ ] **Step 4: Add the inline confirm block + reset on outcome change**

In the Log Call Outcome card, change the outcome `<select>`'s `onChange` to clear a pending confirm:

```jsx
                        onChange={e => { setOutcome(e.target.value); setPermanentConfirm(null) }}
```

Then, immediately AFTER the existing `isPermanent` warning block (the `{isPermanent && (<div ...>⚠ This outcome will permanently suppress...</div>)}` at ~729-733) and BEFORE the button row (`<div className="flex justify-end">` at ~734), insert:

```jsx
                {permanentConfirm && (
                  <div className="text-[12px] text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 space-y-2">
                    <div>⚠ {permanentConfirm}</div>
                    <div className="flex gap-2 justify-end">
                      <button className="btn-secondary text-xs"
                              onClick={() => setPermanentConfirm(null)}>Cancel</button>
                      <button className="btn-primary text-xs"
                              disabled={submit.isPending}
                              onClick={() => submit.mutate({ confirmPermanent: true })}>
                        {submit.isPending ? 'Removing…' : 'Confirm & Remove'}
                      </button>
                    </div>
                  </div>
                )}
```

(The existing "Log outcome" button keeps calling `submit.mutate()` — no args → `confirm_permanent` false → a permanent outcome now surfaces this confirm instead of failing silently.)

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: succeeds, no errors referencing `Recalls.jsx`.

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/pages/Recalls.jsx
git commit -m "feat(recalls): inline confirm for permanent outcomes (was failing silently)"
```

---

## Final Verification (after all tasks)
- [ ] `cd backend && ./venv/bin/python -m pytest tests/test_pellet_recall_router.py -q` → all PASS.
- [ ] `cd frontend && npm run build` → clean.
- [ ] No new failures: `cd backend && ./venv/bin/python -m pytest tests/ -k "recall" -q`.

## Notes for the implementer
- **No backend code change.** The 409 + `confirm_permanent` already exist; Task 1 only proves it for the pellet endpoint.
- **Same pattern, two modals**, intentionally not extracted into a shared component (their mutations + surrounding markup differ). Each holds its own `permanentConfirm` state.
- The normal submit button is unchanged (calls `mutate()` with no args). The 409 reveals the inline confirm; "Confirm & Remove" calls `mutate({ confirmPermanent: true })`.
- Clearing `permanentConfirm` when the outcome selection changes prevents a stale confirm for a different outcome.
