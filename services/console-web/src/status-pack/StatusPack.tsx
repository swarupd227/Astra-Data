/**
 * The Status Pack -- story S10.2.1, opening F10.2.
 *
 * §15.3.1's own AC: "generated weekly as an editable narrative with the numbers and
 * charts of the Board, exportable to PDF and PPTX; edits are stored with the version."
 * See `status_pack.py`'s own module docstring for the disclosed readings this screen
 * relies on:
 *
 * - "generated weekly" is read as POST-triggered by a Programme Manager, not a
 *   scheduler this platform does not have -- the same "a real action, not a claim of
 *   automation nobody could verify" posture already used elsewhere in this codebase.
 * - "the numbers ... of the Board" is the same real `kpi_strip`/`train_swimlanes`/
 *   `milestone_rail`/`exception_ageing` facts the Programme Board itself renders, frozen
 *   onto the pack at generation time rather than re-read live -- the pack is a snapshot,
 *   matching "edits are stored with the version" needing something stable to attach an
 *   edit to.
 * - "charts" are rendered as real tables in the PDF/PPTX exports, not native charts --
 *   a disclosed scope boundary (see `render_pdf`/`render_pptx`).
 * - The reader gate here is deliberately `ArtizentDep` only -- no client persona is named
 *   anywhere in spec/backlog as a Status Pack reader (unlike the Calibration Report's
 *   named client analytics lead), so "publish to client" records a real state transition
 *   (`published_at`) only, not an actual outward delivery this platform cannot verify.
 */

import { useCallback, useEffect, useState } from 'react';

import { downloadBlob } from '../lib/download';
import type { Api, Identity, StatusPackData } from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
  liveTick?: number;
}

export function StatusPack({ api, identity, liveTick }: Props): JSX.Element {
  const [pack, setPack] = useState<StatusPackData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [narrativeDraft, setNarrativeDraft] = useState('');
  const [busy, setBusy] = useState(false);

  const canManage = identity.roles.includes('programme_manager');

  const load = useCallback(() => {
    let live = true;
    setLoading(true);
    api
      .statusPack(identity)
      .then((response) => {
        if (!live) return;
        setPack(response);
        setNarrativeDraft(response.narrative);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        // The service answers "no Status Pack has been generated yet" as a real 400
        // (`InvalidRequestError`, `errors.py`) -- not a 404, since nothing named by the
        // request path is missing; the request itself is simply premature. Read as the
        // screen's own empty state rather than a failure.
        if (caught instanceof ApiError && caught.status === 400) {
          setPack(null);
          setError(null);
        } else {
          setError(caught instanceof ApiError ? caught.message : 'The Status Pack could not be read.');
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
      const response = await api.generateStatusPack(identity);
      setPack(response);
      setNarrativeDraft(response.narrative);
      setNotice(`Generated version ${response.version} for the week of ${response.week_of}.`);
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The Status Pack could not be generated.');
    } finally {
      setBusy(false);
    }
  }, [api, identity]);

  const saveEdit = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const response = await api.editStatusPack(narrativeDraft, identity);
      setPack(response);
      setNotice(`Saved as version ${response.version}.`);
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The edit could not be saved.');
    } finally {
      setBusy(false);
    }
  }, [api, identity, narrativeDraft]);

  const publish = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const response = await api.publishStatusPack(identity);
      setPack(response);
      setNotice('Published to the client.');
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The pack could not be published.');
    } finally {
      setBusy(false);
    }
  }, [api, identity]);

  const exportAs = useCallback(
    async (kind: 'pdf' | 'pptx') => {
      setBusy(true);
      try {
        const blob = kind === 'pdf' ? await api.statusPackPdf(identity) : await api.statusPackPptx(identity);
        downloadBlob(blob, `status-pack.${kind}`);
      } catch {
        setNotice(`The ${kind.toUpperCase()} could not be exported.`);
      } finally {
        setBusy(false);
      }
    },
    [api, identity],
  );

  const dirty = pack !== null && narrativeDraft !== pack.narrative;

  return (
    <div className="workspace status-pack">
      <section className="pane" aria-label="Status Pack">
        <header className="pane-header">
          <h2>Status Pack</h2>
          {pack && (
            <span className={`pill ${pack.published_at ? 'ok' : 'idle'} mono`}>
              v{pack.version} · week of {pack.week_of}
              {pack.published_at ? ' · published' : ''}
            </span>
          )}
        </header>
        <div className="pane-body">
          {error ? (
            <div className="banner">{error}</div>
          ) : loading && !pack ? (
            <p className="empty">Reading the Status Pack…</p>
          ) : !pack ? (
            <p className="empty">No Status Pack has been generated yet.</p>
          ) : (
            <div className="detail">
              <h3>Narrative</h3>
              {canManage ? (
                <textarea
                  className="status-pack-narrative"
                  rows={8}
                  value={narrativeDraft}
                  onChange={(e) => setNarrativeDraft(e.target.value)}
                />
              ) : (
                <p>{pack.narrative}</p>
              )}

              <h3>KPI strip (as of generation)</h3>
              <dl>
                <dt>MUs by state</dt>
                <dd>
                  {Object.entries(pack.report.kpis.mus_by_state)
                    .map(([state, count]) => `${state}: ${count}`)
                    .join(', ') || '—'}
                </dd>
                <dt>First-pass parity</dt>
                <dd>
                  {pack.report.kpis.first_pass_parity.first_pass_rate === null
                    ? '—'
                    : `${(pack.report.kpis.first_pass_parity.first_pass_rate * 100).toFixed(1)}%`}
                </dd>
                <dt>Absorption vs calibrated baseline</dt>
                <dd>
                  {pack.report.kpis.absorption.mean_ratio === null
                    ? '—'
                    : `${(pack.report.kpis.absorption.mean_ratio * 100).toFixed(1)}% (threshold ${(pack.report.kpis.absorption.threshold * 100).toFixed(0)}%)`}
                </dd>
                <dt>Gates due this week</dt>
                <dd>
                  {pack.report.kpis.gates_due_this_week.due_this_week_count} due, {' '}
                  {pack.report.kpis.gates_due_this_week.already_breached_count} already breached
                </dd>
                <dt>Spend vs budget</dt>
                <dd>
                  ${Math.round(pack.report.kpis.spend_vs_budget.spend).toLocaleString('en-US')} / $
                  {Math.round(pack.report.kpis.spend_vs_budget.budget).toLocaleString('en-US')}
                </dd>
              </dl>

              <h3>Train swimlanes</h3>
              <p>
                {pack.report.swimlanes.trains.length} trains,{' '}
                {pack.report.swimlanes.trains.reduce((sum, t) => sum + t.blocked_count, 0)} blocked MUs
                {pack.report.swimlanes.orphaned_blocked_count > 0
                  ? ` (plus ${pack.report.swimlanes.orphaned_blocked_count} orphaned)`
                  : ''}
              </p>

              <h3>Milestones</h3>
              <p>{pack.report.milestones.rail.length} entries on the milestone rail</p>
            </div>
          )}
        </div>
        <footer className="statusbar">
          {notice && <span>{notice}</span>}
          <span className="spacer" />
          {canManage ? (
            <>
              <button type="button" className="btn" disabled={busy} onClick={() => void generate()}>
                {pack ? "Regenerate this week's pack" : 'Generate this week’s pack'}
              </button>
              {pack && (
                <>
                  <button type="button" className="btn" disabled={busy || !dirty} onClick={() => void saveEdit()}>
                    Save edit
                  </button>
                  <button
                    type="button"
                    className="btn primary"
                    disabled={busy || !!pack.published_at}
                    onClick={() => void publish()}
                  >
                    {pack.published_at ? 'Published' : 'Publish to client'}
                  </button>
                </>
              )}
            </>
          ) : (
            <span className="faint">Generating, editing and publishing are the Programme Manager&rsquo;s actions.</span>
          )}
          {pack && (
            <>
              <button type="button" className="btn" disabled={busy} onClick={() => void exportAs('pdf')}>
                Export as PDF
              </button>
              <button type="button" className="btn" disabled={busy} onClick={() => void exportAs('pptx')}>
                Export as PPTX
              </button>
            </>
          )}
        </footer>
      </section>
    </div>
  );
}
