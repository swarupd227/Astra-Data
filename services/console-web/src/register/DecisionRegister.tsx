/**
 * The Decision Register -- §15.3.6, story S10.4.2.
 *
 * "A Decision Register of every gate decision and adjudication, so that I can answer
 * 'who approved this and on what evidence' without asking anyone." One
 * `GET /v1/decisions` call returns every real `GateDecision` this identity may read --
 * the G1-G4 approval workflow *and* the Exception Desk's own four "adjudications" are
 * the identical ontology node server-side (see `decision_register.py`'s own module
 * docstring), so this screen renders one flat list, not two.
 *
 * **Filters and search are client-side over one already-fetched list** -- the identical
 * "one server round trip, then filter locally" convention `GateInbox.tsx`'s own gate/
 * site filters already established, so free-text search updates on every keystroke with
 * no debounce needed. CSV/PDF export re-sends the *current* filter state as real server
 * query params (`decisionRegisterQueryString`), so "export" means "export what you are
 * looking at," not a second, unfiltered dump.
 *
 * **A row's own evidence bundle is fetched lazily, on click** -- `api.decisionEvidence`,
 * the identical "do not pay for evidence no one asked to see" discipline
 * `MigrationUnitPage.tsx`'s own lazy Provenance section already set. An image artefact
 * previews inline via `getArtefactContent` + an object URL (identical to
 * `MigrationUnitPage.tsx`'s own `ArtefactPreview`); a decision whose evidence does not
 * resolve to a stored artefact says so honestly rather than showing a broken preview.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import type { Api, DecisionEvidenceBundle, DecisionRegisterItem, Identity } from '../lib/api';
import { ApiError } from '../lib/api';
import { downloadBlob } from '../lib/download';

interface Props {
  api: Api;
  identity: Identity;
  liveTick?: number;
}

function EvidencePanel({
  api, identity, bundle, onClose,
}: {
  api: Api; identity: Identity; bundle: DecisionEvidenceBundle; onClose: () => void;
}): JSX.Element {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { decision, artefact } = bundle;

  const loadArtefact = useCallback(async () => {
    if (!artefact) return;
    setLoading(true);
    try {
      const blob = await api.getArtefactContent(artefact.id, identity);
      setObjectUrl(URL.createObjectURL(blob));
    } finally {
      setLoading(false);
    }
  }, [api, identity, artefact]);

  useEffect(() => () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  }, [objectUrl]);

  return (
    <aside className="pane evidence-bundle" aria-label="Evidence bundle">
      <header className="pane-header">
        <h3>{decision.subject_name}</h3>
        <span className="spacer" />
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </header>
      <div className="pane-body detail">
        <p><span className="pill idle mono">{decision.gate}</span> {decision.decision}</p>
        <p>Approver: {decision.approver ?? '—'} {decision.approver_role ? `(${decision.approver_role.replace(/_/g, ' ')})` : ''}</p>
        {decision.countersigner && (
          <p>Countersigned by: {decision.countersigner} ({decision.countersigner_role?.replace(/_/g, ' ')})</p>
        )}
        {decision.rationale && <p>Rationale: {decision.rationale}</p>}
        {decision.version_hash && <p className="faint mono">version_hash: {decision.version_hash}</p>}
        {decision.target_date && <p>Target date: {decision.target_date}</p>}
        <p className="faint">{decision.timestamp}</p>
        <h4>Evidence</h4>
        {!artefact && <p className="empty">This decision names no evidence that resolves to a stored artefact.</p>}
        {artefact && !objectUrl && artefact.media_type.startsWith('image/') && (
          <button type="button" className="btn" disabled={loading} onClick={() => void loadArtefact()}>
            {loading ? 'Loading…' : 'Show evidence artefact'}
          </button>
        )}
        {objectUrl && <img src={objectUrl} alt="Evidence artefact" className="mu-artefact-preview" />}
        {artefact && !artefact.media_type.startsWith('image/') && (
          <p className="faint">
            Stored artefact {artefact.id} ({artefact.media_type}, {artefact.size_bytes} bytes).
          </p>
        )}
      </div>
    </aside>
  );
}

export function DecisionRegister({ api, identity, liveTick }: Props): JSX.Element {
  const [items, setItems] = useState<DecisionRegisterItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [gateFilter, setGateFilter] = useState<string>('all');
  const [decisionFilter, setDecisionFilter] = useState<string>('all');
  const [approverFilter, setApproverFilter] = useState('');
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState<DecisionEvidenceBundle | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [exporting, setExporting] = useState<'csv' | 'pdf' | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.decisionRegister({}, identity);
      setItems(result.items);
    } catch (caught: unknown) {
      setItems(null);
      setError(caught instanceof ApiError ? caught.message : 'The Decision Register could not be read.');
    } finally {
      setLoading(false);
    }
  }, [api, identity]);

  useEffect(() => {
    void load();
  }, [load, liveTick]);

  const gates = useMemo(
    () => Array.from(new Set((items ?? []).map((i) => i.gate))).sort(),
    [items],
  );
  const decisions = useMemo(
    () => Array.from(new Set((items ?? []).map((i) => i.decision))).sort(),
    [items],
  );

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const approverNeedle = approverFilter.trim().toLowerCase();
    return (items ?? []).filter((item) => {
      if (gateFilter !== 'all' && item.gate !== gateFilter) return false;
      if (decisionFilter !== 'all' && item.decision !== decisionFilter) return false;
      if (approverNeedle && !(item.approver ?? '').toLowerCase().includes(approverNeedle)) return false;
      if (needle) {
        const haystack = [item.subject_name, item.subject_ref, item.approver, item.countersigner, item.rationale]
          .filter(Boolean).join(' ').toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
  }, [items, gateFilter, decisionFilter, approverFilter, q]);

  const currentFilters = useMemo(
    () => ({
      gate: gateFilter === 'all' ? undefined : gateFilter,
      decision: decisionFilter === 'all' ? undefined : decisionFilter,
      approver: approverFilter.trim() || undefined,
      q: q.trim() || undefined,
    }),
    [gateFilter, decisionFilter, approverFilter, q],
  );

  const openEvidence = useCallback(async (id: string) => {
    setEvidenceError(null);
    try {
      const bundle = await api.decisionEvidence(id, identity);
      setSelected(bundle);
    } catch (caught: unknown) {
      setEvidenceError(caught instanceof ApiError ? caught.message : 'The evidence bundle could not be read.');
    }
  }, [api, identity]);

  const exportCsv = useCallback(async () => {
    setExporting('csv');
    try {
      const blob = await api.decisionRegisterCsv(currentFilters, identity);
      downloadBlob(blob, 'decision-register.csv');
    } finally {
      setExporting(null);
    }
  }, [api, identity, currentFilters]);

  const exportPdf = useCallback(async () => {
    setExporting('pdf');
    try {
      const blob = await api.decisionRegisterPdf(currentFilters, identity);
      downloadBlob(blob, 'decision-register.pdf');
    } finally {
      setExporting(null);
    }
  }, [api, identity, currentFilters]);

  return (
    <div className="workspace decision-register-workspace">
      <section className="pane" aria-label="Decision Register">
        <header className="pane-header">
          <h2>Decision Register</h2>
          <label>
            Gate
            <select value={gateFilter} onChange={(e) => setGateFilter(e.target.value)}>
              <option value="all">All gates</option>
              {gates.map((gate) => <option key={gate} value={gate}>{gate}</option>)}
            </select>
          </label>
          <label>
            Decision
            <select value={decisionFilter} onChange={(e) => setDecisionFilter(e.target.value)}>
              <option value="all">All decisions</option>
              {decisions.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
          <label>
            Approver
            <input type="text" value={approverFilter} onChange={(e) => setApproverFilter(e.target.value)} />
          </label>
          <label>
            Search
            <input type="text" value={q} onChange={(e) => setQ(e.target.value)} placeholder="subject, rationale…" />
          </label>
          <span className="spacer" />
          <button type="button" className="btn" disabled={exporting !== null} onClick={() => void exportCsv()}>
            {exporting === 'csv' ? 'Exporting…' : 'Export CSV'}
          </button>
          <button type="button" className="btn" disabled={exporting !== null} onClick={() => void exportPdf()}>
            {exporting === 'pdf' ? 'Exporting…' : 'Export signed PDF'}
          </button>
        </header>
        <div className="pane-body">
          {error && <div className="banner">{error}</div>}
          {!error && loading && !items && <p className="empty">Reading the Decision Register…</p>}
          {!error && items && items.length === 0 && <p className="empty">No decision has been recorded yet.</p>}
          {!error && items && items.length > 0 && filtered.length === 0 && (
            <p className="empty">No decision matches these filters.</p>
          )}
          {evidenceError && <div className="banner">{evidenceError}</div>}
          {filtered.length > 0 && (
            <table className="estate decision-register-table">
              <thead>
                <tr>
                  <th>Gate</th><th>Decision</th><th>Subject</th><th>Approver</th>
                  <th>Countersigner</th><th>When</th><th />
                </tr>
              </thead>
              <tbody>
                {filtered.map((item) => (
                  <tr key={item.id}>
                    <td><span className="pill idle mono">{item.gate}</span></td>
                    <td>{item.decision}</td>
                    <td>{item.subject_name}</td>
                    <td>{item.approver ?? '—'}</td>
                    <td>{item.countersigner ?? '—'}</td>
                    <td className="faint">{item.timestamp}</td>
                    <td>
                      <button type="button" className="btn" onClick={() => void openEvidence(item.id)}>
                        Open evidence
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
      {selected && (
        <EvidencePanel api={api} identity={identity} bundle={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
