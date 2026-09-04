/**
 * The right pane: the selected workbook, its scope history and its lineage mini-graph.
 *
 * The four actions §15.3.2 asks for live here. Three of them work; "Open MU" does not,
 * because the Cartographer creates Migration Units in E3 and nothing has. It is rendered
 * disabled with the reason the API gives, rather than hidden — a programme manager reading
 * the specification will look for it, and an absent button teaches them nothing.
 */

import type { Identity, Workbook, WorkbookDetail } from '../lib/api';
import { Lineage } from './Lineage';
import { Maybe, ParseStatus, count, percent } from './format';

interface Props {
  workbook: Workbook | null;
  detail: WorkbookDetail | null;
  loading: boolean;
  identity: Identity;
  onReTier: () => void;
  onWithdraw: () => void;
  onReinstate: () => void;
  onReharvest: () => void;
  reharvesting: boolean;
}

export function WorkbookPanel({
  workbook,
  detail,
  loading,
  identity,
  onReTier,
  onWithdraw,
  onReinstate,
  onReharvest,
  reharvesting,
}: Props): JSX.Element {
  if (!workbook) {
    return (
      <p className="empty">
        Select a workbook to see its lineage,
        <br />
        scope history and actions.
      </p>
    );
  }

  const isProgrammeManager = identity.roles.includes('programme_manager');
  const scopeTitle = isProgrammeManager
    ? undefined
    : 'Changing programme scope is the Programme Manager’s (spec §15.1)';
  const withdrawn = detail?.scope.current.withdrawn ?? workbook.withdrawn;

  return (
    <div className="detail">
      <div>
        <h3>{workbook.name}</h3>
        <p className="mono faint" style={{ margin: '2px 0 0' }}>
          {workbook.luid}
        </p>
      </div>

      <dl>
        <dt>Site</dt>
        <dd>
          <Maybe value={workbook.site} />
        </dd>
        <dt>Project</dt>
        <dd>
          <Maybe value={workbook.project} />
        </dd>
        <dt>Parse</dt>
        <dd>
          <ParseStatus workbook={workbook} />
        </dd>
        <dt>Views 90d</dt>
        <dd>
          {count(workbook.views_90d)}
          {workbook.distinct_viewers_90d !== null && (
            <span className="faint"> · {count(workbook.distinct_viewers_90d)} viewers</span>
          )}
        </dd>
        <dt>Owner</dt>
        <dd>
          <Maybe value={workbook.owner} />
        </dd>
        <dt>Calculations</dt>
        <dd>
          {count(workbook.calculated_fields)}{' '}
          <span className="faint" title="Class mix needs the Transpiler (E5/F5.1)">
            · unclassified
          </span>
        </dd>
        <dt>Tier</dt>
        <dd>
          {detail?.scope.current.tier ? (
            <span className="pill idle">{detail.scope.current.tier.toLowerCase()}</span>
          ) : (
            <span className="faint">not declared</span>
          )}
        </dd>
      </dl>

      {withdrawn && detail?.scope.current.withdrawn_reason && (
        <p className="pill bad" style={{ display: 'block', padding: '6px 10px', borderRadius: 6 }}>
          Withdrawn from scope — {detail.scope.current.withdrawn_reason}
        </p>
      )}

      <div>
        <h4 className="section-title">Actions</h4>
        <div className="actions-row">
          <button
            type="button"
            className="btn"
            disabled
            title={detail?.migration_unit_reason ?? 'No Migration Unit exists yet (E3).'}
          >
            Open MU
          </button>
          <button type="button" className="btn" onClick={onReharvest} disabled={reharvesting}>
            {reharvesting ? 'Harvesting…' : 'Re-harvest site'}
          </button>
          <button
            type="button"
            className="btn"
            onClick={onReTier}
            disabled={!isProgrammeManager}
            title={scopeTitle}
          >
            Re-tier…
          </button>
          {withdrawn ? (
            <button
              type="button"
              className="btn"
              onClick={onReinstate}
              disabled={!isProgrammeManager}
              title={scopeTitle}
            >
              Reinstate…
            </button>
          ) : (
            <button
              type="button"
              className="btn danger"
              onClick={onWithdraw}
              disabled={!isProgrammeManager}
              title={scopeTitle}
            >
              Withdraw…
            </button>
          )}
        </div>
        {!isProgrammeManager && (
          <p className="faint" style={{ marginTop: 6, fontSize: 11.5 }}>
            Re-tier and withdraw are the Programme Manager’s (§15.1).
          </p>
        )}
      </div>

      <div>
        <h4 className="section-title">Lineage</h4>
        {loading && !detail ? (
          <p className="faint">Reading…</p>
        ) : detail ? (
          <Lineage detail={detail} />
        ) : (
          <p className="faint">No lineage available.</p>
        )}
      </div>

      {detail && detail.scope.decisions.length > 0 && (
        <div>
          <h4 className="section-title">Scope decisions</h4>
          {detail.scope.decisions
            .slice()
            .reverse()
            .map((decision) => (
              <div className="decision" key={decision.id}>
                <div>
                  <strong>{label(decision.kind)}</strong>
                  {decision.to && ` → ${decision.to.toLowerCase()}`}
                  {decision.from && <span className="faint"> (from {decision.from.toLowerCase()})</span>}
                </div>
                <div>{decision.reason}</div>
                <div className="who">
                  {decision.decided_by} · {new Date(decision.decided_at).toLocaleString('en-GB')}
                </div>
              </div>
            ))}
        </div>
      )}

      {detail && (
        <p className="faint" style={{ fontSize: 11.5, margin: 0 }}>
          Parse quality {percent(workbook.parse_quality)} · read from the estate graph, not
          entered by anyone (§15.2).
        </p>
      )}
    </div>
  );
}

function label(kind: string): string {
  switch (kind) {
    case 'RE_TIER':
      return 'Tier';
    case 'WITHDRAW':
      return 'Withdrawn from scope';
    case 'REINSTATE':
      return 'Reinstated';
    default:
      return kind;
  }
}
