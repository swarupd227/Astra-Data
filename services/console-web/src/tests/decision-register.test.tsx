/**
 * The Decision Register -- story S10.4.2, §15.3.6.
 *
 * Tested through what an InfoSec reviewer (this screen's own client persona -- see
 * `App.tsx`'s own `CLIENT_VISIBLE_SURFACES` docstring) sees: the flat list of real
 * `GateDecision` rows the server already sent, client-side gate/decision/approver/search
 * filtering over that one fetched list, opening a row's own evidence bundle (with and
 * without a resolving artefact), and the CSV/signed-PDF export actions.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DecisionRegister } from '../register/DecisionRegister';
import { ApiError, type Identity } from '../lib/api';
import {
  decisionEvidenceBundle,
  decisionRegisterItem,
  decisionRegisterResponse,
  fakeApi,
} from './fixtures';

const INFOSEC: Identity = { principal: 'user:infosec@client.example', roles: ['client_infosec_reviewer'] };

describe('reading the register', () => {
  it('shows the real rows the server already sent', async () => {
    const api = fakeApi();
    render(<DecisionRegister api={api} identity={INFOSEC} />);

    expect(await screen.findByText('Risk Positions')).toBeInTheDocument();
    const table = screen.getByRole('table');
    expect(within(table).getByText('G2')).toBeInTheDocument();
    expect(within(table).getByText('APPROVED')).toBeInTheDocument();
  });

  it('says nothing has been recorded when the register is empty', async () => {
    const api = fakeApi();
    api.decisionRegister = async () => decisionRegisterResponse({ items: [] });
    render(<DecisionRegister api={api} identity={INFOSEC} />);

    expect(await screen.findByText('No decision has been recorded yet.')).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.decisionRegister = async () => {
      throw new ApiError(503, 'unavailable', 'the Decision Register is not available');
    };
    render(<DecisionRegister api={api} identity={INFOSEC} />);

    expect(await screen.findByText(/the Decision Register is not available/)).toBeInTheDocument();
  });

  it('shows a G3 adjudication and a G3 review as two distinct rows under the same gate', async () => {
    const api = fakeApi();
    api.decisionRegister = async () => decisionRegisterResponse({
      items: [
        decisionRegisterItem({ id: 'gd_review', gate: 'G3', decision: 'APPROVED', subject_name: 'Daily VaR' }),
        decisionRegisterItem({
          id: 'gd_adjudication', gate: 'G3', decision: 'PATCHED',
          subject_name: 'FILTER_CONTEXT on Daily VaR',
        }),
      ],
    });
    render(<DecisionRegister api={api} identity={INFOSEC} />);

    expect(await screen.findByText('Daily VaR')).toBeInTheDocument();
    expect(screen.getByText('FILTER_CONTEXT on Daily VaR')).toBeInTheDocument();
    expect(within(screen.getByRole('table')).getByText('PATCHED')).toBeInTheDocument();
  });
});

describe('filters and search', () => {
  it('filters by gate, decision and approver, and searches free text', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.decisionRegister = async () => decisionRegisterResponse({
      items: [
        decisionRegisterItem({ id: 'gd_1', gate: 'G2', decision: 'APPROVED', subject_name: 'Treasury Book', approver: 'user:alice@client.example' }),
        decisionRegisterItem({ id: 'gd_2', gate: 'G4', decision: 'DEFERRED', subject_name: 'GTAA Site', approver: 'user:bob@client.example' }),
      ],
    });
    render(<DecisionRegister api={api} identity={INFOSEC} />);
    await screen.findByText('Treasury Book');

    await user.selectOptions(screen.getByLabelText('Gate'), 'G4');
    expect(screen.queryByText('Treasury Book')).not.toBeInTheDocument();
    expect(screen.getByText('GTAA Site')).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Gate'), 'all');
    await user.type(screen.getByLabelText('Approver'), 'alice');
    expect(screen.getByText('Treasury Book')).toBeInTheDocument();
    expect(screen.queryByText('GTAA Site')).not.toBeInTheDocument();

    await user.clear(screen.getByLabelText('Approver'));
    await user.type(screen.getByLabelText('Search'), 'gtaa');
    expect(screen.queryByText('Treasury Book')).not.toBeInTheDocument();
    expect(screen.getByText('GTAA Site')).toBeInTheDocument();
  });
});

describe('opening evidence', () => {
  it('shows the decision record and the resolved artefact', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.decisionEvidence = async () => decisionEvidenceBundle({
      decision: decisionRegisterItem({ rationale: 'A real rationale.' }),
      artefact: {
        id: 'art_1', kind: 'g3_card_snapshot', mu_ref: 'wb_1', case_id: '',
        content_hash: 'sha256:abc', media_type: 'application/json', size_bytes: 42,
        width: null, height: null,
        produced_by: { adapter: null, adapter_version: null, interface_version: null },
        recorded_by: 'user:owner@client.example', recorded_at: '2027-06-01T09:00:00.000Z',
      },
    });
    render(<DecisionRegister api={api} identity={INFOSEC} />);
    await screen.findByText('Risk Positions');

    await user.click(screen.getByRole('button', { name: 'Open evidence' }));

    const panel = await screen.findByRole('complementary', { name: 'Evidence bundle' });
    expect(within(panel).getByText(/A real rationale\./)).toBeInTheDocument();
    expect(within(panel).getByText(/Stored artefact art_1/)).toBeInTheDocument();
  });

  it('discloses no artefact honestly when the evidence does not resolve', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.decisionEvidence = async () => decisionEvidenceBundle({ artefact: null });
    render(<DecisionRegister api={api} identity={INFOSEC} />);
    await screen.findByText('Risk Positions');

    await user.click(screen.getByRole('button', { name: 'Open evidence' }));

    const panel = await screen.findByRole('complementary', { name: 'Evidence bundle' });
    expect(
      within(panel).getByText('This decision names no evidence that resolves to a stored artefact.'),
    ).toBeInTheDocument();
  });

  it('closes the evidence panel', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<DecisionRegister api={api} identity={INFOSEC} />);
    await screen.findByText('Risk Positions');

    await user.click(screen.getByRole('button', { name: 'Open evidence' }));
    await screen.findByRole('complementary', { name: 'Evidence bundle' });
    await user.click(screen.getByRole('button', { name: 'Close' }));

    expect(screen.queryByRole('complementary', { name: 'Evidence bundle' })).not.toBeInTheDocument();
  });
});

describe('export', () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => 'blob:test');
    URL.revokeObjectURL = vi.fn();
  });

  it('exports CSV and signed PDF', async () => {
    const user = userEvent.setup();
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const api = fakeApi();
    render(<DecisionRegister api={api} identity={INFOSEC} />);
    await screen.findByText('Risk Positions');

    await user.click(screen.getByRole('button', { name: 'Export CSV' }));
    await user.click(screen.getByRole('button', { name: 'Export signed PDF' }));

    expect(clicked).toHaveBeenCalledTimes(2);
    clicked.mockRestore();
  });
});
