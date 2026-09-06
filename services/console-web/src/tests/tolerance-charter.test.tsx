/**
 * The Tolerance Charter editor — S7.1.1.
 *
 * Tested through what a parity engineer, a client analytics lead and everyone else sees
 * and does: read the charter and what each rule means, edit and save a new version, see
 * that changing an already-approved charter needs the client analytics lead's own
 * sign-off, approve at G1, and simulate against a workbook with no prior run — plus that
 * nobody but the Parity Engineer gets the Save button, the same hide-not-disable
 * convention every other role-gated action in this console already follows.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { App, surfaceFromPath } from '../App';
import { ToleranceCharter } from '../charter/ToleranceCharter';
import { ApiError, type Identity } from '../lib/api';
import { fakeApi } from './fixtures';

const PARITY: Identity = { principal: 'user:parity@artizent.example', roles: ['parity_engineer'] };
const ANALYTICS_LEAD: Identity = { principal: 'user:lead@client.example', roles: ['client_analytics_lead'] };
const OTHER: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };

function renderScreen(identity: Identity = PARITY, api = fakeApi()) {
  return { api, ...render(<ToleranceCharter api={api} identity={identity} />) };
}

describe('reading the charter', () => {
  it('shows the version and every block with its inline explanation', async () => {
    renderScreen();

    expect(await screen.findByText('version 0')).toBeInTheDocument();
    expect(screen.getByText('Numeric')).toBeInTheDocument();
    expect(screen.getByText('Waiver rules')).toBeInTheDocument();
    expect(
      screen.getByText(/Two numbers pass if they differ by no more than this absolute amount/),
    ).toBeInTheDocument();
  });

  it('shows the current numeric values', async () => {
    renderScreen();

    const row = (await screen.findByText('abs_epsilon')).closest('tr')!;
    expect(within(row).getByLabelText('Numeric — abs_epsilon')).toHaveValue(0.005);
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.toleranceCharter = async () => {
      throw new ApiError(503, 'unavailable', 'the Tolerance Charter is not available');
    };

    render(<ToleranceCharter api={api} identity={PARITY} />);

    expect(await screen.findByText(/the Tolerance Charter is not available/)).toBeInTheDocument();
  });
});

describe('editing and saving', () => {
  it('lets the parity engineer edit a numeric field and save a new version', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(PARITY);

    const row = (await screen.findByText('abs_epsilon')).closest('tr')!;
    const input = within(row).getByLabelText('Numeric — abs_epsilon');
    await user.clear(input);
    await user.type(input, '0.01');
    await user.click(screen.getByRole('button', { name: 'Save new version' }));

    expect(await screen.findByText(/Saved version 1\./)).toBeInTheDocument();
    expect(screen.getByText('version 1')).toBeInTheDocument();
    expect(api.recorded.some((r) => r.kind === 'SAVE_TOLERANCE_CHARTER')).toBe(true);
  });

  it('disables Save until something actually changed', async () => {
    renderScreen(PARITY);

    await screen.findByText('Numeric');
    expect(screen.getByRole('button', { name: 'Save new version' })).toBeDisabled();
  });

  it('hides Save and the revision fields from anyone but the Parity Engineer', async () => {
    renderScreen(OTHER);

    await screen.findByText('Numeric');
    expect(screen.queryByRole('button', { name: 'Save new version' })).not.toBeInTheDocument();
    expect(screen.getByText(/Editing the Tolerance Charter is the Parity Engineer/)).toBeInTheDocument();
    expect(screen.getByLabelText('Numeric — abs_epsilon')).toBeDisabled();
  });

  it('refuses a revision after G1 without the client analytics lead ack, and succeeds with it', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(PARITY);
    await api.approveG1(0, 'user:parity@artizent.example', 'Agreed at kickoff.', ANALYTICS_LEAD);

    const row = (await screen.findByText('abs_epsilon')).closest('tr')!;
    const input = within(row).getByLabelText('Numeric — abs_epsilon');
    await user.clear(input);
    await user.type(input, '0.02');
    await user.click(screen.getByRole('button', { name: 'Save new version' }));

    expect(await screen.findByText(/client analytics lead/)).toBeInTheDocument();

    await user.type(screen.getByLabelText(/Client analytics lead acknowledgement/), 'user:lead@client.example');
    await user.type(screen.getByLabelText('Reason for the change'), 'Client requested a looser tolerance.');
    await user.click(screen.getByRole('button', { name: 'Save new version' }));

    expect(await screen.findByText(/Recorded a fresh G1 approval/)).toBeInTheDocument();
  });
});

describe('approving at G1', () => {
  it('is hidden from anyone but the client analytics lead', async () => {
    renderScreen(PARITY);
    await screen.findByText('Numeric');
    expect(screen.queryByRole('heading', { name: 'Approve at G1' })).not.toBeInTheDocument();
  });

  it('lets the client analytics lead approve the current version', async () => {
    const user = userEvent.setup();
    renderScreen(ANALYTICS_LEAD);

    await screen.findByRole('heading', { name: 'Approve at G1' });
    await user.type(screen.getByLabelText(/Countersigned by/), 'user:parity@artizent.example');
    await user.type(screen.getByLabelText('Rationale'), 'Agreed at kickoff with the client.');
    await user.click(screen.getByRole('button', { name: 'Approve at G1' }));

    expect(await screen.findByText(/Version 0 approved at G1/)).toBeInTheDocument();
  });
});

describe('simulating', () => {
  it('reports no prior run for a workbook with none', async () => {
    const user = userEvent.setup();
    renderScreen(PARITY);

    await screen.findByRole('heading', { name: 'Simulate' });
    await user.type(screen.getByLabelText('Workbook'), 'wb_no_run');
    await user.click(screen.getByRole('button', { name: 'Simulate' }));

    expect(await screen.findByText('no ParityRun exists yet for this workbook')).toBeInTheDocument();
  });
});

describe('the shell', () => {
  it('offers Tolerance Charter as a surface', async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} environment="local" initialRole="parity_engineer" />);

    await user.click(screen.getByRole('button', { name: 'Tolerance Charter' }));

    expect(await screen.findByRole('heading', { name: 'Tolerance Charter' })).toBeInTheDocument();
  });

  it('maps /charter to the Tolerance Charter surface', () => {
    expect(surfaceFromPath('/charter')).toBe('charter');
  });

  it('offers Parity Engineer and Client Analytics Lead as roles', () => {
    render(<App api={fakeApi()} environment="local" />);
    expect(screen.getByRole('option', { name: 'Parity Engineer' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Client Analytics Lead' })).toBeInTheDocument();
  });
});
