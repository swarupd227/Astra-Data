/**
 * Notification Preferences -- story S10.5.2, §15.5.
 *
 * Tested through what a report owner sees and does: the real, saved (or honestly
 * defaulted) channels/events/digest mode, saving a real change, a read/save failure,
 * and "Send digests now" hidden for a client role and shown for an Artizent one.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { NotificationPreferences } from '../notifications/NotificationPreferences';
import { ApiError, type Identity } from '../lib/api';
import { fakeApi, notificationPreferenceOptions, notificationPreferences } from './fixtures';

const REPORT_OWNER: Identity = { principal: 'user:owner@client.example', roles: ['client_report_owner'] };
const PROGRAMME_MANAGER: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };

describe('reading preferences', () => {
  it('shows the honest defaults when nothing was ever saved', async () => {
    const api = fakeApi();
    api.notificationPreferences = async () => notificationPreferences({ updated_at: null });
    render(<NotificationPreferences api={api} identity={REPORT_OWNER} />);

    expect(await screen.findByText('using the defaults')).toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toBeChecked();
    expect(screen.getByLabelText('Teams')).toBeChecked();
    expect(screen.getByLabelText('Gate request')).toBeChecked();
    expect(screen.getByLabelText('Immediate')).toBeChecked();
  });

  it('shows a real saved preference, not the defaults', async () => {
    const api = fakeApi();
    api.notificationPreferences = async () => notificationPreferences({
      channels: ['email'], events: ['regression_fail'], digest_mode: 'daily',
      updated_at: '2027-06-01T09:00:00.000Z',
    });
    render(<NotificationPreferences api={api} identity={REPORT_OWNER} />);

    await screen.findByText(/saved 2027-06-01/);
    expect(screen.getByLabelText('Email')).toBeChecked();
    expect(screen.getByLabelText('Teams')).not.toBeChecked();
    expect(screen.getByLabelText('Regression fail')).toBeChecked();
    expect(screen.getByLabelText('Gate request')).not.toBeChecked();
    expect(screen.getByLabelText('Daily digest')).toBeChecked();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.notificationPreferences = async () => {
      throw new ApiError(503, 'unavailable', 'notification preferences are not available');
    };
    render(<NotificationPreferences api={api} identity={REPORT_OWNER} />);

    expect(await screen.findByText(/notification preferences are not available/)).toBeInTheDocument();
  });
});

describe('saving preferences', () => {
  it('unchecking a channel and saving persists the real change', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<NotificationPreferences api={api} identity={REPORT_OWNER} />);
    await screen.findByText('using the defaults');

    await user.click(screen.getByLabelText('Teams'));
    await user.click(screen.getByLabelText('Daily digest'));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Saved.')).toBeInTheDocument();
    expect(api.recorded).toContainEqual(
      expect.objectContaining({ kind: 'SAVE_NOTIFICATION_PREFERENCES', reason: 'daily' }),
    );
  });

  it('shows a real API refusal on save failure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<NotificationPreferences api={api} identity={REPORT_OWNER} />);
    await screen.findByText('using the defaults');
    api.failNext(new ApiError(400, 'invalid_request', 'digest_mode must be one of [\'daily\', \'immediate\']'));

    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText(/digest_mode must be one of/)).toBeInTheDocument();
  });
});

describe('digest delivery', () => {
  it('hides "Send digests now" for a client role', async () => {
    const api = fakeApi();
    render(<NotificationPreferences api={api} identity={REPORT_OWNER} />);
    await screen.findByText('using the defaults');

    expect(screen.queryByRole('button', { name: 'Send digests now' })).not.toBeInTheDocument();
  });

  it('shows and runs "Send digests now" for an Artizent role', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.sendNotificationDigests = async () => ({ digests_sent: [], count: 3 });
    render(<NotificationPreferences api={api} identity={PROGRAMME_MANAGER} />);
    await screen.findByText('using the defaults');

    await user.click(screen.getByRole('button', { name: 'Send digests now' }));

    expect(await screen.findByText('Sent 3 digest(s).')).toBeInTheDocument();
  });
});

describe('options', () => {
  it('renders every real choice the server offers', async () => {
    const api = fakeApi();
    api.notificationPreferenceOptions = async () => notificationPreferenceOptions();
    render(<NotificationPreferences api={api} identity={REPORT_OWNER} />);

    await screen.findByText('using the defaults');
    for (const label of ['Email', 'Teams', 'Gate request', 'Exception assigned', 'Regression fail', 'Train re-plan', 'Immediate', 'Daily digest']) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
  });
});
