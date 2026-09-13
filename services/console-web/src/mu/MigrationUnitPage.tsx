/**
 * The Migration Unit page -- §15.4, story S10.3.1, opening F10.3.
 *
 * "One page per report with everything about it, so that there is a single URL to send
 * to anyone about any report." One `GET /v1/mu/{workbook_id}` call returns every
 * section §15.4 names (see `mu_page.py`'s own module docstring for exactly how each one
 * is assembled); the server itself decides how much of it a caller sees -- an Artizent
 * role gets the full document, a client report owner gets a strict slice with
 * `exceptions` absent and `artefacts.model_ref`/`.measures`/`.git` all honestly
 * empty/null, never the full document with fields hidden here.
 *
 * **The URL is `?workbook=`, not a path segment** -- this SPA has no path-param router
 * (confirmed: `App.tsx`'s own `SURFACES` is a flat array matched by exact top-level
 * segment); the identical deep-link convention `G3Card.tsx`/`ParityDashboard.tsx`
 * already use (`lib/deep-link.ts`, read once at mount, written back on load) gives
 * "a single URL to send to anyone" the same way those two screens already do.
 *
 * **Provenance is its own lazy fetch, not part of the initial payload.** §15.4 excludes
 * it from every client view, and `mu_page.py`'s own docstring discloses it as the one
 * real, un-avoidable fan-out in this whole page -- fetched only once an Artizent viewer
 * actually expands that section, so the initial page-open budget (§15.6: 500 ms p95)
 * never pays for it.
 *
 * **Artefact previews (the Source screenshot, report page thumbnails) lazy-load their
 * own bytes.** The page's own JSON carries only metadata (an artefact id, kind,
 * dimensions) -- the same "an `<img src>` cannot carry this console's own identity
 * headers" problem SSE and PDF/PPTX export already had (ADR 0072, ADR 0073), so a
 * preview is fetched on demand via `getBlob()` (`GET /v1/artefacts/{id}/content`, story
 * S10.3.1 widened to the report owner too) and rendered from an object URL only once a
 * reader actually asks to see it, not inline with the rest of the page.
 */

import { useCallback, useEffect, useState } from 'react';

import type {
  Api,
  Identity,
  MuArtefactRecord,
  MuPageResponse,
  MuProvenanceRecord,
} from '../lib/api';
import { ApiError } from '../lib/api';
import { setDeepLinkParam } from '../lib/deep-link';
import { isArtizentRole } from '../lib/roles';

interface Props {
  api: Api;
  identity: Identity;
  initialWorkbookId?: string;
}

function pct(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}

function gatePillClass(decision: string | null): string {
  if (decision === 'APPROVED') return 'pill ok';
  if (decision === 'CHANGES_REQUESTED' || decision === 'REDESIGN') return 'pill bad';
  return 'pill idle';
}

function ArtefactPreview({
  api, identity, record, label,
}: {
  api: Api; identity: Identity; record: MuArtefactRecord; label: string;
}): JSX.Element {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const blob = await api.getArtefactContent(record.id, identity);
      setObjectUrl(URL.createObjectURL(blob));
    } finally {
      setLoading(false);
    }
  }, [api, identity, record.id]);

  useEffect(() => () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  }, [objectUrl]);

  if (objectUrl) {
    return <img src={objectUrl} alt={label} className="mu-artefact-preview" />;
  }
  return (
    <button type="button" className="btn" disabled={loading} onClick={() => void load()}>
      {loading ? 'Loading…' : `Show ${label}`}
    </button>
  );
}

export function MigrationUnitPage({ api, identity, initialWorkbookId }: Props): JSX.Element {
  const [workbookId, setWorkbookId] = useState(initialWorkbookId ?? '');
  const [loadedWorkbookId, setLoadedWorkbookId] = useState<string | null>(null);
  const [page, setPage] = useState<MuPageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [provenance, setProvenance] = useState<MuProvenanceRecord[] | null>(null);
  const [provenanceBusy, setProvenanceBusy] = useState(false);
  const [provenanceError, setProvenanceError] = useState<string | null>(null);

  const isArtizent = isArtizentRole(identity.roles);

  const load = useCallback(
    async (id: string) => {
      if (!id.trim()) return;
      setBusy(true);
      setError(null);
      setProvenance(null);
      setProvenanceError(null);
      try {
        const result = await api.muPage(id.trim(), identity);
        setPage(result);
        setLoadedWorkbookId(id.trim());
      } catch (caught: unknown) {
        setPage(null);
        setLoadedWorkbookId(null);
        setError(caught instanceof ApiError ? caught.message : 'The Migration Unit page could not be read.');
      } finally {
        setBusy(false);
      }
    },
    [api, identity],
  );

  useEffect(() => {
    setDeepLinkParam('workbook', loadedWorkbookId);
  }, [loadedWorkbookId]);

  useEffect(() => {
    if (initialWorkbookId) void load(initialWorkbookId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadProvenance = useCallback(async () => {
    if (!loadedWorkbookId) return;
    setProvenanceBusy(true);
    setProvenanceError(null);
    try {
      const result = await api.muProvenance(loadedWorkbookId, identity);
      setProvenance(result.records);
    } catch (caught: unknown) {
      setProvenanceError(caught instanceof ApiError ? caught.message : 'Provenance could not be read.');
    } finally {
      setProvenanceBusy(false);
    }
  }, [api, identity, loadedWorkbookId]);

  return (
    <div className="workspace mu-page-workspace">
      <section className="pane mu-page" aria-label="Migration Unit page">
        <header className="pane-header">
          <h2>Migration Unit page</h2>
          <label>
            Workbook
            <input
              type="text"
              value={workbookId}
              onChange={(e) => setWorkbookId(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void load(workbookId)}
              placeholder="workbook id"
            />
          </label>
          <button type="button" className="btn" disabled={busy || !workbookId.trim()} onClick={() => void load(workbookId)}>
            {busy ? 'Loading…' : 'Open'}
          </button>
        </header>
        <div className="pane-body">
          {error && <div className="banner">{error}</div>}
          {!error && !page && !busy && <p className="empty">Enter a workbook id to open its Migration Unit page.</p>}
          {busy && !page && <p className="empty">Reading the Migration Unit page…</p>}

          {page && (
            <div className="detail">
              <section aria-label="Header" className="mu-header">
                <h3>{page.header.name ?? page.workbook_id}</h3>
                <dl>
                  <dt>Site / Project</dt>
                  <dd>{page.header.site?.name ?? '—'} / {page.header.project?.name ?? '—'}</dd>
                  <dt>State</dt>
                  <dd>{page.header.state ?? 'not yet in a train'}</dd>
                  <dt>Tier</dt>
                  <dd>{page.header.tier ?? 'not yet tiered'}</dd>
                  <dt>Family</dt>
                  <dd>{page.header.family ? `${page.header.family.name} (${page.header.family.state})` : 'not yet clustered'}</dd>
                  <dt>Train</dt>
                  <dd>{page.header.train ? `${page.header.train.name} (#${page.header.train.sequence})` : 'not yet sequenced'}</dd>
                  <dt>Owner</dt>
                  <dd>{page.header.owner?.name ?? 'unassigned'}</dd>
                </dl>
                <div className="actions-row" role="group" aria-label="Gate status strip">
                  {page.header.gate_status_strip.map((g) => (
                    <span key={g.gate} className={gatePillClass(g.decision)}>
                      {g.gate}: {g.decision ?? 'pending'}
                    </span>
                  ))}
                </div>
              </section>

              <section aria-label="Source">
                <h3>Source</h3>
                <p>
                  {page.source.worksheets.length} worksheet(s), {page.source.dashboards.length} dashboard(s),{' '}
                  {page.source.datasources.length} datasource(s), {page.source.calculated_fields.length} calculation(s)
                </p>
                <table className="estate">
                  <thead>
                    <tr><th>Calculation</th><th>Class</th></tr>
                  </thead>
                  <tbody>
                    {page.source.calculated_fields.map((c) => (
                      <tr key={c.id}><td>{c.name}</td><td>{c.class ?? 'unclassified'}</td></tr>
                    ))}
                  </tbody>
                </table>
                {page.source.screenshot ? (
                  <ArtefactPreview api={api} identity={identity} record={page.source.screenshot} label="screenshot" />
                ) : (
                  <p className="faint">No screenshot captured yet.</p>
                )}
              </section>

              <section aria-label="Artefacts">
                <h3>Artefacts</h3>
                {page.artefacts.report ? (
                  <>
                    <p>
                      {page.artefacts.report.pages.length} page(s), {page.artefacts.report.visual_count} visual(s)
                    </p>
                    <div className="actions-row">
                      {page.artefacts.report.visuals.map((v) => (
                        <span key={v.id} className="pill idle mono">{v.page}</span>
                      ))}
                    </div>
                  </>
                ) : (
                  <p className="faint">No report has been composed yet.</p>
                )}
                {page.artefacts.documentation ? (
                  <p>Documentation generated {page.artefacts.documentation.generated_at}.</p>
                ) : (
                  <p className="faint">No documentation has been generated yet.</p>
                )}
                {isArtizent && (
                  <>
                    <h4>Measures</h4>
                    {page.artefacts.measures.length === 0 ? (
                      <p className="faint">No candidate measures yet.</p>
                    ) : (
                      <ul>
                        {page.artefacts.measures.map((m, i) => (
                          <li key={i}>{m.name} -- {m.dedup_decision}</li>
                        ))}
                      </ul>
                    )}
                    <h4>Git</h4>
                    {page.artefacts.git ? (
                      <p className="mono">{page.artefacts.git.git_ref} @ {page.artefacts.git.git_commit_sha}</p>
                    ) : (
                      <p className="faint">No deploy has run yet.</p>
                    )}
                  </>
                )}
              </section>

              <section aria-label="Parity">
                <h3>Parity</h3>
                {page.parity ? (
                  <>
                    <p className={page.parity.passes_the_charter ? 'pill ok' : 'pill bad'}>
                      {page.parity.passes_the_charter ? 'Passes the charter' : 'Does not yet pass the charter'}
                      {' '}(charter {page.parity.charter_version})
                    </p>
                    <table className="estate">
                      <thead>
                        <tr><th>Sheet</th><th className="numeric">Pass</th><th className="numeric">Fail</th><th className="numeric">First-pass</th></tr>
                      </thead>
                      <tbody>
                        {page.parity.sheets.map((s) => (
                          <tr key={s.sheet_ref}>
                            <td>{s.sheet_name}</td>
                            <td className="numeric">{s.pass}</td>
                            <td className="numeric">{s.fail}</td>
                            <td className="numeric">{pct(s.first_pass_rate)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                ) : (
                  <p className="faint">No ParityRun has ever covered this workbook yet.</p>
                )}
              </section>

              {isArtizent && page.exceptions && (
                <section aria-label="Exceptions">
                  <h3>Exceptions</h3>
                  {page.exceptions.cases.length === 0 ? (
                    <p className="faint">No exception has ever opened for this workbook.</p>
                  ) : (
                    <table className="estate">
                      <thead>
                        <tr><th>Class</th><th>State</th><th>Decisions</th></tr>
                      </thead>
                      <tbody>
                        {page.exceptions.cases.map((c) => (
                          <tr key={c.id} className={c.state === 'BLOCKED' ? 'flagged' : undefined}>
                            <td>{c.class}</td>
                            <td>{c.state}</td>
                            <td>{c.decisions.map((d) => d.decision).join(', ') || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </section>
              )}

              <section aria-label="Gates">
                <h3>Gates</h3>
                <dl>
                  <dt>G1 -- Tolerance Charter</dt>
                  <dd>{page.gates.g1?.decision ?? 'no decision yet'}</dd>
                  <dt>G2 -- Model (inherited from family)</dt>
                  <dd>{page.gates.g2?.decision ?? 'no decision yet'}</dd>
                  <dt>G4 -- Site status</dt>
                  <dd>{page.gates.g4?.decision ?? 'no decision yet'}</dd>
                </dl>
                <h4>G3 card</h4>
                <p>
                  {page.gates.g3.proof.cases_pass}/{page.gates.g3.proof.cases_run} parity cases pass -- {' '}
                  {page.gates.g3.latest_decision?.decision ?? 'no decision yet'}
                </p>
              </section>

              <section aria-label="Timeline">
                <h3>Timeline</h3>
                {page.timeline.events.length === 0 ? (
                  <p className="faint">No event has been recorded for this workbook yet.</p>
                ) : (
                  <ul className="milestone-rail">
                    {page.timeline.events.map((e) => (
                      <li key={e.sequence}>
                        <span className="mono">{e.event.time}</span>
                        <span>{e.event.type}</span>
                        <span className="faint">{e.event.principal as string}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {isArtizent && (
                <section aria-label="Provenance">
                  <h3>Provenance</h3>
                  {provenanceError && <div className="banner">{provenanceError}</div>}
                  {provenance === null ? (
                    <button type="button" className="btn" disabled={provenanceBusy} onClick={() => void loadProvenance()}>
                      {provenanceBusy ? 'Loading…' : 'Load provenance'}
                    </button>
                  ) : provenance.length === 0 ? (
                    <p className="faint">No provenance record exists for this MU's own artefacts yet.</p>
                  ) : (
                    <ul>
                      {provenance.map((r) => (
                        <li key={r.id} className="mono">
                          {r.mode} -- {r.inputs.subject_ref} -- {r.created_at}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
