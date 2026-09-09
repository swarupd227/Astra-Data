/**
 * The Exception Desk -- §11.3, story S8.3.1, opening F8.3.
 *
 * "A queue of ExceptionCases ordered by train sequence, with the evidence bundle in the
 * case, so that I never open Tableau to work out what an exception is." Queue columns:
 * MU, failure class, passes consumed, train, age, assignee; filters by train, class,
 * site, assignee; bulk assign. Case page: evidence (failing cells, key diffs, filter
 * context, parameter values), artefact (current DAX/M with source calc alongside,
 * Mender pass history), decision.
 *
 * Reads (`GET /v1/exception-desk`, `GET /v1/exceptions/{id}`) are attempted for any role
 * -- the API's own `ExceptionDeskReaderDep` already covers Artizent or the client report
 * owner, so a role this screen refuses simply sees the error banner, the identical
 * posture every other screen in this console already takes (no client-side read gate
 * duplicates the server's own). Bulk assign and every decision are the Migration
 * Engineer's own action (`MigrationEngineerDep`) -- hidden, not disabled, for anyone
 * else, the same convention `RegressionMonitor.tsx`'s own "Schedule" button and
 * `ParityDashboard.tsx`'s own "Re-run parity" already set.
 *
 * **A decision closes or blocks the case it was made against** -- selecting one from the
 * queue after a decision reloads both the queue (the case may have left it) and the case
 * page (its own state/decision now reflect what just happened).
 */

import { useCallback, useEffect, useState } from 'react';

import type {
  Api,
  ExceptionCaseDetail,
  ExceptionQueueEntry,
  Identity,
} from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
}

type DecisionKind = 'patch' | 'redesign' | 'model_defect' | 'source_defect';

function formatAge(seconds: number | null): string {
  if (seconds === null) return '—';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

export function ExceptionDesk({ api, identity }: Props): JSX.Element {
  const [trainFilter, setTrainFilter] = useState('');
  const [classFilter, setClassFilter] = useState('');
  const [siteFilter, setSiteFilter] = useState('');
  const [assigneeFilter, setAssigneeFilter] = useState('');

  const [entries, setEntries] = useState<ExceptionQueueEntry[] | null>(null);
  const [queueLoading, setQueueLoading] = useState(true);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [queueNonce, setQueueNonce] = useState(0);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkAssignee, setBulkAssignee] = useState('');
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkNotice, setBulkNotice] = useState<string | null>(null);

  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [caseDetail, setCaseDetail] = useState<ExceptionCaseDetail | null>(null);
  const [caseLoading, setCaseLoading] = useState(false);
  const [caseError, setCaseError] = useState<string | null>(null);

  const [decisionKind, setDecisionKind] = useState<DecisionKind>('patch');
  const [rationale, setRationale] = useState('');
  const [dax, setDax] = useState('');
  const [workspace, setWorkspace] = useState('dev');
  const [redesignRoute, setRedesignRoute] = useState<'desktop' | 'foundry'>('desktop');
  const [desktopCommitHash, setDesktopCommitHash] = useState('');
  const [sourceResolution, setSourceResolution] = useState<'REPRODUCE' | 'FIX_WITH_SIGN_OFF'>('REPRODUCE');
  const [ownerSignOff, setOwnerSignOff] = useState('');
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [decisionNotice, setDecisionNotice] = useState<string | null>(null);

  const canDecide = identity.roles.includes('migration_engineer');

  useEffect(() => {
    let live = true;
    setQueueLoading(true);
    api
      .exceptionQueue(
        {
          train: trainFilter || null,
          failureClass: classFilter || null,
          site: siteFilter || null,
          assignee: assigneeFilter || null,
        },
        identity,
      )
      .then((response) => {
        if (!live) return;
        setEntries(response.entries);
        setQueueError(null);
        setSelectedIds(new Set());
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setEntries(null);
        setQueueError(
          caught instanceof ApiError ? caught.message : 'The Exception Desk queue could not be read.',
        );
      })
      .finally(() => live && setQueueLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, identity, queueNonce]);

  const reloadQueue = useCallback(() => setQueueNonce((value) => value + 1), []);

  const refreshCase = useCallback(
    async (caseId: string) => {
      setCaseLoading(true);
      setCaseError(null);
      try {
        setCaseDetail(await api.exceptionCase(caseId, identity));
      } catch (caught: unknown) {
        setCaseDetail(null);
        setCaseError(caught instanceof ApiError ? caught.message : 'The case page could not be read.');
      } finally {
        setCaseLoading(false);
      }
    },
    [api, identity],
  );

  const loadCase = useCallback(
    async (caseId: string) => {
      setSelectedCaseId(caseId);
      setDecisionNotice(null);
      setRationale('');
      setDax('');
      setDesktopCommitHash('');
      setOwnerSignOff('');
      await refreshCase(caseId);
    },
    [refreshCase],
  );

  const toggleSelected = useCallback((caseId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(caseId)) next.delete(caseId);
      else next.add(caseId);
      return next;
    });
  }, []);

  const bulkAssign = useCallback(async () => {
    if (selectedIds.size === 0 || !bulkAssignee.trim()) return;
    setBulkBusy(true);
    setBulkNotice(null);
    try {
      const result = await api.bulkAssignExceptions(Array.from(selectedIds), bulkAssignee.trim(), identity);
      setBulkNotice(`Assigned ${result.count} case(s) to ${result.assignee}.`);
      setBulkAssignee('');
      reloadQueue();
    } catch (caught: unknown) {
      setBulkNotice(
        caught instanceof ApiError
          ? caught.forbidden
            ? 'Bulk assign is the Migration Engineer’s action.'
            : caught.message
          : 'The cases could not be assigned.',
      );
    } finally {
      setBulkBusy(false);
    }
  }, [api, identity, selectedIds, bulkAssignee, reloadQueue]);

  const submitDecision = useCallback(async () => {
    if (!selectedCaseId) return;
    setDecisionBusy(true);
    setDecisionNotice(null);
    try {
      if (decisionKind === 'patch') {
        const result = await api.patchException(selectedCaseId, dax, rationale, workspace, identity);
        setDecisionNotice(
          result.outcome === 'closed'
            ? `Patched and closed -- every case re-proved PASS (${result.cases_reproved.length}).`
            : `Patched, but ${result.cases_still_failing.length} case(s) still fail -- the case stays OPEN.`,
        );
      } else if (decisionKind === 'redesign') {
        const result = await api.redesignException(
          selectedCaseId, redesignRoute, rationale, identity,
          redesignRoute === 'desktop' ? desktopCommitHash : undefined,
        );
        setDecisionNotice(
          result.route === 'desktop'
            ? `Redesign recorded -- closed with Desktop commit ${desktopCommitHash}.`
            : 'Redesign recorded -- routed to the Foundry.',
        );
      } else if (decisionKind === 'model_defect') {
        await api.decideModelDefect(selectedCaseId, rationale, identity);
        setDecisionNotice('Model defect recorded -- routed to the Foundry as a change to the family.');
      } else {
        const result = await api.decideSourceDefect(
          selectedCaseId, rationale, sourceResolution, identity,
          sourceResolution === 'FIX_WITH_SIGN_OFF' ? ownerSignOff : undefined,
        );
        setDecisionNotice(
          `Source defect recorded and closed -- ${result.resolution.toLowerCase()}, owner notified.`,
        );
      }
      await refreshCase(selectedCaseId);
      reloadQueue();
    } catch (caught: unknown) {
      setDecisionNotice(caught instanceof ApiError ? caught.message : 'The decision could not be recorded.');
    } finally {
      setDecisionBusy(false);
    }
  }, [
    api, identity, selectedCaseId, decisionKind, dax, rationale, workspace,
    redesignRoute, desktopCommitHash, sourceResolution, ownerSignOff, refreshCase, reloadQueue,
  ]);

  return (
    <div className="workspace exception-desk">
      <section className="pane" aria-label="Exception Desk">
        <header className="pane-header">
          <h2>Exception Desk</h2>
          {entries && <span className="pill idle mono">{entries.length} in queue</span>}
        </header>
        <div className="pane-body">
          <div className="row-actions">
            <label>
              Train
              <input type="text" value={trainFilter} onChange={(e) => setTrainFilter(e.target.value)} />
            </label>
            <label>
              Class
              <input type="text" value={classFilter} onChange={(e) => setClassFilter(e.target.value)} />
            </label>
            <label>
              Site
              <input type="text" value={siteFilter} onChange={(e) => setSiteFilter(e.target.value)} />
            </label>
            <label>
              Assignee
              <input type="text" value={assigneeFilter} onChange={(e) => setAssigneeFilter(e.target.value)} />
            </label>
            <button type="button" className="btn" onClick={reloadQueue} disabled={queueLoading}>
              {queueLoading ? 'Filtering…' : 'Filter'}
            </button>
          </div>

          {queueError ? (
            <div className="banner">{queueError}</div>
          ) : queueLoading && !entries ? (
            <p className="empty">Reading the Exception Desk…</p>
          ) : !entries || entries.length === 0 ? (
            <p className="empty">No open or blocked exception matches these filters.</p>
          ) : (
            <table className="estate">
              <caption className="visually-hidden">
                Every live ExceptionCase ordered by train sequence then age
              </caption>
              <thead>
                <tr>
                  {canDecide && <th className="visually-hidden">Select</th>}
                  <th>MU</th>
                  <th>Failure class</th>
                  <th>Passes consumed</th>
                  <th>Train</th>
                  <th>Age</th>
                  <th>Assignee</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr
                    key={entry.id}
                    aria-selected={entry.id === selectedCaseId}
                    onClick={() => void loadCase(entry.id)}
                    style={{ cursor: 'pointer' }}
                  >
                    {canDecide && (
                      <td onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={`Select ${entry.id}`}
                          checked={selectedIds.has(entry.id)}
                          onChange={() => toggleSelected(entry.id)}
                        />
                      </td>
                    )}
                    <td className="mono">{entry.mu_ref}</td>
                    <td>{entry.class ?? '—'}</td>
                    <td>{entry.passes_consumed ?? 0}</td>
                    <td>{entry.train_sequence === null ? <span className="faint">unsequenced</span> : entry.train_sequence}</td>
                    <td>{formatAge(entry.age_seconds)}</td>
                    <td>{entry.assignee ?? <span className="faint">unassigned</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {canDecide && (
          <footer className="statusbar">
            {bulkNotice && <span>{bulkNotice}</span>}
            <span className="spacer" />
            <label>
              Assign {selectedIds.size} selected to
              <input
                type="text"
                value={bulkAssignee}
                onChange={(e) => setBulkAssignee(e.target.value)}
                placeholder="engineer"
              />
            </label>
            <button
              type="button"
              className="btn"
              disabled={selectedIds.size === 0 || !bulkAssignee.trim() || bulkBusy}
              onClick={() => void bulkAssign()}
            >
              {bulkBusy ? 'Assigning…' : 'Bulk assign'}
            </button>
          </footer>
        )}
      </section>

      {selectedCaseId && (
        <section className="pane" aria-label="Case page">
          <header className="pane-header">
            <h2>Case {selectedCaseId}</h2>
            {caseDetail && (
              <>
                <span className="pill idle mono">{caseDetail.state}</span>
                {caseDetail.decision && <span className="pill idle mono">{String(caseDetail.decision)}</span>}
              </>
            )}
          </header>
          <div className="pane-body">
            {caseError ? (
              <div className="banner">{caseError}</div>
            ) : caseLoading && !caseDetail ? (
              <p className="empty">Reading the case…</p>
            ) : caseDetail ? (
              <>
                <h3>Evidence</h3>
                <table className="estate">
                  <thead>
                    <tr>
                      <th>Case</th>
                      <th>Grain key</th>
                      <th>Measure</th>
                      <th>Expected</th>
                      <th>Candidate</th>
                      <th>Delta</th>
                    </tr>
                  </thead>
                  <tbody>
                    {caseDetail.evidence.failing_cells.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="empty">No failing cells.</td>
                      </tr>
                    ) : (
                      caseDetail.evidence.failing_cells.map((cell, index) => (
                        <tr key={index}>
                          <td className="mono">{cell.case_ref}</td>
                          <td>{JSON.stringify(cell.grain_key)}</td>
                          <td>{cell.measure}</td>
                          <td>{String(cell.expected)}</td>
                          <td>{String(cell.candidate)}</td>
                          <td>{cell.delta === null ? '—' : cell.delta}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
                <p className="faint">
                  Key diffs -- missing {JSON.stringify(caseDetail.evidence.missing_keys)}, extra{' '}
                  {JSON.stringify(caseDetail.evidence.extra_keys)}
                </p>
                <p className="faint">Filter context -- {JSON.stringify(caseDetail.evidence.filter_ctx)}</p>
                <p className="faint">Parameter values -- {JSON.stringify(caseDetail.evidence.param_values)}</p>

                <h3>Artefact</h3>
                <dl>
                  <dt>Source calc</dt>
                  <dd>{caseDetail.artefact.calc_name ?? '—'}</dd>
                  <dd className="mono">{caseDetail.artefact.source_formula ?? '—'}</dd>
                  <dt>Current DAX</dt>
                  <dd className="mono">{caseDetail.artefact.current_dax ?? '—'}</dd>
                  <dt>Current M</dt>
                  <dd className="mono">{caseDetail.artefact.current_m_query ?? '—'}</dd>
                </dl>

                <h3>Mender pass history</h3>
                <table className="estate">
                  <thead>
                    <tr>
                      <th>Pass</th>
                      <th>Strategy</th>
                      <th>Result</th>
                      <th>Reproved</th>
                      <th>Still failing</th>
                    </tr>
                  </thead>
                  <tbody>
                    {caseDetail.mender_passes.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="empty">No Mender passes yet.</td>
                      </tr>
                    ) : (
                      caseDetail.mender_passes.map((pass) => (
                        <tr key={pass.id}>
                          <td>{pass.pass_number}</td>
                          <td>{pass.strategy}</td>
                          <td>{pass.result}</td>
                          <td>{pass.cases_reproved.length}</td>
                          <td>{pass.cases_still_failing.length}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>

                <h3>Decision</h3>
                {canDecide ? (
                  <div className="charter-block">
                    <label>
                      Kind
                      <select
                        value={decisionKind}
                        onChange={(e) => setDecisionKind(e.target.value as DecisionKind)}
                      >
                        <option value="patch">Patch</option>
                        <option value="redesign">Redesign</option>
                        <option value="model_defect">Model defect</option>
                        <option value="source_defect">Source defect</option>
                      </select>
                    </label>

                    {decisionKind === 'patch' && (
                      <>
                        <label>
                          New DAX
                          <textarea value={dax} onChange={(e) => setDax(e.target.value)} rows={3} />
                        </label>
                        <label>
                          Workspace
                          <input type="text" value={workspace} onChange={(e) => setWorkspace(e.target.value)} />
                        </label>
                      </>
                    )}

                    {decisionKind === 'redesign' && (
                      <>
                        <label>
                          Route
                          <select
                            value={redesignRoute}
                            onChange={(e) => setRedesignRoute(e.target.value as 'desktop' | 'foundry')}
                          >
                            <option value="desktop">Open in Desktop</option>
                            <option value="foundry">Route to Foundry</option>
                          </select>
                        </label>
                        {redesignRoute === 'desktop' && (
                          <label>
                            Desktop commit hash
                            <input
                              type="text"
                              value={desktopCommitHash}
                              onChange={(e) => setDesktopCommitHash(e.target.value)}
                            />
                          </label>
                        )}
                      </>
                    )}

                    {decisionKind === 'source_defect' && (
                      <>
                        <label>
                          Resolution
                          <select
                            value={sourceResolution}
                            onChange={(e) =>
                              setSourceResolution(e.target.value as 'REPRODUCE' | 'FIX_WITH_SIGN_OFF')
                            }
                          >
                            <option value="REPRODUCE">Reproduce faithfully</option>
                            <option value="FIX_WITH_SIGN_OFF">Fix with owner sign-off</option>
                          </select>
                        </label>
                        {sourceResolution === 'FIX_WITH_SIGN_OFF' && (
                          <label>
                            Owner sign-off
                            <textarea
                              value={ownerSignOff}
                              onChange={(e) => setOwnerSignOff(e.target.value)}
                              rows={2}
                            />
                          </label>
                        )}
                      </>
                    )}

                    <label>
                      Rationale (at least one real sentence)
                      <textarea value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} />
                    </label>

                    <button
                      type="button"
                      className="btn primary"
                      disabled={decisionBusy || rationale.trim().length < 20}
                      onClick={() => void submitDecision()}
                    >
                      {decisionBusy ? 'Recording…' : 'Record decision'}
                    </button>
                  </div>
                ) : (
                  <span className="faint">Recording a decision is the Migration Engineer&rsquo;s action.</span>
                )}
              </>
            ) : null}
          </div>
          {decisionNotice && (
            <footer className="statusbar">
              <span>{decisionNotice}</span>
            </footer>
          )}
        </section>
      )}
    </div>
  );
}
