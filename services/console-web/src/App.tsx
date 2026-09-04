/**
 * The console shell.
 *
 * One screen so far — the Estate Explorer (S1.4.1). The Lineage View, Parse Quality Queue
 * and Usage & Ownership screens are the rest of F1.4 and will hang off the same shell.
 *
 * **Identity is a stated stub.** Until E11 brings Entra ID the service reads
 * `X-Astra-Principal` and `X-Astra-Roles` headers, so the console has to send something.
 * It sends a role a person picks, and the top bar says plainly that this is not sign-in.
 * A fake login screen would be worse than an honest selector: it would look like a
 * security control to everybody who saw a screenshot.
 */

import { useMemo, useState } from 'react';

import { Admin } from './admin/Admin';
import { EstateExplorer } from './estate/EstateExplorer';
import { ModelProposal } from './g2/ModelProposal';
import { createApi, type Identity } from './lib/api';
import { LineageView } from './lineage/LineageView';
import { ModelDetail } from './modeller/ModelDetail';
import { ProgrammeBoard } from './programme/ProgrammeBoard';
import { ParseQualityQueue } from './quality/ParseQualityQueue';
import { WaveBoard } from './trains/WaveBoard';

/** The Estate surface's screens (§15.3.2), plus the Programme Board's own figure (S3.1.3),
 * the Wave Board's drag/WIP mechanics (S3.2.2), Model Detail's proposal editing and G2
 * submission (S4.1.2), the Model Proposal client view with its own G2 decisions (S4.2.1),
 * and — since S4.3.2 — a single Admin sub-screen for the conformance ruleset (§2.4 lists
 * "Admin" among the Migration Architect's own surfaces; §15.3.7 names five Admin screens,
 * none of them this one, so this story adds its own rather than waiting on a screen no
 * other backlog story claims). The full Programme Board is S10.2.1's, the Foundry
 * Workbench's fuller family queue is nobody's yet, and the other five Admin screens
 * (Platform Health, Pattern Library, Model Gateway & TokenOps, Data Handling, Tenant &
 * Access) belong to E11/E12. */
export const SURFACES = [
  { key: 'estate', label: 'Estate Explorer' },
  { key: 'lineage', label: 'Lineage View' },
  { key: 'quality', label: 'Parse Quality Queue' },
  { key: 'programme', label: 'Programme Board' },
  { key: 'trains', label: 'Wave Board' },
  { key: 'models', label: 'Model Detail' },
  { key: 'proposal', label: 'Model Proposal' },
  { key: 'admin', label: 'Admin' },
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
];

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
  const [surface, setSurface] = useState<Surface>(
    initialSurface ??
      surfaceFromPath(typeof window === 'undefined' ? '/' : window.location.pathname),
  );

  const identity: Identity = useMemo(() => {
    const chosen = ROLES.find((option) => option.value === role) ?? ROLES[0]!;
    return { principal: chosen.principal, roles: [chosen.value] };
  }, [role]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span>Astra Data</span>
          <span className="product">Migration Console</span>
        </div>
        <span className="env-chip" title="Environment chrome is deliberately distinct (§15.6)">
          {environment}
        </span>
        <nav aria-label="Surfaces" className="surfaces">
          {SURFACES.map((option) => (
            <button
              type="button"
              key={option.key}
              className="surface-tab"
              aria-current={surface === option.key}
              onClick={() => {
                setSurface(option.key);
                if (typeof window !== 'undefined') {
                  const path = option.key === 'estate' ? '/' : `/${option.key}`;
                  window.history.replaceState(null, '', `${path}${window.location.search}`);
                }
              }}
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
          <select id="role" value={role} onChange={(event) => setRole(event.target.value)}>
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
    </div>
  );
}

/** The screen is in the path, so any of them can be linked to. */
export function surfaceFromPath(pathname: string): Surface {
  const found = SURFACES.find((option) => pathname.startsWith(`/${option.key}`));
  return found?.key ?? 'estate';
}

