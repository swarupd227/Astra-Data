/**
 * The G3 gate card -- §15.5, story S9.1.1, opening F9.1, opening E9.
 *
 * "A gate card that tells me in 30 seconds what I am approving." Five real sections --
 * what, proof, visual, changes, next -- read from `GET /v1/workbooks/{id}:g3-card`, the
 * identical real facts `parity_dashboard.py`/`exception_desk.py`/`foundry_routing.py`
 * already compute, assembled fresh by `g3_card.py` (see its own docstring for exactly
 * how each section is resolved, and what stays honestly absent -- waivers and a human
 * visual review both real, both empty until something else in this codebase writes
 * one).
 *
 * **Approve / Request changes / Ask a question are the report owner's own three
 * buttons** -- hidden, not disabled, for anyone else, the identical hide-not-disable
 * convention `RegressionMonitor.tsx`'s own "Schedule" and `ExceptionDesk.tsx`'s own
 * decision form already set. **Open report links to the Parity Dashboard** -- the
 * closest real screen this platform has (no dedicated "view the deployed report"
 * screen exists anywhere, confirmed) -- a real `<a href>` navigation, not a client
 * router hop, since no screen in this console has ever linked to another one.
 *
 * **"Renders identically on desktop, mobile and as a Teams adaptive card"** -- one
 * component, one single-column layout at every width (`.g3-card-workspace` in
 * `styles.css`, not the Estate Explorer's own three-pane grid every other multi-pane
 * screen has been quietly inheriting) -- and a real Adaptive Card 1.5 JSON export
 * (`?format=adaptive_card`) carrying the identical five sections, fetched and shown
 * verbatim below the console's own rendering so the two can be compared directly.
 */

import { useCallback, useEffect, useState } from 'react';

import type { Api, G3Card as G3CardData, G3Question, Identity } from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
}

function percent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}

function decisionPillClass(decision: string | null): string {
  if (decision === 'APPROVED') return 'pill ok';
  if (decision === 'CHANGES_REQUESTED') return 'pill bad';
  return 'pill idle';
}

export function G3Card({ api, identity }: Props): JSX.Element {
  const [workbookId, setWorkbookId] = useState('');
  const [loadedWorkbookId, setLoadedWorkbookId] = useState<string | null>(null);
  const [card, setCard] = useState<G3CardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [questions, setQuestions] = useState<G3Question[] | null>(null);

  const [adaptiveCard, setAdaptiveCard] = useState<Record<string, unknown> | null>(null);
  const [adaptiveBusy, setAdaptiveBusy] = useState(false);

  const [rationale, setRationale] = useState('');
  const [countersignedBy, setCountersignedBy] = useState('');
  const [questionText, setQuestionText] = useState('');
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [decisionNotice, setDecisionNotice] = useState<string | null>(null);

  const canDecide = identity.roles.includes('client_report_owner');

  const loadQuestions = useCallback(
    async (id: string) => {
      try {
        const response = await api.g3Questions(id, identity);
        setQuestions(response.questions);
      } catch {
        setQuestions(null);
      }
    },
    [api, identity],
  );

  const load = useCallback(
    async (id: string) => {
      if (!id.trim()) return;
      setBusy(true);
      setError(null);
      setAdaptiveCard(null);
      try {
        const result = await api.g3Card(id.trim(), identity);
        setCard(result);
        setLoadedWorkbookId(id.trim());
        await loadQuestions(id.trim());
      } catch (caught: unknown) {
        setCard(null);
        setLoadedWorkbookId(null);
        setError(caught instanceof ApiError ? caught.message : 'The G3 gate card could not be read.');
      } finally {
        setBusy(false);
      }
    },
    [api, identity, loadQuestions],
  );

  useEffect(() => {
    setRationale('');
    setCountersignedBy('');
    setQuestionText('');
    setDecisionNotice(null);
  }, [loadedWorkbookId]);

  const loadAdaptiveCard = useCallback(async () => {
    if (!loadedWorkbookId) return;
    setAdaptiveBusy(true);
    try {
      const result = await api.g3Card(loadedWorkbookId, identity, 'adaptive_card');
      setAdaptiveCard(result as unknown as Record<string, unknown>);
    } catch (caught: unknown) {
      setDecisionNotice(caught instanceof ApiError ? caught.message : 'The adaptive card could not be read.');
    } finally {
      setAdaptiveBusy(false);
    }
  }, [api, identity, loadedWorkbookId]);

  const approve = useCallback(async () => {
    if (!loadedWorkbookId) return;
    setDecisionBusy(true);
    setDecisionNotice(null);
    try {
      await api.approveG3(loadedWorkbookId, rationale, countersignedBy, identity);
      setDecisionNotice('Approved -- recorded, countersigned.');
      await load(loadedWorkbookId);
    } catch (caught: unknown) {
      setDecisionNotice(caught instanceof ApiError ? caught.message : 'Approval could not be recorded.');
    } finally {
      setDecisionBusy(false);
    }
  }, [api, identity, loadedWorkbookId, rationale, countersignedBy, load]);

  const requestChanges = useCallback(async () => {
    if (!loadedWorkbookId) return;
    setDecisionBusy(true);
    setDecisionNotice(null);
    try {
      await api.requestChangesG3(loadedWorkbookId, rationale, identity);
      setDecisionNotice('Changes requested -- recorded.');
      await load(loadedWorkbookId);
    } catch (caught: unknown) {
      setDecisionNotice(caught instanceof ApiError ? caught.message : 'The request could not be recorded.');
    } finally {
      setDecisionBusy(false);
    }
  }, [api, identity, loadedWorkbookId, rationale, load]);

  const askQuestion = useCallback(async () => {
    if (!loadedWorkbookId) return;
    setDecisionBusy(true);
    setDecisionNotice(null);
    try {
      await api.askG3Question(loadedWorkbookId, questionText, identity);
      setDecisionNotice('Question sent.');
      setQuestionText('');
      await loadQuestions(loadedWorkbookId);
    } catch (caught: unknown) {
      setDecisionNotice(caught instanceof ApiError ? caught.message : 'The question could not be sent.');
    } finally {
      setDecisionBusy(false);
    }
  }, [api, identity, loadedWorkbookId, questionText, loadQuestions]);

  return (
    <div className="workspace g3-card-workspace">
      <section className="pane" aria-label="G3 Gate Card">
        <header className="pane-header">
          <h2>G3 · Parity acceptance</h2>
          {card?.latest_decision && (
            <span className={decisionPillClass(card.latest_decision.decision)}>{card.latest_decision.decision}</span>
          )}
        </header>
        <div className="pane-body">
          <label>
            Workbook
            <input
              type="text"
              value={workbookId}
              onChange={(e) => setWorkbookId(e.target.value)}
              placeholder="workbook id"
            />
          </label>
          {error && <div className="banner">{error}</div>}
          {!error && !card && !busy && (
            <p className="empty">Enter a workbook id to open its G3 gate card.</p>
          )}
          {busy && <p className="empty">Reading the gate card…</p>}

          {card && (
            <>
              <h3>What</h3>
              <p>
                {card.what.name ?? card.workbook_id} ({card.what.site ?? 'unknown site'}) -- {card.what.pages} page(s),{' '}
                {card.what.visuals} visual(s)
              </p>

              <h3>Proof</h3>
              <p>
                {card.proof.cases_pass}/{card.proof.cases_run} parity cases PASS -- charter{' '}
                {card.proof.charter_version ?? '—'}
                {card.proof.sampled ? ' -- sampled' : ' -- full compare'}
                {card.proof.passes_the_charter && <span className="pill ok"> passes the charter</span>}
              </p>
              {card.proof.waivers.length === 0 ? (
                <p className="faint">No waivers.</p>
              ) : (
                <ul>
                  {card.proof.waivers.map((waiver, index) => (
                    <li key={index}>
                      {waiver.subject_ref}: {waiver.rationale} ({waiver.approver})
                    </li>
                  ))}
                </ul>
              )}

              <h3>Visual</h3>
              <p>
                structural {percent(card.visual.structural_score)} -- image {percent(card.visual.image_score)} --{' '}
                {card.visual.human_review_status}
              </p>

              <h3>Changes</h3>
              <p>
                {card.changes.c4_decisions.length} C4 decision(s), {card.changes.redesigns.length} redesign(s)
                {card.changes.model && (
                  <>
                    {' '}-- model {card.changes.model.name ?? card.changes.model.family_id} (
                    {card.changes.model.state ?? 'unknown'}
                    {card.changes.model.approved_at ? `, approved ${card.changes.model.approved_at}` : ''})
                  </>
                )}
              </p>

              <h3>Next</h3>
              <p>
                {card.next.on_approval} ({card.next.parallel_window_weeks} weeks)
              </p>

              {questions && questions.length > 0 && (
                <>
                  <h3>Questions</h3>
                  <ul>
                    {questions.map((q) => (
                      <li key={q.id}>
                        {q.question} -- {q.asked_by}
                      </li>
                    ))}
                  </ul>
                </>
              )}

              <div className="charter-block">
                <label>
                  Rationale (at least one sentence)
                  <textarea value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} />
                </label>
                {canDecide ? (
                  <>
                    <label>
                      Countersigned by
                      <input
                        type="text"
                        value={countersignedBy}
                        onChange={(e) => setCountersignedBy(e.target.value)}
                        placeholder="Migration Engineer"
                      />
                    </label>
                    <div className="row-actions">
                      <button
                        type="button"
                        className="btn primary"
                        disabled={decisionBusy || rationale.trim().length < 20 || !countersignedBy.trim()}
                        onClick={() => void approve()}
                      >
                        {decisionBusy ? 'Approving…' : 'Approve'}
                      </button>
                      <button
                        type="button"
                        className="btn"
                        disabled={decisionBusy || rationale.trim().length < 20}
                        onClick={() => void requestChanges()}
                      >
                        Request changes…
                      </button>
                    </div>
                    <label>
                      Ask a question
                      <textarea value={questionText} onChange={(e) => setQuestionText(e.target.value)} rows={2} />
                    </label>
                    <button
                      type="button"
                      className="btn"
                      disabled={decisionBusy || questionText.trim().length < 5}
                      onClick={() => void askQuestion()}
                    >
                      Ask a question
                    </button>
                  </>
                ) : (
                  <span className="faint">Approve / Request changes / Ask a question are the report owner&rsquo;s.</span>
                )}
                <a className="btn" href={`/parity?workbook=${card.workbook_id}`}>
                  Open report
                </a>
              </div>

              <button type="button" className="btn" disabled={adaptiveBusy} onClick={() => void loadAdaptiveCard()}>
                {adaptiveBusy ? 'Rendering…' : 'Preview as Teams adaptive card'}
              </button>
              {adaptiveCard && (
                <pre className="mono" style={{ whiteSpace: 'pre-wrap' }}>
                  {JSON.stringify(adaptiveCard, null, 2)}
                </pre>
              )}
            </>
          )}
        </div>
        <footer className="statusbar">
          {decisionNotice && <span>{decisionNotice}</span>}
          <span className="spacer" />
          <button type="button" className="btn primary" disabled={busy || !workbookId.trim()} onClick={() => void load(workbookId)}>
            {busy ? 'Loading…' : 'Load'}
          </button>
        </footer>
      </section>
    </div>
  );
}
