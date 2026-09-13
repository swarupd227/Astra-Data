/**
 * The Decommission Tracker -- §15.3.4, story S9.2.2 (continuing F9.2), extended by
 * S9.3.1 (opening F9.3) with the per-site readiness checklist, the G4 card, and the
 * per-MU owner confirmation.
 *
 * "Views on the Power BI report (Fabric activity) and the Tableau view (Metadata API)
 * are both captured weekly; the ratio is shown on the Decommission Tracker. Configurable
 * adoption threshold contributes to G4 readiness." Its own distinct screen from the
 * Release Board (confirmed by direct read of both rows in §15.3.4's own table) -- the
 * Release Board's own axis is pipeline stage and the parallel-run window; this screen's
 * own axis is per-MU real adoption against the configured threshold, plus (since S9.3.1)
 * per-site G4 readiness.
 *
 * Reading the tracker and a site's own G4 card is open to any Artizent role, the report
 * owner, or the client licence administrator (`require_decommission_tracker_reader`,
 * matching the server's own gate). Setting the adoption threshold is the Migration
 * Architect's own action; triggering a capture on demand is the Programme Manager's
 * (both S9.2.2). Confirming a single MU's own decommission readiness is the report
 * owner's (§14.4's own "owner confirmation received"); authorising or deferring G4 is
 * the licence administrator's alone (§13.1's own G4 row), countersigned by the
 * Programme Manager -- both S9.3.1.
 *
 * The G4 card is loaded on demand, one site at a time ("Open G4 card"), not eagerly for
 * every site on this screen's own load -- the identical single-lookup shape `G3Card.tsx`
 * already takes, avoiding an N+1 fetch the moment a real estate has many sites.
 */

import { useCallback, useEffect, useState } from 'react';

import type { Api, DecommissionTrackerSite, G4Card, Identity, ReadinessItem } from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString();
}

function meetsThresholdPill(meets: boolean | null): JSX.Element {
  if (meets === null) return <span className="pill idle">not yet captured</span>;
  return meets ? <span className="pill ok">meets threshold</span> : <span className="pill bad">below threshold</span>;
}

function readinessPill(item: ReadinessItem): JSX.Element {
  const evidence = Object.entries(item.evidence)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(', ');
  return (
    <span key={item.key} className={item.met ? 'pill ok' : 'pill bad'} title={evidence}>
      {item.label}
    </span>
  );
}

export function DecommissionTracker({ api, identity }: Props): JSX.Element {
  const [sites, setSites] = useState<DecommissionTrackerSite[]>([]);
  const [threshold, setThreshold] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const [thresholdInput, setThresholdInput] = useState('');
  const [savingThreshold, setSavingThreshold] = useState(false);
  const [thresholdNotice, setThresholdNotice] = useState<string | null>(null);

  const [capturing, setCapturing] = useState(false);
  const [captureNotice, setCaptureNotice] = useState<string | null>(null);

  const [confirmingWorkbookId, setConfirmingWorkbookId] = useState<string | null>(null);
  const [confirmNotice, setConfirmNotice] = useState<string | null>(null);

  const [openSiteId, setOpenSiteId] = useState<string | null>(null);
  const [g4Card, setG4Card] = useState<G4Card | null>(null);
  const [g4Loading, setG4Loading] = useState(false);
  const [g4Error, setG4Error] = useState<string | null>(null);

  const [rationale, setRationale] = useState('');
  const [countersignedBy, setCountersignedBy] = useState('');
  const [approving, setApproving] = useState(false);
  const [approveNotice, setApproveNotice] = useState<string | null>(null);

  const [deferReason, setDeferReason] = useState('');
  const [deferTargetDate, setDeferTargetDate] = useState('');
  const [deferring, setDeferring] = useState(false);
  const [deferNotice, setDeferNotice] = useState<string | null>(null);

  const canSetThreshold = identity.roles.includes('migration_architect');
  const canCapture = identity.roles.includes('programme_manager');
  const canConfirmDecommission = identity.roles.includes('client_report_owner');
  const canDecideG4 = identity.roles.includes('client_licence_admin');

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .decommissionTracker(identity)
      .then((response) => {
        if (!live) return;
        setSites(response.sites);
        setThreshold(response.threshold);
        setThresholdInput(String(response.threshold));
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(caught instanceof ApiError ? caught.message : 'The Decommission Tracker could not be read.');
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  const saveThreshold = useCallback(async () => {
    const parsed = Number(thresholdInput);
    if (Number.isNaN(parsed) || parsed < 0 || parsed > 1) {
      setThresholdNotice('Threshold must be a fraction between 0 and 1.');
      return;
    }
    setSavingThreshold(true);
    setThresholdNotice(null);
    try {
      const config = await api.setAdoptionConfig(parsed, identity);
      setThresholdNotice(`Adoption threshold set to ${formatPercent(config.threshold)}.`);
      reload();
    } catch (caught: unknown) {
      setThresholdNotice(
        caught instanceof ApiError
          ? caught.forbidden
            ? 'Setting the adoption threshold is the Migration Architect’s action.'
            : caught.message
          : 'The threshold could not be saved.',
      );
    } finally {
      setSavingThreshold(false);
    }
  }, [api, identity, reload, thresholdInput]);

  const captureNow = useCallback(async () => {
    setCapturing(true);
    setCaptureNotice(null);
    try {
      const result = await api.captureAdoption(identity);
      setCaptureNotice(`Captured ${result.count} snapshot${result.count === 1 ? '' : 's'} just now.`);
      reload();
    } catch (caught: unknown) {
      setCaptureNotice(
        caught instanceof ApiError
          ? caught.forbidden
            ? 'Triggering a capture is the Programme Manager’s action.'
            : caught.message
          : 'The capture could not be recorded.',
      );
    } finally {
      setCapturing(false);
    }
  }, [api, identity, reload]);

  const openG4 = useCallback(
    async (siteId: string) => {
      // Deliberately does not clear `approveNotice`/`deferNotice` -- `approveG4`/`deferG4`
      // both call this again afterward to refresh the card, and a reload must not erase
      // the very success/failure notice it just set. The "Open G4 card" button clears
      // both explicitly, since opening a fresh site is a new session.
      setOpenSiteId(siteId);
      setG4Card(null);
      setG4Error(null);
      setG4Loading(true);
      try {
        const card = await api.g4Card(siteId, identity);
        setG4Card(card);
      } catch (caught: unknown) {
        setG4Error(caught instanceof ApiError ? caught.message : 'The G4 card could not be read.');
      } finally {
        setG4Loading(false);
      }
    },
    [api, identity],
  );

  const closeG4 = useCallback(() => {
    setOpenSiteId(null);
    setG4Card(null);
    setG4Error(null);
    setRationale('');
    setCountersignedBy('');
    setApproveNotice(null);
    setDeferReason('');
    setDeferTargetDate('');
    setDeferNotice(null);
  }, []);

  const confirmMu = useCallback(
    async (workbookId: string) => {
      setConfirmingWorkbookId(workbookId);
      setConfirmNotice(null);
      try {
        await api.confirmDecommission(workbookId, identity);
        setConfirmNotice(`Confirmed for ${workbookId}.`);
        if (openSiteId) await openG4(openSiteId);
      } catch (caught: unknown) {
        setConfirmNotice(
          caught instanceof ApiError
            ? caught.forbidden
              ? 'Confirming decommission readiness is the report owner’s action.'
              : caught.message
            : 'The confirmation could not be recorded.',
        );
      } finally {
        setConfirmingWorkbookId(null);
      }
    },
    [api, identity, openSiteId, openG4],
  );

  const approveG4 = useCallback(async () => {
    if (!openSiteId) return;
    setApproving(true);
    setApproveNotice(null);
    try {
      const result = await api.approveG4(openSiteId, rationale, countersignedBy, identity);
      setApproveNotice(`Approved -- ${result.archived_count} source workbook(s) archived.`);
      await openG4(openSiteId);
      reload();
    } catch (caught: unknown) {
      setApproveNotice(
        caught instanceof ApiError
          ? caught.forbidden
            ? 'Authorising G4 is the licence administrator’s action.'
            : caught.message
          : 'The approval could not be recorded.',
      );
    } finally {
      setApproving(false);
    }
  }, [api, identity, openSiteId, rationale, countersignedBy, openG4, reload]);

  const deferG4 = useCallback(async () => {
    if (!openSiteId) return;
    setDeferring(true);
    setDeferNotice(null);
    try {
      await api.deferG4(openSiteId, deferReason, deferTargetDate, identity);
      setDeferNotice('Deferred with the reason and target date recorded.');
      await openG4(openSiteId);
    } catch (caught: unknown) {
      setDeferNotice(
        caught instanceof ApiError
          ? caught.forbidden
            ? 'Deferring G4 is the licence administrator’s action.'
            : caught.message
          : 'The deferral could not be recorded.',
      );
    } finally {
      setDeferring(false);
    }
  }, [api, identity, openSiteId, deferReason, deferTargetDate, openG4]);

  return (
    <div className="workspace decommission-tracker">
      <section className="pane" aria-label="Decommission Tracker">
        <header className="pane-header">
          <h2>Decommission Tracker</h2>
          {threshold !== null && <span className="pill idle mono">threshold {formatPercent(threshold)}</span>}
        </header>
        <div className="pane-body">
          {error ? (
            <div className="banner">{error}</div>
          ) : loading && sites.length === 0 ? (
            <p className="empty">Reading the Decommission Tracker…</p>
          ) : sites.length === 0 ? (
            <p className="empty">No site has a released workbook yet.</p>
          ) : (
            sites.map((site) => (
              <div key={site.site_id} className="release-train">
                <h3>
                  {site.name}{' '}
                  <span className="faint">
                    ({site.meeting_threshold_count} of {site.released_mu_count} meeting threshold)
                  </span>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => {
                      setApproveNotice(null);
                      setDeferNotice(null);
                      void openG4(site.site_id);
                    }}
                  >
                    Open G4 card
                  </button>
                </h3>
                <table className="estate">
                  <caption className="visually-hidden">
                    Every released MU at {site.name}, its own source/target views and adoption ratio
                  </caption>
                  <thead>
                    <tr>
                      <th>MU</th>
                      <th>Source views</th>
                      <th>Target views</th>
                      <th>Ratio</th>
                      <th>Captured</th>
                      <th>Status</th>
                      {canConfirmDecommission && <th>Owner confirmation</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {site.mus.map((mu) => (
                      <tr key={mu.workbook_id}>
                        <td>{mu.name}</td>
                        <td className="numeric">
                          {mu.snapshot === null ? (
                            <span className="faint">—</span>
                          ) : mu.snapshot.source_views === null ? (
                            <span className="faint" title="The source adapter's own usage capability is absent.">
                              unavailable
                            </span>
                          ) : (
                            mu.snapshot.source_views
                          )}
                        </td>
                        <td className="numeric">{mu.snapshot === null ? <span className="faint">—</span> : mu.snapshot.target_views}</td>
                        <td className="numeric">
                          {mu.snapshot === null || mu.snapshot.ratio === null ? (
                            <span className="faint">—</span>
                          ) : (
                            formatPercent(mu.snapshot.ratio)
                          )}
                        </td>
                        <td>{mu.snapshot === null ? <span className="faint">—</span> : formatDate(mu.snapshot.captured_at)}</td>
                        <td>{meetsThresholdPill(mu.snapshot?.meets_threshold ?? null)}</td>
                        {canConfirmDecommission && (
                          <td>
                            <button
                              type="button"
                              className="btn"
                              onClick={() => void confirmMu(mu.workbook_id)}
                              disabled={confirmingWorkbookId === mu.workbook_id}
                            >
                              {confirmingWorkbookId === mu.workbook_id ? 'Confirming…' : 'Confirm'}
                            </button>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))
          )}
        </div>
        <footer className="statusbar">
          {canCapture && (
            <button type="button" className="btn" onClick={() => void captureNow()} disabled={capturing}>
              {capturing ? 'Capturing…' : 'Capture now'}
            </button>
          )}
          {captureNotice && <span>{captureNotice}</span>}
          {confirmNotice && <span>{confirmNotice}</span>}
          <span className="spacer" />
          {!canSetThreshold && !canCapture && !canConfirmDecommission && !canDecideG4 && (
            <span className="faint">
              Setting the threshold is the Migration Architect&rsquo;s action; capturing on demand is the
              Programme Manager&rsquo;s; confirming an MU is the report owner&rsquo;s; deciding G4 is the
              licence administrator&rsquo;s.
            </span>
          )}
        </footer>
      </section>

      {canSetThreshold && (
        <section className="pane" aria-label="Adoption threshold">
          <header className="pane-header">
            <h2>Adoption threshold</h2>
          </header>
          <div className="pane-body">
            <label>
              Fraction of source views a released report must reach (0–1)
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={thresholdInput}
                onChange={(e) => setThresholdInput(e.target.value)}
              />
            </label>
            <div className="row-actions">
              <button type="button" className="btn primary" onClick={() => void saveThreshold()} disabled={savingThreshold}>
                {savingThreshold ? 'Saving…' : 'Save threshold'}
              </button>
            </div>
          </div>
          <footer className="statusbar">{thresholdNotice && <span>{thresholdNotice}</span>}</footer>
        </section>
      )}

      {openSiteId && (
        <section className="pane" aria-label="G4 card">
          <header className="pane-header">
            <h2>G4 Decommission{g4Card ? `: ${g4Card.name}` : ''}</h2>
            <button type="button" className="btn" onClick={closeG4}>
              Close
            </button>
          </header>
          <div className="pane-body">
            {g4Error ? (
              <div className="banner">{g4Error}</div>
            ) : g4Loading && !g4Card ? (
              <p className="empty">Reading the G4 card…</p>
            ) : g4Card ? (
              <>
                <p>
                  <span className={g4Card.ready ? 'pill ok' : 'pill bad'}>
                    {g4Card.ready ? 'ready for G4' : 'not yet ready'}
                  </span>{' '}
                  {g4Card.licence_tier && <span className="pill idle mono">tier {g4Card.licence_tier}</span>}{' '}
                  <span className="pill idle mono">{g4Card.released_mu_count} released MU(s)</span>
                </p>
                <p>{g4Card.checklist.map(readinessPill)}</p>
                <p>{g4Card.confirmation_text}</p>
                {g4Card.latest_decision && (
                  <p className="faint">
                    Latest decision: {g4Card.latest_decision.decision} by {g4Card.latest_decision.approver}
                    {g4Card.latest_decision.countersigner ? `, countersigned by ${g4Card.latest_decision.countersigner}` : ''}
                    {g4Card.latest_decision.target_date ? `, target date ${formatDate(g4Card.latest_decision.target_date)}` : ''}
                  </p>
                )}

                {canDecideG4 && (
                  <>
                    <div>
                      <label>
                        Rationale
                        <input type="text" value={rationale} onChange={(e) => setRationale(e.target.value)} />
                      </label>
                      <label>
                        Countersigned by (Programme Manager)
                        <input type="text" value={countersignedBy} onChange={(e) => setCountersignedBy(e.target.value)} />
                      </label>
                      <div className="row-actions">
                        <button type="button" className="btn primary" onClick={() => void approveG4()} disabled={approving}>
                          {approving ? 'Approving…' : 'Authorise decommission (G4)'}
                        </button>
                      </div>
                      {approveNotice && <p>{approveNotice}</p>}
                    </div>

                    <div>
                      <label>
                        Defer reason
                        <input type="text" value={deferReason} onChange={(e) => setDeferReason(e.target.value)} />
                      </label>
                      <label>
                        New target date
                        <input type="datetime-local" value={deferTargetDate} onChange={(e) => setDeferTargetDate(e.target.value)} />
                      </label>
                      <div className="row-actions">
                        <button type="button" className="btn" onClick={() => void deferG4()} disabled={deferring}>
                          {deferring ? 'Deferring…' : 'Defer'}
                        </button>
                      </div>
                      {deferNotice && <p>{deferNotice}</p>}
                    </div>
                  </>
                )}
              </>
            ) : null}
          </div>
          <footer className="statusbar">
            {!canDecideG4 && (
              <span className="faint">Authorising or deferring G4 is the licence administrator&rsquo;s action.</span>
            )}
          </footer>
        </section>
      )}
    </div>
  );
}
