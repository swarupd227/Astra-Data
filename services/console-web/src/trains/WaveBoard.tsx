/**
 * The Wave Board — story S3.2.2.
 *
 * "Drag MUs between trains within scheduler constraints, so that re-planning is a board
 * action, not a spreadsheet exercise." Trains are columns; within a column, cards group by
 * their §3.2 state (this module never changes that state — see `trains.py`'s own note that
 * a card's state is set once, at proposal time, and carried forward by a move).
 *
 * Two drag actions, matching the two the story names:
 *  - drop a card on another card **in the same train** → re-sequence to that position;
 *  - drop a card **in a different train** (on a card or the empty column) → move it there.
 *
 * A move that would split a family across trains is refused outright — the error is shown
 * as-is, nothing to retry. A move that would exceed a configured WIP limit is different: it
 * comes back asking for a reason, and this screen prompts for exactly one and resubmits the
 * same move with it attached, rather than making a Programme Manager guess upfront whether
 * one will be needed.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import type { Api, Identity, Train, TrainEvent, TrainMember, TrainProjection } from '../lib/api';
import { ApiError } from '../lib/api';

const MIN_REASON = 8;

interface Props {
  api: Api;
  identity: Identity;
}

interface DragState {
  workbookId: string;
  fromTrainId: string;
}

interface PendingMove {
  workbookId: string;
  toTrainId: string;
  message: string;
}

function groupByState(members: TrainMember[]): [string, TrainMember[]][] {
  const groups = new Map<string, TrainMember[]>();
  for (const member of members) {
    const list = groups.get(member.state) ?? [];
    list.push(member);
    groups.set(member.state, list);
  }
  return [...groups.entries()];
}

export function WaveBoard({ api, identity }: Props): JSX.Element {
  const [trains, setTrains] = useState<Train[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [drag, setDrag] = useState<DragState | null>(null);
  const [pendingMove, setPendingMove] = useState<PendingMove | null>(null);
  const [wipDialogTrain, setWipDialogTrain] = useState<Train | null>(null);
  const [eventsTrain, setEventsTrain] = useState<Train | null>(null);
  const [projections, setProjections] = useState<Map<string, TrainProjection>>(new Map());

  const canEdit = identity.roles.includes('programme_manager');

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .trains(identity)
      .then((response) => {
        if (!live) return;
        setTrains(response.trains);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(caught instanceof ApiError ? caught.message : 'The trains could not be read.');
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, nonce]);

  useEffect(() => {
    // Best-effort: a projection badge is a nice-to-have on this board (the Programme
    // Board is where the acceptance criterion's "flagged" list actually lives), so a
    // failure here is silent rather than a second banner competing with the trains one.
    let live = true;
    api
      .trainProjections(identity)
      .then((response) => {
        if (!live) return;
        setProjections(new Map(response.projections.map((p) => [p.train_id, p])));
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [api, identity, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const trainName = useCallback(
    (id: string) => trains?.find((t) => t.id === id)?.name ?? id,
    [trains],
  );

  const move = useCallback(
    async (workbookId: string, toTrainId: string, reason?: string) => {
      setBusy(true);
      setNotice(null);
      try {
        await api.moveMember(toTrainId, workbookId, identity, reason);
        setNotice(`Moved into ${trainName(toTrainId)}.`);
        setPendingMove(null);
        reload();
      } catch (caught: unknown) {
        if (caught instanceof ApiError && caught.status === 400 && /WIP/i.test(caught.message)) {
          setPendingMove({ workbookId, toTrainId, message: caught.message });
        } else {
          setNotice(caught instanceof ApiError ? caught.message : 'The move could not be recorded.');
        }
      } finally {
        setBusy(false);
      }
    },
    [api, identity, reload, trainName],
  );

  const resequence = useCallback(
    async (workbookId: string, trainId: string, position: number) => {
      setBusy(true);
      setNotice(null);
      try {
        await api.resequenceMember(trainId, workbookId, position, identity);
        setNotice(`Reordered within ${trainName(trainId)}.`);
        reload();
      } catch (caught: unknown) {
        setNotice(
          caught instanceof ApiError ? caught.message : 'The reorder could not be recorded.',
        );
      } finally {
        setBusy(false);
      }
    },
    [api, identity, reload, trainName],
  );

  const onDragStart = useCallback((workbookId: string, fromTrainId: string) => {
    setDrag({ workbookId, fromTrainId });
  }, []);

  const onDropOnCard = useCallback(
    (targetTrainId: string, target: TrainMember) => {
      if (!drag || busy) return;
      const { workbookId, fromTrainId } = drag;
      setDrag(null);
      if (workbookId === target.id) return;
      if (fromTrainId === targetTrainId) {
        void resequence(workbookId, targetTrainId, target.sequence);
      } else {
        void move(workbookId, targetTrainId);
      }
    },
    [drag, busy, move, resequence],
  );

  const onDropOnColumn = useCallback(
    (targetTrainId: string) => {
      if (!drag || busy) return;
      const { workbookId, fromTrainId } = drag;
      setDrag(null);
      if (fromTrainId === targetTrainId) return;
      void move(workbookId, targetTrainId);
    },
    [drag, busy, move],
  );

  return (
    <div className="wave-board">
      {error && <div className="banner">{error}</div>}

      {loading && !trains ? (
        <p className="empty">Reading trains…</p>
      ) : !trains || trains.length === 0 ? (
        <p className="empty">
          No release trains yet.
          <br />
          Propose one first (<code>POST /v1/trains:propose</code>).
        </p>
      ) : (
        <div className="wave-columns">
          {trains.map((train) => (
            <TrainColumn
              key={train.id}
              train={train}
              projection={projections.get(train.id) ?? null}
              canEdit={canEdit}
              dragging={drag}
              onDragStart={onDragStart}
              onDropOnCard={onDropOnCard}
              onDropOnColumn={onDropOnColumn}
              onOpenWipDialog={() => setWipDialogTrain(train)}
              onOpenEvents={() => setEventsTrain(train)}
            />
          ))}
        </div>
      )}

      <footer className="statusbar">
        {notice && <span>{notice}</span>}
        <span className="spacer" />
        {!canEdit && (
          <span className="faint">Moving and resequencing is the Programme Manager&rsquo;s.</span>
        )}
      </footer>

      {pendingMove && (
        <WipReasonDialog
          message={pendingMove.message}
          busy={busy}
          onCancel={() => setPendingMove(null)}
          onConfirm={(reason) => void move(pendingMove.workbookId, pendingMove.toTrainId, reason)}
        />
      )}

      {wipDialogTrain && (
        <WipLimitsDialog
          train={wipDialogTrain}
          api={api}
          identity={identity}
          onClose={() => setWipDialogTrain(null)}
          onSaved={() => {
            setWipDialogTrain(null);
            reload();
          }}
        />
      )}

      {eventsTrain && (
        <TrainEventsDialog
          train={eventsTrain}
          api={api}
          identity={identity}
          onClose={() => setEventsTrain(null)}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- the column

interface ColumnProps {
  train: Train;
  projection: TrainProjection | null;
  canEdit: boolean;
  dragging: DragState | null;
  onDragStart: (workbookId: string, fromTrainId: string) => void;
  onDropOnCard: (trainId: string, target: TrainMember) => void;
  onDropOnColumn: (trainId: string) => void;
  onOpenWipDialog: () => void;
  onOpenEvents: () => void;
}

function TrainColumn({
  train,
  projection,
  canEdit,
  onDragStart,
  onDropOnCard,
  onDropOnColumn,
  onOpenWipDialog,
  onOpenEvents,
}: ColumnProps): JSX.Element {
  const groups = useMemo(() => groupByState(train.members), [train.members]);
  const trainLimit = train.wip_limits?.train ?? null;
  const trainExceeded = trainLimit != null && train.size > trainLimit;

  return (
    <section
      className="pane wave-train"
      aria-label={train.name}
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        onDropOnColumn(train.id);
      }}
    >
      <header className="pane-header wave-train-header">
        <h2>{train.name}</h2>
        <span className="pill idle">{train.size} MUs</span>
        {trainLimit != null && (
          <span className={`pill ${trainExceeded ? 'bad' : 'ok'}`} title="Train WIP limit">
            WIP {train.size}/{trainLimit}
          </span>
        )}
        {train.overridden && (
          <span className="pill warn" title={train.override_reason ?? undefined}>
            edited
          </span>
        )}
        {projection && (
          <span
            className={`pill ${projection.flagged ? 'bad' : projection.projected_end ? 'ok' : 'idle'}`}
            title={projection.reason}
          >
            {projection.projected_end
              ? `proj. ${projection.projected_end}`
              : 'no projection'}
          </span>
        )}
        <span className="spacer" />
        {canEdit && (
          <button type="button" className="btn small" onClick={onOpenWipDialog}>
            WIP limit…
          </button>
        )}
        <button type="button" className="btn small" onClick={onOpenEvents}>
          Activity
        </button>
      </header>

      <div className="pane-body wave-train-body">
        {groups.length === 0 && <p className="empty">No MUs.</p>}
        {groups.map(([state, members]) => {
          const stateLimit = train.wip_limits?.states?.[state];
          const stateExceeded = stateLimit != null && members.length > stateLimit;
          return (
            <div key={state} className="wave-state-group">
              <h3 className="section-title">
                {state.toLowerCase()}
                <span className={stateExceeded ? 'wave-state-count exceeded' : 'wave-state-count'}>
                  {stateLimit != null ? `${members.length}/${stateLimit}` : members.length}
                </span>
              </h3>
              {members.map((member) => (
                <div
                  key={member.id}
                  className="wave-card"
                  draggable={canEdit}
                  onDragStart={() => onDragStart(member.id, train.id)}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onDropOnCard(train.id, member);
                  }}
                >
                  {member.name}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------- the WIP reason dialog

function WipReasonDialog({
  message,
  busy,
  onConfirm,
  onCancel,
}: {
  message: string;
  busy: boolean;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}): JSX.Element {
  const [reason, setReason] = useState('');
  const tooShort = reason.trim().length < MIN_REASON;

  return (
    <div className="scrim" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="wip-reason-title">
        <h2 id="wip-reason-title">This move exceeds a WIP limit</h2>
        <p>{message}</p>
        <label htmlFor="wip-reason">Reason to proceed anyway</label>
        <textarea id="wip-reason" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={tooShort || busy}
            onClick={() => onConfirm(reason.trim())}
          >
            {busy ? 'Moving…' : 'Move anyway'}
          </button>
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------- the WIP limits dialog

function WipLimitsDialog({
  train,
  api,
  identity,
  onClose,
  onSaved,
}: {
  train: Train;
  api: Api;
  identity: Identity;
  onClose: () => void;
  onSaved: () => void;
}): JSX.Element {
  const [trainLimit, setTrainLimit] = useState(
    train.wip_limits?.train != null ? String(train.wip_limits.train) : '',
  );
  const existingStates = train.wip_limits?.states ?? {};
  const existingState = Object.keys(existingStates)[0];
  const [stateName, setStateName] = useState(existingState ?? '');
  const [stateLimit, setStateLimit] = useState(
    existingState ? String(existingStates[existingState]) : '',
  );
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tooShort = reason.trim().length < MIN_REASON;

  const save = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const limits: Record<string, number> = {};
      if (stateName.trim() && stateLimit.trim()) {
        limits[stateName.trim().toUpperCase()] = Number(stateLimit);
      }
      await api.setWipLimits(
        train.id,
        trainLimit.trim() ? Number(trainLimit) : null,
        limits,
        reason.trim(),
        identity,
      );
      onSaved();
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : 'The WIP limits could not be saved.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="scrim" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="wip-limits-title">
        <h2 id="wip-limits-title">WIP limits — {train.name}</h2>

        <label htmlFor="wip-train-limit">Train limit</label>
        <input
          id="wip-train-limit"
          type="number"
          min={1}
          value={trainLimit}
          onChange={(e) => setTrainLimit(e.target.value)}
          placeholder="No limit"
        />

        <label htmlFor="wip-state-name">State limit (optional)</label>
        <div className="actions-row">
          <input
            id="wip-state-name"
            value={stateName}
            onChange={(e) => setStateName(e.target.value)}
            placeholder="e.g. CLUSTERED"
            style={{ flex: 2 }}
          />
          <input
            type="number"
            min={1}
            value={stateLimit}
            onChange={(e) => setStateLimit(e.target.value)}
            placeholder="Limit"
            style={{ flex: 1 }}
          />
        </div>

        <label htmlFor="wip-reason-field">Reason</label>
        <textarea id="wip-reason-field" value={reason} onChange={(e) => setReason(e.target.value)} />
        {error && <p className="field-error">{error}</p>}

        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn primary" disabled={tooShort || busy} onClick={() => void save()}>
            {busy ? 'Saving…' : 'Save limits'}
          </button>
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------- the events panel

function TrainEventsDialog({
  train,
  api,
  identity,
  onClose,
}: {
  train: Train;
  api: Api;
  identity: Identity;
  onClose: () => void;
}): JSX.Element {
  const [events, setEvents] = useState<TrainEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .trainEvents(train.id, identity)
      .then((response) => {
        if (live) setEvents(response.events);
      })
      .catch((caught: unknown) => {
        if (live) setError(caught instanceof ApiError ? caught.message : 'Events could not be read.');
      });
    return () => {
      live = false;
    };
  }, [api, identity, train.id]);

  return (
    <div className="scrim" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="wave-events-title">
        <h2 id="wave-events-title">Recent activity — {train.name}</h2>
        {error && <p className="field-error">{error}</p>}
        {!error && events === null && <p className="empty">Reading…</p>}
        {events && events.length === 0 && <p className="empty">Nothing recorded yet.</p>}
        {events && events.length > 0 && (
          <ul className="wave-events">
            {[...events].reverse().map((item) => (
              <li key={item.sequence}>
                <span className="mono">{item.event.type}</span>
                <span className="muted"> · {item.event.time}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
