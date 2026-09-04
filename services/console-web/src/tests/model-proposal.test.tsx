/**
 * The Model Proposal (client view), S4.2.1.
 *
 * Tested through what a data owner sees and does: pick a family from the "for review"
 * list, read the plain-language summary and reports, ask/reply/answer a question, and
 * approve or request changes — each gated to the client data owner role, and approval
 * gated further on every question being answered.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { App, surfaceFromPath } from '../App';
import { ModelProposal } from '../g2/ModelProposal';
import type { Identity } from '../lib/api';
import { designDocument, fakeApi, familiesResponse, familyRecord, g2Question } from './fixtures';

const OWNER: Identity = { principal: 'user:owner@client.example', roles: ['client_data_owner'] };
const SME: Identity = { principal: 'user:sme@artizent.example', roles: ['semantic_model_engineer'] };

function renderScreen(identity: Identity = OWNER, api = fakeApi()) {
  return { api, ...render(<ModelProposal api={api} identity={identity} />) };
}

function withFamily(overrides: Parameters<typeof familyRecord>[0]) {
  return familiesResponse({ families: [familyRecord(overrides)] });
}

describe('the family list', () => {
  it('shows families waiting on review', async () => {
    renderScreen(
      OWNER,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamily({ id: 'fam_one', name: 'Risk Positions', state: 'IN_REVIEW' }),
      ),
    );

    expect(await screen.findByText('Risk Positions')).toBeInTheDocument();
  });

  it('says nothing is waiting rather than an empty screen', async () => {
    renderScreen(OWNER, fakeApi(undefined, undefined, undefined, undefined, undefined, undefined, { families: [], count: 0 }));

    expect(await screen.findByText(/Nothing is waiting on your review/)).toBeInTheDocument();
  });

  it('offers the domain-scope field to a data owner', async () => {
    renderScreen(
      OWNER,
      fakeApi(undefined, undefined, undefined, undefined, undefined, undefined, withFamily({ id: 'fam_one', state: 'IN_REVIEW' })),
    );

    expect(await screen.findByLabelText('Your domain(s)')).toBeInTheDocument();
  });

  it('does not offer the domain-scope field to a non data owner', async () => {
    renderScreen(
      SME,
      fakeApi(undefined, undefined, undefined, undefined, undefined, undefined, withFamily({ id: 'fam_one', state: 'IN_REVIEW' })),
    );

    await screen.findByText(/Approving, requesting changes/);
    expect(screen.queryByLabelText('Your domain(s)')).not.toBeInTheDocument();
  });
});

describe('the proposal', () => {
  function apiWithProposal(question = g2Question()) {
    return fakeApi(
      undefined, undefined, undefined, undefined, undefined, undefined,
      withFamily({
        id: 'fam_one', name: 'Risk Positions', state: 'IN_REVIEW',
        members: ['Daily VaR', 'Weekly VaR'],
      }),
      { fam_one: designDocument({ family_id: 'fam_one' }) },
      { fam_one: [question] },
    );
  }

  it('renders what the model is, in plain language', async () => {
    renderScreen(OWNER, apiWithProposal());

    expect(await screen.findByText('One row per Desk and Trade Date.')).toBeInTheDocument();
    expect(await screen.findByText(/This model brings together/)).toBeInTheDocument();
  });

  it('lists the reports that use it', async () => {
    renderScreen(OWNER, apiWithProposal());

    expect(await screen.findByText('Daily VaR')).toBeInTheDocument();
    expect(screen.getByText('Weekly VaR')).toBeInTheDocument();
  });

  it('shows an open question with its asker', async () => {
    renderScreen(OWNER, apiWithProposal());

    expect(await screen.findByText(/table 'positions' is sourced from custom SQL/)).toBeInTheDocument();
    expect(screen.getByText(/asked by agent:modeller/)).toBeInTheDocument();
  });

  it('a non data owner cannot approve, request changes, or ask', async () => {
    renderScreen(SME, apiWithProposal());

    await screen.findByText('Risk Positions');
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Request changes' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Ask a question')).not.toBeInTheDocument();
  });
});

describe('answering a question', () => {
  it('replying appears in the thread', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(OWNER, (() => {
      const store = fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamily({ id: 'fam_one', state: 'IN_REVIEW' }), {}, { fam_one: [g2Question()] },
      );
      return store;
    })());

    const field = await screen.findByLabelText(/Reply to:/);
    await user.type(field, 'Confirmed with the source team.');
    await user.click(screen.getByRole('button', { name: 'Reply' }));

    expect(api.recorded).toContainEqual({ kind: 'REPLY_TO_QUESTION', id: 'q_one', reason: '' });
  });

  it('marking a question answered removes its reply controls', async () => {
    const user = userEvent.setup();
    renderScreen(
      OWNER,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamily({ id: 'fam_one', state: 'IN_REVIEW' }), {}, { fam_one: [g2Question()] },
      ),
    );

    await user.click(await screen.findByRole('button', { name: 'Mark answered' }));

    expect(await screen.findByText('ANSWERED')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Mark answered' })).not.toBeInTheDocument();
  });

  it('a data owner can ask a new question', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(
      OWNER,
      fakeApi(undefined, undefined, undefined, undefined, undefined, undefined, withFamily({ id: 'fam_one', state: 'IN_REVIEW' })),
    );

    await screen.findByText('Risk Positions');
    const field = screen.getByLabelText('Ask a question');
    await user.type(field, 'Why is this refreshed daily rather than hourly?');
    await user.click(screen.getByRole('button', { name: 'Ask' }));

    expect(api.recorded).toContainEqual({ kind: 'ASK_QUESTION', id: 'fam_one', reason: '' });
  });
});

describe('approving and requesting changes', () => {
  it('approve is disabled while a question is open', async () => {
    renderScreen(
      OWNER,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamily({ id: 'fam_one', state: 'IN_REVIEW' }), {}, { fam_one: [g2Question()] },
      ),
    );

    expect(await screen.findByRole('button', { name: 'Approve' })).toBeDisabled();
  });

  it('approving records the decision once every question is answered', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(
      OWNER,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamily({ id: 'fam_one', state: 'IN_REVIEW' }),
        { fam_one: designDocument({ family_id: 'fam_one' }) },
      ),
    );

    await user.type(await screen.findByLabelText('Semantic Model Engineer countersigning'), SME.principal);
    await user.type(screen.getByLabelText('Rationale'), 'Reviewed and approved.');
    await user.click(screen.getByRole('button', { name: 'Approve' }));

    expect(api.recorded).toContainEqual({ kind: 'APPROVE_G2', id: 'fam_one', reason: 'Reviewed and approved.' });
    expect(await screen.findByText('Approved.')).toBeInTheDocument();
  });

  it('requesting changes records a comment', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(
      OWNER,
      fakeApi(undefined, undefined, undefined, undefined, undefined, undefined, withFamily({ id: 'fam_one', state: 'IN_REVIEW' })),
    );

    await user.type(await screen.findByLabelText('Comment (for request changes)'), 'Please add descriptions.');
    await user.click(screen.getByRole('button', { name: 'Request changes' }));

    expect(api.recorded).toContainEqual({ kind: 'REQUEST_CHANGES_G2', id: 'fam_one', reason: 'Please add descriptions.' });
    expect(await screen.findByText(/Sent back to DRAFT/)).toBeInTheDocument();
  });
});

describe('the shell', () => {
  it('offers Model Proposal as a surface', async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} environment="local" />);

    await user.click(screen.getByRole('button', { name: 'Model Proposal' }));

    expect(await screen.findByText('For review')).toBeInTheDocument();
  });

  it('maps /proposal to Model Proposal', () => {
    expect(surfaceFromPath('/proposal')).toBe('proposal');
  });
});
