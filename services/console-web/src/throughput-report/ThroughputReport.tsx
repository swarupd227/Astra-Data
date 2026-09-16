/**
 * Throughput and cost metrics -- story S6.2.3.
 *
 * §backlog's own AC: "custodians live per week, agent acceptance and credits per
 * custodian per day, so that reporting is generated... Weekly report exported for the
 * client cadence... Cost per custodian visible from query tags."
 *
 * Three vocabulary translations confirmed by the user before any code was written
 * (see `throughput_metrics.py`'s own module docstring for the full research trail):
 * custodian = this platform's own `Site`, credits = real LLM token cost, agent
 * acceptance = the existing MU-acceptance fact (`commercial_ledger`). "Generated
 * weekly" is a `POST`-triggered snapshot a Programme Manager asks for, the identical
 * reading `status-pack/StatusPack.tsx` already established, not a background scheduler
 * this console has no way to run. Export is a real CSV, not a second JSON read.
 */

import { useCallback, useEffect, useState } from 'react';

import { downloadBlob } from '../lib/download';
import type { Api, Identity, ThroughputReportData } from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
  liveTick?: number;
}

export function ThroughputReport({ api, identity, liveTick }: Props): JSX.Element {
  const [report, setReport] = useState<ThroughputReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const canGenerate = identity.roles.includes('programme_manager');

  const load = useCallback(() => {
    let live = true;
    setLoading(true);
    api
      .throughputReport(identity)
      .then((response) => {
        if (!live) return;
        setReport(response);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        // The service answers "no throughput report has been generated yet" as a real
        // 400 (`InvalidRequestError`) -- the identical "premature, not missing" reading
        // `StatusPack.tsx` already gives its own equivalent empty state.
        if (caught instanceof ApiError && caught.status === 400) {
          setReport(null);
          setError(null);
        } else {
          setError(caught instanceof ApiError ? caught.message : 'The throughput report could not be read.');
        }
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity]);

  useEffect(() => load(), [load, liveTick]);

  const generate = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const response = await api.generateThroughputReport(identity);
      setReport(response);
      setNotice(`Generated (trailing ${response.weeks} weeks / ${response.days} days).`);
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The report could not be generated.');
    } finally {
      setBusy(false);
    }
  }, [api, identity]);

  const exportCsv = useCallback(async () => {
    setBusy(true);
    try {
      const blob = await api.throughputReportCsv(identity);
      downloadBlob(blob, 'throughput-report.csv');
    } catch {
      setNotice('The CSV could not be exported.');
    } finally {
      setBusy(false);
    }
  }, [api, identity]);

  return (
    <div className="workspace throughput-report">
      <section className="pane" aria-label="Throughput and cost">
        <header className="pane-header">
          <h2>Throughput &amp; Cost</h2>
          {report && (
            <span className="pill idle mono">
              generated {new Date(report.generated_at).toLocaleString()}
            </span>
          )}
        </header>
        <div className="pane-body">
          {error ? (
            <div className="banner">{error}</div>
          ) : loading && !report ? (
            <p className="empty">Reading the throughput report…</p>
          ) : !report ? (
            <p className="empty">No throughput report has been generated yet.</p>
          ) : (
            <div className="detail">
              <h3>Custodians live per week</h3>
              <table className="estate">
                <thead>
                  <tr>
                    <th>Week of</th>
                    <th>Custodians live</th>
                  </tr>
                </thead>
                <tbody>
                  {report.custodians_live_per_week.length === 0 ? (
                    <tr>
                      <td colSpan={2} className="empty">
                        No custodian activity in the trailing {report.weeks} weeks yet.
                      </td>
                    </tr>
                  ) : (
                    report.custodians_live_per_week.map((row) => (
                      <tr key={row.week_of}>
                        <td>{row.week_of}</td>
                        <td>{row.custodians_live}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>

              <h3>Agent acceptance per custodian per day</h3>
              <table className="estate">
                <thead>
                  <tr>
                    <th>Day</th>
                    <th>Custodian</th>
                    <th>Accepted</th>
                  </tr>
                </thead>
                <tbody>
                  {report.agent_acceptance_per_custodian_per_day.length === 0 ? (
                    <tr>
                      <td colSpan={3} className="empty">
                        No MU acceptances in the trailing {report.days} days yet.
                      </td>
                    </tr>
                  ) : (
                    report.agent_acceptance_per_custodian_per_day.map((row) => (
                      <tr key={`${row.day}-${row.site_id ?? 'none'}`}>
                        <td>{row.day}</td>
                        <td>{row.custodian}</td>
                        <td>{row.accepted}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>

              <h3>Credits (real LLM cost) per custodian per day</h3>
              <table className="estate">
                <thead>
                  <tr>
                    <th>Day</th>
                    <th>Custodian</th>
                    <th>Calls</th>
                    <th>Tokens in</th>
                    <th>Tokens out</th>
                    <th>Credits (USD)</th>
                  </tr>
                </thead>
                <tbody>
                  {report.credits_per_custodian_per_day.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="empty">
                        No query-tagged gateway calls in the trailing {report.days} days yet.
                      </td>
                    </tr>
                  ) : (
                    report.credits_per_custodian_per_day.map((row) => (
                      <tr key={`${row.day}-${row.site_id ?? 'none'}`}>
                        <td>{row.day}</td>
                        <td>{row.custodian}</td>
                        <td>{row.calls}</td>
                        <td>{row.tokens_in.toLocaleString()}</td>
                        <td>{row.tokens_out.toLocaleString()}</td>
                        <td>${row.credits_usd.toFixed(4)}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <footer className="statusbar">
          {notice && <span>{notice}</span>}
          <span className="spacer" />
          {canGenerate ? (
            <button type="button" className="btn" disabled={busy} onClick={() => void generate()}>
              Generate
            </button>
          ) : (
            <span className="faint">Generating is the Programme Manager&rsquo;s own action.</span>
          )}
          {report && (
            <button type="button" className="btn" disabled={busy} onClick={() => void exportCsv()}>
              Export as CSV
            </button>
          )}
        </footer>
      </section>
    </div>
  );
}
