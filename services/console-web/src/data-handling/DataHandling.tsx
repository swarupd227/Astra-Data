/**
 * Data Handling -- story S11.4.1, opens F11.4.
 *
 * "As an InfoSec reviewer, I want a Data Handling screen that states exactly what
 * reaches a model endpoint and lets me confirm it, so that the inference boundary is a
 * signed position, not an assurance."
 *
 * §15.1 names this the InfoSec reviewer's own landing screen; §15.3.7 names it its own
 * distinct Admin row, separate from Tenant & Access -- this is a new top-level surface,
 * the identical "own top-level surface, not an Admin sub-screen" call the Tolerance
 * Charter and Pattern Library each already made for the same "spec files this under a
 * screen that does not exist yet" situation (`ToleranceCharter.tsx`'s own docstring).
 *
 * The inference boundary table (what is sent / never sent) is fixed, spec-verbatim
 * content -- not editable here. Only the signable position (providers, retention
 * terms, redaction rules) is real, versioned, platform-engineer-editable tenant
 * configuration; editing it real-invalidates whatever signature was last recorded
 * (`signed` is a server-computed comparison, never a flag this screen sets itself).
 *
 * "Sign boundary" is deliberately not gated the same way every other action on this
 * screen is -- it is the InfoSec reviewer's own confirmation alone, hidden (not
 * disabled) for every other role including Artizent's own platform engineer, the same
 * hide-not-disable convention every gated action in this console already uses.
 *
 * Story S11.4.2 added a fourth pane, Content logging -- off by default; enabling and
 * disabling it are both the InfoSec reviewer's own action too, the identical gate
 * "Sign boundary" already has and for the identical reason: this is the client's own
 * real control over whether literal request/response text is ever persisted, not
 * Artizent's to switch on for them. The duration field is real, bounded, InfoSec-
 * specified input (not a single fixed window) -- capped both client-side and, for
 * real, server-side at `MAX_CONTENT_LOGGING_MINUTES`.
 */

import { useCallback, useEffect, useState } from 'react';

import type {
  Api,
  BoundaryTestResult,
  DataHandlingProvider,
  DataHandlingStatus,
  Identity,
} from '../lib/api';
import { ApiError } from '../lib/api';

const MAX_CONTENT_LOGGING_MINUTES = 1440;

interface Props {
  api: Api;
  identity: Identity;
}

export function DataHandling({ api, identity }: Props): JSX.Element {
  const [status, setStatus] = useState<DataHandlingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [editing, setEditing] = useState(false);
  const [providersDraft, setProvidersDraft] = useState<DataHandlingProvider[]>([]);
  const [retentionDraft, setRetentionDraft] = useState('');
  const [redactionDraft, setRedactionDraft] = useState('');
  const [editNotice, setEditNotice] = useState<string | null>(null);

  const [signing, setSigning] = useState(false);
  const [signNotice, setSignNotice] = useState<string | null>(null);

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<BoundaryTestResult | null>(null);

  const [durationMinutes, setDurationMinutes] = useState('60');
  const [loggingBusy, setLoggingBusy] = useState(false);
  const [loggingNotice, setLoggingNotice] = useState<string | null>(null);

  const canEdit = identity.roles.includes('platform_engineer');
  const canSign = identity.roles.includes('client_infosec_reviewer');
  const canControlContentLogging = identity.roles.includes('client_infosec_reviewer');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.dataHandling(identity);
      setStatus(result);
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : 'Data Handling could not be read.');
    } finally {
      setLoading(false);
    }
  }, [api, identity]);

  useEffect(() => {
    void load();
  }, [load]);

  const startEdit = useCallback(() => {
    if (!status) return;
    setProvidersDraft(status.position.providers.map((p) => ({ ...p })));
    setRetentionDraft(status.position.retention_terms);
    setRedactionDraft(status.position.redaction_rules.join('\n'));
    setEditNotice(null);
    setEditing(true);
  }, [status]);

  const saveEdit = useCallback(async () => {
    try {
      await api.saveDataHandlingPosition(
        {
          providers: providersDraft,
          retention_terms: retentionDraft,
          redaction_rules: redactionDraft.split('\n').map((r) => r.trim()).filter(Boolean),
        },
        identity,
      );
      setEditing(false);
      setTestResult(null);
      await load();
    } catch (caught: unknown) {
      setEditNotice(caught instanceof ApiError ? caught.message : 'The position could not be saved.');
    }
  }, [api, identity, providersDraft, retentionDraft, redactionDraft, load]);

  const sign = useCallback(async () => {
    setSigning(true);
    setSignNotice(null);
    try {
      await api.signDataHandlingBoundary(identity);
      setSignNotice('Signed.');
      await load();
    } catch (caught: unknown) {
      setSignNotice(caught instanceof ApiError ? caught.message : 'The boundary could not be signed.');
    } finally {
      setSigning(false);
    }
  }, [api, identity, load]);

  const runBoundaryTest = useCallback(async () => {
    setTesting(true);
    try {
      const result = await api.verifyDataHandlingBoundary(identity);
      setTestResult(result);
    } catch (caught: unknown) {
      setTestResult({
        passed: false, sentinel: '', checked_at: new Date().toISOString(),
        detail: caught instanceof ApiError ? caught.message : 'The boundary test could not be run.',
      });
    } finally {
      setTesting(false);
    }
  }, [api, identity]);

  const enableLogging = useCallback(async () => {
    const minutes = Number.parseInt(durationMinutes, 10);
    if (!Number.isFinite(minutes) || minutes < 1 || minutes > MAX_CONTENT_LOGGING_MINUTES) {
      setLoggingNotice(`Enter a whole number of minutes from 1 to ${MAX_CONTENT_LOGGING_MINUTES}.`);
      return;
    }
    setLoggingBusy(true);
    setLoggingNotice(null);
    try {
      await api.enableContentLogging(minutes, identity);
      await load();
    } catch (caught: unknown) {
      setLoggingNotice(caught instanceof ApiError ? caught.message : 'Content logging could not be enabled.');
    } finally {
      setLoggingBusy(false);
    }
  }, [api, identity, durationMinutes, load]);

  const disableLogging = useCallback(async () => {
    setLoggingBusy(true);
    setLoggingNotice(null);
    try {
      await api.disableContentLogging(identity);
      await load();
    } catch (caught: unknown) {
      setLoggingNotice(caught instanceof ApiError ? caught.message : 'Content logging could not be disabled.');
    } finally {
      setLoggingBusy(false);
    }
  }, [api, identity, load]);

  return (
    <div className="workspace data-handling-workspace">
      <section className="pane" aria-label="Providers and retention">
        <header className="pane-header">
          <h2>Providers and retention</h2>
          <span className="faint">spec §18.3 -- configured per tenant</span>
        </header>
        <div className="pane-body">
          {error && <div className="banner">{error}</div>}
          {editNotice && <p className="faint">{editNotice}</p>}
          {!error && loading && !status && <p className="empty">Reading the data-handling position…</p>}
          {!error && status && !editing && (
            <>
              <table className="estate">
                <thead>
                  <tr><th>Provider</th><th>Model</th><th>Region</th></tr>
                </thead>
                <tbody>
                  {status.position.providers.map((provider, index) => (
                    <tr key={`${provider.name}-${index}`}>
                      <td>{provider.name}</td>
                      <td className="mono faint">{provider.model}</td>
                      <td className="faint">{provider.region ?? 'not applicable'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p><strong>Retention terms</strong></p>
              <p className="faint">{status.position.retention_terms}</p>
              <p className="faint">version {status.position.version}</p>
              {canEdit && <button type="button" className="btn" onClick={startEdit}>Edit</button>}
            </>
          )}
          {editing && (
            <div className="detail">
              {providersDraft.map((provider, index) => (
                <div key={index} className="statusbar">
                  <label>
                    Name
                    <input
                      type="text" value={provider.name}
                      onChange={(e) => setProvidersDraft((prev) =>
                        prev.map((p, i) => (i === index ? { ...p, name: e.target.value } : p)))}
                    />
                  </label>
                  <label>
                    Model
                    <input
                      type="text" value={provider.model}
                      onChange={(e) => setProvidersDraft((prev) =>
                        prev.map((p, i) => (i === index ? { ...p, model: e.target.value } : p)))}
                    />
                  </label>
                  <label>
                    Region
                    <input
                      type="text" value={provider.region ?? ''}
                      onChange={(e) => setProvidersDraft((prev) =>
                        prev.map((p, i) => (i === index ? { ...p, region: e.target.value || null } : p)))}
                    />
                  </label>
                </div>
              ))}
              <label>
                Retention terms
                <textarea
                  value={retentionDraft} rows={3}
                  onChange={(e) => setRetentionDraft(e.target.value)}
                />
              </label>
              <label>
                Redaction rules (one per line)
                <textarea
                  value={redactionDraft} rows={4}
                  onChange={(e) => setRedactionDraft(e.target.value)}
                />
              </label>
              <footer className="statusbar">
                <button type="button" className="btn" onClick={() => setEditing(false)}>Cancel</button>
                <span className="spacer" />
                <button type="button" className="btn" onClick={() => void saveEdit()}>
                  Save (invalidates the current signature)
                </button>
              </footer>
            </div>
          )}
        </div>
      </section>

      <section className="pane" aria-label="Inference boundary">
        <header className="pane-header">
          <h2>Inference boundary</h2>
          <span className="faint">spec §18.3 -- what reaches a model endpoint, fixed by this platform's own design</span>
        </header>
        <div className="pane-body">
          {!error && status && (
            <>
              <p><strong>Sent</strong></p>
              <ul>
                {status.inference_boundary_table.sent.map((item) => <li key={item}>{item}</li>)}
              </ul>
              <p><strong>Never sent</strong></p>
              <ul>
                {status.inference_boundary_table.never_sent.map((item) => <li key={item}>{item}</li>)}
              </ul>
              <p><strong>Redaction rules</strong></p>
              <ul>
                {status.position.redaction_rules.map((rule) => <li key={rule}>{rule}</li>)}
              </ul>
            </>
          )}
        </div>
      </section>

      <section className="pane" aria-label="Sign-off and boundary test">
        <header className="pane-header">
          <h2>Sign-off and boundary test</h2>
          <span className="faint">spec §15.3.7 -- a signed position, not an assurance</span>
        </header>
        <div className="pane-body">
          {signNotice && <p className="faint">{signNotice}</p>}
          {!error && status && (
            <>
              {status.signed ? (
                <p>
                  <span className="pill ok">Signed</span>{' '}
                  by {status.signoff?.reviewer} at version {status.signoff?.position_version}, {status.signoff?.signed_at}
                </p>
              ) : (
                <p>
                  <span className="pill bad">Not signed</span>{' '}
                  {status.signoff
                    ? `-- the position changed since version ${status.signoff.position_version} was signed; re-sign required.`
                    : '-- this position has never been signed.'}
                </p>
              )}
              {canSign && (
                <button type="button" className="btn" disabled={signing || status.signed} onClick={() => void sign()}>
                  {signing ? 'Signing…' : 'Sign boundary'}
                </button>
              )}
            </>
          )}

          <hr />

          <button type="button" className="btn" disabled={testing} onClick={() => void runBoundaryTest()}>
            {testing ? 'Running…' : 'Run boundary test'}
          </button>
          {testResult && (
            <p className={testResult.passed ? 'faint' : 'banner'}>
              {testResult.passed ? <span className="pill ok">PASS</span> : <span className="pill bad">FAIL</span>}{' '}
              {testResult.detail}
            </p>
          )}
        </div>
      </section>

      <section className="pane" aria-label="Content logging">
        <header className="pane-header">
          <h2>Content logging</h2>
          <span className="faint">spec §18.3 -- off by default, InfoSec-granted for a bounded window</span>
        </header>
        <div className="pane-body">
          {loggingNotice && <p className="faint">{loggingNotice}</p>}
          {!error && status && (
            <>
              {status.content_logging_grant?.active ? (
                <p>
                  <span className="pill ok">Active</span>{' '}
                  enabled by {status.content_logging_grant.enabled_by}, expires {status.content_logging_grant.expires_at}
                </p>
              ) : (
                <p>
                  <span className="pill idle">Off</span>{' '}
                  {status.content_logging_grant
                    ? `-- last active window ended ${
                        status.content_logging_grant.revoked_at ?? status.content_logging_grant.expires_at
                      }.`
                    : '-- no window has ever been granted.'}
                </p>
              )}
              <p className="faint">
                While off, every gateway request and response is still logged by its own hash; only the
                literal text is withheld.
              </p>
              {canControlContentLogging && (
                <>
                  {!status.content_logging_grant?.active ? (
                    <div className="statusbar">
                      <label>
                        Minutes
                        <input
                          type="number" min={1} max={MAX_CONTENT_LOGGING_MINUTES}
                          value={durationMinutes}
                          onChange={(e) => setDurationMinutes(e.target.value)}
                        />
                      </label>
                      <button type="button" className="btn" disabled={loggingBusy} onClick={() => void enableLogging()}>
                        {loggingBusy ? 'Enabling…' : 'Enable content logging'}
                      </button>
                    </div>
                  ) : (
                    <button type="button" className="btn danger" disabled={loggingBusy} onClick={() => void disableLogging()}>
                      {loggingBusy ? 'Disabling…' : 'Disable now'}
                    </button>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
