# ADR 0071 — Console shell lands each role on the closest real screen, not a new one

Status: accepted · 13 September 2026 · Story S10.1.1, opening E10 (Migration Console) and F10.1

## Context

S10.1.1 opens E10 (Migration Console) and F10.1 (Console shell, roles and
navigation) — the backlog's own AC, verbatim: *"As a platform engineer, I want a
React / TypeScript console shell with Entra ID sign-in, role-based navigation and
tenant branding, so that every role lands on the screen its day starts on."*

- Roles from §15.1 with landing pages: PM → Programme Board; model engineer →
  Foundry Workbench; migration engineer → Exception Desk; parity engineer → Parity
  Dashboard; platform engineer → Platform Health; data owner → Gate Inbox (G2);
  report owner → Gate Inbox (G3); licence admin → Decommission Tracker; InfoSec →
  Data Handling
- Client roles see only the client surfaces and their domain scope; Artizent roles
  see everything; navigation is generated from role
- Prod / test / dev environment is visibly distinct in the chrome
- Every screen is reachable by URL; deep links to an MU, a case, a gate card and a
  run are stable

Confirmed by direct research before writing anything: §15.1's own role table names
five screens that do not exist anywhere in this console yet (Foundry Workbench,
Platform Health, Gate Inbox filtered to G2, Gate Inbox filtered to G3, Data
Handling) — none of their own real content (adapter health, queue depths,
redaction rules, boundary tests, a real multi-item G3 queue) exists as a real,
queryable fact anywhere in this codebase. E10's own risk register is explicit
about exactly this risk: *"Console scope grows beyond the seven surfaces | Stories
added to E10 without an engine feature behind them | Rule: no screen without an
engine feature; PM approves any E10 addition."* "Entra ID sign-in" itself is not
something §15 specifies architecturally anywhere (confirmed by direct search) — it
is future E11 scope this story does not take on.

## Decisions

### 1. No new screen is built for a role §15.1 names but this codebase has no engine feature for

Following E10's own risk-register rule literally, each of the five non-existent
named screens is read against the closest *real* screen already built, each
reading disclosed on its own line in `App.tsx`'s own module docstring:

- **Semantic Model Engineer → Model Detail** (`models`), not "Foundry Workbench".
  `ModelDetail.tsx`'s own docstring already discloses "the Foundry Workbench's
  fuller family queue is nobody's yet" — this is the real screen a model engineer
  already works families and designs through today.
- **Platform Engineer → Parse Quality Queue** (`quality`), not "Platform Health"
  (`Admin.tsx`). `Admin.tsx` is disclosed as the Migration Architect's own
  single-purpose surface (conformance rules only) — landing a Platform Engineer
  there would put them on a screen built for a different role's own action, every
  bit of it disabled for them. `quality/ParseQualityQueue.tsx`'s own README
  section already calls it plainly "a platform engineer's screen."
- **Client Data Owner → Model Proposal** (`proposal`), not "Gate Inbox (G2)".
  `ModelProposal.tsx` already *is* that inbox in every way that matters: it
  fetches every family awaiting review into a real list (`familiesForReview`) and
  opens the selected one's own detail. The tab's own label differs; the behaviour
  is the AC's own noun.
- **Client Report Owner → G3 Acceptance** (`g3`), not a new "Gate Inbox (G3)".
  §15.1 itself offers a second reading for this exact role, verbatim: "Gate Inbox
  (filtered to G3) / **Migration Unit page**." No real multi-item G3 queue exists
  (`G3Card.tsx` is a single-workbook lookup, confirmed by direct read — no
  list-fetching call anywhere in that file); the spec's own second reading is
  landed on instead.
- **Client InfoSec Reviewer → Estate Explorer** (`estate`), not "Data Handling".
  Nothing InfoSec-shaped exists anywhere yet (confirmed: zero real gates, zero UI
  checks for `client_infosec_reviewer` anywhere in this console before this
  story). Estate Explorer is the closest real "what data exists, where" screen
  this platform has.

Every other role's landing page names a screen that already exists and is landed
on directly: PM → Programme Board, migration engineer → Exception Desk, parity
engineer → Parity Dashboard, licence admin → Decommission Tracker.

### 2. Client nav visibility is derived from each screen's own real role gate, not a second hand-maintained list

`CLIENT_VISIBLE_SURFACES` is built directly from a full grep across every screen
in this console for its own `identity.roles.includes(...)` checks, not invented
independently — a second, hand-authored visibility table could silently drift
from what a role can actually do the moment either side changed without the
other. Artizent roles (`roles.ARTIZENT_ROLE_VALUES`, mirroring `graph-svc`'s own
`roles.ARTIZENT_ROLES`, transcribed by hand since the console has no session to
ask the server with before it has rendered anything) see every surface, per the
AC's own words.

Nav filtering is deliberately **convenience only, not a security boundary** — the
identical "a console that only hides a button is not a permission model" posture
every screen's own action-gating already takes. A direct URL to a surface outside
a role's own nav still renders; every mutating call is still re-checked by the
real server-side role dependency regardless of what the tab bar shows. A shared
link or a notification should not silently 404 just because one role's own nav
does not carry a tab for it.

### 3. Changing "Acting as" re-lands on the new role's own surface and clears the query string

The identical thing a real sign-in would do, and the only sensible behaviour once
nav itself is role-filtered — staying on a surface the new role's own tab bar no
longer shows would leave a screen open with no way back to it from the nav. The
query string is cleared on a role change so a deep-link parameter left over from
the role just vacated (e.g. `?workbook=`) cannot silently carry into an unrelated
screen the new role lands on.

### 4. Deep links are a shared, tiny query-string convention, read once at mount

`lib/deep-link.ts`'s `getDeepLinkParam`/`setDeepLinkParam` — plain
`URLSearchParams` reads and `history.replaceState` writes, the same "state is in
the URL, not just in memory" posture `useEstate.ts` already set for its own
filters. Each of the AC's four deep-link nouns maps to one existing screen:

- **an MU** → Estate Explorer's own pre-existing `?search=` filter persistence
  (`useEstate.ts`, built since S1.4.1), plus this story's own new "auto-select
  when the filter narrows to exactly one real workbook" rule.
  `useEstate.ts` already owns Estate Explorer's *entire* query string — reading it
  on init and writing it back via `replaceState` on every filter change — so a
  second, competing `?workbook=` parameter on this screen would be silently
  overwritten by that existing effect on the very next filter change. Rather than
  add a second URL-owning mechanism, `EstateExplorer.tsx` gained one new effect:
  when the filtered result narrows to exactly one workbook and nothing is
  selected yet, that workbook is auto-selected. This makes the *existing*
  `?search=<luid or name>` link a complete, working deep link with no new
  query-string ownership needed.
- **a case** → Exception Desk's `?case=`, read once at mount
  (`initialCaseId` prop) and written back inside the existing `loadCase`
  callback.
- **a gate card** → G3 Acceptance's `?workbook=`. This closes a real,
  pre-existing dead link: `G3Card.tsx`'s own "Open report" button has pointed at
  `/parity?workbook=...` since S9.1.1, but `ParityDashboard.tsx` never read the
  `workbook` query parameter back until now.
- **a run** → the Regression Monitor's `?workbook=`, since that screen has no
  other per-item detail view to link to; narrows its own table to the one real
  matching row, with an honest empty state if the id matches nothing and a "Show
  every released workbook" link to clear the focus.

### 5. The environment chip's colour is driven by its own value, not one fixed style

`data-env` (already plumbed from `VITE_ASTRA_ENV` in `main.tsx` since before this
story) now drives real, distinct colours in `styles.css` — prod red (the existing
`--bad` token), test amber (`--warn`), dev blue (`--accent`), everything else
neutral — closing a real gap where every environment value rendered identically
styled regardless of which one was actually running.

### 6. Back/forward is kept in sync with the URL by a `popstate` listener

`surface` state was previously read from the URL once, at mount, only — a real
gap against "every screen is reachable by URL": clicking the browser's own Back
button changed the address bar but left the previously-rendered surface on
screen. A `popstate` listener re-derives `surface` from `window.location.pathname`
via the existing `surfaceFromPath` function whenever the browser's own history
navigation fires.

## Consequences

- New `services/console-web/src/lib/roles.ts`: `ARTIZENT_ROLE_VALUES`,
  `isArtizentRole` — extracted from `RegressionMonitor.tsx`'s own identical
  inline list (S7.7.1's export gate) rather than duplicated a second time; that
  screen now imports this constant too.
- New `services/console-web/src/lib/deep-link.ts`: `getDeepLinkParam`,
  `setDeepLinkParam`.
- `App.tsx`: `LANDING_SURFACE`, `landingSurfaceFor`, `CLIENT_VISIBLE_SURFACES`,
  `visibleSurfacesFor`; a lazy-initialized `surface` state that lands the bare
  root path on the current role's own landing surface; a `popstate` listener; new
  `navigate`/`changeRole` callbacks; nav buttons now map over `visibleSurfaces`
  instead of the raw `SURFACES` constant; the env chip gained a `data-env`
  attribute; `ROLES` gained `client_infosec_reviewer`.
- `styles.css`: `.env-chip` gained per-`data-env` colour rules (prod/test/dev),
  replacing one fixed style.
- `RegressionMonitor.tsx`: `initialWorkbookId` prop, focus-filtering to one
  workbook with a "Show every released workbook" clear action and an honest empty
  state; `canExport` now reads `isArtizentRole` instead of a hardcoded inline
  role array.
- `G3Card.tsx`, `ExceptionDesk.tsx`, `ParityDashboard.tsx`: `initialWorkbookId`/
  `initialCaseId` props, each read once at mount and written back into the URL
  whenever that screen's own load succeeds.
- `EstateExplorer.tsx`: a new effect auto-selecting the sole matching workbook
  once `useEstate`'s own filtered result narrows to exactly one.
- Verified: 335 console-web tests passing (a new `app.test.tsx`, 20 tests,
  covering every role's own landing page, nav filtering, role-change re-landing,
  the environment chip per value, and browser back/forward; new deep-link
  `describe` blocks added to `g3-card.test.tsx`, `parity-dashboard.test.tsx`,
  `exception-desk.test.tsx`, `regression-monitor.test.tsx`, and
  `EstateExplorer.test.tsx`); `tsc --noEmit`/`eslint`/`vite build` all clean.
- **A real regression found and fixed by the full test run**:
  `parity-dashboard.test.tsx`'s own pre-existing "offers Parity Dashboard as a
  surface" test rendered `<App>` as `client_report_owner` and clicked a "Parity
  Dashboard" nav tab that role's own filtered nav no longer shows — a direct,
  intended consequence of this story's own nav-filtering AC, not a defect. Fixed
  by changing the test to render as `parity_engineer` (a real Artizent role that
  legitimately sees every surface), with a comment disclosing why.
- Live-smoke-tested against the real Docker stack (`console-web` image rebuilt
  and recreated; `graph-svc`/`postgres` already running): Programme Manager lands
  on Programme Board with all sixteen surfaces in nav and real programme figures;
  switching "Acting as" to Client Report Owner re-lands on G3 Acceptance with nav
  filtered to exactly `G3 Acceptance`/`Decommission Tracker`; navigating directly
  to `/g3?workbook=<real id>` auto-loaded that workbook's real G3 gate card
  against the live graph (What/Proof/Visual/Changes/Next all populated from real
  data); navigating to `/parity?workbook=<same id>` auto-attempted the load and
  surfaced the honest `no ParityRun exists yet` error, proving the deep-link
  mechanism itself works correctly independent of what data exists; navigating
  to `/estate?search=<a name matching exactly one real workbook>` auto-selected
  that workbook's own detail panel and lineage graph with no click, against the
  real estate graph.

## Alternatives considered

**Build the five named-but-missing screens (Foundry Workbench, Platform Health,
two Gate Inbox variants, Data Handling) so every §15.1 role has its own literally
named landing page.** Rejected — see decision 1. E10's own risk register exists
specifically to block this: none of these screens' real content is backed by any
engine feature this codebase has built, and building them now would be exactly
the scope creep that rule names.

**Hand-maintain a second nav-visibility table alongside each screen's own role
gate.** Rejected — see decision 2. Two independent lists for the same fact drift
the moment one changes without the other; deriving visibility from the real gates
means there is only one place a role's own access is ever decided.

**Add a second, dedicated `?workbook=` query parameter to Estate Explorer for the
deep-link "an MU" noun.** Rejected — see decision 4. `useEstate.ts` already owns
that screen's entire query string; a second writer would race it on every filter
change, with the existing effect's own `replaceState` silently discarding
whatever the new one had just set.

**Make nav-level filtering a real security boundary** (refuse to render a surface
a role's own nav does not list). Rejected — see decision 2. Every other console
screen's own action-gating already treats hiding as UX convenience, not
authorization, enforced instead by the real server-side role dependency on each
mutating call; making the shell's own nav the one exception would create a
console-side permission model this codebase does not otherwise have and the
server does not know to honour.
