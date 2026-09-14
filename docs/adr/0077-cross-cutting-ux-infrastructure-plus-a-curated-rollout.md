# ADR 0077 — Cross-cutting UX: real infrastructure, applied to a curated set of screens

Status: accepted · 14 September 2026 · Story S10.5.1, opening F10.5

## Context

S10.5.1 — the backlog's own AC, verbatim: *"As an InfoSec reviewer, I want accessibility,
responsiveness and localisation baselines, so that the console meets the client's
standards for internal tools."*

- WCAG 2.2 AA on gate cards, Exception Desk and evidence views (automated axe checks in
  CI, manual screen-reader test on the three screens)
- Gate Inbox, gate cards and MU page usable on mobile; Programme Board and Parity
  Dashboard tablet-first
- en-GB and en-US strings externalised; all dates shown in the viewer's timezone with
  the charter timezone noted where relevant

This is the first story in this codebase that builds a11y test tooling, a responsive
breakpoint discipline, and a locale/date-formatting layer from nothing — there was no
`axe`/`jest-axe`, no locale module, and (confirmed by direct research) two of the eight
`@media` rules already in `styles.css` had a real, unnoticed bug that made every
"single-column" screen after S10.1.1's own console-layout fix (Gate Inbox, gate cards,
the Migration Unit page, and five more) revert to a three-pane mobile layout at narrow
widths anyway.

**Unlike every prior F10 story, this one's own AC names screens for two of its three
clauses but not the third.** The a11y and responsive clauses each name exactly the
screens they cover; the i18n/date clause names none. Read as this story's own version of
the "AC narrower than spec section" discipline ADR 0071/ADR 0075/ADR 0076 already
established, in reverse: the *infrastructure* (locale module, date formatter, strings
dictionary) is built for the whole console, real and reusable; the *rollout* — which
screens actually call it — is scoped to the six real surfaces the other two clauses
already name (gate cards, Exception Desk, evidence views, Gate Inbox, the MU page,
Programme Board, Parity Dashboard), the same "infrastructure now, adopt screen by
screen" precedent `components/Explain.tsx`'s own registry already set in S10.1.2.

## Decisions

### 1. Automated axe checks are a real vitest file, not a second CI job

`jest-axe` (the mature, stable package; `vitest-axe` has no stable release, confirmed by
direct search) wraps real `axe-core` — the same engine Chrome DevTools' own accessibility
panel runs, not a hand-rolled ruleset. `tests/a11y.test.tsx` asserts `toHaveNoViolations()`
against G3Card (both a report owner's decidable view and an Artizent reader's
undecidable one — the two real render branches), the Exception Desk (the queue and a
real case's own evidence pane), and the Decision Register (the table and, with a
resolved artefact rendered, the evidence bundle panel — "evidence views," read as these
two real surfaces since they are the ones this codebase actually has). Riding the
existing `npm run test` step (already in `.github/workflows/ci.yml`'s `console` job)
satisfies the AC's own "in CI" clause without a second pipeline.

### 2. A real, found-and-fixed WCAG contrast failure, at the token level — not per-screen

Computed contrast ratios (WCAG relative-luminance formula) against this app's own real
CSS tokens found two failures, both light and dark: `--text-faint` (3.10:1 light / 3.70:1
dark against `--bg-panel`, needs 4.5:1 for SC 1.4.3 normal text — `.faint` is used at
normal size throughout, not only at the ≥18pt/14pt-bold size where 3:1 would suffice),
and `--border-strong` (1.61:1 / 1.75:1, needs 3:1 for SC 1.4.11 non-text UI component
contrast — every `.btn`/input/card border in the app). Fixed once, in the token, not
per-screen: `--text-faint` → `#68717d`/`#828da0` (4.94:1/5.22:1), `--border-strong` →
`#7d8694`/`#647087` (3.68:1/3.50:1) — a strict, app-wide improvement from a two-line
change, the identical "fix the shared thing once" posture `format.tsx`'s own docstring
already states for numbers.

### 3. A real, empty `<th />` found by the tool doing its job

`jest-axe` caught `empty-table-header` on the Decision Register's own "Actions" column
(S10.4.2, `<th />` with no accessible name) on the very first run. Fixed with
`.visually-hidden` (an existing utility class, confirmed already used elsewhere) rather
than visible text, since the column's own row-level "Open evidence" button already names
its own action. Left in this ADR as the concrete proof the automated check is real, not
theatre — a violation this story's own new tooling found and this story fixed, not a
clean run staged to look thorough.

### 4. "Manual screen-reader test" is disclosed as an accessibility-tree read, not a literal screen reader run

No OS-level screen reader (VoiceOver/NVDA/JAWS) is drivable from this environment,
confirmed. The manual pass against the three named screens was a direct read of the
browser's own accessibility tree — role, accessible name, and reading order, the same
three facts a screen reader itself consumes — against the real, running console with
real demo-estate data (not only fixtures): the G3 gate card's own five headed sections in
order with a role-appropriate disclosure text in place of hidden action buttons; the
Exception Desk's own labelled filter row and honest empty-queue state; the Decision
Register's own table and evidence panel, including the real "no evidence resolves to a
stored artefact" disclosure read correctly in place. Disclosed here as this specific,
real methodology rather than an unverifiable claim of a literal screen reader.

### 5. Two real, latent CSS bugs found live — not only the three named "mobile" screens' own new work

**A `.workspace` media-query cascade bug, older than this story.** `@media (max-width:
1239px)`/`@media (max-width: 860px)` each redeclare `.workspace`'s own
`grid-template-columns`/`grid-template-rows` unconditionally — a rule with equal
specificity to, and declared *after*, the single-column override list (`.g3-card-
workspace`, `.exception-desk`, etc. — first built for six screens by the "Console layout
fix" this README already documents, then extended for Gate Inbox/the MU page by S10.4.1/
S10.3.1's own... actually never extended; confirmed live, at 375px, by measuring
`Exception Desk`'s own single pane crushed into a fixed `22vh` (~179px) row, most of the
screen left blank grey below it — the exact bug the override list exists to prevent,
silently reintroduced by these two rules at every narrow width, for every single-column
screen, the whole time. Fixed with a `:not(...)` exclusion list mirroring the override
list exactly, confirmed live afterward: zero horizontal or crushed-pane layout issues
on Gate Inbox, the G3 gate card and the Migration Unit page at 375px.

**A nested-flex-container wrap bug, found live, affecting every screen's own nav.**
`.topbar { flex-wrap: wrap }` only wraps `.topbar`'s own direct children relative to each
other; `nav.surfaces` (the 21 nav buttons) and `.identity` (the "Acting as"/Locale
selectors) are each their *own* flex container, neither with its own `flex-wrap` —
defaulting to `nowrap`, with no default `min-width: 0` either, so neither shrinks or
wraps. Confirmed live: at 375px the nav's own content measured 1636px wide, running off
the right edge and taking "Gate Inbox"/"Decision Register" with it — unreachable by
tapping, the single worst possible reading of "usable on mobile." Fixed with `flex-wrap:
wrap; min-width: 0` on both, inside the existing 860px breakpoint (where `.topbar`
already turns wrapping on) rather than a new one.

### 6. `formatDate` is a second, deliberately date-only function — not `formatDateTime` reused

`ProgrammeBoard.tsx`'s own milestone rail mixes two real, differently-typed date sources
in one list: `ReleaseTrain.planned_start`/`.planned_end`/`.actual_start`/`.actual_end`
(ontology `T.DATE` — no time-of-day) and `GateDecision.timestamp` (`T.TIMESTAMP` — a real
instant), confirmed by direct reading of `ontology/nodes.py`. Converting a `T.DATE`
value through `formatDateTime`'s own timezone math would risk shifting the calendar date
itself for a viewer behind UTC (`new Date("2027-03-04")` parses as UTC midnight — a
viewer at UTC-5 would see "03" render where "04" is correct). `formatDate` reorders the
`YYYY-MM-DD` string's own digits per locale directly, never constructing a `Date` or
touching a timezone; `MilestoneRailPane` picks the right one per `milestone.kind`.

### 7. The charter-timezone note is fetched best-effort, since its own real reader gate does not include this screen's own real client role

`require_tolerance_charter_reader` (`deps.py`, S7.1.1) allows Artizent roles and
`client_analytics_lead` — not `client_report_owner`, the Parity Dashboard's own real
client reader (`require_parity_dashboard_reader`). Calling `api.toleranceCharter` from
`ParityDashboard.tsx` to read `dates.timezone` therefore gets a real 403 for that role, by
design, not a bug to route around — caught and left silently absent (`charterTimezone:
null`) rather than surfaced as a dashboard-breaking error over a secondary fact; the note
itself (`formatDateTimeWithCharterNote`) only ever appends when a real timezone was
fetched *and* differs from the viewer's own resolved zone, so a viewer already in that
zone is not shown a redundant note either.

### 8. The i18n strings dictionary is real but genuinely small — not padded to look complete

Direct search across every screen this story's own AC names found no genuine en-GB/en-US
spelling divergence in this console's real user-facing text — "programme" and "licence"
are this platform's own canonical domain nouns (used identically in the product spec,
role names, and every screen's own copy), not a British-English styling choice a US
reader would expect swapped. `lib/locale.ts`'s own `STRINGS` dictionary holds the one
real entry this scope found (`licence`/`license`, used in `App.tsx`'s own "Client Licence
Administrator" role label) — real, used, and extensible for a future screen that
introduces a genuinely divergent word, not invented divergence to look more complete
than the actual language difference is. The locale's own real, measurable value is date/
time formatting (decision 6 and `formatDateTime`'s own day-first/month-first, 24h/12h
split), not vocabulary substitution.

## Consequences

- `services/graph-svc`: no changes. Every clause of this story's own AC is a
  `console-web`-only concern.
- `services/console-web`: new `lib/locale.ts` (`LocaleCode`, `LOCALES`, `DEFAULT_LOCALE`,
  `t`, `viewerTimezone`, `formatDateTime`, `formatDate`,
  `formatDateTimeWithCharterNote`); new `tests/a11y.test.tsx` (6 tests) and
  `tests/locale.test.tsx` (8 tests); new devDependencies `jest-axe`/`@types/jest-axe`,
  wired into `tests/setup.ts`. `App.tsx` gained a `Locale` selector (mirrors "Acting as"
  exactly, defaults to `en-GB`) threaded into `DecisionRegister`/`G3Card`/
  `MigrationUnitPage`/`ProgrammeBoard`/`ParityDashboard`. `styles.css` gained: two
  WCAG-AA token fixes (light and dark); the `:not(...)`-scoped media-query fix; the
  `.surfaces`/`.identity` nested-flex-wrap fix; a new ≤599px breakpoint (`.pane-header`/
  `.statusbar` wrap, a `.btn`/input/select minimum touch-target height); an
  `overflow-wrap` fix on `.detail dd`. `.github/workflows/ci.yml` gained one comment
  documenting that the existing `Test` step is now also this story's own "automated axe
  checks in CI."
- Verified: `services/console-web` — 436 tests passing (14 new: 6 axe/WCAG, 8 locale/
  date-formatting); one real, pre-existing empty `<th />` (S10.4.2) found and fixed by
  the new axe tooling on its first run; `tsc --noEmit`/`eslint`/`vite build` all clean.
  One test-suite flake (`quality.test.tsx`, a screen this story never touches) observed
  once under concurrent load and confirmed clean in isolation — not a regression.
- Live-verified against the real Docker stack (console-web image rebuilt four times
  across this story's own iteration, each real bug fixed and reverified before moving
  on): the Locale selector switching a real Programme Board's own `family_count_
  confirmed_at` and every milestone-rail row between day-first/24h and month-first/12h
  live, against real data, including both a `T.DATE` train milestone (date-only, no time
  shown) and a `T.TIMESTAMP` gate milestone (full instant) rendering correctly side by
  side in the same list; the Decision Register's own table and evidence panel doing the
  identical live switch, including a real G2 decision's evidence honestly reporting "no
  evidence resolves to a stored artefact" (a `SemanticModel` reference, not an artefact)
  and a real G3 decision's evidence opening a real stored `g3_card_snapshot`; the
  charter-timezone fetch itself confirmed real (`GET /v1/tolerance-charter` → 200,
  `dates.timezone: "UTC"`) though no live `ParityRun` exists in the current demo estate
  to observe the note text rendered against (the mechanism's branch logic is covered by
  `locale.test.tsx` instead); zero horizontal overflow and zero crushed-pane layout,
  confirmed by measuring `document.body.scrollWidth` against the viewport width
  directly, on Gate Inbox/G3 Acceptance/the Migration Unit page at 375px and on
  Programme Board/Parity Dashboard at 768px, after the two CSS-cascade bugs (decision 5)
  were found and fixed.

## Alternatives considered

**Apply the locale/date-formatting rollout to every screen in the console, not just the
six this story's AC already names.** Rejected — see the Context section's own reading.
The i18n clause names no screens, but the other two clauses of the identical AC do; a
uniform rollout across all 26 screens would be answering a materially larger, un-asked
question, the same over-build this codebase's own "AC narrower than spec section"
discipline exists to catch in the other direction.

**Manufacture additional en-GB/en-US dictionary entries (e.g. translate "programme" to
"program") so the strings dictionary looks fuller.** Rejected — see decision 8.
"Programme" and "licence" are this product's own real domain vocabulary, confirmed
identical in the spec text itself; treating them as a dialect choice to swap would
misrepresent the product, not localise it.

**Scope the two CSS-cascade bugs (decision 5) as "pre-existing, out of scope" and leave
them for a future story**, since neither was introduced by this one. Rejected. Both sit
directly across this story's own "usable on mobile" AC for Gate Inbox, gate cards and the
Migration Unit page — a screen whose own nav is unreachable by tapping is not "usable on
mobile" regardless of which story's commit introduced the underlying rule, and finding
this by actually testing at the emulated width (rather than only reading CSS) is what a
"baseline" story exists to catch.

**Give `formatDateTime` an optional `dateOnly` flag instead of a second, separate
`formatDate` function.** Rejected — see decision 6. The two real inputs are different
ontology types with genuinely different safe operations (one must never touch a
timezone); a boolean flag toggling behaviour inside one function reads as one thing
doing two things, where two small, honestly-named functions read as what each one does.
