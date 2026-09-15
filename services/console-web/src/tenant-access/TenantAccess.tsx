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

import type { AgentRecord, Api, ExecutionSafetyPolicy, Identity, SvidRecord } from '../lib/api';
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

  const canRevoke = identity.roles.includes('platform_engineer');
  const canEditPolicy = identity.roles.includes('platform_engineer');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [agentResult, svidResult, policyResult] = await Promise.all([
        api.agentRecords(identity),
        api.svidRecords(identity),
        api.executionSafetyPolicy(identity),
      ]);
      setAgents(agentResult.agents);
      setSvids(svidResult.svids);
      setSafetyPolicy(policyResult);
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
