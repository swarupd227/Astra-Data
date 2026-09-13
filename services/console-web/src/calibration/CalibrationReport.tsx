/**
 * The Calibration Report -- story S10.2.1, opening F10.2.
 *
 * §15.3.1's own row: *"The §14.3 report rendered: class-mix by tier, coverage gauges,
 * parity rates, C4 reasons histogram, family count, cost per report, stage timings;
 * comparison panel to the pre-calibration assumptions. | Sign report; open any MU in the
 * set; export."* See `calibration_wave.py`'s own module docstring for why "per F13.2" in
 * the backlog AC is read as F13.1/S13.1.2's report instead (the spec's own row content
 * matches that story almost verbatim), and for which two named fields — elapsed time per
 * stage, executor strategy mix — are honestly not built, disclosed on the response
 * rather than fabricated.
 *
 * "Sign report" is the Programme Manager's own action (§15.3.1) with a typed
 * countersigner name for the client analytics lead — the identical "no separate
 * authenticated second action" disclosed limitation `G3Card.tsx`'s own Approve form
 * already carries for its own countersigner field. Hidden, not disabled, for anyone
 * else, the identical hide-not-disable convention every other role-gated action in this
 * console already uses.
 *
 * "Open any MU in the set" is not built here — no real multi-item set of MUs this report
 * covers is named anywhere in its own real data (the report is an estate-wide rollup,
 * not a per-MU list); a reader who wants one real workbook already has the Estate
 * Explorer.
 */

import { useCallback, useEffect, useState } from 'react';

import { downloadBlob } from '../lib/download';
import type { Api, CalibrationReportResponse, Identity } from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
  liveTick?: number;
}

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`;
}

function currency(value: number): string {
  return `$${Math.round(value).toLocaleString('en-US')}`;
}

export function CalibrationReport({ api, identity, liveTick }: Props): JSX.Element {
  const [result, setResult] = useState<CalibrationReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [countersignedBy, setCountersignedBy] = useState('');
  const [signing, setSigning] = useState(false);
  const [signNotice, setSignNotice] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const canSign = identity.roles.includes('programme_manager');

  const load = useCallback(() => {
    let live = true;
    setLoading(true);
    api
      .calibrationReport(identity)
      .then((response) => {
        if (!live) return;
        setResult(response);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(caught instanceof ApiError ? caught.message : 'The Calibration Report could not be read.');
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity]);

  useEffect(() => load(), [load, liveTick]);

  const sign = useCallback(async () => {
    setSigning(true);
    setSignNotice(null);
    try {
      const baseline = await api.signCalibrationReport(countersignedBy, identity);
      setSignNotice(`Signed as version ${baseline.version} -- countersigned by ${baseline.countersigned_by}.`);
      setCountersignedBy('');
      load();
    } catch (caught: unknown) {
      setSignNotice(
        caught instanceof ApiError
          ? caught.forbidden
            ? "Signing the Calibration Report is the Programme Manager's action."
            : caught.message
          : 'The report could not be signed.',
      );
    } finally {
      setSigning(false);
    }
  }, [api, identity, countersignedBy, load]);

  const exportPdf = useCallback(async () => {
    setExporting(true);
    try {
      const blob = await api.calibrationReportPdf(identity);
      downloadBlob(blob, 'calibration-report.pdf');
    } catch {
      setSignNotice('The PDF could not be exported.');
    } finally {
      setExporting(false);
    }
  }, [api, identity]);

  return (
    <div className="workspace calibration-report">
      <section className="pane" aria-label="Calibration Report">
        <header className="pane-header">
          <h2>Calibration Report</h2>
          {result?.baseline && (
            <span className="pill idle mono">last signed v{result.baseline.version}</span>
          )}
        </header>
        <div className="pane-body">
          {error ? (
            <div className="banner">{error}</div>
          ) : loading && !result ? (
            <p className="empty">Reading the Calibration Report…</p>
          ) : !result ? null : (
            <div className="detail">
              <h3>Class mix vs calibration targets</h3>
              <table className="estate">
                <thead>
                  <tr>
                    <th>Class</th>
                    <th className="numeric">Count</th>
                    <th className="numeric">Measured</th>
                    <th className="numeric">Target</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(result.report.calibration_targets).map(([class_, target]) => (
                    <tr key={class_}>
                      <td>{class_}</td>
                      <td className="numeric">{result.report.class_mix.counts[class_] ?? 0}</td>
                      <td className="numeric">{result.report.class_mix.percentages[class_] ?? 0}%</td>
                      <td className="numeric">{target}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <h3>Coverage and parity</h3>
              <dl>
                <dt>Rule coverage</dt>
                <dd>{result.report.rule_coverage.percentage}%</dd>
                <dt>Active patterns</dt>
                <dd>
                  {result.report.pattern_coverage.active_count} of {result.report.pattern_coverage.total_count}
                </dd>
                <dt>First-pass parity by tier</dt>
                <dd>
                  {Object.entries(result.report.first_pass_parity_by_tier)
                    .map(([tier, stats]) => `${tier}: ${pct(stats.first_pass_rate)}`)
                    .join(', ')}
                </dd>
                <dt>Mean Mender passes</dt>
                <dd>
                  {result.report.mean_mender_passes.available
                    ? `${result.report.mean_mender_passes.mean_passes_to_pass?.toFixed(2)} over ${result.report.mean_mender_passes.closed_count} closed cases`
                    : result.report.mean_mender_passes.detail}
                </dd>
              </dl>

              <h3>C4 rate and reasons</h3>
              <p>
                {result.report.c4.c4_count} of {result.report.c4.total_count} calculated fields
                ({pct(result.report.c4.c4_rate)})
              </p>
              {Object.keys(result.report.c4.by_reason).length === 0 ? (
                <p className="empty">No C4 field has been classified yet.</p>
              ) : (
                <ul>
                  {Object.entries(result.report.c4.by_reason).map(([reason, entry]) => (
                    <li key={reason} title={entry.guidance}>
                      {reason}: {entry.count}
                    </li>
                  ))}
                </ul>
              )}

              <h3>Family count and parse quality</h3>
              <dl>
                <dt>Family count</dt>
                <dd>
                  {result.report.families.family_count} (planned {result.report.families.planned_family_count})
                </dd>
                <dt>Mean reports per family</dt>
                <dd>
                  {result.report.families.mean_reports_per_family === null
                    ? '—'
                    : result.report.families.mean_reports_per_family.toFixed(1)}
                </dd>
                <dt>Mean parse quality</dt>
                <dd>{pct(result.report.parse_quality.mean_parse_quality)}</dd>
              </dl>

              <h3>Cost per report by tier</h3>
              <ul>
                {Object.entries(result.report.cost_per_report_by_tier).map(([tier, price]) => (
                  <li key={tier}>
                    {tier}: {currency(price)}
                  </li>
                ))}
              </ul>

              <h3>Not yet built</h3>
              <p className="faint">{result.report.elapsed_time_per_stage.detail}</p>
              <p className="faint">{result.report.executor_strategy_mix.detail}</p>

              {result.comparison && (
                <>
                  <h3>Comparison to the last signed baseline</h3>
                  <ul>
                    {Object.entries(result.comparison).map(([key, value]) => (
                      <li key={key} className="mono">
                        {key}: {JSON.stringify(value)}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </div>
        <footer className="statusbar">
          {signNotice && <span>{signNotice}</span>}
          <span className="spacer" />
          {canSign ? (
            <>
              <label>
                Countersigned by (client analytics lead)
                <input
                  type="text"
                  value={countersignedBy}
                  onChange={(e) => setCountersignedBy(e.target.value)}
                  placeholder="A. Mehta"
                />
              </label>
              <button
                type="button"
                className="btn primary"
                disabled={signing || !countersignedBy.trim()}
                onClick={() => void sign()}
              >
                {signing ? 'Signing…' : 'Sign report'}
              </button>
            </>
          ) : (
            <span className="faint">Signing the Calibration Report is the Programme Manager&rsquo;s action.</span>
          )}
          <button type="button" className="btn" disabled={exporting || !result} onClick={() => void exportPdf()}>
            {exporting ? 'Exporting…' : 'Export as PDF'}
          </button>
        </footer>
      </section>
    </div>
  );
}
