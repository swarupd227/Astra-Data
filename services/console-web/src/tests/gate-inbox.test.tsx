/**
 * The Gate Inbox -- story S10.4.1, opening F10.4.
 *
 * Tested through what a data owner, a report owner and a licence admin each see: the
 * server's own role-dispatched card stack (never re-filtered client-side), the gate
 * type/site filters, the countersigner-role "who is next" disclosure, and that each
 * card's own actions reuse the identical existing per-gate route -- see
 * `inbox/GateInbox.tsx`'s own docstring.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { GateInbox } from '../inbox/GateInbox';
import { ApiError, type Identity } from '../lib/api';
import {
  designDocument,
  fakeApi,
  familiesResponse,
  familyRecord,
  gateInboxItem,
  gateInboxResponse,
} from './fixtures';

/** `approveG2`/`requestChangesG2`'s own fake implementation (shared with `ModelProposal
 * .tsx`) requires a real `IN_REVIEW` family with a real design document on file --
 * the identical precondition the real server action enforces. */
function fakeApiWithFamilyInReview() {
  return fakeApi(
    undefined, undefined, undefined, undefined, undefined, undefined,
    familiesResponse({ families: [familyRecord({ id: 'fam_one', name: 'Risk Positions', state: 'IN_REVIEW' })] }),
    { fam_one: designDocument({ family_id: 'fam_one' }) },
  );
}

const DATA_OWNER: Identity = { principal: 'user:owner@client.example', roles: ['client_data_owner'] };
const REPORT_OWNER: Identity = { principal: 'user:owner@client.example', roles: ['client_report_owner'] };
const LICENCE_ADMIN: Identity = { principal: 'user:admin@client.example', roles: ['client_licence_admin'] };

describe('reading the inbox', () => {
  it('shows the real items the server already sent, unfiltered by role client-side', async () => {
    const api = fakeApi();
    render(<GateInbox api={api} identity={DATA_OWNER} />);

    const card = (await screen.findByText('Risk Positions')).closest('li')!;
    expect(within(card).getByText('G2')).toBeInTheDocument();
    expect(within(card).getByText('domain: risk')).toBeInTheDocument();
  });

  it('says nothing is waiting when the inbox is empty', async () => {
    const api = fakeApi();
    api.gateInbox = async () => gateInboxResponse({ items: [] });
    render(<GateInbox api={api} identity={DATA_OWNER} />);

    expect(await screen.findByText('Nothing is waiting for you.')).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.gateInbox = async () => {
      throw new ApiError(503, 'unavailable', 'the Gate Inbox is not available');
    };
    render(<GateInbox api={api} identity={DATA_OWNER} />);

    expect(await screen.findByText(/the Gate Inbox is not available/)).toBeInTheDocument();
  });

  it('shows real days waiting and a breached pill for G2, honestly absent for G3/G4', async () => {
    const api = fakeApi();
    api.gateInbox = async () =>
      gateInboxResponse({
        items: [
          gateInboxItem({ gate: 'G2', subject_ref: 'fam_one', days_waiting: 6, breached: true }),
          gateInboxItem({ gate: 'G3', subject_ref: 'wb_one', name: 'Daily VaR', days_waiting: null, breached: false, approver_role: 'client_report_owner' }),
        ],
      });
    render(<GateInbox api={api} identity={DATA_OWNER} />);

    expect(await screen.findByText('6 working day(s) waiting')).toHaveClass('pill', 'bad');
    expect(await screen.findByText('Daily VaR')).toBeInTheDocument();
    expect(screen.queryByText('null working day(s) waiting')).not.toBeInTheDocument();
  });

  it("shows the real countersigner role as who's next", async () => {
    const api = fakeApi();
    render(<GateInbox api={api} identity={DATA_OWNER} />);

    expect(await screen.findByText('Needs countersign by: semantic model engineer')).toBeInTheDocument();
  });
});

describe('filters', () => {
  it('filters by gate type and by site', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.gateInbox = async () =>
      gateInboxResponse({
        items: [
          gateInboxItem({ gate: 'G2', subject_ref: 'fam_one', name: 'Risk Positions', site: null }),
          gateInboxItem({
            gate: 'G3', subject_ref: 'wb_one', name: 'Daily VaR', site: 'RQA',
            approver_role: 'client_data_owner',
          }),
          gateInboxItem({
            gate: 'G4', subject_ref: 'site_one', name: 'GTAA Site', site: 'GTAA',
            approver_role: 'client_data_owner',
          }),
        ],
      });
    render(<GateInbox api={api} identity={DATA_OWNER} />);
    await screen.findByText('Risk Positions');

    await user.selectOptions(screen.getByLabelText('Gate type'), 'G3');
    expect(screen.queryByText('Risk Positions')).not.toBeInTheDocument();
    expect(screen.getByText('Daily VaR')).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Gate type'), 'all');
    await user.selectOptions(screen.getByLabelText('Site'), 'GTAA');
    expect(screen.queryByText('Risk Positions')).not.toBeInTheDocument();
    expect(screen.queryByText('Daily VaR')).not.toBeInTheDocument();
    expect(screen.getByText('GTAA Site')).toBeInTheDocument();
  });
});

describe('acting on a card', () => {
  it('hides the decision controls from anyone but the real approver role', async () => {
    const api = fakeApi();
    render(<GateInbox api={api} identity={REPORT_OWNER} />);
    await screen.findByText('Risk Positions');

    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Deciding this G2 card is the client data owner’s action/),
    ).toBeInTheDocument();
  });

  it('approves a G2 item with the real countersigner and rationale', async () => {
    const user = userEvent.setup();
    const api = fakeApiWithFamilyInReview();
    render(<GateInbox api={api} identity={DATA_OWNER} />);
    await screen.findByText('Risk Positions');

    await user.type(screen.getByLabelText('Rationale'), 'A real rationale sentence.');
    await user.type(screen.getByLabelText('Countersigned by'), 'S. Engineer');
    await user.click(screen.getByRole('button', { name: 'Approve' }));

    expect(await screen.findByText('Approved.')).toBeInTheDocument();
    expect(api.recorded).toContainEqual(
      expect.objectContaining({ kind: 'APPROVE_G2', id: 'fam_one' }),
    );
  });

  it('requests changes on a G2 item', async () => {
    const user = userEvent.setup();
    const api = fakeApiWithFamilyInReview();
    render(<GateInbox api={api} identity={DATA_OWNER} />);
    await screen.findByText('Risk Positions');

    await user.type(screen.getByLabelText('Rationale'), 'Please clarify the grain.');
    await user.click(screen.getByRole('button', { name: 'Request changes' }));

    expect(await screen.findByText('Sent back for changes.')).toBeInTheDocument();
  });

  it('asks a question on a G3 item', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.gateInbox = async () =>
      gateInboxResponse({
        items: [gateInboxItem({
          gate: 'G3', subject_ref: 'wb_one', name: 'Daily VaR', approver_role: 'client_report_owner',
          countersigner_role: 'migration_engineer', can_request_changes: true, can_ask_question: true,
        })],
      });
    render(<GateInbox api={api} identity={REPORT_OWNER} />);
    await screen.findByText('Daily VaR');

    await user.type(screen.getByLabelText('Question'), 'Why is this table extracted daily?');
    await user.click(screen.getByRole('button', { name: 'Ask a question' }));

    expect(await screen.findByText('Question asked.')).toBeInTheDocument();
  });

  it('defers a G4 item with a reason and target date, and shows the API refusal on failure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.gateInbox = async () =>
      gateInboxResponse({
        items: [gateInboxItem({
          gate: 'G4', subject_ref: 'site_one', name: 'GTAA', approver_role: 'client_licence_admin',
          countersigner_role: 'programme_manager', can_ask_question: false, can_request_changes: false,
          can_defer: true,
        })],
      });
    api.failNext(new ApiError(403, 'forbidden', 'programme manager only'));
    render(<GateInbox api={api} identity={LICENCE_ADMIN} />);
    await screen.findByText('GTAA');

    await user.type(screen.getByLabelText('Rationale'), 'Not ready yet.');
    await user.type(screen.getByLabelText('Target date'), '2027-08-01');
    await user.click(screen.getByRole('button', { name: 'Defer' }));

    // A 403 is re-worded into the real, structural refusal rather than echoed verbatim
    // -- see `inbox/GateInbox.tsx`'s own `run()` helper.
    expect(await screen.findByText("Deciding a G4 card is the client licence admin's action.")).toBeInTheDocument();
  });
});

describe('notify', () => {
  it('sends new-request and SLA-reminder notices and reports the counts', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<GateInbox api={api} identity={DATA_OWNER} />);
    await screen.findByText('Risk Positions');

    await user.click(screen.getByRole('button', { name: 'Notify' }));

    expect(await screen.findByText(/Sent 1 new-request notice\(s\) and 0 SLA reminder\(s\)\./)).toBeInTheDocument();
  });
});
