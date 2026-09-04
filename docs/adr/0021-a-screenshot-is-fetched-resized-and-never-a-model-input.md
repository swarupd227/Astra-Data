# ADR 0021 — A screenshot is fetched, resized, and never a model input

Status: accepted · 3 September 2026 · Story S2.4.2 (E2 / F2.4)

## Context

S2.4.2 asks for a screenshot of the source view for each sheet, *"so that I can compare what
I see today with what I will see."* Two criteria: `capture_visual` returns a PNG per sheet and
per dashboard at a configurable size using the REST image endpoint, and the images are stored
in the artefact store, linked to the MU — never sent to a model endpoint.

The comparison this serves is §10.6's, and it is explicitly advisory: a screenshot of the
source and a rendered target visual are compared *perceptually*, the score sits next to the
structural one, and neither gates G3. What that comparison needs from this story is an image
that is real (Tableau's own render, not an invention) and comparable (the same size as
whatever the target side produces).

## Decisions

### 1. Screenshot needs nothing this deployment might lack

Unlike extract read and live replay (S2.4.1), REST `queryViewImage` is a call this adapter
already knows how to make — no Hyper API, no warehouse credential. `screenshot` is therefore
claimed unconditionally in `BASE_CAPABILITIES` rather than computed from a port, and the golden
corpus carries two visual cases (a sheet and a dashboard) so the conformance suite's "visual
capture" check runs rather than skips on every CI run — the same reasoning S2.4.1 applied to
the determinism check: a claim that never gets exercised is a check that cannot fail.

### 2. A sheet and a dashboard are both "views" — one lookup for both

Tableau's views listing does not distinguish a worksheet from a dashboard; both are published
views with an id and a name. `views.py` extracts the lookup S2.4.1 wrote for `ParityCase.sheet`
into `resolve_view_id`, shared by `execution.py` and the new `visual.py`, so "not a published
view" means the same thing and is worded the same way for a parity case and a visual case
alike. The golden deployment's `list_views` now returns the workbook's "Overview" dashboard
alongside its sheets, so the shared path is exercised by both stories.

### 3. Tableau's image endpoint has no caller-chosen size — so this adapter resizes what it gets

`queryViewImage` renders at whatever size the sheet or dashboard is laid out at, with only a
DPI choice (`resolution=high`). S2.4.2's own criterion is "at a configurable size", and §10.6
needs the two images it compares to be the same size for a perceptual score to mean anything.

So `visual.py` fetches the render and resizes it to exactly `case.width` x `case.height` with
Pillow (`Image.Resampling.LANCZOS`) before returning it — never cropped, never padded to hide
a mismatch, a genuine re-encode of what Tableau actually sent. The golden deployment's own
render is a fixed 960x720 regardless of what is asked for, specifically so a test asking for
the default 1200x800 only gets it because the adapter changed it — proving the resize path
runs rather than coincidentally matching.

Pillow (HPND-licensed, open source) is the one new dependency this story adds, to
`adapter-tableau` only — decoding and re-encoding a raster image is not a reasonable thing to
hand-roll, the same judgement this codebase already made for SQL (`sqlglot`) and the
calculation grammar (`lark`).

### 4. The artefact store is a table, "for now" — the same answer as provenance and conformance

§5.2 gives object storage and content addressing to `artefact-svc`, which does not exist. This
is the third time that gap has been reached: S1.3.2's provenance records and S2.1.2's
conformance reports both answered it the same way — the record lives in Postgres, content-
addressed, behind a port (`ArtefactStore`), so relocating it to a real object store later
changes one adapter and not the callers.

One table rather than one per kind: `kind` names what an artefact *is* ("visual_capture"
today, an evidence bundle at E7, a PBIR thumbnail at E6), and the shape a binary artefact
needs — content, a hash, a size, who produced it, what it is linked to — does not change with
what is inside it.

### 5. `mu_ref` is a name the caller states, not a foreign key

E3 has not created a Migration Unit table yet — there is nothing to reference. Until it does,
the accepted `mu_ref` is the workbook LUID: §3.1 makes an MU "one source workbook and
everything the platform produces for it", so the two share an identity in every way this store
cares about. When E3 mints real MU ids, callers pass those instead and nothing here changes —
the same shape `migration_units.py` already uses for an MU id elsewhere in this service.

### 6. "Never sent to a model endpoint" is a structural guarantee, not a runtime check

Nothing in this codebase today assembles model context from anything but the estate graph
(`context/`), and this story does not touch that path. The guarantee added here is about what
comes *later*, when a context contract or an agent might plausibly reach for artefact
metadata: `ArtefactRecord` — the shape `get()` returns, and the only shape a future contract
could reference — never carries the bytes. Only `content()` does, called from exactly one
route (`GET /v1/artefacts/{id}/content`), documented as existing for the console's own image
tag. A contract that included an `ArtefactRecord` could not leak an image through it even by
accident, because the bytes are not a field on the thing being included.

This is deliberately weaker than a runtime enforcement point — there is no gateway to enforce
against yet (E11/E12). It is the strongest guarantee available today, and it is the one that
does not need to change when the gateway arrives: a contract assembled from records that never
carry pixels stays that way regardless of what checks the gateway later adds.

### 7. Two read shapes, on purpose

`GET /v1/artefacts/{id}` (metadata) and `GET /v1/artefacts/{id}/content` (bytes) are separate
routes rather than one endpoint with a query flag, so that "does this response carry an image"
is answered by which URL was called, not by a parameter a caller could get wrong.

## Consequences

- `capture_visual` never raises `UnsupportedCapability` for the Tableau adapter — screenshot is
  always claimed. It still raises `AdapterError` for a view that is not published, the same way
  `fetch` raises for an asset that does not exist; unlike `execute_case`, there is no
  INCONCLUSIVE outcome to fall back on, because `VisualCapture` carries none.
- Every tenant that already promoted the Tableau adapter is unaffected by this story alone —
  the interface version does not move (S2.4.1 already took it to 1.1; this story adds a
  capability, which is additive per ADR 0015).
- Storing and retrieving artefacts has a real HTTP surface today, with no automated caller —
  the same position provenance was in at S1.3.2. E7's Proof Engine is expected to call
  `POST /v1/artefacts` when it orchestrates §10.6's comparison; until then this seam is
  exercised by the adapter's own conformance suite and by hand.
- A client that has not accepted Pillow's licence has nothing to accept — HPND is permissive
  and Pillow ships in the adapter's own image, not a client dependency.

## Alternatives considered

**Return Tableau's native-size image unresized, and let the caller resize it.** Rejected: it
would push the "configurable size" criterion onto every caller, and it would make the adapter's
own conformance check ("captured at the size requested") false by construction.

**Store bytes on `ArtefactRecord` and let a route choose whether to include them.** Rejected —
see decision 6. A field that is *usually* omitted is a field a future caller can still ask for;
a field that does not exist cannot be.

**Wait for E3/E7 before building the artefact store at all.** Rejected on the same grounds
S1.3.2 and S2.1.2 already settled: the record is real and useful today (Platform Health, an
engineer checking a capture by hand), and building the seam now means E7 has something to call
rather than something to invent.
