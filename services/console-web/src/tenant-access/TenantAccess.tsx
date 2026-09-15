/**
 * Tenant & Access -- story S11.1.2, opens F11.1.
 *
 * "As a platform engineer, I want each agent to run under its own non-human identity
 * with least privilege, so that an agent's actions are attributable and its reach is
 * bounded. Identity issuance, rotation and revocation are visible in Tenant & Access."
 *
 * §15.3.7 names a much broader Admin screen under this title (roles, users/Entra groups,
 * site/domain scoping, service principals, secrets references -- "Assign role; scope;
 * rotate"). This screen builds only what this story makes real: the declared
 * `AgentRecord` catalog (spec §8.1, `agent_identity.py`) and the real SVID issuance/
 * rotation/revocation trail (`workload_identity.py`) -- the identical "closest real
 * thing, not the whole named screen" precedent this console already sets elsewhere
 * (`App.tsx`'s own docstring, ADR 0071).
 *
 * **Revoke is the platform engineer's own action** (`deps.py`'s `PlatformEngineerDep`,
 * this story's own literal persona) -- hidden, not disabled, for every other role, the
 * same hide-not-disable convention every other role-gated action in this console
 * already uses.
 */

import { useCallback, useEffect, useState } from 'react';

import type {
  AgentRecord,
  Api,
  ChainVerificationResult,
  DailyRoot,
  EvidenceChainStatus,
  ExecutionSafetyPolicy,
  Identity,
  RetentionState,
  SvidRecord,
} from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
}

const STATUS_LABEL: Record<SvidRecord['status'], string> = {
  active: 'Active',
  expired: 'Expired',
  revoked: 'Revoked',
};

export function TenantAccess({ api, identity }: Props): JSX.Element {
  const [agents, setAgents] = useState<AgentRecord[] | null>(null);
  const [svids, setSvids] = useState<SvidRecord[] | null>(null);
  const [safetyPolicy, setSafetyPolicy] = useState<ExecutionSafetyPolicy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedAgent, setSelectedAgent] = useState<AgentRecord | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [revokeReason, setRevokeReason] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [editingPolicy, setEditingPolicy] = useState(false);
  const [policyDraft, setPolicyDraft] = useState('');
  const [policyNotice, setPolicyNotice] = useState<string | null>(null);
  const [chainStatus, setChainStatus] = useState<EvidenceChainStatus | null>(null);
  const [dailyRoots, setDailyRoots] = useState<DailyRoot[] | null>(null);
  const [chainNotice, setChainNotice] = useState<string | null>(null);
  const [lastVerification, setLastVerification] = useState<ChainVerificationResult | null>(null);
  const [retention, setRetention] = useState<RetentionState | null>(null);
  const [editingRetention, setEditingRetention] = useState(false);
  const [retentionDraft, setRetentionDraft] = useState('');
  const [retentionNotice, setRetentionNotice] = useState<string | null>(null);

  const canRevoke = identity.roles.includes('platform_engineer');
  const canEditPolicy = identity.roles.includes('platform_engineer');
  const canOperateChain = identity.roles.includes('platform_engineer');
  const canEditRetention = identity.roles.includes('platform_engineer');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [agentResult, svidResult, policyResult, chainResult, rootsResult, retentionResult] = await Promise.all([
        api.agentRecords(identity),
        api.svidRecords(identity),
        api.executionSafetyPolicy(identity),
        api.evidenceChainStatus(identity),
        api.dailyRoots(identity),
        api.retentionState(identity),
      ]);
      setAgents(agentResult.agents);
      setSvids(svidResult.svids);
      setSafetyPolicy(policyResult);
      setChainStatus(chainResult);
      setDailyRoots(rootsResult.daily_roots);
      setRetention(retentionResult);
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : 'Tenant & Access could not be read.');
    } finally {
      setLoading(false);
    }
  }, [api, identity]);

  useEffect(() => {
    void load();
  }, [load]);

  const startRevoke = useCallback((jti: string) => {
    setRevoking(jti);
    setRevokeReason('');
    setNotice(null);
  }, []);

  const confirmRevoke = useCallback(async () => {
    if (!revoking) return;
    try {
      await api.revokeSvid(revoking, revokeReason, identity);
      setNotice('Revoked.');
      setRevoking(null);
      await load();
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The SVID could not be revoked.');
    }
  }, [api, identity, revoking, revokeReason, load]);

  const startEditPolicy = useCallback(() => {
    setPolicyDraft((safetyPolicy?.production_workspaces ?? []).join(', '));
    setPolicyNotice(null);
    setEditingPolicy(true);
  }, [safetyPolicy]);

  const savePolicy = useCallback(async () => {
    const workspaces = policyDraft.split(',').map((w) => w.trim()).filter(Boolean);
    try {
      const saved = await api.saveExecutionSafetyPolicy(workspaces, identity);
      setSafetyPolicy(saved);
      setEditingPolicy(false);
      setPolicyNotice(null);
    } catch (caught: unknown) {
      setPolicyNotice(caught instanceof ApiError ? caught.message : 'The policy could not be saved.');
    }
  }, [api, identity, policyDraft]);

  const advanceChain = useCallback(async () => {
    setChainNotice(null);
    try {
      const result = await api.advanceEvidenceChain(identity);
      setChainNotice(`Chained ${result.entries_added} new entries.`);
      await load();
    } catch (caught: unknown) {
      setChainNotice(caught instanceof ApiError ? caught.message : 'The chain could not be advanced.');
    }
  }, [api, identity, load]);

  const verifyChain = useCallback(async () => {
    setChainNotice(null);
    try {
      const result = await api.verifyEvidenceChain(identity);
      setLastVerification(result);
      setChainNotice(
        result.intact
          ? `Intact -- ${result.entries_checked} entries verified.`
          : `Break found at chain_seq ${result.first_break?.chain_seq}.`,
      );
    } catch (caught: unknown) {
      setChainNotice(caught instanceof ApiError ? caught.message : 'The chain could not be verified.');
    }
  }, [api, identity]);

  const startEditRetention = useCallback(() => {
    setRetentionDraft(String(retention?.retention_years ?? 7));
    setRetentionNotice(null);
    setEditingRetention(true);
  }, [retention]);

  const saveRetention = useCallback(async () => {
    const years = Number.parseInt(retentionDraft, 10);
    if (!Number.isFinite(years) || years < 1) {
      setRetentionNotice('Enter a whole number of years.');
      return;
    }
    try {
      await api.saveRetentionPolicy(years, identity);
      setEditingRetention(false);
      setRetentionNotice(null);
      await load();
    } catch (caught: unknown) {
      setRetentionNotice(caught instanceof ApiError ? caught.message : 'The retention policy could not be saved.');
    }
  }, [api, identity, retentionDraft, load]);

  return (
    <div className="workspace tenant-access-workspace">
      <section className="pane" aria-label="Agent catalog">
        <header className="pane-header">
          <h2>Agent catalog</h2>
          <span className="faint">spec §8.1 -- this platform's own declared agents</span>
        </header>
        <div className="pane-body">
          {error && <div className="banner">{error}</div>}
          {!error && loading && !agents && <p className="empty">Reading the agent catalog…</p>}
          {!error && agents && (
            <table className="estate tenant-access-agents-table">
              <thead>
                <tr>
                  <th>Agent</th><th>Version</th><th>Autonomy</th><th>Scope</th>
                  <th><span className="visually-hidden">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {agents.map((agent) => (
                  <tr key={agent.id}>
                    <td>
                      {agent.id}
                      {!agent.real && (
                        <span className="pill idle" title="Declared for completeness; no real running code exists for this agent yet">
                          not yet built
                        </span>
                      )}
                    </td>
                    <td className="faint mono">{agent.version}</td>
                    <td><span className="pill idle mono">{agent.autonomy}</span></td>
                    <td>
                      {agent.scope.unrestricted
                        ? <span className="faint">unrestricted</span>
                        : <span className="pill idle">narrowed</span>}
                    </td>
                    <td>
                      <button type="button" className="btn" onClick={() => setSelectedAgent(agent)}>
                        View charter
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="pane" aria-label="SVID issuance, rotation and revocation">
        <header className="pane-header">
          <h2>Workload identities (SVIDs)</h2>
          <span className="faint">disclosed, not yet connected to a live SPIRE server -- see the ADR</span>
        </header>
        <div className="pane-body">
          {notice && <p className="faint">{notice}</p>}
          {!error && svids && svids.length === 0 && <p className="empty">No SVID has been issued yet.</p>}
          {!error && svids && svids.length > 0 && (
            <table className="estate tenant-access-svids-table">
              <thead>
                <tr>
                  <th>Agent</th><th>Run</th><th>SPIFFE ID</th><th>Serial</th>
                  <th>Issued</th><th>Status</th>
                  <th><span className="visually-hidden">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {svids.map((svid) => (
                  <tr key={svid.jti}>
                    <td>{svid.agent_id}</td>
                    <td className="faint mono">{svid.run_id}</td>
                    <td className="faint mono">{svid.spiffe_id}</td>
                    <td>{svid.serial}</td>
                    <td className="faint">{svid.issued_at}</td>
                    <td><span className="pill idle">{STATUS_LABEL[svid.status]}</span></td>
                    <td>
                      {canRevoke && svid.status === 'active' && (
                        <button type="button" className="btn danger" onClick={() => startRevoke(svid.jti)}>
                          Revoke
                        </button>
                      )}
                      {svid.status === 'revoked' && svid.revocation_reason && (
                        <span className="faint">{svid.revocation_reason}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="pane" aria-label="Execution safety policy">
        <header className="pane-header">
          <h2>Execution safety</h2>
          <span className="faint">spec §18.2 -- which workspaces this tenant calls production</span>
        </header>
        <div className="pane-body">
          {policyNotice && <p className="faint">{policyNotice}</p>}
          {!error && loading && !safetyPolicy && <p className="empty">Reading the execution-safety policy…</p>}
          {!error && safetyPolicy && !editingPolicy && (
            <>
              <p>
                <strong>Production workspaces</strong>:{' '}
                {safetyPolicy.production_workspaces.length > 0
                  ? safetyPolicy.production_workspaces.join(', ')
                  : <span className="faint">(none named -- every workspace is open to any Parity Engineer)</span>}
              </p>
              <p className="faint">
                Execution against a named workspace is limited to the regression runner; version {safetyPolicy.version}.
              </p>
              {canEditPolicy && (
                <button type="button" className="btn" onClick={startEditPolicy}>Edit</button>
              )}
            </>
          )}
          {editingPolicy && (
            <div className="detail">
              <label>
                Production workspaces (comma-separated)
                <input
                  type="text"
                  value={policyDraft}
                  onChange={(e) => setPolicyDraft(e.target.value)}
                />
              </label>
              <footer className="statusbar">
                <button type="button" className="btn" onClick={() => setEditingPolicy(false)}>Cancel</button>
                <span className="spacer" />
                <button type="button" className="btn" onClick={() => void savePolicy()}>Save</button>
              </footer>
            </div>
          )}
        </div>
      </section>

      <section className="pane" aria-label="Evidence chain">
        <header className="pane-header">
          <h2>Evidence chain</h2>
          <span className="faint">spec §4.5/§18.4 -- an append-only, hash-linked record</span>
        </header>
        <div className="pane-body">
          {chainNotice && <p className="faint">{chainNotice}</p>}
          {!error && loading && !chainStatus && <p className="empty">Reading the chain's own status…</p>}
          {!error && chainStatus && (
            <>
              <p>
                <strong>Tip</strong>: {chainStatus.total_entries === 0
                  ? <span className="faint">nothing chained yet</span>
                  : <>seq {chainStatus.tip_seq}, <span className="mono faint">{chainStatus.tip_hash}</span></>}
              </p>
              {Object.keys(chainStatus.by_category).length > 0 && (
                <ul>
                  {Object.entries(chainStatus.by_category).map(([category, count]) => (
                    <li key={category}>{category}: {count}</li>
                  ))}
                </ul>
              )}
              {lastVerification && (
                <p className={lastVerification.intact ? 'faint' : 'banner'}>
                  {lastVerification.intact
                    ? `Last verification: intact (${lastVerification.entries_checked} entries).`
                    : `Last verification: BROKEN at chain_seq ${lastVerification.first_break?.chain_seq} -- ${lastVerification.first_break?.detail}`}
                </p>
              )}
              {canOperateChain && (
                <p>
                  <button type="button" className="btn" onClick={() => void advanceChain()}>Advance now</button>{' '}
                  <button type="button" className="btn" onClick={() => void verifyChain()}>Verify now</button>
                </p>
              )}
            </>
          )}
          {!error && dailyRoots && dailyRoots.length > 0 && (
            <table className="estate">
              <thead>
                <tr><th>Day</th><th>Entries</th><th>Root hash</th><th>Anchor</th></tr>
              </thead>
              <tbody>
                {dailyRoots.map((root) => (
                  <tr key={root.id}>
                    <td>{root.day}</td>
                    <td>{root.entry_count}</td>
                    <td className="faint mono">{root.root_hash}</td>
                    <td className="faint">{root.anchor_kind ?? 'not anchored'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="pane" aria-label="Retention">
        <header className="pane-header">
          <h2>Retention</h2>
          <span className="faint">spec §18.4 -- configurable per tenant</span>
        </header>
        <div className="pane-body">
          {retentionNotice && <p className="faint">{retentionNotice}</p>}
          {!error && loading && !retention && <p className="empty">Reading the retention policy…</p>}
          {!error && retention && !editingRetention && (
            <>
              <p><strong>Policy</strong>: {retention.policy} ({retention.retention_years} years)</p>
              <p className="faint">{retention.reason}</p>
              {canEditRetention && (
                <button type="button" className="btn" onClick={startEditRetention}>Edit</button>
              )}
            </>
          )}
          {editingRetention && (
            <div className="detail">
              <label>
                Retention (years)
                <input
                  type="number"
                  min={1}
                  value={retentionDraft}
                  onChange={(e) => setRetentionDraft(e.target.value)}
                />
              </label>
              <footer className="statusbar">
                <button type="button" className="btn" onClick={() => setEditingRetention(false)}>Cancel</button>
                <span className="spacer" />
                <button type="button" className="btn" onClick={() => void saveRetention()}>Save</button>
              </footer>
            </div>
          )}
        </div>
      </section>

      {selectedAgent && (
        <aside className="pane" aria-label="Agent charter">
          <header className="pane-header">
            <h3>{selectedAgent.id}</h3>
            <span className="spacer" />
            <button type="button" className="btn" onClick={() => setSelectedAgent(null)}>Close</button>
          </header>
          <div className="pane-body detail">
            <p><strong>Consumes</strong>: {selectedAgent.charter.consumes.join('; ') || '—'}</p>
            <p><strong>Produces</strong>: {selectedAgent.charter.produces.join('; ') || '—'}</p>
            <p><strong>Prohibited</strong>: {selectedAgent.charter.prohibited.join('; ') || '(none declared)'}</p>
            <p><strong>Owner</strong>: {selectedAgent.owner}</p>
            {!selectedAgent.scope.unrestricted && (
              <>
                <h4>Enforced scope</h4>
                {selectedAgent.scope.allowed_node_types && (
                  <p>Node types: {selectedAgent.scope.allowed_node_types.join(', ')}</p>
                )}
                {selectedAgent.scope.forbidden_properties.length > 0 && (
                  <p>Forbidden properties: {selectedAgent.scope.forbidden_properties.join(', ')}</p>
                )}
                {selectedAgent.scope.allowed_task_classes && (
                  <p>Gateway task classes: {selectedAgent.scope.allowed_task_classes.join(', ')}</p>
                )}
                {selectedAgent.scope.allowed_artefact_kinds && (
                  <p>Artefact kinds: {selectedAgent.scope.allowed_artefact_kinds.join(', ') || '(none)'}</p>
                )}
              </>
            )}
          </div>
        </aside>
      )}

      {revoking && (
        <aside className="pane" aria-label="Revoke SVID">
          <header className="pane-header">
            <h3>Revoke SVID</h3>
          </header>
          <div className="pane-body detail">
            <label>
              Reason
              <textarea
                value={revokeReason}
                onChange={(e) => setRevokeReason(e.target.value)}
                rows={3}
              />
            </label>
            <footer className="statusbar">
              <button type="button" className="btn" onClick={() => setRevoking(null)}>Cancel</button>
              <span className="spacer" />
              <button
                type="button"
                className="btn danger"
                disabled={revokeReason.trim().length < 8}
                onClick={() => void confirmRevoke()}
              >
                Confirm revoke
              </button>
            </footer>
          </div>
        </aside>
      )}
    </div>
  );
}
