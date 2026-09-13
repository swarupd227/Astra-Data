/**
 * The Decommission Tracker, S9.2.2, continuing F9.2.
 *
 * Tested through what each role sees: per-site MUs with their own real source/target
 * views and adoption ratio; the honest "unavailable"/"not yet captured" states; setting
 * the threshold gated to the Migration Architect; capturing on demand gated to the
 * Programme Manager.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ApiError, type Identity } from '../lib/api';
import { DecommissionTracker } from '../release/DecommissionTracker';
import {
  adoptionSnapshot,
  decommissionTracker,
  decommissionTrackerMu,
  decommissionTrackerSite,
  fakeApi,
} from './fixtures';

const ARCHITECT: Identity = {
  principal: 'user:architect@artizent.example',
  roles: ['migration_architect'],
};
const PM: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };
const LICENCE_ADMIN: Identity = {
  principal: 'user:licence.admin@client.example',
  roles: ['client_licence_admin'],
};

describe('the tracker', () => {
  it('shows a site\'s own MUs with their real views and ratio', async () => {
    const api = fakeApi();
    api.decommissionTracker = async () =>
      decommissionTracker({
        sites: [
          decommissionTrackerSite({
            site_id: 'site-rqa', name: 'RQA',
            mus: [decommissionTrackerMu({
              workbook_id: 'wb_1', name: 'Daily VaR',
              snapshot: adoptionSnapshot({ source_views: 100, target_views: 90, ratio: 0.9, meets_threshold: true }),
            })],
          }),
        ],
      });
    render(<DecommissionTracker api={api} identity={LICENCE_ADMIN} />);

    const row = (await screen.findByText('Daily VaR')).closest('tr')!;
    expect(within(row).getByText('100')).toBeInTheDocument();
    expect(within(row).getByText('90')).toBeInTheDocument();
    expect(within(row).getByText('90%')).toBeInTheDocument();
    expect(within(row).getByText('meets threshold')).toBeInTheDocument();
  });

  it('is honest when the source usage capability is absent', async () => {
    const api = fakeApi();
    api.decommissionTracker = async () =>
      decommissionTracker({
        sites: [
          decommissionTrackerSite({
            mus: [decommissionTrackerMu({
              workbook_id: 'wb_1', name: 'Daily VaR',
              snapshot: adoptionSnapshot({ source_views: null, target_views: 90, ratio: null, meets_threshold: null }),
            })],
          }),
        ],
      });
    render(<DecommissionTracker api={api} identity={LICENCE_ADMIN} />);

    const row = (await screen.findByText('Daily VaR')).closest('tr')!;
    expect(within(row).getByText('unavailable')).toBeInTheDocument();
    expect(within(row).getByText('not yet captured')).toBeInTheDocument();
  });

  it('shows "not yet captured" for a released MU with no snapshot yet', async () => {
    const api = fakeApi();
    api.decommissionTracker = async () =>
      decommissionTracker({
        sites: [
          decommissionTrackerSite({
            mus: [decommissionTrackerMu({ workbook_id: 'wb_1', name: 'Daily VaR', snapshot: null })],
          }),
        ],
      });
    render(<DecommissionTracker api={api} identity={LICENCE_ADMIN} />);

    expect(await screen.findByText('not yet captured')).toBeInTheDocument();
  });

  it('says no site has a released workbook yet, honestly', async () => {
    const api = fakeApi();
    api.decommissionTracker = async () => decommissionTracker({ sites: [] });
    render(<DecommissionTracker api={api} identity={LICENCE_ADMIN} />);

    expect(await screen.findByText(/No site has a released workbook yet/)).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.decommissionTracker = async () => {
      throw new ApiError(503, 'unavailable', 'the Decommission Tracker is not available on this deployment');
    };
    render(<DecommissionTracker api={api} identity={LICENCE_ADMIN} />);

    expect(await screen.findByText(/the Decommission Tracker is not available/)).toBeInTheDocument();
  });
});

describe('the configured threshold', () => {
  it('lets a migration architect set a real threshold', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<DecommissionTracker api={api} identity={ARCHITECT} />);

    const input = await screen.findByLabelText(/Fraction of source views/);
    await user.clear(input);
    await user.type(input, '0.6');
    await user.click(screen.getByRole('button', { name: 'Save threshold' }));

    expect(await screen.findByText(/set to 60%/)).toBeInTheDocument();
    expect(api.recorded).toContainEqual({ kind: 'SET_ADOPTION_CONFIG', id: '', reason: '0.6' });
  });

  it('hides the threshold panel for a non-migration-architect', async () => {
    const api = fakeApi();
    render(<DecommissionTracker api={api} identity={PM} />);

    await screen.findByText('Decommission Tracker');
    expect(screen.queryByLabelText(/Fraction of source views/)).not.toBeInTheDocument();
  });

  it('refuses an out-of-range threshold before calling the API', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.setAdoptionConfig = async () => {
      throw new Error('should not be called');
    };
    render(<DecommissionTracker api={api} identity={ARCHITECT} />);

    const input = await screen.findByLabelText(/Fraction of source views/);
    await user.clear(input);
    await user.type(input, '1.5');
    await user.click(screen.getByRole('button', { name: 'Save threshold' }));

    expect(await screen.findByText(/must be a fraction between 0 and 1/)).toBeInTheDocument();
  });
});

describe('capturing on demand', () => {
  it('lets a programme manager trigger a real capture', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<DecommissionTracker api={api} identity={PM} />);

    await user.click(await screen.findByRole('button', { name: 'Capture now' }));

    expect(await screen.findByText(/Captured \d+ snapshot/)).toBeInTheDocument();
    expect(api.recorded).toContainEqual({ kind: 'CAPTURE_ADOPTION', id: '', reason: '' });
  });

  it('hides the Capture now action for a non-programme-manager', async () => {
    const api = fakeApi();
    render(<DecommissionTracker api={api} identity={ARCHITECT} />);

    await screen.findByText('Decommission Tracker');
    expect(screen.queryByRole('button', { name: 'Capture now' })).not.toBeInTheDocument();
  });

  it('surfaces a real API refusal', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.captureAdoption = async () => {
      throw new ApiError(403, 'forbidden', 'triggering a capture is the Programme Manager’s action');
    };
    render(<DecommissionTracker api={api} identity={PM} />);

    await user.click(await screen.findByRole('button', { name: 'Capture now' }));

    expect(await screen.findByText(/Programme Manager’s action/)).toBeInTheDocument();
  });
});
