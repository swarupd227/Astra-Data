/**
 * The reason dialog behind every scope action.
 *
 * Spec §15.2: "Every action is a record ... with a reason field that is required, not
 * optional." The API enforces that, and so does this — not because the client is trusted,
 * but because a user should learn the rule before they lose their typing to a 422.
 *
 * Focus is moved into the dialog on open and Escape closes it. §15.6 requires full keyboard
 * operation; a modal that traps a keyboard user is the most common way that is broken.
 */

import { useEffect, useRef, useState } from 'react';

export const MIN_REASON = 10;

interface Props {
  title: string;
  description: string;
  confirmLabel: string;
  danger?: boolean;
  tiers?: string[];
  currentTier?: string | null;
  busy?: boolean;
  error?: string | null;
  onConfirm: (reason: string, tier?: string) => void;
  onCancel: () => void;
}

export function ReasonDialog({
  title,
  description,
  confirmLabel,
  danger,
  tiers,
  currentTier,
  busy,
  error,
  onConfirm,
  onCancel,
}: Props): JSX.Element {
  const [reason, setReason] = useState('');
  const [tier, setTier] = useState(currentTier ?? tiers?.[0] ?? '');
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

  return (
    <div className="scrim" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="reason-title">
        <h2 id="reason-title">{title}</h2>
        <p>{description}</p>

        {tiers && (
          <>
            <label htmlFor="reason-tier">Tier</label>
            <select id="reason-tier" value={tier} onChange={(e) => setTier(e.target.value)}>
              {tiers.map((option) => (
                <option key={option} value={option}>
                  {option.toLowerCase()}
                </option>
              ))}
            </select>
          </>
        )}

        <label htmlFor="reason-text">Reason</label>
        <textarea
          id="reason-text"
          ref={field}
          value={reason}
          aria-describedby="reason-help"
          onChange={(e) => setReason(e.target.value)}
          onBlur={() => setTouched(true)}
          placeholder="What changed, and who agreed it"
        />
        {touched && tooShort && (
          <p className="field-error" id="reason-help">
            A reason of at least {MIN_REASON} characters. This record outlives everyone who
            remembers the conversation.
          </p>
        )}
        {!touched && (
          <p className="faint" id="reason-help" style={{ margin: '-8px 0 10px', fontSize: 11.5 }}>
            Required. Kept on the decision record for the life of the programme.
          </p>
        )}
        {error && <p className="field-error">{error}</p>}

        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className={`btn ${danger ? 'danger' : 'primary'}`}
            disabled={tooShort || busy}
            onClick={() => onConfirm(reason.trim(), tiers ? tier : undefined)}
          >
            {busy ? 'Recording…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
