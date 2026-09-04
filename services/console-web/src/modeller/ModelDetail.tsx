/**
 * The Model Detail screen — story S4.1.2.
 *
 * "As a model engineer, I want to edit the proposal in the Model Detail screen and submit
 * it for G2, so that the design the client approves is the design we build."
 *
 * No Foundry Workbench exists yet (§15.3.2's family list/queue is unassigned to any
 * backlog story so far) — the family list on the left is the minimal navigation this
 * screen needs to be reachable at all, not an attempt at that fuller screen. It reads
 * `GET /v1/families`, already built (S3.1.1).
 *
 * Five tabs, matching S4.1.2's own acceptance criteria exactly — not §15.3.2's fuller list
 * (which adds a sixth, "Versions"): Design, Measures, RLS, Open Questions, Build. Editing
 * is deliberately narrow (ADR 0029) — the grain statement, a table's storage mode, and one
 * relationship's cardinality — and only while the family is DRAFT (§12.2's own rule).
 *
 * "Class" and "pattern" on the Measures tab are shown as pending: the Transpiler (E5) is
 * what produces them, and this screen names the gap rather than rendering a blank column,
 * the same convention the Estate Explorer set for MU properties nothing produces yet.
 *
 * Since S4.3.1, the Build tab renders the latest real build attempt — result, commit,
 * workspace, and every step's own pass/fail with its detail — reading `GET /v1/families/
 * {id}/build`. A design builds automatically the moment it is approved at G2
 * (`routes_g2.approve_route`); "Build now"/"Rebuild" here is the manual retry a failed
 * automatic attempt's own logged reason calls for.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import type {
  Api,
  BuildRecord,
  DesignDocument,
  FamilyRecord,
  FamilyTransition,
  Identity,
  ModelVersion,
} from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
}

type TabKey = 'design' | 'measures' | 'rls' | 'questions' | 'build' | 'versions';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'design', label: 'Design' },
  { key: 'measures', label: 'Measures' },
  { key: 'rls', label: 'RLS' },
  { key: 'questions', label: 'Open Questions' },
  { key: 'build', label: 'Build' },
  { key: 'versions', label: 'Versions' },
];

export function ModelDetail({ api, identity }: Props): JSX.Element {
  const [families, setFamilies] = useState<FamilyRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const canEdit = identity.roles.includes('semantic_model_engineer');

  useEffect(() => {
    let live = true;
    api
      .families(identity)
      .then((response) => {
        if (!live) return;
        setFamilies(response.families);
        setError(null);
        setSelectedId((current) => current ?? response.families[0]?.id ?? null);
      })
      .catch((caught: unknown) => {
        if (live) setError(caught instanceof ApiError ? caught.message : 'Families could not be read.');
      });
    return () => {
      live = false;
    };
  }, [api, identity, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const selected = families?.find((f) => f.id === selectedId) ?? null;

  return (
    <div className="model-detail-workspace">
      <section className="pane family-list" aria-label="Model families">
        <header className="pane-header">
          <h2>Families</h2>
        </header>
        <div className="pane-body">
          {error && <div className="banner">{error}</div>}
          {!error && families === null && <p className="empty">Reading families…</p>}
          {!error && families && families.length === 0 && <p className="empty">No families yet.</p>}
          {families && families.length > 0 && (
            <ul className="family-list-items">
              {families.map((family) => (
                <li key={family.id}>
                  <button
                    type="button"
                    className="family-list-item"
                    aria-current={family.id === selectedId}
                    onClick={() => setSelectedId(family.id)}
                  >
                    <span className="family-list-name">{family.name}</span>
                    <span className={`pill ${stateTone(family.state)}`}>{family.state ?? 'UNKNOWN'}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {selected ? (
        <ModelDetailPanel
          key={selected.id}
          api={api}
          identity={identity}
          family={selected}
          canEdit={canEdit}
          onFamilyChanged={reload}
        />
      ) : (
        <section className="pane" aria-label="Model Detail">
          <div className="pane-body">
            <p className="empty">Select a family to see its design.</p>
          </div>
        </section>
      )}
    </div>
  );
}

function stateTone(state: string | null): string {
  if (state === 'IN_REVIEW' || state === 'APPROVED') return 'warn';
  if (state === 'BUILT' || state === 'PUBLISHED') return 'ok';
  if (state === 'DRAFT') return 'idle';
  return 'idle';
}

// -------------------------------------------------------------------- the detail panel

function ModelDetailPanel({
  api,
  identity,
  family,
  canEdit,
  onFamilyChanged,
}: {
  api: Api;
  identity: Identity;
  family: FamilyRecord;
  canEdit: boolean;
  onFamilyChanged: () => void;
}): JSX.Element {
  const [design, setDesign] = useState<DesignDocument | null | 'none'>(null);
  const [designError, setDesignError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>('design');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [transitions, setTransitions] = useState<FamilyTransition[]>([]);
  const [designNonce, setDesignNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setDesign(null);
    api
      .getDesign(family.id, identity)
      .then((doc) => {
        if (live) {
          setDesign(doc);
          setDesignError(null);
        }
      })
      .catch((caught: unknown) => {
        if (!live) return;
        if (caught instanceof ApiError && caught.status === 404) {
          setDesign('none');
          setDesignError(null);
        } else {
          setDesign('none');
          setDesignError(caught instanceof ApiError ? caught.message : 'The design could not be read.');
        }
      });
    return () => {
      live = false;
    };
  }, [api, identity, family.id, designNonce]);

  useEffect(() => {
    let live = true;
    api
      .familyTransitions(family.id, identity)
      .then((response) => {
        if (live) setTransitions(response.transitions);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [api, identity, family.id, designNonce]);

  const reloadDesign = useCallback(() => setDesignNonce((n) => n + 1), []);

  const proposeDesign = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      await api.proposeDesign(family.id, identity);
      setNotice('Design proposal generated.');
      reloadDesign();
    } catch (caught: unknown) {
      setNotice(
        caught instanceof ApiError ? caught.message : 'The design proposal could not be generated.',
      );
    } finally {
      setBusy(false);
    }
  }, [api, identity, family.id, reloadDesign]);

  const accept = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      await api.acceptFamily(family.id, identity);
      setNotice('Family accepted into DRAFT.');
      onFamilyChanged();
      reloadDesign();
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The family could not be accepted.');
    } finally {
      setBusy(false);
    }
  }, [api, identity, family.id, onFamilyChanged, reloadDesign]);

  const submit = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.submitForReview(family.id, identity);
      setNotice(`Submitted for G2 — version ${result.version}.`);
      onFamilyChanged();
      reloadDesign();
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The design could not be submitted.');
    } finally {
      setBusy(false);
    }
  }, [api, identity, family.id, onFamilyChanged, reloadDesign]);

  const isDraft = family.state === 'DRAFT';
  const canAccept = canEdit && (family.state === 'PROPOSED' || family.state === 'SINGLETON');
  const canSubmit = canEdit && isDraft && design !== null && design !== 'none';

  return (
    <section className="pane model-detail-panel" aria-label={`Model Detail — ${family.name}`}>
      <header className="pane-header model-detail-header">
        <h2>{family.name}</h2>
        <span className={`pill ${stateTone(family.state)}`}>{family.state ?? 'UNKNOWN'}</span>
        {design && design !== 'none' && design.version && (
          <span className="pill idle mono" title="Frozen at submission">
            {design.version.slice(0, 15)}…
          </span>
        )}
        <span className="spacer" />
        {canEdit && (family.state === 'PROPOSED' || family.state === 'SINGLETON') ? (
          design === 'none' ? (
            <button type="button" className="btn small" disabled={busy} onClick={() => void proposeDesign()}>
              {busy ? 'Generating…' : 'Generate proposal'}
            </button>
          ) : (
            <button type="button" className="btn small primary" disabled={!canAccept || busy} onClick={() => void accept()}>
              {busy ? 'Accepting…' : 'Accept'}
            </button>
          )
        ) : null}
        {canEdit && isDraft && (
          <button type="button" className="btn small primary" disabled={!canSubmit || busy} onClick={() => void submit()}>
            {busy ? 'Submitting…' : 'Submit for G2'}
          </button>
        )}
      </header>

      <nav className="model-detail-tabs" aria-label="Model Detail tabs">
        {TABS.map((option) => (
          <button
            type="button"
            key={option.key}
            className="model-detail-tab"
            aria-current={tab === option.key}
            onClick={() => setTab(option.key)}
          >
            {option.label}
          </button>
        ))}
      </nav>

      <div className="pane-body model-detail-body">
        {designError && <div className="banner">{designError}</div>}
        {design === null && !designError && <p className="empty">Reading the design…</p>}
        {design === 'none' && !designError && (
          <p className="empty">
            No design proposal has been generated for this family yet.
            <br />
            {canEdit ? 'Click "Generate proposal" above.' : 'A Semantic Model Engineer generates one.'}
          </p>
        )}
        {design && design !== 'none' && tab === 'design' && (
          <DesignTab
            api={api}
            identity={identity}
            family={family}
            design={design}
            editable={isDraft && canEdit}
            onChanged={reloadDesign}
          />
        )}
        {design && design !== 'none' && tab === 'measures' && <MeasuresTab design={design} />}
        {design && design !== 'none' && tab === 'rls' && <RlsTab design={design} />}
        {design && design !== 'none' && tab === 'questions' && <OpenQuestionsTab design={design} />}
        {tab === 'build' && (
          <BuildTab
            api={api}
            identity={identity}
            family={family}
            transitions={transitions}
            canEdit={canEdit}
            onFamilyChanged={onFamilyChanged}
          />
        )}
        {tab === 'versions' && (
          <VersionsTab
            api={api}
            identity={identity}
            family={family}
            canEdit={canEdit}
            onFamilyChanged={onFamilyChanged}
            onDesignChanged={reloadDesign}
          />
        )}
      </div>

      <footer className="statusbar">
        {notice && <span>{notice}</span>}
        <span className="spacer" />
        {!canEdit && <span className="faint">Editing a design is the Semantic Model Engineer&rsquo;s.</span>}
      </footer>
    </section>
  );
}

// ------------------------------------------------------------------------- Design tab

function DesignTab({
  api,
  identity,
  family,
  design,
  editable,
  onChanged,
}: {
  api: Api;
  identity: Identity;
  family: FamilyRecord;
  design: DesignDocument;
  editable: boolean;
  onChanged: () => void;
}): JSX.Element {
  const [grainDraft, setGrainDraft] = useState(design.grain_statement ?? '');
  const [savingGrain, setSavingGrain] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setGrainDraft(design.grain_statement ?? '');
  }, [design.grain_statement]);

  const saveGrain = async (): Promise<void> => {
    setSavingGrain(true);
    setError(null);
    try {
      await api.editGrainStatement(family.id, grainDraft, identity);
      onChanged();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : 'The grain statement could not be saved.');
    } finally {
      setSavingGrain(false);
    }
  };

  const setMode = async (tableId: string, mode: string): Promise<void> => {
    setError(null);
    try {
      await api.setTableMode(family.id, tableId, mode, identity);
      onChanged();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : 'The storage mode could not be saved.');
    }
  };

  const setCardinality = async (fromTable: string, toTable: string, cardinality: string): Promise<void> => {
    setError(null);
    try {
      await api.setRelationshipCardinality(family.id, fromTable, toTable, cardinality, identity);
      onChanged();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : 'The cardinality could not be saved.');
    }
  };

  return (
    <div className="detail">
      {error && <p className="field-error">{error}</p>}

      <div>
        <h3>Grain</h3>
        {editable ? (
          <div className="actions-row">
            <textarea
              aria-label="Grain statement"
              value={grainDraft}
              onChange={(e) => setGrainDraft(e.target.value)}
              rows={2}
              style={{ flex: 1 }}
            />
            <button
              type="button"
              className="btn small"
              disabled={savingGrain || grainDraft.trim() === (design.grain_statement ?? '')}
              onClick={() => void saveGrain()}
            >
              {savingGrain ? 'Saving…' : 'Save'}
            </button>
          </div>
        ) : (
          <p>{design.grain_statement ?? '—'}</p>
        )}
      </div>

      <div>
        <h3>Conformed dimensions</h3>
        {design.conformed_dimensions.length === 0 ? (
          <p className="faint">None identified.</p>
        ) : (
          <ul className="plain-list">
            {design.conformed_dimensions.map((dim) => (
              <li key={dim.dimension}>
                <strong>{dim.dimension}</strong>
                {dim.shared_with_family_ids.length > 0 ? (
                  <span className="muted"> — shared with {dim.shared_with_family_ids.length} other family/families</span>
                ) : (
                  <span className="faint"> — not shared with any other family</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h3>Tables</h3>
        <table className="estate">
          <thead>
            <tr>
              <th>Table</th>
              <th>Schema</th>
              <th className="numeric">Row estimate</th>
              <th>Mode</th>
            </tr>
          </thead>
          <tbody>
            {design.tables.map((table) => (
              <tr key={table.id}>
                <td>{table.name}</td>
                <td>{table.schema ?? '—'}</td>
                <td className="numeric">{table.row_estimate?.toLocaleString() ?? '—'}</td>
                <td>
                  {editable ? (
                    <select
                      aria-label={`Storage mode for ${table.name}`}
                      value={table.mode}
                      onChange={(e) => void setMode(table.id, e.target.value)}
                    >
                      <option value="import">import</option>
                      <option value="directquery">directquery</option>
                      <option value="directlake">directlake</option>
                    </select>
                  ) : (
                    <span title={table.mode_reason}>{table.mode}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div>
        <h3>Relationships</h3>
        {design.relationships.length === 0 ? (
          <p className="faint">No relationships proposed.</p>
        ) : (
          <>
            <RelationshipDiagram design={design} />
            <table className="estate">
              <thead>
                <tr>
                  <th>From</th>
                  <th>To</th>
                  <th>Cardinality</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {design.relationships.map((rel) => (
                  <tr key={`${rel.from_table}-${rel.to_table}`}>
                    <td>{tableName(design, rel.from_table)}</td>
                    <td>{tableName(design, rel.to_table)}</td>
                    <td>
                      {editable ? (
                        <select
                          aria-label={`Cardinality for ${tableName(design, rel.from_table)} to ${tableName(design, rel.to_table)}`}
                          value={rel.cardinality ?? ''}
                          onChange={(e) => void setCardinality(rel.from_table, rel.to_table, e.target.value)}
                        >
                          <option value="" disabled>
                            unset — {rel.confidence}
                          </option>
                          <option value="one_to_many">one_to_many</option>
                          <option value="many_to_one">many_to_one</option>
                          <option value="one_to_one">one_to_one</option>
                        </select>
                      ) : (
                        <span title={rel.reason}>{rel.cardinality ?? 'ambiguous'}</span>
                      )}
                    </td>
                    <td>{rel.confidence}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}

function tableName(design: DesignDocument, tableId: string): string {
  return design.tables.find((t) => t.id === tableId)?.name ?? tableId;
}

/** A deterministic box-and-line diagram — every table in one row, a straight line per
 * relationship with its cardinality as a label. Handful-of-tables scale (a family's
 * design, not an estate), so no force layout is warranted (§15.4.2's own precedent: a
 * layout algorithm is worth its determinism cost only when it earns it). */
function RelationshipDiagram({ design }: { design: DesignDocument }): JSX.Element {
  const boxWidth = 130;
  const gap = 40;
  const y = 30;
  const positions = useMemo(() => {
    const map = new Map<string, number>();
    design.tables.forEach((table, index) => {
      map.set(table.id, index * (boxWidth + gap) + boxWidth / 2);
    });
    return map;
  }, [design.tables]);
  const width = Math.max(design.tables.length * (boxWidth + gap), 200);

  return (
    <svg
      className="relationship-diagram"
      viewBox={`0 0 ${width} 90`}
      role="img"
      aria-label="Relationships between candidate tables"
    >
      {design.relationships.map((rel) => {
        const x1 = positions.get(rel.from_table);
        const x2 = positions.get(rel.to_table);
        if (x1 === undefined || x2 === undefined) return null;
        return (
          <g key={`${rel.from_table}-${rel.to_table}`}>
            <line x1={x1} y1={y} x2={x2} y2={y} className="relationship-line" />
            <text x={(x1 + x2) / 2} y={y - 6} textAnchor="middle" className="relationship-label">
              {rel.cardinality ?? '?'}
            </text>
          </g>
        );
      })}
      {design.tables.map((table, index) => {
        const cx = index * (boxWidth + gap) + boxWidth / 2;
        return (
          <g key={table.id}>
            <rect x={cx - boxWidth / 2} y={y - 15} width={boxWidth} height={30} rx={5} className="relationship-box" />
            <text x={cx} y={y + 4} textAnchor="middle" className="relationship-box-label">
              {table.name}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ----------------------------------------------------------------------- Measures tab

function MeasuresTab({ design }: { design: DesignDocument }): JSX.Element {
  return (
    <div className="detail">
      <p className="faint">
        Class and pattern are the Transpiler&rsquo;s (E5, not built) — shown pending until then.
      </p>
      <table className="estate">
        <thead>
          <tr>
            <th>Candidate measure</th>
            <th>Source calculations</th>
            <th>Dedup decision</th>
            <th>Class</th>
            <th>Pattern</th>
          </tr>
        </thead>
        <tbody>
          {design.candidate_measures.length === 0 ? (
            <tr>
              <td colSpan={5} className="empty">
                No candidate measures.
              </td>
            </tr>
          ) : (
            design.candidate_measures.map((measure) => (
              <tr key={measure.name}>
                <td>{measure.name}</td>
                <td>{measure.source_calc_refs.length}</td>
                <td>{measure.dedup_decision}</td>
                <td>
                  <span className="pill idle">pending</span>
                </td>
                <td>
                  <span className="pill idle">pending</span>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

// ----------------------------------------------------------------------------- RLS tab

function RlsTab({ design }: { design: DesignDocument }): JSX.Element {
  return (
    <div className="detail">
      {design.rls_role_detail.length === 0 ? (
        <p className="empty">No RLS roles scaffolded — no member workbook was flagged rls: true.</p>
      ) : (
        <table className="estate">
          <thead>
            <tr>
              <th>Role</th>
              <th>Expression</th>
              <th>Source workbooks</th>
            </tr>
          </thead>
          <tbody>
            {design.rls_role_detail.map((role) => (
              <tr key={role.name}>
                <td>{role.name}</td>
                <td className="mono">{role.expression}</td>
                <td>{role.source_workbook_ids.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- Open Questions tab

function OpenQuestionsTab({ design }: { design: DesignDocument }): JSX.Element {
  return (
    <div className="detail">
      {design.open_questions.length === 0 ? (
        <p className="empty">No open questions — nothing needs the data owner&rsquo;s attention yet.</p>
      ) : (
        <ul className="plain-list">
          {design.open_questions.map((question, index) => (
            <li key={index}>
              <span className="pill warn">{question.category}</span> {question.question}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------- Build tab

function BuildTab({
  api,
  identity,
  family,
  transitions,
  canEdit,
  onFamilyChanged,
}: {
  api: Api;
  identity: Identity;
  family: FamilyRecord;
  transitions: FamilyTransition[];
  canEdit: boolean;
  onFamilyChanged: () => void;
}): JSX.Element {
  const [build, setBuild] = useState<BuildRecord | null | 'loading'>('loading');
  const [buildError, setBuildError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const everBuilt = family.state === 'BUILT' || family.state === 'PUBLISHED';
  const everSubmitted = everBuilt || family.state === 'APPROVED' || family.state === 'IN_REVIEW';

  useEffect(() => {
    let live = true;
    setBuild('loading');
    api
      .getBuild(family.id, identity)
      .then((response) => {
        if (live) {
          setBuild(response.build);
          setBuildError(null);
        }
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setBuild(null);
        setBuildError(caught instanceof ApiError ? caught.message : 'The build log could not be read.');
      });
    return () => {
      live = false;
    };
  }, [api, identity, family.id, nonce]);

  const triggerBuild = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const record = await api.triggerBuild(family.id, identity);
      setNotice(record.state === 'SUCCEEDED' ? 'Build succeeded.' : 'Build failed — see the log below.');
      onFamilyChanged();
      setNonce((n) => n + 1);
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The build could not be started.');
    } finally {
      setBusy(false);
    }
  }, [api, identity, family.id, onFamilyChanged]);

  const canBuild = canEdit && (family.state === 'APPROVED' || family.state === 'BUILT');

  return (
    <div className="detail">
      {notice && <p className={build && build !== 'loading' && build.state === 'FAILED' ? 'field-error' : 'faint'}>{notice}</p>}

      <div>
        <h3>State</h3>
        <p>
          <span className={`pill ${stateTone(family.state)}`}>{family.state ?? 'UNKNOWN'}</span>
          {family.conformance_ruleset_version !== null && (
            <span className="muted"> · checked against conformance ruleset version {family.conformance_ruleset_version}</span>
          )}
        </p>
        {!everSubmitted && (
          <p className="faint">
            A design builds automatically the moment it is approved at G2 — nothing to show yet.
          </p>
        )}
        {canBuild && (
          <button type="button" className="btn small" disabled={busy} onClick={() => void triggerBuild()}>
            {busy ? 'Building…' : everBuilt ? 'Rebuild' : 'Build now'}
          </button>
        )}
      </div>

      {everSubmitted && (
        <div>
          <h3>Latest build</h3>
          {buildError && <div className="banner">{buildError}</div>}
          {build === 'loading' && !buildError && <p className="empty">Reading the build log…</p>}
          {build === null && !buildError && (
            <p className="empty">No build has run yet — approval triggers one automatically.</p>
          )}
          {build && build !== 'loading' && (
            <>
              <dl>
                <dt>Result</dt>
                <dd>
                  <span className={`pill ${build.state === 'SUCCEEDED' ? 'ok' : 'bad'}`}>{build.state}</span>
                </dd>
                <dt>Commit</dt>
                <dd className="mono">{build.git_commit_sha || '—'}</dd>
                <dt>Workspace</dt>
                <dd>{build.workspace ?? '—'}</dd>
                <dt>Triggered by</dt>
                <dd>{build.triggered_by}</dd>
                <dt>Started</dt>
                <dd>{build.started_at}</dd>
                <dt>Finished</dt>
                <dd>{build.finished_at}</dd>
              </dl>
              <h4>Steps</h4>
              <ul className="plain-list">
                {build.steps.map((step, index) => (
                  <li key={index}>
                    <span className={`pill ${step.ok ? 'ok' : 'bad'}`}>{step.name}</span>{' '}
                    <span className="muted">{step.detail}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      <div>
        <h3>Transition history</h3>
        {transitions.length === 0 ? (
          <p className="empty">No transitions recorded yet.</p>
        ) : (
          <ul className="plain-list">
            {[...transitions].reverse().map((t, index) => (
              <li key={index}>
                <span className="mono">{t.from_state ?? 'created'} → {t.to_state}</span>
                <span className="muted"> · {t.by} · {t.at}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------- Versions tab

const MIN_CHANGE_REQUEST_REASON = 10;

/** Story S4.3.3: "a second version of a published model... without breaking released
 * reports". Lists every version this family has ever had (newest first, per the service's
 * own `list_model_versions`), and drives the two actions that move between them — a
 * change request opening v(n+1) as DRAFT while v(n) stays PUBLISHED untouched, and
 * promoting a BUILT version, which deprecates its predecessor with the date. Not a diff
 * view — §15.3.2's fuller "Versions (hash, approver, diff)" tab is a later story; this one
 * is scoped to exactly what S4.3.3 asked for: "the console shows both." */
function VersionsTab({
  api,
  identity,
  family,
  canEdit,
  onFamilyChanged,
  onDesignChanged,
}: {
  api: Api;
  identity: Identity;
  family: FamilyRecord;
  canEdit: boolean;
  onFamilyChanged: () => void;
  onDesignChanged: () => void;
}): JSX.Element {
  const [versions, setVersions] = useState<ModelVersion[] | 'loading'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setVersions('loading');
    api
      .getVersions(family.id, identity)
      .then((response) => {
        if (live) {
          setVersions(response.versions);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setVersions([]);
        setError(caught instanceof ApiError ? caught.message : 'The version history could not be read.');
      });
    return () => {
      live = false;
    };
  }, [api, identity, family.id, family.state, nonce]);

  const refresh = useCallback(() => {
    setNonce((n) => n + 1);
    onFamilyChanged();
    onDesignChanged();
  }, [onFamilyChanged, onDesignChanged]);

  const requestNewVersion = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.requestNewVersion(family.id, reason, identity);
      setNotice(`Change request opened — v${result.version_number} is now DRAFT.`);
      setReason('');
      refresh();
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The change request could not be opened.');
    } finally {
      setBusy(false);
    }
  }, [api, identity, family.id, reason, refresh]);

  const promote = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.promote(family.id, identity);
      setNotice(
        result.deprecated_version_number !== null
          ? `Promoted v${result.version_number} to PUBLISHED — v${result.deprecated_version_number} is now DEPRECATED.`
          : `Promoted v${result.version_number} to PUBLISHED.`,
      );
      refresh();
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The version could not be promoted.');
    } finally {
      setBusy(false);
    }
  }, [api, identity, family.id, refresh]);

  const canRequestNewVersion = canEdit && family.state === 'PUBLISHED';
  const canPromote = canEdit && family.state === 'BUILT';
  const reasonTooShort = reason.trim().length < MIN_CHANGE_REQUEST_REASON;

  return (
    <div className="detail">
      {notice && <p className="faint">{notice}</p>}
      {error && <div className="banner">{error}</div>}

      {canRequestNewVersion && (
        <div>
          <h3>Request a new version</h3>
          <p className="muted">
            Opens v(n+1) as DRAFT, an editable copy of the currently PUBLISHED design. The
            PUBLISHED version stays exactly as it is — live reports are unaffected — until
            v(n+1) is built and promoted.
          </p>
          <div className="actions-row">
            <textarea
              aria-label="Change request reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              placeholder="Why is this change needed? (at least 10 characters)"
              style={{ flex: 1 }}
            />
            <button
              type="button"
              className="btn small primary"
              disabled={busy || reasonTooShort}
              onClick={() => void requestNewVersion()}
            >
              {busy ? 'Opening…' : 'Request new version'}
            </button>
          </div>
        </div>
      )}

      {canPromote && (
        <div>
          <h3>Promote</h3>
          <p className="muted">
            Deploys this BUILT version to the published workspace and marks it PUBLISHED. If
            an earlier version is currently PUBLISHED, it becomes DEPRECATED with today&rsquo;s
            date.
          </p>
          <button type="button" className="btn small primary" disabled={busy} onClick={() => void promote()}>
            {busy ? 'Promoting…' : 'Promote to PUBLISHED'}
          </button>
        </div>
      )}

      <div>
        <h3>Version history</h3>
        {versions === 'loading' && !error && <p className="empty">Reading version history…</p>}
        {versions !== 'loading' && versions.length === 0 && !error && (
          <p className="empty">No version has been generated for this family yet.</p>
        )}
        {versions !== 'loading' && versions.length > 0 && (
          <table className="estate">
            <thead>
              <tr>
                <th>Version</th>
                <th>State</th>
                <th>Generated</th>
                <th>Published</th>
                <th>Deprecated</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.semantic_model_id}>
                  <td>v{v.version_number}</td>
                  <td>
                    <span className={`pill ${stateTone(v.state)}`}>{v.state ?? 'UNKNOWN'}</span>
                  </td>
                  <td>{v.design_generated_at ?? '—'}</td>
                  <td>{v.published_at ?? '—'}</td>
                  <td>{v.deprecated_at ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
