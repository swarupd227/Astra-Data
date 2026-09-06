/**
 * The Pattern Library — story S5.5.3, closing F5.5.
 *
 * "As a platform engineer, I want a Pattern Library screen, so that I can see what the
 * platform has learned and govern it. Lists patterns by class and state with applications,
 * pass/fail, first seen, provenance origin; candidates awaiting promotion are a queue.
 * Actions: promote, retire with reason, edit guards (creates a new version), export."
 *
 * Governing a pattern (promote, retire, edit guards) is the platform engineer's — the same
 * `PlatformEngineerDep` role S5.5.1's own promote route already drives, reused for S5.5.2's
 * manual retirement and S5.5.3's guards edit; everyone else reads the identical screen with
 * no action buttons, the same hide-not-disable convention every other role-gated action in
 * this console already follows (Admin, S4.3.2). "Export" needs no role: it downloads the
 * same list the screen already rendered, nothing a viewer could not already see.
 *
 * The queue is CANDIDATE patterns sorted by how close each is to promotion (most distinct
 * passing calcs first, ties broken by fewest applications so a pattern proving itself
 * quickly surfaces before one accumulating slowly) — the same "ordered by what resolving it
 * unlocks" reasoning the Parse Quality Queue (S1.4.3) already applies to a different queue.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { ReasonDialog } from '../estate/ReasonDialog';
import type { Api, Identity, PatternProvenance, PatternRecord } from '../lib/api';
import { ApiError } from '../lib/api';
import { EditGuardsDialog } from './EditGuardsDialog';

interface Props {
  api: Api;
  identity: Identity;
}

function stateTone(state: PatternRecord['promotion_state']): string {
  if (state === 'ACTIVE') return 'ok';
  if (state === 'CANDIDATE') return 'warn';
  return 'bad';
}

function originLabel(provenance: PatternProvenance): string {
  return provenance.origin ?? '—';
}

type Pending = { kind: 'retire'; pattern: PatternRecord } | { kind: 'edit-guards'; pattern: PatternRecord } | null;

export function PatternLibrary({ api, identity }: Props): JSX.Element {
  const [patterns, setPatterns] = useState<PatternRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const canGovern = identity.roles.includes('platform_engineer');

  useEffect(() => {
    let live = true;
    api
      .patterns(identity)
      .then((response) => {
        if (!live) return;
        setPatterns(response.patterns);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (live) setError(caught instanceof ApiError ? caught.message : 'The Pattern Library could not be read.');
      });
    return () => {
      live = false;
    };
  }, [api, identity, nonce]);

  const reload = useCallback(() => setNonce((v) => v + 1), []);

  const queue = useMemo(() => {
    if (!patterns) return [];
    return patterns
      .filter((p) => p.promotion_state === 'CANDIDATE')
      .sort(
        (a, b) =>
          b.distinct_passing_calcs - a.distinct_passing_calcs
          || a.applications - b.applications
          || a.id.localeCompare(b.id),
      );
  }, [patterns]);

  const byClassAndState = useMemo(() => {
    if (!patterns) return [];
    const stateOrder: Record<PatternRecord['promotion_state'], number> = { ACTIVE: 0, CANDIDATE: 1, RETIRED: 2 };
    return [...patterns].sort(
      (a, b) =>
        a.class.localeCompare(b.class)
        || stateOrder[a.promotion_state] - stateOrder[b.promotion_state]
        || a.id.localeCompare(b.id),
    );
  }, [patterns]);

  const selected = patterns?.find((p) => p.id === selectedId) ?? null;

  const runPromote = useCallback(
    async (pattern: PatternRecord) => {
      setActionBusy(true);
      setActionError(null);
      try {
        const updated = await api.promotePattern(pattern.id, identity);
        setNotice(`Promoted ${updated.id} to ACTIVE.`);
        reload();
      } catch (caught: unknown) {
        setActionError(caught instanceof ApiError ? caught.message : 'The pattern could not be promoted.');
      } finally {
        setActionBusy(false);
      }
    },
    [api, identity, reload],
  );

  const runRetire = useCallback(
    async (reason: string) => {
      if (pending?.kind !== 'retire') return;
      setActionBusy(true);
      try {
        const updated = await api.retirePattern(pending.pattern.id, reason, identity);
        setNotice(`Retired ${updated.id}.`);
        setPending(null);
        reload();
      } catch (caught: unknown) {
        setActionError(caught instanceof ApiError ? caught.message : 'The pattern could not be retired.');
      } finally {
        setActionBusy(false);
      }
    },
    [api, identity, pending, reload],
  );

  const runEditGuards = useCallback(
    async (guards: string[], reason: string) => {
      if (pending?.kind !== 'edit-guards') return;
      setActionBusy(true);
      try {
        const created = await api.editPatternGuards(pending.pattern.id, guards, reason, identity);
        setNotice(`Created version ${created.version} (${created.id}).`);
        setSelectedId(created.id);
        setPending(null);
        reload();
      } catch (caught: unknown) {
        setActionError(caught instanceof ApiError ? caught.message : "The pattern's guards could not be edited.");
      } finally {
        setActionBusy(false);
      }
    },
    [api, identity, pending, reload],
  );

  const exportLibrary = useCallback(() => {
    if (!patterns) return;
    const blob = new Blob([JSON.stringify(patterns, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `pattern-library-${new Date().toISOString().slice(0, 10)}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }, [patterns]);

  return (
    <div className="workspace pattern-library-workspace">
      <section className="pane" aria-label="Pattern Library">
        <header className="pane-header">
          <h2>Pattern Library</h2>
          <span className="pill idle mono">{patterns?.length ?? 0} patterns</span>
          <span className="spacer" />
          <button type="button" className="btn small" onClick={reload}>
            Refresh
          </button>
          <button type="button" className="btn small" disabled={!patterns} onClick={exportLibrary}>
            Export
          </button>
        </header>
        <div className="pane-body">
          {error && <div className="banner">{error}</div>}
          {!error && patterns === null && <p className="empty">Reading the Pattern Library…</p>}
          {patterns && (
            <>
              <h3 className="section-title">
                Candidates awaiting promotion ({queue.length})
              </h3>
              {queue.length === 0 ? (
                <p className="faint">No candidate is currently awaiting promotion.</p>
              ) : (
                <table className="estate" aria-label="Promotion queue">
                  <thead>
                    <tr>
                      <th>Pattern</th>
                      <th className="numeric">Distinct passes</th>
                      <th className="numeric">Applications</th>
                      <th className="numeric">Failures</th>
                    </tr>
                  </thead>
                  <tbody>
                    {queue.map((pattern) => (
                      <PatternRow
                        key={pattern.id}
                        pattern={pattern}
                        selected={pattern.id === selectedId}
                        onSelect={() => setSelectedId(pattern.id)}
                      />
                    ))}
                  </tbody>
                </table>
              )}

              <h3 className="section-title">All patterns, by class and state</h3>
              <table className="estate" aria-label="Every pattern">
                <thead>
                  <tr>
                    <th>Pattern</th>
                    <th>Class</th>
                    <th>State</th>
                    <th className="numeric">Applications</th>
                    <th className="numeric">Pass</th>
                    <th className="numeric">Fail</th>
                    <th>First seen</th>
                    <th>Origin</th>
                  </tr>
                </thead>
                <tbody>
                  {byClassAndState.map((pattern) => (
                    <tr
                      key={pattern.id}
                      aria-selected={pattern.id === selectedId}
                      tabIndex={0}
                      onClick={() => setSelectedId(pattern.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          setSelectedId(pattern.id);
                        }
                      }}
                    >
                      <td>
                        <code>{pattern.id}</code>
                        {pattern.version > 1 && <span className="faint"> v{pattern.version}</span>}
                      </td>
                      <td>{pattern.class}</td>
                      <td>
                        <span className={`pill ${stateTone(pattern.promotion_state)}`}>
                          {pattern.promotion_state}
                        </span>
                      </td>
                      <td className="numeric">{pattern.applications}</td>
                      <td className="numeric">{pattern.pass_total}</td>
                      <td className="numeric">{pattern.failure_count}</td>
                      <td>{pattern.provenance.first_seen ?? '—'}</td>
                      <td>{originLabel(pattern.provenance)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {byClassAndState.length === 0 && (
                <p className="empty">No pattern has been generalised from a proof yet.</p>
              )}
            </>
          )}
        </div>
        <footer className="statusbar">
          {notice && <span>{notice}</span>}
        </footer>
      </section>

      <section className="pane" aria-label="Pattern detail">
        <header className="pane-header">
          <h2>Detail</h2>
        </header>
        <div className="pane-body">
          {!selected && <p className="empty">Select a pattern to see its guards, template and provenance.</p>}
          {selected && (
            <div className="detail">
              <h3>{selected.id}</h3>
              <dl>
                <dt>Class</dt>
                <dd>{selected.class}</dd>
                <dt>State</dt>
                <dd>
                  <span className={`pill ${stateTone(selected.promotion_state)}`}>{selected.promotion_state}</span>
                </dd>
                <dt>Version</dt>
                <dd>
                  {selected.version}
                  {selected.supersedes_id && <span className="faint"> (supersedes {selected.supersedes_id})</span>}
                </dd>
                <dt>Target template</dt>
                <dd><code>{selected.target_template}</code></dd>
                <dt>Guards</dt>
                <dd>
                  {selected.guards.length === 0 ? (
                    <span className="faint">none</span>
                  ) : (
                    <ul>
                      {selected.guards.map((guard) => (
                        <li key={guard}>{guard}</li>
                      ))}
                    </ul>
                  )}
                </dd>
                <dt>Applications</dt>
                <dd>{selected.applications} ({selected.pass_total} pass, {selected.failure_count} fail)</dd>
                <dt>Distinct passing calcs</dt>
                <dd>{selected.distinct_passing_calcs}</dd>
                <dt>First seen</dt>
                <dd>{selected.provenance.first_seen ?? '—'}</dd>
                <dt>Origin</dt>
                <dd>{originLabel(selected.provenance)}</dd>
                {selected.provenance.promoted_at && (
                  <>
                    <dt>Promoted</dt>
                    <dd>{selected.provenance.promoted_at} by {selected.provenance.approved_by}</dd>
                  </>
                )}
                {selected.provenance.retired_at && (
                  <>
                    <dt>Retired</dt>
                    <dd>
                      {selected.provenance.retired_at} by {selected.provenance.retired_by}
                      <br />
                      <span className="faint">{selected.provenance.retirement_reason}</span>
                    </dd>
                  </>
                )}
                {selected.provenance.edited_at && (
                  <>
                    <dt>Edited from</dt>
                    <dd>
                      {selected.provenance.edited_from} — {selected.provenance.edit_reason}
                    </dd>
                  </>
                )}
              </dl>

              {actionError && <p className="field-error">{actionError}</p>}

              {canGovern ? (
                <div className="actions-row">
                  {selected.promotion_state === 'CANDIDATE' && (
                    <button
                      type="button"
                      className="btn primary"
                      disabled={actionBusy}
                      onClick={() => void runPromote(selected)}
                    >
                      Promote
                    </button>
                  )}
                  {selected.promotion_state !== 'RETIRED' && (
                    <>
                      <button
                        type="button"
                        className="btn"
                        disabled={actionBusy}
                        onClick={() => setPending({ kind: 'edit-guards', pattern: selected })}
                      >
                        Edit guards…
                      </button>
                      <button
                        type="button"
                        className="btn danger"
                        disabled={actionBusy}
                        onClick={() => setPending({ kind: 'retire', pattern: selected })}
                      >
                        Retire…
                      </button>
                    </>
                  )}
                </div>
              ) : (
                <span className="faint">Promoting, retiring and editing guards is the Platform Engineer&rsquo;s.</span>
              )}
            </div>
          )}
        </div>
      </section>

      {pending?.kind === 'retire' && (
        <ReasonDialog
          title={`Retire ${pending.pattern.id}`}
          description="A retired pattern stops matching new calculations, and every Measure it produced is re-queued for regeneration."
          confirmLabel="Retire"
          danger
          busy={actionBusy}
          error={actionError}
          onConfirm={(reason) => void runRetire(reason)}
          onCancel={() => setPending(null)}
        />
      )}
      {pending?.kind === 'edit-guards' && (
        <EditGuardsDialog
          patternId={pending.pattern.id}
          initialGuards={pending.pattern.guards}
          busy={actionBusy}
          error={actionError}
          onConfirm={(guards, reason) => void runEditGuards(guards, reason)}
          onCancel={() => setPending(null)}
        />
      )}
    </div>
  );
}

function PatternRow({
  pattern,
  selected,
  onSelect,
}: {
  pattern: PatternRecord;
  selected: boolean;
  onSelect: () => void;
}): JSX.Element {
  return (
    <tr
      aria-selected={selected}
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <td><code>{pattern.id}</code></td>
      <td className="numeric">{pattern.distinct_passing_calcs}</td>
      <td className="numeric">{pattern.applications}</td>
      <td className="numeric">{pattern.failure_count}</td>
    </tr>
  );
}
