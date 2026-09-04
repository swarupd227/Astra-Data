/**
 * The right pane: one construct, and the three things S1.4.3 lets you do about it.
 *
 * The two that record something ask for a reason first, for the same reason every other
 * action in this console does (§15.2). They are different reasons and the dialog says so:
 * accepting a construct is a judgement that the platform may proceed without reading it,
 * and raising an issue is a description of what the grammar should do instead.
 */

import { useEffect, useRef, useState } from 'react';

import type { ConstructGroup } from '../lib/api';

/** The floor the API enforces. Asked for here so nobody loses their typing to a 422. */
export const MIN_DETAIL = 10;

interface Props {
  group: ConstructGroup | null;
  pending: 'ignorable' | 'issue' | null;
  busy: boolean;
  error: string | null;
  reharvesting: boolean;
  onMarkIgnorable: () => void;
  onOpenIssue: () => void;
  onReharvest: () => void;
  onConfirm: (reason: string, summary?: string) => void;
  onCancel: () => void;
}

export function ConstructPanel({
  group,
  pending,
  busy,
  error,
  reharvesting,
  onMarkIgnorable,
  onOpenIssue,
  onReharvest,
  onConfirm,
  onCancel,
}: Props): JSX.Element {
  if (!group) {
    return (
      <p className="empty">
        Select a construct to see where it appears
        <br />
        and what to do about it.
      </p>
    );
  }

  const raised = group.issue;

  return (
    <div className="detail">
      <div>
        <h3 className="section-title">Construct</h3>
        <pre className="construct-text">{group.construct}</pre>
      </div>

      <dl>
        <dt>Releases</dt>
        <dd>
          {group.workbooks_released_if_resolved > 0 ? (
            <strong>
              {group.workbooks_released_if_resolved} workbook
              {group.workbooks_released_if_resolved === 1 ? '' : 's'}
            </strong>
          ) : (
            <span className="faint">none on its own</span>
          )}
        </dd>
        <dt>Occurrences</dt>
        <dd>
          {group.occurrences} across {group.workbooks} workbook
          {group.workbooks === 1 ? '' : 's'}
        </dd>
        <dt>Sites</dt>
        <dd>{group.sites.join(', ') || '—'}</dd>
      </dl>

      {group.workbooks_released_if_resolved === 0 && group.workbooks > 0 && (
        <p className="faint" style={{ fontSize: 11.5, margin: 0 }}>
          Fixing this alone releases nothing: every workbook holding it has other
          unrecognised constructs too. It still has to be fixed — it is just not the one to
          start with.
        </p>
      )}

      {group.example_location && (
        <div>
          <h4 className="section-title">Example</h4>
          <dl>
            {Object.entries(group.example_location)
              .filter(([, value]) => value)
              .map(([key, value]) => (
                <div key={key} style={{ display: 'contents' }}>
                  <dt>{key.replace(/_/g, ' ')}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
          </dl>
        </div>
      )}

      {raised && (
        <p className="pill warn" style={{ display: 'block', padding: '6px 10px', borderRadius: 6 }}>
          Grammar issue {raised.external.ref ?? raised.state.toLowerCase()} — opened by{' '}
          {raised.opened_by}
        </p>
      )}

      <div>
        <h4 className="section-title">Actions</h4>
        <div className="actions-row">
          <button type="button" className="btn" onClick={onMarkIgnorable}>
            Mark ignorable…
          </button>
          <button
            type="button"
            className="btn"
            onClick={onOpenIssue}
            disabled={Boolean(raised)}
            title={
              raised
                ? 'An issue is already open for this construct. A second is not a second problem.'
                : undefined
            }
          >
            Open grammar issue…
          </button>
          <button type="button" className="btn" onClick={onReharvest} disabled={reharvesting}>
            {reharvesting ? 'Harvesting…' : 'Request re-harvest'}
          </button>
        </div>
      </div>

      {pending === 'ignorable' && (
        <ReasonDialog
          title="Mark construct ignorable"
          description="The grammar still cannot read it; you are deciding the platform may proceed anyway. Every workbook holding it is re-scored, and the reason is shown to whoever asks why one was released."
          confirmLabel="Accept and re-score"
          label="Reason"
          busy={busy}
          error={error}
          onConfirm={(reason) => onConfirm(reason)}
          onCancel={onCancel}
        />
      )}

      {pending === 'issue' && (
        <ReasonDialog
          title="Open grammar issue"
          description="Raises a ticket carrying this construct verbatim and every place it was found. Say what the grammar should do with it — whoever picks it up will not have been in the conversation."
          confirmLabel="Raise issue"
          label="What the grammar should do"
          withSummary
          busy={busy}
          error={error}
          onConfirm={onConfirm}
          onCancel={onCancel}
        />
      )}
    </div>
  );
}

interface DialogProps {
  title: string;
  description: string;
  confirmLabel: string;
  label: string;
  withSummary?: boolean;
  busy: boolean;
  error: string | null;
  onConfirm: (reason: string, summary?: string) => void;
  onCancel: () => void;
}

function ReasonDialog({
  title,
  description,
  confirmLabel,
  label,
  withSummary,
  busy,
  error,
  onConfirm,
  onCancel,
}: DialogProps): JSX.Element {
  const [reason, setReason] = useState('');
  const [summary, setSummary] = useState('');
  const field = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    field.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const tooShort = reason.trim().length < MIN_DETAIL;

  return (
    <div
      className="scrim"
      role="presentation"
      onMouseDown={(e) => e.target === e.currentTarget && onCancel()}
    >
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="construct-action">
        <h2 id="construct-action">{title}</h2>
        <p>{description}</p>

        {withSummary && (
          <>
            <label htmlFor="issue-summary">Summary</label>
            <textarea
              id="issue-summary"
              style={{ minHeight: 40 }}
              value={summary}
              onChange={(event) => setSummary(event.target.value)}
              placeholder="One line. Defaults to the construct."
            />
          </>
        )}

        <label htmlFor="construct-reason">{label}</label>
        <textarea
          id="construct-reason"
          ref={field}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        {error && <p className="field-error">{error}</p>}

        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={tooShort || busy}
            onClick={() => onConfirm(reason.trim(), summary.trim() || undefined)}
          >
            {busy ? 'Recording…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
