/**
 * The Model Proposal (client view) — story S4.2.1.
 *
 * "As a data owner, I want to review a model design for my domain in plain language and
 * approve it or ask a question, so that I sign off what I understand."
 *
 * A deliberately different, calmer document from Model Detail (S4.1.2): no table ids, no
 * storage-mode dropdowns, no relationship diagram — what the model is, what reports use
 * it, what changes for the business user in plain language, and the open questions a data
 * owner actually needs to act on. §15.2: "client surfaces are calm... platform detail is
 * Artizent-only by default."
 *
 * Approving requires asserting a domain scope (`X-Astra-Domain-Scope`, spec §18.1's own
 * "real until E11 maps it for real" posture) — the small text field below the family
 * picker is that assertion, not a directory lookup.
 */

import { useCallback, useEffect, useState } from 'react';

import type { Api, G2Question, Identity, ModelProposal as Proposal } from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
}

export function ModelProposal({ api, identity }: Props): JSX.Element {
  const [families, setFamilies] = useState<{ id: string; name: string; state: string | null }[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [domainScopeText, setDomainScopeText] = useState('');
  const [nonce, setNonce] = useState(0);

  const isDataOwner = identity.roles.includes('client_data_owner');
  const domainScope = domainScopeText
    .split(',')
    .map((d) => d.trim())
    .filter(Boolean);
  const scopedIdentity: Identity = { ...identity, domainScope };

  useEffect(() => {
    let live = true;
    api
      .familiesForReview(identity)
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
      <section className="pane family-list" aria-label="Families for review">
        <header className="pane-header">
          <h2>For review</h2>
        </header>
        <div className="pane-body">
          {isDataOwner && (
            <div className="domain-scope-field">
              <label htmlFor="domain-scope">Your domain(s)</label>
              <input
                id="domain-scope"
                value={domainScopeText}
                onChange={(e) => setDomainScopeText(e.target.value)}
                placeholder="e.g. risk, treasury"
              />
            </div>
          )}
          {error && <div className="banner">{error}</div>}
          {!error && families === null && <p className="empty">Reading families…</p>}
          {!error && families && families.length === 0 && (
            <p className="empty">Nothing is waiting on your review.</p>
          )}
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
                    <span className="pill idle">{family.state ?? 'UNKNOWN'}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {selected ? (
        <ProposalPanel
          key={selected.id}
          api={api}
          identity={identity}
          scopedIdentity={scopedIdentity}
          isDataOwner={isDataOwner}
          familyId={selected.id}
          onDecided={reload}
        />
      ) : (
        <section className="pane" aria-label="Model Proposal">
          <div className="pane-body">
            <p className="empty">Select a family to review its proposal.</p>
          </div>
        </section>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- the panel

function ProposalPanel({
  api,
  identity,
  scopedIdentity,
  isDataOwner,
  familyId,
  onDecided,
}: {
  api: Api;
  identity: Identity;
  scopedIdentity: Identity;
  isDataOwner: boolean;
  familyId: string;
  onDecided: () => void;
}): JSX.Element {
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [countersignedBy, setCountersignedBy] = useState('');
  const [rationale, setRationale] = useState('');
  const [comment, setComment] = useState('');
  const [newQuestion, setNewQuestion] = useState('');
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setProposal(null);
    api
      .proposal(familyId, identity)
      .then((doc) => {
        if (live) {
          setProposal(doc);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (live) setError(caught instanceof ApiError ? caught.message : 'The proposal could not be read.');
      });
    return () => {
      live = false;
    };
  }, [api, identity, familyId, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  const approve = async (): Promise<void> => {
    setBusy(true);
    setNotice(null);
    try {
      await api.approveG2(familyId, countersignedBy, rationale, scopedIdentity);
      setNotice('Approved.');
      reload();
      onDecided();
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The design could not be approved.');
    } finally {
      setBusy(false);
    }
  };

  const requestChanges = async (): Promise<void> => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.requestChangesG2(familyId, comment, scopedIdentity);
      setNotice(`Sent back to DRAFT (review cycle ${result.g2_cycle_count}).`);
      reload();
      onDecided();
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The request could not be recorded.');
    } finally {
      setBusy(false);
    }
  };

  const askQuestion = async (): Promise<void> => {
    setBusy(true);
    setNotice(null);
    try {
      await api.askQuestion(familyId, newQuestion, 'general', identity);
      setNewQuestion('');
      reload();
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The question could not be recorded.');
    } finally {
      setBusy(false);
    }
  };

  const canApprove =
    isDataOwner && proposal?.state === 'IN_REVIEW' && proposal.unanswered_count === 0;
  const canRequestChanges = isDataOwner && proposal?.state === 'IN_REVIEW';
  const canAsk = isDataOwner && proposal?.state === 'IN_REVIEW';

  return (
    <section className="pane model-detail-panel" aria-label={proposal ? `Model Proposal — ${proposal.name}` : 'Model Proposal'}>
      <div className="pane-body model-detail-body">
        {error && <div className="banner">{error}</div>}
        {proposal === null && !error && <p className="empty">Reading the proposal…</p>}

        {proposal && (
          <div className="detail">
            <div>
              <h3>{proposal.name}</h3>
              <p>
                <span className="pill idle">{proposal.state ?? 'UNKNOWN'}</span>
                {proposal.domain && <span className="muted"> · {proposal.domain}</span>}
              </p>
            </div>

            <div>
              <h3>What this model is</h3>
              <p>{proposal.grain_statement ?? 'The grain has not been drafted yet.'}</p>
              <p>{proposal.plain_summary}</p>
            </div>

            <div>
              <h3>Reports that use it</h3>
              {proposal.reports.length === 0 ? (
                <p className="faint">No reports listed yet.</p>
              ) : (
                <ul className="plain-list">
                  {proposal.reports.map((report) => (
                    <li key={report}>{report}</li>
                  ))}
                </ul>
              )}
            </div>

            <div>
              <h3>
                Open questions
                {proposal.unanswered_count > 0 && (
                  <span className="pill bad" style={{ marginLeft: 8 }}>
                    {proposal.unanswered_count} unanswered
                  </span>
                )}
              </h3>
              {proposal.open_questions.length === 0 ? (
                <p className="faint">No questions raised.</p>
              ) : (
                <ul className="plain-list">
                  {proposal.open_questions.map((question) => (
                    <QuestionRow key={question.id} api={api} identity={identity} question={question} onChanged={reload} />
                  ))}
                </ul>
              )}
              {canAsk && (
                <div className="actions-row" style={{ marginTop: 8 }}>
                  <textarea
                    aria-label="Ask a question"
                    value={newQuestion}
                    onChange={(e) => setNewQuestion(e.target.value)}
                    placeholder="Ask a question about this design…"
                    rows={2}
                    style={{ flex: 1 }}
                  />
                  <button type="button" className="btn small" disabled={busy || newQuestion.trim().length < 5} onClick={() => void askQuestion()}>
                    Ask
                  </button>
                </div>
              )}
            </div>

            {isDataOwner && proposal.state === 'IN_REVIEW' && (
              <div>
                <h3>Your decision</h3>
                <label htmlFor="countersigned-by">Semantic Model Engineer countersigning</label>
                <input
                  id="countersigned-by"
                  value={countersignedBy}
                  onChange={(e) => setCountersignedBy(e.target.value)}
                  placeholder="user:sme@artizent.example"
                />
                <label htmlFor="rationale">Rationale</label>
                <textarea id="rationale" value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} />
                <div className="dialog-actions">
                  <button
                    type="button"
                    className="btn primary"
                    disabled={!canApprove || busy || countersignedBy.trim().length === 0 || rationale.trim().length < 8}
                    onClick={() => void approve()}
                    title={proposal.unanswered_count > 0 ? 'Answer every open question first' : undefined}
                  >
                    {busy ? 'Working…' : 'Approve'}
                  </button>
                </div>
                <label htmlFor="rc-comment">Comment (for request changes)</label>
                <textarea id="rc-comment" value={comment} onChange={(e) => setComment(e.target.value)} rows={2} />
                <div className="dialog-actions">
                  <button
                    type="button"
                    className="btn"
                    disabled={!canRequestChanges || busy || comment.trim().length < 8}
                    onClick={() => void requestChanges()}
                  >
                    {busy ? 'Working…' : 'Request changes'}
                  </button>
                </div>
              </div>
            )}
            {!isDataOwner && (
              <p className="faint">Approving, requesting changes and asking a question are the data owner&rsquo;s.</p>
            )}
          </div>
        )}
      </div>

      <footer className="statusbar">
        {notice && <span>{notice}</span>}
      </footer>
    </section>
  );
}

// ------------------------------------------------------------------------- one question

function QuestionRow({
  api,
  identity,
  question,
  onChanged,
}: {
  api: Api;
  identity: Identity;
  question: G2Question;
  onChanged: () => void;
}): JSX.Element {
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);

  const sendReply = async (): Promise<void> => {
    setBusy(true);
    try {
      await api.replyToQuestion(question.id, reply, identity);
      setReply('');
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const markAnswered = async (): Promise<void> => {
    setBusy(true);
    try {
      await api.answerQuestion(question.id, identity);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <li>
      <div>
        <span className={`pill ${question.state === 'OPEN' ? 'warn' : 'ok'}`}>{question.state}</span>{' '}
        {question.question}
        <span className="muted"> — asked by {question.asked_by}</span>
      </div>
      {question.thread.length > 0 && (
        <ul className="wave-events">
          {question.thread.map((message, index) => (
            <li key={index}>
              <span className="mono">{message.from}</span>
              <span className="muted">: {message.text}</span>
            </li>
          ))}
        </ul>
      )}
      {question.state === 'OPEN' && (
        <div className="actions-row">
          <input
            aria-label={`Reply to: ${question.question}`}
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            placeholder="Reply…"
            style={{ flex: 1 }}
          />
          <button type="button" className="btn small" disabled={busy || reply.trim().length < 2} onClick={() => void sendReply()}>
            Reply
          </button>
          <button type="button" className="btn small" disabled={busy} onClick={() => void markAnswered()}>
            Mark answered
          </button>
        </div>
      )}
    </li>
  );
}
