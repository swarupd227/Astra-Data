/**
 * Data Handling -- story S11.4.1, opens F11.4.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { DataHandling } from '../data-handling/DataHandling';
import { ApiError } from '../lib/api';
import { dataHandlingPosition, dataHandlingStatus, fakeApi } from './fixtures';

const ARTIZENT_IDENTITY = { principal: 'user:p.eng@artizent.example', roles: ['platform_engineer'] };
const INFOSEC_IDENTITY = { principal: 'user:infosec@client.example', roles: ['client_infosec_reviewer'] };
const OTHER_CLIENT_IDENTITY = { principal: 'user:owner@client.example', roles: ['client_report_owner'] };

describe('providers and retention', () => {
  it('shows the one real wired provider and its own retention terms', async () => {
    render(<DataHandling api={fakeApi()} identity={INFOSEC_IDENTITY} />);
    expect(await screen.findByText('anthropic')).toBeInTheDocument();
    expect(screen.getByText('Not yet reviewed for this tenant.')).toBeInTheDocument();
  });

  it('hides Edit for a role that is not the platform engineer', async () => {
    render(<DataHandling api={fakeApi()} identity={INFOSEC_IDENTITY} />);
    await screen.findByText('anthropic');
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
  });

  it('saves a real, edited position -- a new version', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<DataHandling api={api} identity={ARTIZENT_IDENTITY} />);
    await screen.findByText('anthropic');

    await user.click(screen.getByRole('button', { name: 'Edit' }));
    const retention = screen.getByLabelText('Retention terms');
    await user.clear(retention);
    await user.type(retention, 'Anthropic does not train on API traffic.');
    await user.click(screen.getByRole('button', { name: /Save/ }));

    expect(await screen.findByText('Anthropic does not train on API traffic.')).toBeInTheDocument();
    expect(screen.getByText('version 2')).toBeInTheDocument();
    expect(api.recorded).toContainEqual(expect.objectContaining({ kind: 'SAVE_DATA_HANDLING_POSITION' }));
  });
});

describe('the inference boundary table', () => {
  it('shows real sent and never-sent content, and the current redaction rules', async () => {
    render(<DataHandling api={fakeApi()} identity={INFOSEC_IDENTITY} />);
    expect(await screen.findByText('Calculation expressions and their ASTs')).toBeInTheDocument();
    expect(screen.getByText('Row-level data of any kind')).toBeInTheDocument();
    expect(screen.getByText(/redacted to a short, non-reversible hash/)).toBeInTheDocument();
  });
});

describe('sign-off', () => {
  it('shows the honest unsigned state when nothing has ever been signed', async () => {
    render(<DataHandling api={fakeApi()} identity={INFOSEC_IDENTITY} />);
    expect(await screen.findByText('Not signed')).toBeInTheDocument();
    expect(screen.getByText(/never been signed/)).toBeInTheDocument();
  });

  it('hides Sign boundary for every role but the InfoSec reviewer', async () => {
    render(<DataHandling api={fakeApi()} identity={ARTIZENT_IDENTITY} />);
    await screen.findByText('Not signed');
    expect(screen.queryByRole('button', { name: 'Sign boundary' })).not.toBeInTheDocument();
  });

  it('lets the InfoSec reviewer sign the current position', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<DataHandling api={api} identity={INFOSEC_IDENTITY} />);
    await screen.findByText('Not signed');

    await user.click(screen.getByRole('button', { name: 'Sign boundary' }));

    expect(await screen.findByText('Signed')).toBeInTheDocument();
    expect(screen.getByText(/user:infosec@client.example/)).toBeInTheDocument();
    expect(api.recorded).toContainEqual(
      expect.objectContaining({ kind: 'SIGN_DATA_HANDLING_BOUNDARY', id: INFOSEC_IDENTITY.principal }),
    );
  });

  it('shows a real, previously recorded signature as signed', async () => {
    const api = fakeApi();
    api.seedDataHandlingStatus(
      dataHandlingStatus({
        signoff: { position_version: 1, reviewer: 'user:infosec@client.example', signed_at: '2027-06-01T00:00:00.000Z' },
        signed: true,
      }),
    );
    render(<DataHandling api={api} identity={OTHER_CLIENT_IDENTITY} />);
    expect(await screen.findByText('Signed')).toBeInTheDocument();
  });

  it('requires re-sign after a real, edited position -- a stale signature is shown as unsigned', async () => {
    const api = fakeApi();
    api.seedDataHandlingStatus(
      dataHandlingStatus({
        position: dataHandlingPosition({ version: 2 }),
        signoff: { position_version: 1, reviewer: 'user:infosec@client.example', signed_at: '2027-06-01T00:00:00.000Z' },
        signed: false,
      }),
    );
    render(<DataHandling api={api} identity={INFOSEC_IDENTITY} />);
    expect(await screen.findByText('Not signed')).toBeInTheDocument();
    expect(screen.getByText(/version 1 was signed; re-sign required/)).toBeInTheDocument();
  });
});

describe('the boundary test', () => {
  it('runs on demand and shows a real pass', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<DataHandling api={api} identity={INFOSEC_IDENTITY} />);
    await screen.findByText('anthropic');

    await user.click(screen.getByRole('button', { name: 'Run boundary test' }));

    expect(await screen.findByText('PASS')).toBeInTheDocument();
    expect(screen.getByText(/never reached the assembled context/)).toBeInTheDocument();
    expect(api.recorded).toContainEqual(expect.objectContaining({ kind: 'VERIFY_DATA_HANDLING_BOUNDARY' }));
  });

  it('is offered to any Artizent role too, not only the InfoSec reviewer', async () => {
    render(<DataHandling api={fakeApi()} identity={ARTIZENT_IDENTITY} />);
    await screen.findByText('anthropic');
    expect(screen.getByRole('button', { name: 'Run boundary test' })).toBeInTheDocument();
  });
});

describe('API refusal', () => {
  it('shows the API refusal rather than a generic failure', async () => {
    const api = fakeApi();
    api.failNext(new ApiError(403, 'forbidden', 'Data Handling is open to Artizent roles and the InfoSec reviewer'));
    render(<DataHandling api={api} identity={OTHER_CLIENT_IDENTITY} />);
    expect(await screen.findByText(/InfoSec reviewer/)).toBeInTheDocument();
  });
});

describe("content logging (story S11.4.2's own bounded-window toggle)", () => {
  it('shows the honest off state when nothing has ever been granted', async () => {
    render(<DataHandling api={fakeApi()} identity={INFOSEC_IDENTITY} />);
    expect(await screen.findByText('Off')).toBeInTheDocument();
    expect(screen.getByText(/no window has ever been granted/)).toBeInTheDocument();
  });

  it('hides the enable control for every role but the InfoSec reviewer', async () => {
    render(<DataHandling api={fakeApi()} identity={ARTIZENT_IDENTITY} />);
    await screen.findByText('Off');
    expect(screen.queryByRole('button', { name: 'Enable content logging' })).not.toBeInTheDocument();
  });

  it('lets the InfoSec reviewer enable a real, bounded window', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<DataHandling api={api} identity={INFOSEC_IDENTITY} />);
    await screen.findByText('Off');

    const minutes = screen.getByLabelText('Minutes');
    await user.clear(minutes);
    await user.type(minutes, '30');
    await user.click(screen.getByRole('button', { name: 'Enable content logging' }));

    expect(await screen.findByText('Active')).toBeInTheDocument();
    expect(screen.getByText(/enabled by user:infosec@client.example/)).toBeInTheDocument();
    expect(api.recorded).toContainEqual(
      expect.objectContaining({ kind: 'ENABLE_CONTENT_LOGGING', id: INFOSEC_IDENTITY.principal, reason: '30' }),
    );
  });

  it('lets the InfoSec reviewer disable an active window early', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.seedDataHandlingStatus(
      dataHandlingStatus({
        content_logging_grant: {
          enabled_by: 'user:infosec@client.example', enabled_at: '2027-06-01T00:00:00.000Z',
          expires_at: '2027-06-01T01:00:00.000Z', revoked_at: null, revoked_by: null, active: true,
        },
      }),
    );
    render(<DataHandling api={api} identity={INFOSEC_IDENTITY} />);
    await screen.findByText('Active');

    await user.click(screen.getByRole('button', { name: 'Disable now' }));

    expect(await screen.findByText('Off')).toBeInTheDocument();
    expect(api.recorded).toContainEqual(
      expect.objectContaining({ kind: 'DISABLE_CONTENT_LOGGING', id: INFOSEC_IDENTITY.principal }),
    );
  });

  it('shows a real, previously active window as off once it has been revoked', async () => {
    const api = fakeApi();
    api.seedDataHandlingStatus(
      dataHandlingStatus({
        content_logging_grant: {
          enabled_by: 'user:infosec@client.example', enabled_at: '2027-06-01T00:00:00.000Z',
          expires_at: '2027-06-01T01:00:00.000Z', revoked_at: '2027-06-01T00:10:00.000Z',
          revoked_by: 'user:infosec@client.example', active: false,
        },
      }),
    );
    render(<DataHandling api={api} identity={ARTIZENT_IDENTITY} />);
    expect(await screen.findByText('Off')).toBeInTheDocument();
    expect(screen.getByText(/last active window ended 2027-06-01T00:10:00.000Z/)).toBeInTheDocument();
  });
});
