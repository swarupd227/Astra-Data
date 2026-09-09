/**
 * The G3 gate card, S9.1.1, opening F9.1/E9.
 *
 * Tested through what a report owner sees and can do: the five real sections, a real
 * waiver and its justification, deciding (Approve/Request changes/Ask a question) gated
 * to the report owner, the rationale/countersigner gate on Approve, and the adaptive
 * card export.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { App, surfaceFromPath } from '../App';
import { G3Card } from '../g3/G3Card';
import { ApiError, type Identity } from '../lib/api';
import { fakeApi, g3Card, g3Question } from './fixtures';

const REPORT_OWNER: Identity = { principal: 'user:owner@client.example', roles: ['client_report_owner'] };
const ENGINEER: Identity = { principal: 'user:engineer@artizent.example', roles: ['migration_engineer'] };

async function loadCard(api: ReturnType<typeof fakeApi>, identity: Identity, workbookId = 'wb_1') {
  const user = userEvent.setup();
  render(<G3Card api={api} identity={identity} />);
  await user.type(screen.getByLabelText('Workbook'), workbookId);
  await user.click(screen.getByRole('button', { name: 'Load' }));
  return user;
}

describe('the card', () => {
  it('shows all five real sections', async () => {
    const api = fakeApi();
    api.g3Card = async () => g3Card();
    await loadCard(api, REPORT_OWNER);

    expect(await screen.findByText(/Daily VaR \(RQA\)/)).toBeInTheDocument();
    expect(screen.getByText(/41\/41 parity cases PASS/)).toBeInTheDocument();
    expect(screen.getByText(/structural 96%/)).toBeInTheDocument();
    expect(screen.getByText(/0 C4 decision\(s\), 0 redesign\(s\)/)).toBeInTheDocument();
    expect(screen.getByText(/promote to test/)).toBeInTheDocument();
  });

  it('shows a real waiver and its justification', async () => {
    const api = fakeApi();
    api.g3Card = async () =>
      g3Card({
        proof: {
          cases_run: 41, cases_pass: 40, charter_version: '3', sampled: false, passes_the_charter: false,
          waivers: [{ subject_ref: 'case_9', approver: 'user:owner@client.example', rationale: 'C4 waiver, signed off.', timestamp: '2027-01-09T00:00:00.000Z' }],
        },
      });
    await loadCard(api, REPORT_OWNER);

    expect(await screen.findByText(/case_9: C4 waiver, signed off\./)).toBeInTheDocument();
  });

  it('says "No waivers" honestly when there are none', async () => {
    const api = fakeApi();
    api.g3Card = async () => g3Card();
    await loadCard(api, REPORT_OWNER);

    expect(await screen.findByText('No waivers.')).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.g3Card = async () => {
      throw new ApiError(403, 'forbidden', 'the G3 gate card is open to Artizent roles and the report owner');
    };
    await loadCard(api, ENGINEER);

    expect(await screen.findByText(/the G3 gate card is open to Artizent roles/)).toBeInTheDocument();
  });
});

describe('deciding', () => {
  it('hides decide controls for anyone but the report owner', async () => {
    const api = fakeApi();
    api.g3Card = async () => g3Card();
    await loadCard(api, ENGINEER);

    await screen.findByText(/Daily VaR/);
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    expect(screen.getByText(/are the report owner/)).toBeInTheDocument();
  });

  it('disables Approve until a real rationale and a countersigner are both present', async () => {
    const api = fakeApi();
    api.g3Card = async () => g3Card();
    const user = await loadCard(api, REPORT_OWNER);
    await screen.findByText(/Daily VaR/);

    const approveButton = screen.getByRole('button', { name: 'Approve' });
    expect(approveButton).toBeDisabled();

    await user.type(screen.getByLabelText('Rationale (at least one sentence)'), 'This report is accurate and ready.');
    expect(approveButton).toBeDisabled();

    await user.type(screen.getByLabelText('Countersigned by'), 'A. Mehta');
    expect(approveButton).toBeEnabled();
  });

  it('approves and shows a real confirmation', async () => {
    const api = fakeApi();
    api.g3Card = async () => g3Card();
    let approved: [string, string, string] | null = null;
    api.approveG3 = async (workbookId, rationale, countersignedBy) => {
      approved = [workbookId, rationale, countersignedBy];
      return { workbook_id: workbookId, gate_decision_id: 'gd_1', decision: 'APPROVED' };
    };
    const user = await loadCard(api, REPORT_OWNER);
    await screen.findByText(/Daily VaR/);

    await user.type(screen.getByLabelText('Rationale (at least one sentence)'), 'This report is accurate and ready.');
    await user.type(screen.getByLabelText('Countersigned by'), 'A. Mehta');
    await user.click(screen.getByRole('button', { name: 'Approve' }));

    expect(await screen.findByText(/Approved -- recorded, countersigned\./)).toBeInTheDocument();
    expect(approved).toEqual(['wb_1', 'This report is accurate and ready.', 'A. Mehta']);
  });

  it('requests changes with just a rationale, no countersigner needed', async () => {
    const api = fakeApi();
    api.g3Card = async () => g3Card();
    let requested: [string, string] | null = null;
    api.requestChangesG3 = async (workbookId, rationale) => {
      requested = [workbookId, rationale];
      return { workbook_id: workbookId, gate_decision_id: 'gd_2', decision: 'CHANGES_REQUESTED' };
    };
    const user = await loadCard(api, REPORT_OWNER);
    await screen.findByText(/Daily VaR/);

    await user.type(screen.getByLabelText('Rationale (at least one sentence)'), 'The visual on page 2 is wrong.');
    await user.click(screen.getByRole('button', { name: 'Request changes…' }));

    expect(await screen.findByText(/Changes requested -- recorded\./)).toBeInTheDocument();
    expect(requested).toEqual(['wb_1', 'The visual on page 2 is wrong.']);
  });

  it('asks a question and shows it in the questions list', async () => {
    const api = fakeApi();
    api.g3Card = async () => g3Card();
    api.g3Questions = async () => ({ questions: [], count: 0 });
    let asked: string | null = null;
    api.askG3Question = async (workbookId, question) => {
      asked = question;
      return g3Question({ workbook_id: workbookId, question });
    };
    const user = await loadCard(api, REPORT_OWNER);
    await screen.findByText(/Daily VaR/);

    await user.type(screen.getByLabelText('Ask a question'), 'Why was this visual redesigned?');
    await user.click(screen.getByRole('button', { name: 'Ask a question' }));

    expect(await screen.findByText('Question sent.')).toBeInTheDocument();
    expect(asked).toBe('Why was this visual redesigned?');
  });

  it('surfaces a decision refusal from the API', async () => {
    const api = fakeApi();
    api.g3Card = async () => g3Card();
    api.approveG3 = async () => {
      throw new ApiError(400, 'invalid_request', 'a G3 decision needs a rationale of at least 20 characters');
    };
    const user = await loadCard(api, REPORT_OWNER);
    await screen.findByText(/Daily VaR/);

    await user.type(screen.getByLabelText('Rationale (at least one sentence)'), 'This report is accurate and ready.');
    await user.type(screen.getByLabelText('Countersigned by'), 'A. Mehta');
    await user.click(screen.getByRole('button', { name: 'Approve' }));

    expect(await screen.findByText(/needs a rationale of at least 20 characters/)).toBeInTheDocument();
  });
});

describe('the adaptive card export', () => {
  it('renders the identical anatomy as a real Adaptive Card document', async () => {
    const api = fakeApi();
    api.g3Card = async (_id, _identity, format) => {
      if (format === 'adaptive_card') {
        return {
          type: 'AdaptiveCard', version: '1.5',
          body: [{ type: 'TextBlock', text: 'G3 · Parity acceptance · Daily VaR' }], actions: [],
        } as never;
      }
      return g3Card();
    };
    const user = await loadCard(api, REPORT_OWNER);
    await screen.findByText(/Daily VaR/);

    await user.click(screen.getByRole('button', { name: 'Preview as Teams adaptive card' }));

    expect(await screen.findByText(/"type": "AdaptiveCard"/)).toBeInTheDocument();
  });
});

describe('the shell', () => {
  it('offers G3 Acceptance as a surface', async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} environment="local" />);

    await user.click(screen.getByRole('button', { name: 'G3 Acceptance' }));

    expect(await screen.findByRole('region', { name: 'G3 Gate Card' })).toBeInTheDocument();
  });

  it('maps /g3 to the G3 gate card', () => {
    expect(surfaceFromPath('/g3')).toBe('g3');
  });
});
