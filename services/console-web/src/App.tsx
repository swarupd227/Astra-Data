/**
 * The console shell -- extended by story S10.1.1, opening E10 (Migration Console) and
 * F10.1 (Console shell, roles and navigation).
 *
 *     "As a platform engineer, I want a React / TypeScript console shell with Entra ID
 *     sign-in, role-based navigation and tenant branding, so that every role lands on
 *     the screen its day starts on.
 *
 *     Acceptance criteria:
 *     - Roles from §15.1 with landing pages: PM -> Programme Board; model engineer ->
 *       Foundry Workbench; migration engineer -> Exception Desk; parity engineer ->
 *       Parity Dashboard; platform engineer -> Platform Health; data owner -> Gate
 *       Inbox (G2); report owner -> Gate Inbox (G3); licence admin -> Decommission
 *       Tracker; InfoSec -> Data Handling
 *     - Client roles see only the client surfaces and their domain scope; Artizent
 *       roles see everything; navigation is generated from role
 *     - Prod / test / dev environment is visibly distinct in the chrome
 *     - Every screen is reachable by URL; deep links to an MU, a case, a gate card and
 *       a run are stable"
 *
 * **Identity is a stated stub.** Until E11 brings Entra ID the service reads
 * `X-Astra-Principal` and `X-Astra-Roles` headers, so the console has to send something.
 * It sends a role a person picks, and the top bar says plainly that this is not sign-in.
 * A fake login screen would be worse than an honest selector: it would look like a
 * security control to everybody who saw a screenshot. "Entra ID sign-in" itself is
 * §15's own future scope, not something §15 ever specifies architecturally (confirmed
 * by direct search of the whole spec: no "Entra ID"/"console shell" text exists in
 * §15 at all) -- this story builds the *landing/navigation* half of the AC, over the
 * identity stub every other screen already accepts.
 *
 * **§15.1's own role table names five screens that do not exist anywhere in this
 * console yet** (confirmed by direct search): Foundry Workbench, (Admin's own)
 * Platform Health, Gate Inbox filtered to G2, Gate Inbox filtered to G3, and (Admin's
 * own) Data Handling. E10's own risk register is explicit that this is not an
 * oversight to fix by building four new screens here: "Console scope grows beyond the
 * seven surfaces | Stories added to E10 without an engine feature behind them | Rule:
 * no screen without an engine feature." None of Platform Health's own content (adapter
 * status, queue depths, executor latencies) or Data Handling's (redaction rules,
 * boundary tests, an InfoSec sign-off card) exists as a real, queryable fact anywhere
 * in this codebase -- building either screen now would be exactly the scope creep that
 * rule exists to block. `LANDING_SURFACE` below instead lands each such role on the
 * closest *real* screen, each reading disclosed on its own line:
 * - Semantic Model Engineer -> **Model Detail** (`models`). §15.1 names "Foundry
 *   Workbench"; `ModelDetail.tsx`'s own docstring already discloses "the Foundry
 *   Workbench's fuller family queue is nobody's yet" -- this is the real screen a
 *   model engineer already works families and designs through today.
 * - Platform Engineer -> **Parse Quality Queue** (`quality`), not `admin`. §15.1 names
 *   "Admin > Platform Health"; `Admin.tsx` itself is disclosed as "the Migration
 *   Architect's own single-purpose surface" (conformance rules only) -- landing a
 *   Platform Engineer there would put them on a screen built for a different role's
 *   own action, all of it disabled for them. `quality/ParseQualityQueue.tsx`'s own
 *   README section already calls it plainly "a platform engineer's screen" -- the
 *   closer, real, already-owned fit.
 * - Client Data Owner -> **Model Proposal** (`proposal`). §15.1 names "Gate Inbox
 *   (filtered to G2)" -- `ModelProposal.tsx` already *is* that inbox in every way that
 *   matters: it fetches every family awaiting review into a real list
 *   (`familiesForReview`) and opens the selected one's own detail, not a single-item
 *   lookup box. The tab's own label differs; the behaviour is the AC's own noun.
 * - Client Report Owner -> **G3 Acceptance** (`g3`). §15.1 itself offers a second
 *   reading for this exact role, verbatim: "Gate Inbox (filtered to G3) / **Migration
 *   Unit page**." No real multi-item G3 queue exists (`G3Card.tsx` is a single-workbook
 *   lookup, confirmed by direct read -- no list-fetching call anywhere in that file),
 *   and building one is exactly the "no screen without an engine feature" scope this
 *   story does not take on. The spec's own second reading is landed on instead: G3's
 *   own gate card already is this platform's closest thing to a "Migration Unit page."
 * - Client InfoSec Reviewer -> **Estate Explorer** (`estate`). §15.1 names "Admin >
 *   Data Handling"; nothing InfoSec-shaped exists anywhere yet (confirmed: zero real
 *   gates, zero UI checks for `client_infosec_reviewer` anywhere in this console before
 *   this story). Estate Explorer is the closest real "what data exists, where" screen
 *   this platform has -- the honest "closest real thing" landing, not a claim that
 *   Data Handling is built.
 *
 * **Client roles see only their own landing surface plus whichever surfaces a real,
 * existing screen already gates them to act on** (`CLIENT_VISIBLE_SURFACES`) -- derived
 * directly from each screen's own `identity.roles.includes(...)` check (confirmed by a
 * full grep across every screen in this console), not a second, hand-maintained list
 * that could quietly drift from what a role can actually do. Artizent roles
 * (`roles.ARTIZENT_ROLE_VALUES`, mirroring graph-svc's own `roles.ARTIZENT_ROLES`) see
 * every surface, per the AC's own words. This is nav-level *convenience* only, the
 * identical "a console that only hides a button is not a permission model" posture
 * every screen's own action-gating already takes -- every mutating call is still
 * re-checked by the real server-side role dependency regardless of what the nav shows,
 * and a direct URL still renders its own screen even for a role whose nav tab is
 * hidden (a notification or a shared link should not silently 404 just because the
 * console's own tab bar does not show that surface to this role).
 *
 * **Changing "Acting as" re-lands on the new role's own landing surface** -- the
 * identical thing a real sign-in would do, and the only sensible behaviour once nav
 * itself is role-filtered: staying on a surface the new role's own tab bar no longer
 * shows would leave a screen open with no way back to it from the nav.
 *
 * **Deep links** (the AC's own four nouns) are query-string based, read once at mount
 * and written back on selection (`lib/deep-link.ts`), the same "state is in the URL,
 * not just in memory" posture `useEstate.ts` already set for its own filters:
 * - **an MU** -> Estate Explorer's own pre-existing `?search=` filter persistence
 *   (`useEstate.ts`, built since S1.4.1) plus this story's own new "auto-select when
 *   the filter narrows to exactly one real workbook" rule -- reusing the screen's own
 *   URL-owning mechanism rather than adding a second one that would race it on every
 *   filter change (`EstateExplorer.tsx`'s own new effect explains why).
 * - **a case** -> Exception Desk's `?case=`.
 * - **a gate card** -> G3 Acceptance's `?workbook=` -- and closes a real, previously
 *   dead link: `G3Card.tsx`'s own "Open report" button has pointed at `/parity?
 *   workbook=...` since S9.1.1, and `ParityDashboard.tsx` never read it back until now.
 * - **a run** -> the Regression Monitor's `?workbook=`, since that screen has no other
 *   per-item detail view to link to; narrows its own table to that one real run.
 *
 * **The environment chip is visibly distinct per value**, not just per text (§15.6) --
 * `data-env` drives real, distinct colours in `styles.css` (prod red, test amber, dev
 * blue, local neutral) rather than one fixed style regardless of which environment is
 * actually running, closing a real gap: the `VITE_ASTRA_ENV` plumbing already existed
 * in `main.tsx` since before this story, but every value rendered identically.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { Admin } from './admin/Admin';
import { ToleranceCharter } from './charter/ToleranceCharter';
import { EstateExplorer } from './estate/EstateExplorer';
import { ExceptionDesk } from './exceptions/ExceptionDesk';
import { ModelProposal } from './g2/ModelProposal';
import { G3Card } from './g3/G3Card';
import { createApi, type Identity } from './lib/api';
import { getDeepLinkParam } from './lib/deep-link';
import { isArtizentRole } from './lib/roles';
import { LineageView } from './lineage/LineageView';
import { ModelDetail } from './modeller/ModelDetail';
import { ParityDashboard } from './parity/ParityDashboard';
import { PatternLibrary } from './patterns/PatternLibrary';
import { ProgrammeBoard } from './programme/ProgrammeBoard';
import { ParseQualityQueue } from './quality/ParseQualityQueue';
import { RegressionMonitor } from './regression/RegressionMonitor';
import { DecommissionTracker } from './release/DecommissionTracker';
import { ReleaseBoard } from './release/ReleaseBoard';
import { WaveBoard } from './trains/WaveBoard';

/** The Estate surface's screens (§15.3.2), plus the Programme Board's own figure (S3.1.3),
 * the Wave Board's drag/WIP mechanics (S3.2.2), Model Detail's proposal editing and G2
 * submission (S4.1.2), the Model Proposal client view with its own G2 decisions (S4.2.1),
 * and — since S4.3.2 — a single Admin sub-screen for the conformance ruleset (§2.4 lists
 * "Admin" among the Migration Architect's own surfaces; §15.3.7 names five Admin screens,
 * none of them this one, so this story adds its own rather than waiting on a screen no
 * other backlog story claims). The Pattern Library (S5.5.3) is its own top-level surface,
 * not an Admin sub-screen — §15.3.7 lists it under Admin, but this story's own acceptance
 * criteria asks for a screen with no dependency on the other four Admin screens (Platform
 * Health, Model Gateway & TokenOps, Data Handling, Tenant & Access), none of which exist
 * yet, so it stands on its own rather than waiting on an Admin shell nobody has built. The
 * full Programme Board is S10.2.1's, the Foundry Workbench's fuller family queue is
 * nobody's yet, and Platform Health/Model Gateway & TokenOps/Data Handling/Tenant & Access
 * still belong to E11/E12.
 * The Tolerance Charter editor (S7.1.1, opening E7) is its own top-level surface for the
 * identical reason the Pattern Library already is: §2.4 names "Parity Dashboard, Charter
 * editor" as the Parity Engineer's own surfaces, and neither is a natural sub-screen of
 * Admin (the Migration Architect's own single-purpose surface). The Parity Dashboard
 * (S7.4.2) is that same named surface, finally built — its own top-level entry for the
 * identical reason, not an Admin sub-screen. The Regression Monitor (S7.7.1, closing
 * F7.7/E7) is its own top-level entry too — the AC's own literal "Regression Monitor
 * screen", not a Parity Dashboard tab: it lists every released workbook at once, the
 * opposite shape from the Parity Dashboard's own one-workbook-at-a-time search. The
 * Exception Desk (S8.3.1, opening F8.3) is its own top-level entry too — §11.3's own
 * named screen, "the Migration Engineer's work queue", not a tab on any of the above.
 * The G3 gate card (S9.1.1, opening F9.1/E9) is a third — §15.5's own named screen, one
 * workbook's own real acceptance decision at a time, the identical single-workbook
 * search shape the Parity Dashboard already set. The Release Board (S9.2.1, opening
 * F9.2) is a fourth — §15.3.4's own named Delivery-surface screen, its own axis (pipeline
 * stage and the parallel-run window) distinct from the Wave Board's §3.2 MU-state
 * kanban. The Decommission Tracker (S9.2.2, continuing F9.2) is a fifth — §15.3.4's own
 * other named Delivery-surface row, confirmed a distinct screen from the Release Board
 * by direct read of both rows in the same table: its own axis is per-MU real adoption
 * against the configured threshold, not pipeline stage. */
export const SURFACES = [
  { key: 'estate', label: 'Estate Explorer' },
  { key: 'lineage', label: 'Lineage View' },
  { key: 'quality', label: 'Parse Quality Queue' },
  { key: 'programme', label: 'Programme Board' },
  { key: 'trains', label: 'Wave Board' },
  { key: 'models', label: 'Model Detail' },
  { key: 'proposal', label: 'Model Proposal' },
  { key: 'admin', label: 'Admin' },
  { key: 'patterns', label: 'Pattern Library' },
  { key: 'charter', label: 'Tolerance Charter' },
  { key: 'parity', label: 'Parity Dashboard' },
  { key: 'regression', label: 'Regression Monitor' },
  { key: 'exceptions', label: 'Exception Desk' },
  { key: 'g3', label: 'G3 Acceptance' },
  { key: 'release', label: 'Release Board' },
  { key: 'decommission', label: 'Decommission Tracker' },
] as const;

export type Surface = (typeof SURFACES)[number]['key'];

export const ROLES: { value: string; label: string; principal: string }[] = [
  {
    value: 'programme_manager',
    label: 'Programme Manager',
    principal: 'user:pm@artizent.example',
  },
  {
    value: 'migration_engineer',
    label: 'Migration Engineer',
    principal: 'user:engineer@artizent.example',
  },
  {
    value: 'platform_engineer',
    label: 'Platform Engineer',
    principal: 'user:p.eng@artizent.example',
  },
  {
    value: 'semantic_model_engineer',
    label: 'Semantic Model Engineer',
    principal: 'user:sme@artizent.example',
  },
  {
    value: 'client_data_owner',
    label: 'Client Data Owner',
    principal: 'user:owner@client.example',
  },
  {
    value: 'client_report_owner',
    label: 'Client Report Owner',
    principal: 'user:owner@client.example',
  },
  {
    value: 'migration_architect',
    label: 'Migration Architect',
    principal: 'user:architect@artizent.example',
  },
  {
    value: 'parity_engineer',
    label: 'Parity Engineer',
    principal: 'user:parity@artizent.example',
  },
  {
    value: 'client_analytics_lead',
    label: 'Client Analytics Lead',
    principal: 'user:lead@client.example',
  },
  {
    value: 'client_licence_admin',
    label: 'Client Licence Administrator',
    principal: 'user:licence.admin@client.example',
  },
  {
    value: 'client_infosec_reviewer',
    label: 'Client InfoSec Reviewer',
    principal: 'user:infosec@client.example',
  },
];

/** §15.1's own role -> landing-page table -- see this module's own docstring for every
 * reading choice where the spec's own named screen does not exist yet. Roles absent
 * from §15.1 itself (Migration Architect, Client Programme Sponsor) get no entry here,
 * the identical omission the spec's own table already has; they keep the pre-existing
 * fallback (`estate`) via `landingSurfaceFor`. */
const LANDING_SURFACE: Partial<Record<string, Surface>> = {
  programme_manager: 'programme',
  semantic_model_engineer: 'models',
  migration_engineer: 'exceptions',
  parity_engineer: 'parity',
  platform_engineer: 'quality',
  client_data_owner: 'proposal',
  client_report_owner: 'g3',
  client_licence_admin: 'decommission',
  client_infosec_reviewer: 'estate',
};

function landingSurfaceFor(role: string): Surface {
  return LANDING_SURFACE[role] ?? 'estate';
}

/** Every surface a client role is real-gated to *act* on today, drawn directly from
 * each screen's own `identity.roles.includes(...)` check (confirmed by a full grep
 * across `services/console-web/src`) -- see this module's own docstring. A client role
 * always sees its own landing surface too, even where that is the only entry. */
const CLIENT_VISIBLE_SURFACES: Partial<Record<string, Surface[]>> = {
  client_data_owner: ['proposal'],
  client_report_owner: ['g3', 'decommission'],
  client_licence_admin: ['decommission'],
  client_analytics_lead: ['charter'],
  client_infosec_reviewer: ['estate'],
};

function visibleSurfacesFor(role: string): (typeof SURFACES)[number][] {
  if (isArtizentRole([role])) return [...SURFACES];
  const allowed = new Set<Surface>([landingSurfaceFor(role), ...(CLIENT_VISIBLE_SURFACES[role] ?? [])]);
  return SURFACES.filter((option) => allowed.has(option.key));
}

export interface AppProps {
  api?: ReturnType<typeof createApi>;
  environment?: string;
  initialRole?: string;
  initialSurface?: Surface;
}

export function App({
  api: injected,
  environment = 'local',
  initialRole,
  initialSurface,
}: AppProps): JSX.Element {
  const api = useMemo(() => injected ?? createApi(), [injected]);
  const [role, setRole] = useState(initialRole ?? ROLES[0]!.value);
  // The screen is in the path, so a lineage view can be linked to like anything else.
  // At the bare root path (no surface requested) a role lands where its own day starts,
  // rather than always defaulting to Estate Explorer regardless of who is "Acting as".
  const [surface, setSurface] = useState<Surface>(() => {
    if (initialSurface) return initialSurface;
    const pathname = typeof window === 'undefined' ? '/' : window.location.pathname;
    return pathname === '/' ? landingSurfaceFor(initialRole ?? ROLES[0]!.value) : surfaceFromPath(pathname);
  });

  // Back/forward previously left `surface` stale, since it was only ever read from the
  // URL once, at mount -- a real gap against "every screen is reachable by URL".
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const onPopState = () => setSurface(surfaceFromPath(window.location.pathname));
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const identity: Identity = useMemo(() => {
    const chosen = ROLES.find((option) => option.value === role) ?? ROLES[0]!;
    return { principal: chosen.principal, roles: [chosen.value] };
  }, [role]);

  const visibleSurfaces = useMemo(() => visibleSurfacesFor(role), [role]);

  const navigate = useCallback((key: Surface, options?: { clearQuery?: boolean }) => {
    setSurface(key);
    if (typeof window === 'undefined') return;
    const path = key === 'estate' ? '/' : `/${key}`;
    const query = options?.clearQuery ? '' : window.location.search;
    window.history.replaceState(null, '', `${path}${query}`);
  }, []);

  const changeRole = useCallback(
    (newRole: string) => {
      setRole(newRole);
      // A fresh identity is a fresh session -- any deep-link query from the role just
      // left behind (e.g. `?workbook=`) would otherwise carry over into a screen the
      // new role lands on for an unrelated reason.
      navigate(landingSurfaceFor(newRole), { clearQuery: true });
    },
    [navigate],
  );

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span>Astra Data</span>
          <span className="product">Migration Console</span>
        </div>
        <span
          className="env-chip"
          data-env={environment}
          title="Environment chrome is deliberately distinct (§15.6)"
        >
          {environment}
        </span>
        <nav aria-label="Surfaces" className="surfaces">
          {visibleSurfaces.map((option) => (
            <button
              type="button"
              key={option.key}
              className="surface-tab"
              aria-current={surface === option.key}
              onClick={() => navigate(option.key)}
            >
              {option.label}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        <div className="identity">
          <label htmlFor="role">
            Acting as
            <span className="visually-hidden"> (role, not sign-in)</span>
          </label>
          <select id="role" value={role} onChange={(event) => changeRole(event.target.value)}>
            {ROLES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <span className="faint" title="Entra ID sign-in arrives with E11/F11.1">
            not signed in
          </span>
        </div>
      </header>

      {surface === 'estate' && <EstateExplorer api={api} identity={identity} />}
      {surface === 'lineage' && <LineageView api={api} identity={identity} />}
      {surface === 'quality' && <ParseQualityQueue api={api} identity={identity} />}
      {surface === 'programme' && <ProgrammeBoard api={api} identity={identity} />}
      {surface === 'trains' && <WaveBoard api={api} identity={identity} />}
      {surface === 'models' && <ModelDetail api={api} identity={identity} />}
      {surface === 'proposal' && <ModelProposal api={api} identity={identity} />}
      {surface === 'admin' && <Admin api={api} identity={identity} />}
      {surface === 'patterns' && <PatternLibrary api={api} identity={identity} />}
      {surface === 'charter' && <ToleranceCharter api={api} identity={identity} />}
      {surface === 'parity' && (
        <ParityDashboard api={api} identity={identity} initialWorkbookId={getDeepLinkParam('workbook') ?? undefined} />
      )}
      {surface === 'regression' && (
        <RegressionMonitor api={api} identity={identity} initialWorkbookId={getDeepLinkParam('workbook') ?? undefined} />
      )}
      {surface === 'exceptions' && (
        <ExceptionDesk api={api} identity={identity} initialCaseId={getDeepLinkParam('case') ?? undefined} />
      )}
      {surface === 'g3' && (
        <G3Card api={api} identity={identity} initialWorkbookId={getDeepLinkParam('workbook') ?? undefined} />
      )}
      {surface === 'release' && <ReleaseBoard api={api} identity={identity} />}
      {surface === 'decommission' && <DecommissionTracker api={api} identity={identity} />}
    </div>
  );
}

/** The screen is in the path, so any of them can be linked to. */
export function surfaceFromPath(pathname: string): Surface {
  const found = SURFACES.find((option) => pathname.startsWith(`/${option.key}`));
  return found?.key ?? 'estate';
}
