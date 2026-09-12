/**
 * The Release Board, S9.2.1, opening F9.2.
 *
 * Tested through what each role sees and can do: per-train MUs with their own real
 * pipeline stage and blockers; "Promote to test" gated to the Platform Engineer;
 * "Promote to prod" gated to the Programme Manager, requiring the shared reason dialog;
 * the per-site parallel-run window panel.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ApiError, type Identity } from '../lib/api';
import { ReleaseBoard } from '../release/ReleaseBoard';
import { fakeApi, releaseBoard, releaseBoardMu } from './fixtures';

const PLATFORM_ENGINEER: Identity = {
  principal: 'user:p.eng@artizent.example',
  roles: ['platform_engineer'],
};
const PM: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };
const REPORT_OWNER: Identity = {
  principal: 'user:owner@client.example',
  roles: ['client_report_owner'],
};

describe('the board', () => {
  it('shows a train\'s own MUs with their real pipeline stage', async () => {
    const api = fakeApi();
    api.releaseBoard = async () =>
      releaseBoard({
        trains: [
          {
            id: 'trn_one', name: 'Train 1',
            mus: [releaseBoardMu({ workbook_id: 'wb_1', name: 'Daily VaR', stage: 'TEST', next_stage: 'prod' })],
          },
        ],
      });
    render(<ReleaseBoard api={api} identity={PM} />);

    const row = (await screen.findByText('Daily VaR')).closest('tr')!;
    expect(within(row).getByText('test')).toBeInTheDocument();
    expect(within(row).getByText('none')).toBeInTheDocument();
  });

  it('shows a real blocker count when a MU cannot yet advance', async () => {
    const api = fakeApi();
    api.releaseBoard = async () =>
      releaseBoard({
        trains: [
          {
            id: 'trn_one', name: 'Train 1',
            mus: [releaseBoardMu({
              workbook_id: 'wb_1', name: 'Daily VaR', stage: 'NOT_ACCEPTED', next_stage: 'test',
              blockers: ['G3 has not been approved for this workbook yet'],
            })],
          },
        ],
      });
    render(<ReleaseBoard api={api} identity={PM} />);

    expect(await screen.findByText('1 blocker')).toBeInTheDocument();
  });

  it('shows the per-site parallel-run window', async () => {
    const api = fakeApi();
    api.releaseBoard = async () =>
      releaseBoard({
        sites: [{
          site_id: 'site-rqa', name: 'RQA', released_mu_count: 1, total_mu_count: 2,
          parallel_run_start: '2027-06-01T09:00:00.000Z', parallel_run_end: '2027-06-29T09:00:00.000Z',
        }],
      });
    render(<ReleaseBoard api={api} identity={PM} />);

    const row = (await screen.findByText('RQA')).closest('tr')!;
    expect(within(row).getByText('1 of 2')).toBeInTheDocument();
  });

  it('says no site has a workbook in a train yet, honestly', async () => {
    const api = fakeApi();
    api.releaseBoard = async () => releaseBoard({ sites: [] });
    render(<ReleaseBoard api={api} identity={PM} />);

    expect(await screen.findByText(/No site has a workbook in a train yet/)).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.releaseBoard = async () => {
      throw new ApiError(503, 'unavailable', 'the Release Board is not available on this deployment');
    };
    render(<ReleaseBoard api={api} identity={PM} />);

    expect(await screen.findByText(/the Release Board is not available/)).toBeInTheDocument();
  });
});

describe('promoting to test (MA-08)', () => {
  it('lets a platform engineer promote an ACCEPTED workbook to test', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.releaseBoard = async () =>
      releaseBoard({
        trains: [{
          id: 'trn_one', name: 'Train 1',
          mus: [releaseBoardMu({ workbook_id: 'wb_1', name: 'Daily VaR', stage: 'ACCEPTED', next_stage: 'test' })],
        }],
      });
    render(<ReleaseBoard api={api} identity={PLATFORM_ENGINEER} />);

    await user.click(await screen.findByRole('button', { name: 'Promote to test' }));

    expect(await screen.findByText(/wb_1 promoted to test/)).toBeInTheDocument();
    expect(api.recorded).toContainEqual({ kind: 'PROMOTE_TO_TEST', id: 'wb_1', reason: '' });
  });

  it('hides the Promote to test action for a non-platform-engineer', async () => {
    const api = fakeApi();
    api.releaseBoard = async () =>
      releaseBoard({
        trains: [{
          id: 'trn_one', name: 'Train 1',
          mus: [releaseBoardMu({ workbook_id: 'wb_1', name: 'Daily VaR', stage: 'ACCEPTED', next_stage: 'test' })],
        }],
      });
    render(<ReleaseBoard api={api} identity={REPORT_OWNER} />);

    await screen.findByText('Daily VaR');
    expect(screen.queryByRole('button', { name: 'Promote to test' })).not.toBeInTheDocument();
  });

  it('disables the action while a real blocker remains', async () => {
    const api = fakeApi();
    api.releaseBoard = async () =>
      releaseBoard({
        trains: [{
          id: 'trn_one', name: 'Train 1',
          mus: [releaseBoardMu({
            workbook_id: 'wb_1', name: 'Daily VaR', stage: 'NOT_ACCEPTED', next_stage: 'test',
            blockers: ['G3 has not been approved for this workbook yet'],
          })],
        }],
      });
    render(<ReleaseBoard api={api} identity={PLATFORM_ENGINEER} />);

    expect(await screen.findByRole('button', { name: 'Promote to test' })).toBeDisabled();
  });
});

describe('promoting to prod (MA-09)', () => {
  it('lets a programme manager promote a TEST workbook to prod with a real rationale', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.releaseBoard = async () =>
      releaseBoard({
        trains: [{
          id: 'trn_one', name: 'Train 1',
          mus: [releaseBoardMu({ workbook_id: 'wb_1', name: 'Daily VaR', stage: 'TEST', next_stage: 'prod' })],
        }],
      });
    render(<ReleaseBoard api={api} identity={PM} />);

    await user.click(await screen.findByRole('button', { name: 'Promote to prod' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Reason'), 'Client sign-off received; releasing now.');
    await user.click(within(dialog).getByRole('button', { name: 'Promote' }));

    expect(await screen.findByText(/wb_1 promoted to prod/)).toBeInTheDocument();
    expect(api.recorded).toContainEqual({
      kind: 'PROMOTE_TO_PROD', id: 'wb_1', reason: 'Client sign-off received; releasing now.',
    });
  });

  it('hides the Promote to prod action for a non-programme-manager', async () => {
    const api = fakeApi();
    api.releaseBoard = async () =>
      releaseBoard({
        trains: [{
          id: 'trn_one', name: 'Train 1',
          mus: [releaseBoardMu({ workbook_id: 'wb_1', name: 'Daily VaR', stage: 'TEST', next_stage: 'prod' })],
        }],
      });
    render(<ReleaseBoard api={api} identity={PLATFORM_ENGINEER} />);

    await screen.findByText('Daily VaR');
    expect(screen.queryByRole('button', { name: 'Promote to prod' })).not.toBeInTheDocument();
  });

  it('shows a real API refusal inside the dialog', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.releaseBoard = async () =>
      releaseBoard({
        trains: [{
          id: 'trn_one', name: 'Train 1',
          mus: [releaseBoardMu({ workbook_id: 'wb_1', name: 'Daily VaR', stage: 'TEST', next_stage: 'prod' })],
        }],
      });
    api.promoteToProd = async () => {
      throw new ApiError(400, 'invalid_request', 'a G3 decision needs a rationale of at least 20 characters');
    };
    render(<ReleaseBoard api={api} identity={PM} />);

    await user.click(await screen.findByRole('button', { name: 'Promote to prod' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Reason'), 'too short but ten');
    await user.click(within(dialog).getByRole('button', { name: 'Promote' }));

    expect(await screen.findByText(/at least 20 characters/)).toBeInTheDocument();
  });
});
