# Manual Mermaid Diagrams — Design Spec

**Date:** 2026-06-28
**Status:** Approved (design), pending implementation plan

## Goal

Replace the ASCII workflow diagram in the in-app operating manual with real, styled
flowcharts rendered by Mermaid, and make Mermaid available to **every** module
manual (it's a shared component). First conversion target: the pellet
`full-workflow` section.

## Background / Current State

- Module manuals render through `frontend/src/components/manual/ModuleManual.jsx`.
  `renderMarkdown(md)` = `marked.parse(md, { breaks: true, gfm: true })` →
  `DOMPurify.sanitize(...)`, injected via `dangerouslySetInnerHTML`.
- There is **no diagram engine**, so the pellet "Full Workflow (Diagram)" section
  (manual slug `full-workflow`, module `pellets`) currently uses ASCII art inside a
  fenced code block — functional but visually poor.
- The component is shared by the LARC and Pellet manual routes (and any future
  module). It has two render sites: the published **Section** view and the editor
  **Preview** pane (both call `renderMarkdown`).
- The frontend bundle is already large (~3.4 MB), so a new dependency must not load
  on routes that don't use it.

## Decision (from brainstorming)

- **Rendering:** lazy dynamic-import of `mermaid`, rendered after the markdown HTML
  is injected. (Chosen over eager import — bundle bloat — and server-side SVG
  pre-render — loses editability.)
- **Visual style:** **semantic colors** — green = main flow, amber = corrections,
  blue = compliance.
- **Structure:** **three focused diagrams** (lifecycle · corrections · compliance),
  each captioned, preceded by a small color key. (Chosen over one combined diagram
  and over a two-diagram split.)

## Architecture

### 1. Mermaid rendering in `ModuleManual.jsx`

- Add `mermaid` as a frontend dependency (lazy-loaded, never imported at module
  top level).
- Keep `renderMarkdown` unchanged. A fenced ` ```mermaid ` block survives `marked`
  + DOMPurify as `<pre><code class="language-mermaid">…</code></pre>` (DOMPurify
  permits `<pre>`/`<code>` and the class attribute).
- New hook `useMermaidRender(ref, deps)`:
  - Runs in a `useEffect` keyed on the rendered body content.
  - If the container has no `code.language-mermaid` node, do nothing (no import).
  - Otherwise `await import('mermaid')`, initialize **once** per page with
    `{ startOnLoad: false, securityLevel: 'strict', flowchart: { curve: 'basis' } }`,
    then for each mermaid block: extract its text, render to SVG with a unique id,
    and replace the `<pre>` node with the SVG.
  - **Fallback:** wrap each render in try/catch; on parse failure, leave the
    original `<pre>` text visible (apply a `whitespace-pre-wrap` class so it wraps)
    rather than showing Mermaid's default error box.
- Apply the hook at **both** render sites — the published Section body and the
  editor Preview — so authors see the diagram live while editing.
- Refs: each rendered body `<div>` gets a `ref`; the hook re-runs when
  `section.body_md` (or the editor's `body`) changes.

### 2. Diagram content (pellet `full-workflow` section)

Rewrite the section `body_md` to:
- A one-line intro sentence.
- A small **color key** line (green = main flow · amber = corrections · blue =
  compliance) as plain text/inline.
- **Three** ` ```mermaid ` blocks, each with a short `####` caption:
  1. **Lifecycle:** `Eligibility → Scheduled → Bag fill → Inserted → Payment →
     Billed → Recall`, with `Recall -. repeat .-> Scheduled`. `classDef flow`
     (green) on all nodes.
  2. **Corrections:** `Billed --Un-bill--> Inserted --Un-insert--> In progress`;
     `Bagged --Un-bag--> In progress`. `classDef fix` (amber).
  3. **Daily compliance loop:** `Start count → Walk shelf/variance → Witness →
     Audit`, `Audit -. next day .-> Start count`, `Disposal --> Audit`.
     `classDef comp` (blue).
- Node labels stay short and contain **no raw HTML** (so `securityLevel: 'strict'`
  is safe); detail lives in the per-step prose sections below.
- **Exact palette (from the approved mockup):**
  - `classDef flow fill:#dcfce7,stroke:#16a34a,color:#14532d;`
  - `classDef fix  fill:#fef3c7,stroke:#d97706,color:#78350f;`
  - `classDef comp fill:#dbeafe,stroke:#2563eb,color:#1e3a8a;`
  - Per-diagram `%%{init: {'theme':'base','themeVariables':{'fontFamily':'ui-sans-serif, system-ui','fontSize':'13px'}}}%%`.
- The Mermaid source itself is the human-readable fallback, so the ASCII version is
  removed (not kept in parallel).

## Security

- Content is staff-authored (manager tier) and trusted. `securityLevel: 'strict'`
  keeps Mermaid from executing click handlers / inline scripts. DOMPurify continues
  to sanitize all non-mermaid HTML. Mermaid injects its own SVG after sanitization;
  acceptable for trusted authors.

## Scope

**In scope:** `ModuleManual.jsx` (rendering hook), `package.json` (mermaid dep),
the pellet `full-workflow` section content (`manual_seed.py`), live prod re-sync of
that section.

**Out of scope:** converting other modules' manuals (they gain the capability but
keep their current content); a markdown "insert diagram" editor helper; offline
bundling of Mermaid (CDN/npm dep is fine).

## Verification

- No frontend test runner exists. Verify by:
  - `npm run build` clean.
  - Local/manual render check of the three diagrams (the visual companion already
    validated the chosen look).
  - Confirm graceful fallback by temporarily feeding a malformed block (manual
    check during implementation).
- Deploy is frontend (renderer) + backend (manual content). The manual content
  re-syncs to prod via the established guarded upsert job (same `full-workflow`
  slug). One hard-refresh for users on the old bundle.

## Risks

- **Bundle size:** mitigated by lazy import (loads only when a mermaid block is
  present).
- **Render timing:** the hook must run after `dangerouslySetInnerHTML` commits;
  keying the effect on body content handles re-renders (edit/save, query refetch).
- **DOMPurify stripping:** verified `<pre><code class="language-…">` survives;
  if a future DOMPurify config tightens, the fallback shows the source text.
