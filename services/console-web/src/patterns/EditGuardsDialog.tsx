/**
 * Edit a pattern's guards — story S5.5.3.
 *
 * "Edit guards (creates a new version)" — the same reason-required discipline
 * `estate/ReasonDialog.tsx` already gives every other governing action, extended with a
 * second field: the guards themselves, one per line (matching how they render in the
 * detail panel and how the API carries them, `string[]`).
 */

import { useEffect, useRef, useState } from 'react';

export const MIN_REASON = 10;

interface Props {
  patternId: string;
  initialGuards: string[];
  busy?: boolean;
  error?: string | null;
  onConfirm: (guards: string[], reason: string) => void;
  onCancel: () => void;
}

export function EditGuardsDialog({
  patternId,
  initialGuards,
  busy,
  error,
  onConfirm,
  onCancel,
}: Props): JSX.Element {
  const [guardsText, setGuardsText] = useState(initialGuards.join('\n'));
  const [reason, setReason] = useState('');
  const [touched, setTouched] = useState(false);
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

  const tooShort = reason.trim().length < MIN_REASON;
  const guards = guardsText
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  return (
    <div className="scrim" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="edit-guards-title">
        <h2 id="edit-guards-title">Edit guards — {patternId}</h2>
        <p>
          Guards are descriptive, not evaluated — a human-readable precondition for
          reviewers, never enforced by the renderer. Saving creates a new version; this
          pattern&rsquo;s own node is retired, not overwritten.
        </p>

        <label htmlFor="guards-text">Guards (one per line)</label>
        <textarea
          id="guards-text"
          ref={field}
          value={guardsText}
          onChange={(e) => setGuardsText(e.target.value)}
          placeholder="a is real&#10;b is a positive amount"
          rows={5}
        />

        <label htmlFor="edit-reason-text">Reason</label>
        <textarea
          id="edit-reason-text"
          value={reason}
          aria-describedby="edit-reason-help"
          onChange={(e) => setReason(e.target.value)}
          onBlur={() => setTouched(true)}
          placeholder="What changed, and why"
        />
        {touched && tooShort && (
          <p className="field-error" id="edit-reason-help">
            A reason of at least {MIN_REASON} characters. This becomes the new version&rsquo;s
            own recorded provenance.
          </p>
        )}
        {!touched && (
          <p className="faint" id="edit-reason-help" style={{ margin: '-8px 0 10px', fontSize: 11.5 }}>
            Required. Kept on the new version&rsquo;s own provenance.
          </p>
        )}
        {error && <p className="field-error">{error}</p>}

        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={tooShort || busy}
            onClick={() => onConfirm(guards, reason.trim())}
          >
            {busy ? 'Saving…' : 'Save new version'}
          </button>
        </div>
      </div>
    </div>
  );
}
