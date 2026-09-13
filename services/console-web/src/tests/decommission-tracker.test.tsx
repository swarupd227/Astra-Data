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
  g4Card,
  readinessItem,
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
const REPORT_OWNER: Identity = {
  principal: 'user:owner@client.example',
  roles: ['client_report_owner'],
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

describe('the G4 card', () => {
  it('opens a site\'s own G4 card with its readiness checklist and confirmation text', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.g4Card = async () => g4Card({ site_id: 'site-rqa', name: 'RQA', confirmation_text: 'I authorise decommissioning RQA.' });
    render(<DecommissionTracker api={api} identity={LICENCE_ADMIN} />);

    await user.click(await screen.findByRole('button', { name: 'Open G4 card' }));

    expect(await screen.findByText('G4 Decommission: RQA')).toBeInTheDocument();
    expect(screen.getByText('ready for G4')).toBeInTheDocument();
    expect(screen.getByText('All in-scope MUs released')).toBeInTheDocument();
    expect(screen.getByText('I authorise decommissioning RQA.')).toBeInTheDocument();
  });

  it('shows an honest not-ready state with the real unmet items', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.g4Card = async () =>
      g4Card({
        ready: false,
        checklist: [readinessItem({ key: 'regression_green', label: 'Regression green', met: false, evidence: { checked: 1, failing: 1 } })],
      });
    render(<DecommissionTracker api={api} identity={LICENCE_ADMIN} />);

    await user.click(await screen.findByRole('button', { name: 'Open G4 card' }));

    expect(await screen.findByText('not yet ready')).toBeInTheDocument();
    expect(screen.getByText('Regression green')).toBeInTheDocument();
  });

  it('surfaces a card read failure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.g4Card = async () => {
      throw new ApiError(404, 'not_found', "no Site 'site-rqa'");
    };
    render(<DecommissionTracker api={api} identity={LICENCE_ADMIN} />);

    await user.click(await screen.findByRole('button', { name: 'Open G4 card' }));

    expect(await screen.findByText(/no Site 'site-rqa'/)).toBeInTheDocument();
  });
});

describe('owner confirmation', () => {
  it('lets a report owner confirm an MU is ready to decommission', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<DecommissionTracker api={api} identity={REPORT_OWNER} />);

    await user.click(await screen.findByRole('button', { name: 'Confirm' }));

    expect(await screen.findByText(/Confirmed for/)).toBeInTheDocument();
    expect(api.recorded).toContainEqual({ kind: 'CONFIRM_DECOMMISSION', id: '01ARZ3NDEKTSV4RRFFQ69G5FAV', reason: '' });
  });

  it('hides the confirmation column for a non-report-owner', async () => {
    const api = fakeApi();
    render(<DecommissionTracker api={api} identity={LICENCE_ADMIN} />);

    await screen.findByText('Decommission Tracker');
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument();
  });
});

describe('authorising or deferring G4', () => {
  it('lets a licence administrator authorise decommission', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<DecommissionTracker api={api} identity={LICENCE_ADMIN} />);

    await user.click(await screen.findByRole('button', { name: 'Open G4 card' }));
    await screen.findByText('G4 Decommission: RQA');
    await user.type(screen.getByLabelText('Rationale'), 'Site is fully ready for decommission.');
    await user.type(screen.getByLabelText('Countersigned by (Programme Manager)'), 'user:pm@artizent.example');
    await user.click(screen.getByRole('button', { name: 'Authorise decommission (G4)' }));

    expect(await screen.findByText(/source workbook\(s\) archived/)).toBeInTheDocument();
    expect(api.recorded).toContainEqual({
      kind: 'APPROVE_G4', id: 'site-rqa', reason: 'Site is fully ready for decommission.',
    });
  });

  it('lets a licence administrator defer with a reason and target date', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<DecommissionTracker api={api} identity={LICENCE_ADMIN} />);

    await user.click(await screen.findByRole('button', { name: 'Open G4 card' }));
    await screen.findByText('G4 Decommission: RQA');
    await user.type(screen.getByLabelText('Defer reason'), 'Client asked for a later cutover date.');
    await user.click(screen.getByRole('button', { name: 'Defer' }));

    expect(await screen.findByText(/Deferred with the reason/)).toBeInTheDocument();
    expect(api.recorded).toContainEqual({
      kind: 'DEFER_G4', id: 'site-rqa', reason: 'Client asked for a later cutover date.',
    });
  });

  it('hides the approve/defer form for a non-licence-administrator', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<DecommissionTracker api={api} identity={PM} />);

    await user.click(await screen.findByRole('button', { name: 'Open G4 card' }));
    await screen.findByText('G4 Decommission: RQA');

    expect(screen.queryByLabelText('Rationale')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Authorise decommission (G4)' })).not.toBeInTheDocument();
  });

  it('surfaces a real API refusal on approve', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.approveG4 = async () => {
      throw new ApiError(400, 'invalid_request', "site 'site-rqa' is not ready for G4: Regression green");
    };
    render(<DecommissionTracker api={api} identity={LICENCE_ADMIN} />);

    await user.click(await screen.findByRole('button', { name: 'Open G4 card' }));
    await screen.findByText('G4 Decommission: RQA');
    await user.type(screen.getByLabelText('Rationale'), 'Site is fully ready for decommission.');
    await user.type(screen.getByLabelText('Countersigned by (Programme Manager)'), 'user:pm@artizent.example');
    await user.click(screen.getByRole('button', { name: 'Authorise decommission (G4)' }));

    expect(await screen.findByText(/not ready for G4/)).toBeInTheDocument();
  });
});
