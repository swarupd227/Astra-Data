/**
 * The Parity Dashboard and per-run view — story S7.4.2, closing F7.4.
 *
 * "As a report owner, I want a Parity Dashboard and per-run view in plain language, so
 * that I can see whether my report is right without reading a diff... Per sheet: cases
 * run, pass, fail, inconclusive, first-pass rate, waived count; failing cells shown as
 * a table with expected / candidate / delta and the filter context... Per MU: pass rate
 * trend across runs and Mender passes... A single 'this report passes the charter'
 * statement with the charter version when all cases pass."
 *
 * **This is the report-owner, plain-language view the backlog's own AC asks for — not
 * §15.3.5's own fuller "Parity Dashboard (parity engineer default)" row** (a KPI strip,
 * a heat grid of MUs × sheets, a failure-class histogram, a pattern-retirements feed).
 * Confirmed by direct research: that denser, more technical screen is a real, separate,
 * later surface this story does not build — see `parity_dashboard.py`'s own docstring
 * for the full account.
 *
 * Reads only: `GET .../parity-dashboard` for the per-sheet/per-MU aggregation,
 * `GET .../parity-run` for the per-run cases table (§15.3.5's own "Parity Run" row).
 * "Re-run" is the one action here — `POST .../:run-parity`, the Parity Engineer's own
 * (S7.4.1) — offered but disabled-with-explanation for every other role, the identical
 * convention `ToleranceCharter.tsx` already established for its own Save button.
 *
 * **§10.4 sampling (story S7.5.1)**: "a sampled PASS is labelled SAMPLED on the
 * verdict" — the per-run cases table already reads `Verdict.sampled` directly (added by
 * that story), so a PASS earned on a stratified sample carries its own SAMPLED pill
 * right here. The G3 card the AC also names does not exist anywhere in this codebase
 * yet (F9.1/S9.1.1's own later, unbuilt scope) — see `diff.py`'s own docstring.
 *
 * **§10.5 visual parity, advisory only (story S7.6.1)**: the per-sheet table gains
 * Structural and Image score columns, read straight off `SheetParityStats` (never a
 * gate — nothing here can turn a PASS into a FAIL or back). Selecting a sheet with both
 * images captured shows them side by side, fetched from `GET .../visual-captures/
 * {visual_id}` as base64 so no separate binary route is needed. "Score visuals" is a
 * second Parity Engineer action alongside "Re-run parity", the identical hide-not-
 * disable convention. The G3 card this AC also names does not exist yet — see
 * `visual_parity.py`'s own docstring.
 */

import { useCallback, useEffect, useState } from 'react';

import type {
  Api,
  Identity,
  ParityDashboardResponse,
  ParityRunResponse,
  RunParityResult,
  RunVisualParityResult,
  VisualCapturePair,
} from '../lib/api';
import { ApiError } from '../lib/api';
import { setDeepLinkParam } from '../lib/deep-link';

interface Props {
  api: Api;
  identity: Identity;
  /** Deep-links straight to this workbook's own dashboard (story S10.1.1) -- closes
   * `G3Card.tsx`'s own "Open report" link, which has pointed here with `?workbook=`
   * since S9.1.1 without this screen ever reading it back. */
  initialWorkbookId?: string;
}

function pillClass(result: string): string {
  if (result === 'PASS') return 'pill ok';
  if (result === 'FAIL') return 'pill bad';
  return 'pill warn';
}

function percent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}

export function ParityDashboard({ api, identity, initialWorkbookId }: Props): JSX.Element {
  const [workbookId, setWorkbookId] = useState(initialWorkbookId ?? '');
  const [loadedWorkbookId, setLoadedWorkbookId] = useState<string | null>(null);
  const [dashboard, setDashboard] = useState<ParityDashboardResponse | null>(null);
  const [run, setRun] = useState<ParityRunResponse | null>(null);
  const [selectedSheet, setSelectedSheet] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [runBusy, setRunBusy] = useState(false);
  const [runNotice, setRunNotice] = useState<string | null>(null);

  const [visualParityBusy, setVisualParityBusy] = useState(false);
  const [visualParityNotice, setVisualParityNotice] = useState<string | null>(null);

  const [captures, setCaptures] = useState<VisualCapturePair | null>(null);
  const [capturesError, setCapturesError] = useState<string | null>(null);

  const canRun = identity.roles.includes('parity_engineer');

  const load = useCallback(
    async (id: string) => {
      if (!id.trim()) return;
      setBusy(true);
      setError(null);
      setSelectedSheet(null);
      try {
        const [dashboardResult, runResult] = await Promise.all([
          api.parityDashboard(id.trim(), identity),
          api.parityRun(id.trim(), identity),
        ]);
        setDashboard(dashboardResult);
        setRun(runResult);
        setLoadedWorkbookId(id.trim());
        setDeepLinkParam('workbook', id.trim());
      } catch (caught: unknown) {
        setDashboard(null);
        setRun(null);
        setLoadedWorkbookId(null);
        setError(
          caught instanceof ApiError
            ? caught.message
            : 'The Parity Dashboard could not be read.',
        );
      } finally {
        setBusy(false);
      }
    },
    [api, identity],
  );

  // A deep link is read once, at mount.
  useEffect(() => {
    if (initialWorkbookId) void load(initialWorkbookId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runParity = useCallback(async () => {
    if (!loadedWorkbookId) return;
    setRunBusy(true);
    setRunNotice(null);
    try {
      const result: RunParityResult = await api.runParity(loadedWorkbookId, identity);
      setRunNotice(
        `Ran ${result.cases_diffed} case(s) under charter version ${result.charter_version}: `
          + `${result.pass} pass, ${result.fail} fail, ${result.inconclusive} inconclusive.`,
      );
      await load(loadedWorkbookId);
    } catch (caught: unknown) {
      setRunNotice(caught instanceof ApiError ? caught.message : 'The parity run could not be started.');
    } finally {
      setRunBusy(false);
    }
  }, [api, identity, loadedWorkbookId, load]);

  const runVisualParity = useCallback(async () => {
    if (!loadedWorkbookId) return;
    setVisualParityBusy(true);
    setVisualParityNotice(null);
    try {
      const result: RunVisualParityResult = await api.runVisualParity(loadedWorkbookId, identity);
      setVisualParityNotice(`Scored ${result.visuals_scored} visual(s) against their own source sheet.`);
      await load(loadedWorkbookId);
    } catch (caught: unknown) {
      setVisualParityNotice(caught instanceof ApiError ? caught.message : 'Visual scoring could not be started.');
    } finally {
      setVisualParityBusy(false);
    }
  }, [api, identity, loadedWorkbookId, load]);

  const activeSheet = dashboard?.sheets.find((sheet) => sheet.sheet_ref === selectedSheet) ?? null;

  const selectSheet = useCallback(
    (sheet: { sheet_ref: string; visual_id: string | null }) => {
      const nextRef = sheet.sheet_ref === selectedSheet ? null : sheet.sheet_ref;
      setSelectedSheet(nextRef);
      setCaptures(null);
      setCapturesError(null);
      if (!nextRef || !loadedWorkbookId || !sheet.visual_id) return;
      void (async () => {
        try {
          setCaptures(await api.visualCaptures(loadedWorkbookId, sheet.visual_id!, identity));
        } catch (caught: unknown) {
          setCapturesError(
            caught instanceof ApiError ? caught.message : 'The side-by-side images could not be read.',
          );
        }
      })();
    },
    [api, identity, loadedWorkbookId, selectedSheet],
  );

  return (
    <div className="workspace parity-workspace">
      <section className="pane" aria-label="Parity Dashboard">
        <header className="pane-header">
          <h2>Parity Dashboard</h2>
          {dashboard && <span className="pill idle mono">charter version {dashboard.charter_version}</span>}
        </header>
        <div className="pane-body">
          <label>
            Workbook
            <input
              type="text"
              value={workbookId}
              onChange={(e) => setWorkbookId(e.target.value)}
              placeholder="workbook id"
            />
          </label>
          {error && <div className="banner">{error}</div>}
          {!error && !dashboard && !busy && (
            <p className="empty">Enter a workbook id to see whether its report passes the charter.</p>
          )}
          {busy && <p className="empty">Reading the Parity Dashboard…</p>}

          {dashboard && (
            <>
              {dashboard.passes_the_charter ? (
                <p className="pill ok" style={{ display: 'block', padding: '10px 14px', borderRadius: 6 }}>
                  This report passes the Tolerance Charter, version {dashboard.charter_version}.
                </p>
              ) : (
                <p className="pill bad" style={{ display: 'block', padding: '10px 14px', borderRadius: 6 }}>
                  This report does not yet pass the Tolerance Charter, version {dashboard.charter_version}.
                </p>
              )}

              <h3>Per sheet</h3>
              <table className="estate">
                <thead>
                  <tr>
                    <th>Sheet</th>
                    <th>Cases run</th>
                    <th>Pass</th>
                    <th>Fail</th>
                    <th>Inconclusive</th>
                    <th>First-pass rate</th>
                    <th>Waived</th>
                    <th title="§10.5: mark type, encodings, axes, sort, reference lines — advisory, never gates">
                      Structural
                    </th>
                    <th title="§10.5: perceptual similarity of the source screenshot and target render — advisory, never gates">
                      Image
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {dashboard.sheets.map((sheet) => (
                    <tr
                      key={sheet.sheet_ref}
                      aria-selected={sheet.sheet_ref === selectedSheet}
                      onClick={() => selectSheet(sheet)}
                      style={{ cursor: 'pointer' }}
                    >
                      <td>{sheet.sheet_name}</td>
                      <td>{sheet.cases_run}</td>
                      <td>
                        <span className="pill ok">{sheet.pass}</span>
                      </td>
                      <td>
                        <span className={sheet.fail > 0 ? 'pill bad' : 'pill idle'}>{sheet.fail}</span>
                      </td>
                      <td>
                        <span className={sheet.inconclusive > 0 ? 'pill warn' : 'pill idle'}>
                          {sheet.inconclusive}
                        </span>
                      </td>
                      <td>{percent(sheet.first_pass_rate)}</td>
                      <td>{sheet.waived_count}</td>
                      <td>{percent(sheet.structural_score)}</td>
                      <td>{percent(sheet.image_score)}</td>
                    </tr>
                  ))}
                  {dashboard.sheets.length === 0 && (
                    <tr>
                      <td colSpan={9} className="empty">
                        No diffed sheets yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>

              {activeSheet && (
                <div className="charter-block">
                  <h3>Failing cells — {activeSheet.sheet_name}</h3>
                  {activeSheet.failing_cells.length === 0 ? (
                    <p className="empty">No failing cells on this sheet.</p>
                  ) : (
                    <table className="estate">
                      <thead>
                        <tr>
                          <th>Grain key</th>
                          <th>Measure</th>
                          <th>Expected</th>
                          <th>Candidate</th>
                          <th>Delta</th>
                          <th>Filter context</th>
                        </tr>
                      </thead>
                      <tbody>
                        {activeSheet.failing_cells.map((cell, index) => (
                          <tr key={index}>
                            <td>{JSON.stringify(cell.grain_key)}</td>
                            <td>{cell.measure}</td>
                            <td>{String(cell.expected)}</td>
                            <td>{String(cell.candidate)}</td>
                            <td>{cell.delta === null ? '—' : cell.delta}</td>
                            <td className="muted">{JSON.stringify(cell.filter_ctx)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  <h3>
                    Side by side — {activeSheet.sheet_name}
                    {activeSheet.structural_score !== null && (
                      <span className="pill idle mono">
                        {' '}
                        structural {percent(activeSheet.structural_score)}
                      </span>
                    )}
                    {activeSheet.image_score !== null && (
                      <span className="pill idle mono"> image {percent(activeSheet.image_score)}</span>
                    )}
                  </h3>
                  {activeSheet.structural_score === null ? (
                    <p className="empty">
                      This sheet has not been scored yet. Score visuals to see how closely its mark
                      type, encodings, axes, sort and reference lines mirror the source (§10.5,
                      advisory — never gates).
                    </p>
                  ) : capturesError ? (
                    <div className="banner">{capturesError}</div>
                  ) : captures ? (
                    <div style={{ display: 'flex', gap: 16 }}>
                      <figure>
                        <figcaption className="faint">Source screenshot</figcaption>
                        <img
                          src={`data:${captures.source.media_type};base64,${captures.source.content_base64}`}
                          alt={`Source screenshot of ${activeSheet.sheet_name}`}
                          style={{ maxWidth: 300, border: '1px solid var(--border, #ccc)' }}
                        />
                      </figure>
                      <figure>
                        <figcaption className="faint">Target render</figcaption>
                        <img
                          src={`data:${captures.target.media_type};base64,${captures.target.content_base64}`}
                          alt={`Target render of ${activeSheet.sheet_name}`}
                          style={{ maxWidth: 300, border: '1px solid var(--border, #ccc)' }}
                        />
                      </figure>
                    </div>
                  ) : (
                    <p className="empty">
                      No captured images for this sheet — the source adapter may not support
                      screenshots.
                    </p>
                  )}
                </div>
              )}

              <h3>Per MU — pass rate trend across runs</h3>
              <table className="estate">
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Started</th>
                    <th>Charter version</th>
                    <th>Cases</th>
                    <th>Pass rate</th>
                  </tr>
                </thead>
                <tbody>
                  {dashboard.trend.runs.map((entry) => (
                    <tr key={entry.run_id}>
                      <td className="mono">{entry.run_id}</td>
                      <td>{entry.started ?? '—'}</td>
                      <td>{entry.charter_version}</td>
                      <td>{entry.cases}</td>
                      <td>{percent(entry.pass_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {dashboard.trend.mender_passes.available ? (
                <p className="faint">
                  Mender passes: mean {dashboard.trend.mender_passes.mean_passes_to_pass?.toFixed(2)} to pass,
                  over {dashboard.trend.mender_passes.closed_count} closed case
                  {dashboard.trend.mender_passes.closed_count === 1 ? '' : 's'}.
                </p>
              ) : (
                <p className="faint" title={dashboard.trend.mender_passes.detail}>
                  Mender passes: not yet available.
                </p>
              )}
            </>
          )}
        </div>
        <footer className="statusbar">
          {visualParityNotice && <span>{visualParityNotice}</span>}
          {runNotice && <span>{runNotice}</span>}
          <span className="spacer" />
          <button type="button" className="btn primary" disabled={busy || !workbookId.trim()} onClick={() => void load(workbookId)}>
            {busy ? 'Loading…' : 'Load'}
          </button>
          {canRun ? (
            <>
              <button
                type="button"
                className="btn"
                disabled={!loadedWorkbookId || runBusy}
                onClick={() => void runParity()}
              >
                {runBusy ? 'Running…' : 'Re-run parity'}
              </button>
              <button
                type="button"
                className="btn"
                disabled={!loadedWorkbookId || visualParityBusy}
                onClick={() => void runVisualParity()}
              >
                {visualParityBusy ? 'Scoring…' : 'Score visuals'}
              </button>
            </>
          ) : (
            <span className="faint">Running parity and scoring visuals are the Parity Engineer&rsquo;s.</span>
          )}
        </footer>
      </section>

      {run && (
        <section className="pane" aria-label="Parity Run">
          <header className="pane-header">
            <h2>Parity Run</h2>
            <span className="pill idle mono">{run.run_id}</span>
          </header>
          <div className="pane-body">
            <dl>
              <dt>Started</dt>
              <dd>{run.started ?? '—'}</dd>
              <dt>Finished</dt>
              <dd>{run.finished ?? '—'}</dd>
              <dt>Charter version</dt>
              <dd>{run.charter_version}</dd>
            </dl>
            <table className="estate">
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Result</th>
                  <th>Failing cells</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {run.verdicts.map((verdict) => (
                  <tr key={verdict.id}>
                    <td className="mono">{verdict.case_ref}</td>
                    <td>
                      <span className={pillClass(verdict.result)}>{verdict.result}</span>
                      {verdict.result === 'PASS' && verdict.sampled && (
                        <span className="pill idle" title="§10.4: compared on a stratified sample, not every row">
                          {' '}SAMPLED
                        </span>
                      )}
                    </td>
                    <td>{verdict.failing_cells.length}</td>
                    <td className="mono">{verdict.evidence_ref ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
