/**
 * The Regression Monitor -- §10.6, story S7.7.1, closing F7.7 and E7.
 *
 * "Regression Monitor screen lists released MUs with last result, schedule and drift
 * alerts." No MU node exists anywhere in this codebase (E3's own still-unbuilt scope) --
 * "released" is `ReportDefinition.deploy_state == "GENERATED"`, the real, disclosed
 * proxy `regression.py`'s own docstring explains. One row per released workbook: its own
 * schedule (if any), its most recent check's result, and whether a real `SOURCE_DRIFT`
 * event still awaits that schedule's attention.
 *
 * "Steward schedules re-runs" is a Programme Manager action here (`ScheduleRegistrationDep`
 * on the API side is `ProgrammeManagerDep`, matching the AC's own literal persona: "As a
 * programme manager, I want ... schedules re-runs") -- offered but hidden for every other
 * role, the identical hide-not-disable convention `ProgrammeBoard.tsx`'s own "Confirm
 * family count" already uses. Scheduling here is deliberately minimal -- one default
 * weekly cadence, no cadence editor -- since the AC asks that scheduling be *possible*,
 * not that every knob be exposed from day one; `POST .../:schedule-regression` itself
 * already accepts a chosen cadence for whichever later story wants to expose one.
 *
 * "At handover suites and a runner are exported" is an Artizent action (any Artizent
 * role, matching `ArtizentDep`'s own gate on the export route) -- the resulting artefact
 * id is shown, not a browser download link: every route in this platform requires the
 * `X-Astra-Principal`/`X-Astra-Roles` headers this console sends itself, which a plain
 * `<a href>` cannot carry (spec §12.4.1's own "no privileged path" -- the console has no
 * session a browser-native download could ride on either).
 */

import { useCallback, useEffect, useState } from 'react';

import type { Api, Identity, RegressionMonitorRow } from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
}

function resultPillClass(result: string | null): string {
  if (result === 'PASS') return 'pill ok';
  if (result === 'FAIL') return 'pill bad';
  if (result === 'INCONCLUSIVE') return 'pill warn';
  return 'pill idle';
}

export function RegressionMonitor({ api, identity }: Props): JSX.Element {
  const [rows, setRows] = useState<RegressionMonitorRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [busyWorkbookId, setBusyWorkbookId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const canSchedule = identity.roles.includes('programme_manager');
  // Every Artizent role (roles.py's own ARTIZENT_ROLES) -- the identical set `ArtizentDep`
  // gates the export route on.
  const canExport = identity.roles.some((role) =>
    [
      'programme_manager',
      'migration_architect',
      'semantic_model_engineer',
      'migration_engineer',
      'parity_engineer',
      'platform_engineer',
    ].includes(role),
  );

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .regressionMonitor(identity)
      .then((response) => {
        if (!live) return;
        setRows(response.workbooks);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(
          caught instanceof ApiError ? caught.message : 'The Regression Monitor could not be read.',
        );
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  const schedule = useCallback(
    async (workbookId: string) => {
      setBusyWorkbookId(workbookId);
      setNotice(null);
      try {
        const created = await api.scheduleRegression(workbookId, 'dev', identity);
        setNotice(`Scheduled ${workbookId} for regression -- ${created.cadence_description}.`);
        reload();
      } catch (caught: unknown) {
        setNotice(
          caught instanceof ApiError
            ? caught.forbidden
              ? 'Scheduling regression is the Programme Manager’s action.'
              : caught.message
            : 'The schedule could not be created.',
        );
      } finally {
        setBusyWorkbookId(null);
      }
    },
    [api, identity, reload],
  );

  const exportSuite = useCallback(
    async (workbookId: string) => {
      setBusyWorkbookId(workbookId);
      setNotice(null);
      try {
        const record = await api.exportRegressionSuite(workbookId, identity);
        setNotice(
          `Exported ${workbookId} -- artefact ${record.id} (${record.size_bytes.toLocaleString()} bytes). ` +
            `Fetch its bytes from GET /v1/artefacts/${record.id}/content.`,
        );
      } catch (caught: unknown) {
        setNotice(
          caught instanceof ApiError
            ? caught.forbidden
              ? 'Exporting a handover suite is an Artizent action.'
              : caught.message
            : 'The suite could not be exported.',
        );
      } finally {
        setBusyWorkbookId(null);
      }
    },
    [api, identity],
  );

  const driftCount = (rows ?? []).filter((r) => r.drift_alert.unaddressed).length;
  const unscheduledCount = (rows ?? []).filter((r) => r.schedule === null).length;

  return (
    <div className="workspace regression-monitor">
      <section className="pane" aria-label="Regression Monitor">
        <header className="pane-header">
          <h2>Regression Monitor</h2>
          {rows && rows.length > 0 && (
            <>
              {driftCount > 0 && <span className="pill bad">{driftCount} drift alert{driftCount === 1 ? '' : 's'}</span>}
              {unscheduledCount > 0 && (
                <span className="pill idle">{unscheduledCount} not scheduled</span>
              )}
            </>
          )}
        </header>
        <div className="pane-body">
          {error ? (
            <div className="banner">{error}</div>
          ) : loading && !rows ? (
            <p className="empty">Reading the Regression Monitor…</p>
          ) : !rows || rows.length === 0 ? (
            <p className="empty">
              No workbook has been released yet -- a workbook appears here once its report
              has been deployed.
            </p>
          ) : (
            <table className="estate">
              <caption className="visually-hidden">
                Every released workbook, its own regression schedule, last result and drift alerts
              </caption>
              <thead>
                <tr>
                  <th>Workbook</th>
                  <th>Schedule</th>
                  <th>Last result</th>
                  <th>Drift</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.workbook_id} className={row.drift_alert.unaddressed ? 'flagged' : undefined}>
                    <td>{row.workbook_name}</td>
                    <td>
                      {row.schedule ? (
                        row.schedule.enabled ? (
                          <span title={`workspace ${row.schedule.workspace}`}>
                            {row.schedule.cadence_description}
                          </span>
                        ) : (
                          <span className="pill bad" title={row.schedule.paused_reason ?? undefined}>
                            paused
                          </span>
                        )
                      ) : (
                        <span className="faint">not scheduled</span>
                      )}
                    </td>
                    <td>
                      <span className={resultPillClass(row.last_result)}>
                        {row.last_result ?? 'never run'}
                      </span>
                    </td>
                    <td>
                      {row.drift_alert.unaddressed ? (
                        <span className="pill bad" title={row.drift_alert.last_drift_at ?? undefined}>
                          unaddressed
                        </span>
                      ) : (
                        <span className="faint">none</span>
                      )}
                    </td>
                    <td>
                      <div className="row-actions">
                        {canSchedule && row.schedule === null && (
                          <button
                            type="button"
                            className="btn"
                            onClick={() => void schedule(row.workbook_id)}
                            disabled={busyWorkbookId === row.workbook_id}
                          >
                            {busyWorkbookId === row.workbook_id ? 'Scheduling…' : 'Schedule'}
                          </button>
                        )}
                        {canExport && (
                          <button
                            type="button"
                            className="btn"
                            onClick={() => void exportSuite(row.workbook_id)}
                            disabled={busyWorkbookId === row.workbook_id}
                          >
                            {busyWorkbookId === row.workbook_id ? 'Exporting…' : 'Export for handover'}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <footer className="statusbar">
          {notice && <span>{notice}</span>}
          <span className="spacer" />
          {!canSchedule && !canExport && (
            <span className="faint">
              Scheduling is the Programme Manager&rsquo;s action; export is Artizent&rsquo;s.
            </span>
          )}
        </footer>
      </section>
    </div>
  );
}
