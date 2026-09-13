/**
 * The Decommission Tracker -- §15.3.4, story S9.2.2, continuing F9.2.
 *
 * "Views on the Power BI report (Fabric activity) and the Tableau view (Metadata API)
 * are both captured weekly; the ratio is shown on the Decommission Tracker. Configurable
 * adoption threshold contributes to G4 readiness." Its own distinct screen from the
 * Release Board (confirmed by direct read of both rows in §15.3.4's own table) -- the
 * Release Board's own axis is pipeline stage and the parallel-run window; this screen's
 * own axis is per-MU real adoption against the configured threshold.
 *
 * Reading is open to any Artizent role, the report owner, or the client licence
 * administrator (`require_decommission_tracker_reader`, matching the server's own gate).
 * Setting the threshold is the Migration Architect's own action -- the identical
 * "owns configurable platform policy" posture the conformance and visual mapping
 * rulesets already use. Triggering a capture on demand is the Programme Manager's,
 * matching `RegressionMonitor.tsx`'s own "Schedule" action's persona.
 *
 * Decommission readiness itself (regression status, owner confirmation, the actual G4
 * card) is deliberately not computed or shown here -- `adoption.py`'s own docstring
 * explains why: the AC's own words are "contributes to," not "computes," G4 readiness.
 */

import { useCallback, useEffect, useState } from 'react';

import type { Api, DecommissionTrackerSite, Identity } from '../lib/api';
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

  const canSetThreshold = identity.roles.includes('migration_architect');
  const canCapture = identity.roles.includes('programme_manager');

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
          <span className="spacer" />
          {!canSetThreshold && !canCapture && (
            <span className="faint">
              Setting the threshold is the Migration Architect&rsquo;s action; capturing on demand is the
              Programme Manager&rsquo;s.
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
    </div>
  );
}
