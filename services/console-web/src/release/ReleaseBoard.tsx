/**
 * The Release Board -- §15.3.4, story S9.2.1, opening F9.2.
 *
 * "Per train: MUs ACCEPTED -> RELEASED with pipeline status; per site: parallel-run
 * window..." -- two panes, matching the AC's own two nouns. Promotion is split across
 * the AC's own two ceilings: MA-08 (test) is the Platform Engineer's own action,
 * MA-09 (prod) is the Programme Manager's, gated the identical hide-not-disable way
 * `RegressionMonitor.tsx`'s own Schedule/Export actions already are. Promoting to prod
 * reuses the shared `ReasonDialog` for its own required rationale (the real 20-character
 * minimum is server-enforced -- MA-09's own "explicit release approval by PM" -- the
 * dialog's own lower client-side hint is a courtesy, not the real gate).
 *
 * Decommission readiness (regression status, adoption sessions, owner confirmation, G4)
 * is deliberately not shown here -- `release.py`'s own docstring explains why: it is a
 * later F9.2 story's own scope, not this AC's.
 */

import { useCallback, useEffect, useState } from 'react';

import { ReasonDialog } from '../estate/ReasonDialog';
import type { Api, Identity, ReleaseBoard as ReleaseBoardData, ReleaseBoardMu } from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
}

function stagePillClass(stage: string): string {
  if (stage === 'PROD') return 'pill ok';
  if (stage === 'TEST') return 'pill idle mono';
  if (stage === 'ACCEPTED') return 'pill idle';
  return 'pill bad';
}

function stageLabel(stage: string): string {
  return stage === 'NOT_ACCEPTED' ? 'not accepted' : stage.toLowerCase();
}

function formatDate(value: string | null): string {
  if (!value) return '—';
  return new Date(value).toLocaleDateString();
}

export function ReleaseBoard({ api, identity }: Props): JSX.Element {
  const [board, setBoard] = useState<ReleaseBoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [busyWorkbookId, setBusyWorkbookId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [prodDialogFor, setProdDialogFor] = useState<string | null>(null);
  const [prodError, setProdError] = useState<string | null>(null);

  const canPromoteToTest = identity.roles.includes('platform_engineer');
  const canPromoteToProd = identity.roles.includes('programme_manager');

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .releaseBoard(identity)
      .then((response) => {
        if (!live) return;
        setBoard(response);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(caught instanceof ApiError ? caught.message : 'The Release Board could not be read.');
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  const promoteToTest = useCallback(
    async (workbookId: string) => {
      setBusyWorkbookId(workbookId);
      setNotice(null);
      try {
        const record = await api.promoteToTest(workbookId, identity);
        setNotice(`${workbookId} promoted to ${record.workspace} (${record.state.toLowerCase()}).`);
        reload();
      } catch (caught: unknown) {
        setNotice(
          caught instanceof ApiError
            ? caught.forbidden
              ? 'Promoting to test is the Platform Engineer’s action.'
              : caught.message
            : 'The promotion could not be recorded.',
        );
      } finally {
        setBusyWorkbookId(null);
      }
    },
    [api, identity, reload],
  );

  const promoteToProd = useCallback(
    async (workbookId: string, rationale: string) => {
      setBusyWorkbookId(workbookId);
      setProdError(null);
      try {
        const record = await api.promoteToProd(workbookId, rationale, identity);
        setNotice(`${workbookId} promoted to ${record.workspace} (${record.state.toLowerCase()}).`);
        setProdDialogFor(null);
        reload();
      } catch (caught: unknown) {
        setProdError(
          caught instanceof ApiError ? caught.message : 'The promotion could not be recorded.',
        );
      } finally {
        setBusyWorkbookId(null);
      }
    },
    [api, identity, reload],
  );

  const renderActions = (mu: ReleaseBoardMu): JSX.Element | null => {
    if (mu.next_stage === 'test' && canPromoteToTest) {
      return (
        <button
          type="button"
          className="btn"
          onClick={() => void promoteToTest(mu.workbook_id)}
          disabled={busyWorkbookId === mu.workbook_id || mu.blockers.length > 0}
          title={mu.blockers.length > 0 ? mu.blockers.join('; ') : undefined}
        >
          {busyWorkbookId === mu.workbook_id ? 'Promoting…' : 'Promote to test'}
        </button>
      );
    }
    if (mu.next_stage === 'prod' && canPromoteToProd) {
      return (
        <button
          type="button"
          className="btn primary"
          onClick={() => setProdDialogFor(mu.workbook_id)}
          disabled={busyWorkbookId === mu.workbook_id || mu.blockers.length > 0}
          title={mu.blockers.length > 0 ? mu.blockers.join('; ') : undefined}
        >
          Promote to prod
        </button>
      );
    }
    return null;
  };

  return (
    <div className="workspace release-board">
      <section className="pane" aria-label="Release Board">
        <header className="pane-header">
          <h2>Release Board</h2>
        </header>
        <div className="pane-body">
          {error ? (
            <div className="banner">{error}</div>
          ) : loading && !board ? (
            <p className="empty">Reading the Release Board…</p>
          ) : !board || board.trains.length === 0 ? (
            <p className="empty">No train has any workbook yet.</p>
          ) : (
            board.trains.map((train) => (
              <div key={train.id} className="release-train">
                <h3>{train.name ?? train.id}</h3>
                <table className="estate">
                  <caption className="visually-hidden">
                    Every MU in {train.name ?? train.id}, its own pipeline stage and blockers
                  </caption>
                  <thead>
                    <tr>
                      <th>MU</th>
                      <th>Stage</th>
                      <th>Blockers</th>
                      <th>Evidence</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {train.mus.map((mu) => (
                      <tr key={mu.workbook_id}>
                        <td>{mu.name}</td>
                        <td>
                          <span className={stagePillClass(mu.stage)}>{stageLabel(mu.stage)}</span>
                        </td>
                        <td>
                          {mu.blockers.length === 0 ? (
                            <span className="faint">none</span>
                          ) : (
                            <span className="pill bad" title={mu.blockers.join('; ')}>
                              {mu.blockers.length} blocker{mu.blockers.length === 1 ? '' : 's'}
                            </span>
                          )}
                        </td>
                        <td>
                          {mu.evidence.length === 0 ? (
                            <span className="faint">none yet</span>
                          ) : (
                            <span className="pill idle mono">{mu.evidence.length} run{mu.evidence.length === 1 ? '' : 's'}</span>
                          )}
                        </td>
                        <td>
                          <div className="row-actions">{renderActions(mu)}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))
          )}
        </div>
        <footer className="statusbar">
          {notice && <span>{notice}</span>}
          <span className="spacer" />
          {!canPromoteToTest && !canPromoteToProd && (
            <span className="faint">
              Promoting to test is the Platform Engineer&rsquo;s action; to prod is the
              Programme Manager&rsquo;s.
            </span>
          )}
        </footer>
      </section>

      <section className="pane" aria-label="Parallel-run window by site">
        <header className="pane-header">
          <h2>Parallel-run window by site</h2>
        </header>
        <div className="pane-body">
          {board && board.sites.length > 0 ? (
            <table className="estate">
              <caption className="visually-hidden">
                Each site&apos;s own parallel-run window, opened at its first real production promotion
              </caption>
              <thead>
                <tr>
                  <th>Site</th>
                  <th>Released</th>
                  <th>Window start</th>
                  <th>Window end</th>
                </tr>
              </thead>
              <tbody>
                {board.sites.map((site) => (
                  <tr key={site.site_id}>
                    <td>{site.name}</td>
                    <td className="numeric">
                      {site.released_mu_count} of {site.total_mu_count}
                    </td>
                    <td>{formatDate(site.parallel_run_start)}</td>
                    <td>{formatDate(site.parallel_run_end)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="empty">No site has a workbook in a train yet.</p>
          )}
        </div>
      </section>

      {prodDialogFor && (
        <ReasonDialog
          title="Promote to production"
          description="MA-09: explicit release approval by the Programme Manager. Recorded on the promotion's own evidence bundle."
          confirmLabel="Promote"
          busy={busyWorkbookId === prodDialogFor}
          error={prodError}
          onConfirm={(reason) => void promoteToProd(prodDialogFor, reason)}
          onCancel={() => {
            setProdDialogFor(null);
            setProdError(null);
          }}
        />
      )}
    </div>
  );
}
