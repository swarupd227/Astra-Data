/**
 * The Gate Inbox -- §15.3.6, story S10.4.1, opening F10.4.
 *
 * "A Gate Inbox that shows only the requests waiting for me, so that I do my part in
 * minutes and get out." One `GET /v1/gate-inbox` call returns a card stack the server
 * has already role-dispatched (see `gate_inbox.py`'s own module docstring): a
 * `client_data_owner` identity only ever receives G2 items, `client_report_owner`
 * only G3, `client_licence_admin` only G4, and an Artizent identity the union of all
 * three. This screen never re-filters by role client-side -- what the server sends is
 * what this identity is allowed to act on.
 *
 * **Every action reuses the identical existing route each gate already has** --
 * `api.approveG2`/`.requestChangesG2`/`.askQuestion`, `api.approveG3`/
 * `.requestChangesG3`/`.askG3Question`, `api.approveG4`/`.deferG4` -- this screen adds
 * no new mutation, only the aggregated read and a "Notify" action
 * (`api.notifyGateInbox`) for the AC's own "email and Teams notification" clause (see
 * `gate_notifications.py`'s own module docstring for why that stays a real,
 * disclosed-local-only record, not a live delivery claim).
 *
 * **"Shows who is next" is the real countersigner *role*, not a named individual** --
 * no gate anywhere in this codebase pre-assigns a specific countersigning person; the
 * countersigner is a plain name the approver themselves types at decision time. This
 * screen shows the real, structural fact it can honestly show: which role must
 * countersign.
 *
 * **Filters are client-side over one already-fetched list** -- §15.3.6's own "filters
 * by gate type and site," applied to the same real items the server returned, not a
 * second server round trip per filter change.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import type { Api, GateInboxItem, Identity } from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
  liveTick?: number;
}

function gatePillClass(item: GateInboxItem): string {
  if (item.breached) return 'pill bad';
  return 'pill idle';
}

function GateInboxCard({
  api, identity, item, onDecided,
}: {
  api: Api; identity: Identity; item: GateInboxItem; onDecided: () => void;
}): JSX.Element {
  const [rationale, setRationale] = useState('');
  const [countersignedBy, setCountersignedBy] = useState('');
  const [question, setQuestion] = useState('');
  const [targetDate, setTargetDate] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const canAct = identity.roles.includes(item.approver_role);

  const run = useCallback(
    async (action: () => Promise<unknown>, successMessage: string) => {
      setBusy(true);
      setNotice(null);
      try {
        await action();
        setNotice(successMessage);
        onDecided();
      } catch (caught: unknown) {
        setNotice(
          caught instanceof ApiError
            ? caught.forbidden
              ? `Deciding a ${item.gate} card is the ${item.approver_role.replace(/_/g, ' ')}'s action.`
              : caught.message
            : 'The decision could not be recorded.',
        );
      } finally {
        setBusy(false);
      }
    },
    [item.approver_role, item.gate, onDecided],
  );

  const approve = useCallback(() => {
    if (item.gate === 'G2') {
      return run(() => api.approveG2(item.subject_ref, countersignedBy, rationale, identity), 'Approved.');
    }
    if (item.gate === 'G3') {
      return run(() => api.approveG3(item.subject_ref, rationale, countersignedBy, identity), 'Approved.');
    }
    return run(() => api.approveG4(item.subject_ref, rationale, countersignedBy, identity), 'Approved.');
  }, [api, identity, item.gate, item.subject_ref, rationale, countersignedBy, run]);

  const requestChanges = useCallback(() => {
    if (item.gate === 'G2') {
      return run(() => api.requestChangesG2(item.subject_ref, rationale, identity), 'Sent back for changes.');
    }
    return run(() => api.requestChangesG3(item.subject_ref, rationale, identity), 'Sent back for changes.');
  }, [api, identity, item.gate, item.subject_ref, rationale, run]);

  const askQuestion = useCallback(() => {
    if (item.gate === 'G2') {
      return run(() => api.askQuestion(item.subject_ref, question, 'general', identity), 'Question asked.');
    }
    return run(() => api.askG3Question(item.subject_ref, question, identity), 'Question asked.');
  }, [api, identity, item.gate, item.subject_ref, question, run]);

  const defer = useCallback(
    () => run(() => api.deferG4(item.subject_ref, rationale, targetDate, identity), 'Deferred.'),
    [api, identity, item.subject_ref, rationale, targetDate, run],
  );

  return (
    <li className={`gate-inbox-card ${item.breached ? 'flagged' : ''}`}>
      <header>
        <span className="pill idle mono">{item.gate}</span>
        <strong>{item.name}</strong>
        {item.site && <span className="faint">{item.site}</span>}
        {item.domain && <span className="faint">domain: {item.domain}</span>}
        <span className="spacer" />
        {item.days_waiting !== null && (
          <span className={gatePillClass(item)}>{item.days_waiting} working day(s) waiting</span>
        )}
      </header>
      <div className="detail">
        {item.open_questions > 0 && <p>{item.open_questions} open question(s)</p>}
        <p className="faint">Needs countersign by: {item.countersigner_role.replace(/_/g, ' ')}</p>
      </div>
      {!canAct ? (
        <p className="faint">
          Deciding this {item.gate} card is the {item.approver_role.replace(/_/g, ' ')}&rsquo;s action.
        </p>
      ) : (
        <footer className="statusbar">
          {notice && <span>{notice}</span>}
          <span className="spacer" />
          <label>
            Rationale
            <textarea rows={1} value={rationale} onChange={(e) => setRationale(e.target.value)} />
          </label>
          <label>
            Countersigned by
            <input type="text" value={countersignedBy} onChange={(e) => setCountersignedBy(e.target.value)} />
          </label>
          <button type="button" className="btn primary" disabled={busy} onClick={() => void approve()}>
            Approve
          </button>
          {item.can_request_changes && (
            <button type="button" className="btn" disabled={busy} onClick={() => void requestChanges()}>
              Request changes
            </button>
          )}
          {item.can_defer && (
            <>
              <label>
                Target date
                <input type="date" value={targetDate} onChange={(e) => setTargetDate(e.target.value)} />
              </label>
              <button type="button" className="btn" disabled={busy} onClick={() => void defer()}>
                Defer
              </button>
            </>
          )}
          {item.can_ask_question && (
            <>
              <label>
                Question
                <input type="text" value={question} onChange={(e) => setQuestion(e.target.value)} />
              </label>
              <button type="button" className="btn" disabled={busy} onClick={() => void askQuestion()}>
                Ask a question
              </button>
            </>
          )}
        </footer>
      )}
    </li>
  );
}

export function GateInbox({ api, identity, liveTick }: Props): JSX.Element {
  const [items, setItems] = useState<GateInboxItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [gateFilter, setGateFilter] = useState<'all' | 'G2' | 'G3' | 'G4'>('all');
  const [siteFilter, setSiteFilter] = useState<string>('all');
  const [notifyBusy, setNotifyBusy] = useState(false);
  const [notifyNotice, setNotifyNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.gateInbox(identity);
      setItems(result.items);
    } catch (caught: unknown) {
      setItems(null);
      setError(caught instanceof ApiError ? caught.message : 'The Gate Inbox could not be read.');
    } finally {
      setLoading(false);
    }
  }, [api, identity]);

  useEffect(() => {
    void load();
  }, [load, liveTick]);

  const notify = useCallback(async () => {
    setNotifyBusy(true);
    setNotifyNotice(null);
    try {
      const result = await api.notifyGateInbox(identity);
      setNotifyNotice(
        `Sent ${result.new_requests_sent.length} new-request notice(s) and `
        + `${result.sla_reminders_sent.length} SLA reminder(s).`,
      );
    } catch (caught: unknown) {
      setNotifyNotice(caught instanceof ApiError ? caught.message : 'Notifications could not be sent.');
    } finally {
      setNotifyBusy(false);
    }
  }, [api, identity]);

  const sites = useMemo(
    () => Array.from(new Set((items ?? []).map((i) => i.site).filter((s): s is string => Boolean(s)))).sort(),
    [items],
  );

  const filtered = useMemo(
    () => (items ?? []).filter(
      (i) => (gateFilter === 'all' || i.gate === gateFilter) && (siteFilter === 'all' || i.site === siteFilter),
    ),
    [items, gateFilter, siteFilter],
  );

  return (
    <div className="workspace gate-inbox-workspace">
      <section className="pane" aria-label="Gate Inbox">
        <header className="pane-header">
          <h2>Gate Inbox</h2>
          <label>
            Gate type
            <select value={gateFilter} onChange={(e) => setGateFilter(e.target.value as typeof gateFilter)}>
              <option value="all">All gates</option>
              <option value="G2">G2</option>
              <option value="G3">G3</option>
              <option value="G4">G4</option>
            </select>
          </label>
          <label>
            Site
            <select value={siteFilter} onChange={(e) => setSiteFilter(e.target.value)}>
              <option value="all">All sites</option>
              {sites.map((site) => (
                <option key={site} value={site}>{site}</option>
              ))}
            </select>
          </label>
          <button type="button" className="btn" disabled={notifyBusy} onClick={() => void notify()}>
            {notifyBusy ? 'Notifying…' : 'Notify'}
          </button>
        </header>
        <div className="pane-body">
          {error && <div className="banner">{error}</div>}
          {!error && loading && !items && <p className="empty">Reading the Gate Inbox…</p>}
          {!error && items && items.length === 0 && <p className="empty">Nothing is waiting for you.</p>}
          {!error && items && items.length > 0 && filtered.length === 0 && (
            <p className="empty">No open request matches these filters.</p>
          )}
          {notifyNotice && <p className="faint">{notifyNotice}</p>}
          {filtered.length > 0 && (
            <ul className="gate-inbox-stack">
              {filtered.map((item) => (
                <GateInboxCard
                  key={`${item.gate}-${item.subject_ref}`} api={api} identity={identity} item={item}
                  onDecided={() => void load()}
                />
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
