/**
 * The Tolerance Charter editor — story S7.1.1, opening E7/F7.1.
 *
 * "As a parity engineer, I want the Tolerance Charter as a versioned document the
 * platform enforces, so that 'the same result' is defined once, agreed at G1, and
 * applied identically to every report... Editor in the console with inline explanation
 * of each rule's effect; 'simulate' re-diffs the last run under the edited charter
 * without executing."
 *
 * §2.4 names "Parity Dashboard, Charter editor" as the Parity Engineer's own surfaces —
 * neither exists yet, and neither is a natural Admin sub-screen (Admin is the Migration
 * Architect's own single-purpose surface, S4.3.2) — so this is its own top-level surface,
 * the same call the Pattern Library (S5.5.3) already made for an analogous "the spec
 * nominally files this under a screen that doesn't exist yet" situation.
 *
 * A save is always a new version, never an overwrite — the identical `Admin`/conformance
 * ruleset shape. Whether a save also needs the client analytics lead's own sign-off (the
 * AC's own "changing the charter after G1 requires...") is decided server-side, not
 * guessed at here: the two extra fields are always offered, and an attempt without them
 * once they are actually required comes back as a plain API refusal, the same "the server
 * is the source of truth for validity" posture `Admin`'s own save path already has.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import type { Api, Identity, SimulateResult, ToleranceCharter as Charter, ToleranceCharterFieldMetadata, ToleranceCharterVersion } from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
}

type Block = keyof Charter;

const BLOCK_LABELS: Record<Block, string> = {
  numeric: 'Numeric',
  nulls: 'Nulls',
  dates: 'Dates',
  strings: 'Strings',
  ordering: 'Ordering',
  rows: 'Rows',
  sampling: 'Sampling',
  params: 'Parameters',
  waiver: 'Waiver rules',
};

const BLOCK_ORDER: Block[] = ['numeric', 'nulls', 'dates', 'strings', 'ordering', 'rows', 'sampling', 'params', 'waiver'];

const PASS_FAIL_FIELDS = new Set([
  'source_null_vs_target_zero',
  'source_null_vs_target_blank',
  'missing_key',
  'extra_key',
]);

function cloneCharter(charter: Charter): Charter {
  return JSON.parse(JSON.stringify(charter)) as Charter;
}

function fieldValue(charter: Charter, block: Block, field: string): unknown {
  return (charter[block] as unknown as Record<string, unknown>)[field];
}

function withField(charter: Charter, block: Block, field: string, value: unknown): Charter {
  const next = cloneCharter(charter);
  (next[block] as unknown as Record<string, unknown>)[field] = value;
  return next;
}

function FieldInput({
  block,
  field,
  value,
  disabled,
  onChange,
}: {
  block: Block;
  field: string;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
}): JSX.Element {
  const label = `${BLOCK_LABELS[block]} — ${field}`;

  if (PASS_FAIL_FIELDS.has(field)) {
    return (
      <select aria-label={label} value={String(value)} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        <option value="PASS">PASS</option>
        <option value="FAIL">FAIL</option>
      </select>
    );
  }
  if (typeof value === 'boolean') {
    return (
      <input
        type="checkbox"
        aria-label={label}
        checked={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  }
  if (typeof value === 'number') {
    return (
      <input
        type="number"
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))}
      />
    );
  }
  if (Array.isArray(value)) {
    return (
      <input
        type="text"
        aria-label={label}
        value={value.join(', ')}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value.split(',').map((v) => v.trim()).filter(Boolean))}
      />
    );
  }
  return (
    <input
      type="text"
      aria-label={label}
      value={String(value ?? '')}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function ToleranceCharter({ api, identity }: Props): JSX.Element {
  const [version, setVersion] = useState<ToleranceCharterVersion | null>(null);
  const [fieldMetadata, setFieldMetadata] = useState<ToleranceCharterFieldMetadata>({});
  const [draft, setDraft] = useState<Charter | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [ack, setAck] = useState('');
  const [reason, setReason] = useState('');

  const [countersignedBy, setCountersignedBy] = useState('');
  const [rationale, setRationale] = useState('');
  const [g1Notice, setG1Notice] = useState<string | null>(null);
  const [g1Busy, setG1Busy] = useState(false);

  const [simWorkbookId, setSimWorkbookId] = useState('');
  const [simResult, setSimResult] = useState<SimulateResult | null>(null);
  const [simBusy, setSimBusy] = useState(false);
  const [simError, setSimError] = useState<string | null>(null);

  const canEdit = identity.roles.includes('parity_engineer');
  const canApproveG1 = identity.roles.includes('client_analytics_lead');

  useEffect(() => {
    let live = true;
    api
      .toleranceCharter(identity)
      .then((response) => {
        if (!live) return;
        setVersion(response.charter);
        setFieldMetadata(response.field_metadata);
        setDraft(cloneCharter(response.charter.charter));
        setError(null);
      })
      .catch((caught: unknown) => {
        if (live) setError(caught instanceof ApiError ? caught.message : 'The Tolerance Charter could not be read.');
      });
    return () => {
      live = false;
    };
  }, [api, identity]);

  const dirty = useMemo(
    () => version !== null && draft !== null && JSON.stringify(draft) !== JSON.stringify(version.charter),
    [version, draft],
  );

  const save = useCallback(async () => {
    if (draft === null) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.saveToleranceCharter(draft, identity, ack || undefined, reason || undefined);
      setVersion(result.charter);
      setDraft(cloneCharter(result.charter.charter));
      const reproved = result.reproved_workbook_ids.length;
      setNotice(
        `Saved version ${result.charter.version}.`
          + (result.is_revision ? ' Recorded a fresh G1 approval.' : '')
          + (reproved > 0 ? ` ${reproved} workbook(s) marked for re-proof.` : ''),
      );
      setAck('');
      setReason('');
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The charter could not be saved.');
    } finally {
      setBusy(false);
    }
  }, [api, identity, draft, ack, reason]);

  const approve = useCallback(async () => {
    if (version === null) return;
    setG1Busy(true);
    setG1Notice(null);
    try {
      const result = await api.approveG1(version.version, countersignedBy, rationale, identity);
      setG1Notice(`Version ${result.version} approved at G1.`);
    } catch (caught: unknown) {
      setG1Notice(caught instanceof ApiError ? caught.message : 'The approval could not be recorded.');
    } finally {
      setG1Busy(false);
    }
  }, [api, identity, version, countersignedBy, rationale]);

  const simulate = useCallback(async () => {
    if (draft === null || !simWorkbookId.trim()) return;
    setSimBusy(true);
    setSimError(null);
    try {
      const result = await api.simulateToleranceCharter(simWorkbookId.trim(), draft, identity);
      setSimResult(result);
    } catch (caught: unknown) {
      setSimError(caught instanceof ApiError ? caught.message : 'The simulation could not run.');
    } finally {
      setSimBusy(false);
    }
  }, [api, identity, draft, simWorkbookId]);

  return (
    <div className="workspace charter-workspace">
      <section className="pane" aria-label="Tolerance Charter">
        <header className="pane-header">
          <h2>Tolerance Charter</h2>
          {version && <span className="pill idle mono">version {version.version}</span>}
        </header>
        <div className="pane-body">
          {error && <div className="banner">{error}</div>}
          {!error && draft === null && <p className="empty">Reading the Tolerance Charter…</p>}
          {draft && (
            <>
              <p className="faint">
                What &ldquo;the same result&rdquo; means, agreed at G1 and applied identically to every
                report (§4.4). A save is always a new, immutable version.
              </p>
              {BLOCK_ORDER.map((block) => (
                <div key={block} className="charter-block">
                  <h3>{BLOCK_LABELS[block]}</h3>
                  <table className="estate">
                    <thead>
                      <tr>
                        <th>Rule</th>
                        <th>Value</th>
                        <th>Effect</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.keys(draft[block] as unknown as Record<string, unknown>).map((field) => (
                        <tr key={field}>
                          <td>
                            <strong>{field}</strong>
                          </td>
                          <td>
                            <FieldInput
                              block={block}
                              field={field}
                              value={fieldValue(draft, block, field)}
                              disabled={!canEdit}
                              onChange={(value) => setDraft((current) => (current ? withField(current, block, field, value) : current))}
                            />
                          </td>
                          <td>
                            <span className="muted">{fieldMetadata[block]?.[field] ?? ''}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
              <dl>
                <dt>Last saved by</dt>
                <dd>{version?.updated_by}</dd>
                <dt>Last saved at</dt>
                <dd>{version?.updated_at ?? 'never — built-in default'}</dd>
              </dl>
              {canEdit && (
                <div className="charter-revision-fields">
                  <label>
                    Client analytics lead acknowledgement
                    <span className="faint"> (only needed if this charter has already been approved at G1)</span>
                    <input
                      type="text"
                      value={ack}
                      onChange={(e) => setAck(e.target.value)}
                      placeholder="user:lead@client.example"
                    />
                  </label>
                  <label>
                    Reason for the change
                    <input type="text" value={reason} onChange={(e) => setReason(e.target.value)} />
                  </label>
                </div>
              )}
            </>
          )}
        </div>
        <footer className="statusbar">
          {notice && <span>{notice}</span>}
          <span className="spacer" />
          {canEdit ? (
            <button type="button" className="btn primary" disabled={!dirty || busy} onClick={() => void save()}>
              {busy ? 'Saving…' : 'Save new version'}
            </button>
          ) : (
            <span className="faint">Editing the Tolerance Charter is the Parity Engineer&rsquo;s.</span>
          )}
        </footer>
      </section>

      {canApproveG1 && version && (
        <section className="pane" aria-label="Approve at G1">
          <header className="pane-header">
            <h2>Approve at G1</h2>
          </header>
          <div className="pane-body">
            <p className="faint">
              Approving version {version.version} as the client analytics lead, countersigned by the
              Parity Engineer (§13.1).
            </p>
            <label>
              Countersigned by (Parity Engineer)
              <input
                type="text"
                value={countersignedBy}
                onChange={(e) => setCountersignedBy(e.target.value)}
                placeholder="user:parity@artizent.example"
              />
            </label>
            <label>
              Rationale
              <input type="text" value={rationale} onChange={(e) => setRationale(e.target.value)} />
            </label>
          </div>
          <footer className="statusbar">
            {g1Notice && <span>{g1Notice}</span>}
            <span className="spacer" />
            <button type="button" className="btn primary" disabled={g1Busy} onClick={() => void approve()}>
              {g1Busy ? 'Approving…' : 'Approve at G1'}
            </button>
          </footer>
        </section>
      )}

      <section className="pane" aria-label="Simulate">
        <header className="pane-header">
          <h2>Simulate</h2>
        </header>
        <div className="pane-body">
          <p className="faint">
            Re-diff a workbook&rsquo;s last run under the edited charter above, without executing
            anything.
          </p>
          <label>
            Workbook
            <input
              type="text"
              value={simWorkbookId}
              onChange={(e) => setSimWorkbookId(e.target.value)}
              placeholder="workbook id"
            />
          </label>
          {simError && <div className="banner">{simError}</div>}
          {simResult && !simResult.has_prior_run && <p className="empty">{simResult.message}</p>}
          {simResult && simResult.has_prior_run && (
            <table className="estate">
              <thead>
                <tr>
                  <th>Grain key</th>
                  <th>Measure</th>
                  <th>Expected</th>
                  <th>Candidate</th>
                  <th>Result</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {simResult.verdicts.map((cell, index) => (
                  <tr key={index}>
                    <td>{String(cell.grain_key)}</td>
                    <td>{String(cell.measure)}</td>
                    <td>{String(cell.expected)}</td>
                    <td>{String(cell.candidate)}</td>
                    <td>{cell.result}</td>
                    <td>{cell.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <footer className="statusbar">
          <span className="spacer" />
          <button type="button" className="btn primary" disabled={simBusy || !simWorkbookId.trim()} onClick={() => void simulate()}>
            {simBusy ? 'Simulating…' : 'Simulate'}
          </button>
        </footer>
      </section>
    </div>
  );
}
