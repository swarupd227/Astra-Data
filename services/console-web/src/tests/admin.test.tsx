/**
 * Admin — the conformance ruleset, S4.3.2.
 *
 * Tested through what an architect sees and does: read the six rules and what each means,
 * toggle one, edit a parameter, save a new version — and that nobody else gets the Save
 * button, the same hide-not-disable convention every other role-gated action already
 * follows in this console.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { Admin } from '../admin/Admin';
import { App, surfaceFromPath } from '../App';
import { ApiError, type Identity } from '../lib/api';
import { fakeApi } from './fixtures';

const ARCHITECT: Identity = {
  principal: 'user:architect@artizent.example',
  roles: ['migration_architect'],
};
const ENGINEER: Identity = {
  principal: 'user:sme@artizent.example',
  roles: ['semantic_model_engineer'],
};

function renderScreen(identity: Identity = ARCHITECT, api = fakeApi()) {
  return { api, ...render(<Admin api={api} identity={identity} />) };
}

describe('reading the ruleset', () => {
  it('shows every rule with its label, description and current version', async () => {
    renderScreen();

    expect(await screen.findByText('version 1')).toBeInTheDocument();
    expect(screen.getByText('Star schema only')).toBeInTheDocument();
    expect(screen.getByText('Naming convention')).toBeInTheDocument();
    expect(screen.getByText('RLS roles tested with a fixture user')).toBeInTheDocument();
    expect(screen.getByText(/No many-to-many without a bridge table/)).toBeInTheDocument();
  });

  it('shows a parameter field for rules that have one', async () => {
    renderScreen();

    const namingRow = (await screen.findByText('Naming convention')).closest('tr')!;
    expect(within(namingRow).getByLabelText(/Maximum name length/)).toHaveValue('100');
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.conformanceRules = async () => {
      throw new ApiError(503, 'unavailable', 'the conformance ruleset is not available');
    };

    render(<Admin api={api} identity={ARCHITECT} />);

    expect(await screen.findByText(/the conformance ruleset is not available/)).toBeInTheDocument();
  });
});

describe('editing and saving', () => {
  it('lets the architect disable a rule and save a new version', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(ARCHITECT);

    const starRow = (await screen.findByText('Star schema only')).closest('tr')!;
    await user.click(within(starRow).getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Save new version' }));

    expect(await screen.findByText('Saved version 2.')).toBeInTheDocument();
    expect(screen.getByText('version 2')).toBeInTheDocument();
    expect(
      api.recorded.some((r) => r.kind === 'SAVE_CONFORMANCE_RULES'),
    ).toBe(true);
  });

  it('lets the architect change a parameter', async () => {
    const user = userEvent.setup();
    renderScreen(ARCHITECT);

    const namingRow = (await screen.findByText('Naming convention')).closest('tr')!;
    const input = within(namingRow).getByLabelText(/Maximum name length/);
    await user.clear(input);
    await user.type(input, '64');
    await user.click(screen.getByRole('button', { name: 'Save new version' }));

    expect(await screen.findByText('Saved version 2.')).toBeInTheDocument();
  });

  it('disables Save until something actually changed', async () => {
    renderScreen(ARCHITECT);

    await screen.findByText('Star schema only');
    expect(screen.getByRole('button', { name: 'Save new version' })).toBeDisabled();
  });

  it('hides Save from anyone but the Migration Architect', async () => {
    renderScreen(ENGINEER);

    await screen.findByText('Star schema only');
    expect(screen.queryByRole('button', { name: 'Save new version' })).not.toBeInTheDocument();
    expect(screen.getByText(/Editing the conformance ruleset is the Migration Architect/)).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /Enable Star schema only/ })).toBeDisabled();
  });

  it('shows the API refusal rather than a generic failure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.failNext(new ApiError(403, 'forbidden', 'editing the conformance ruleset is the architect’s'));
    renderScreen(ARCHITECT, api);

    const starRow = (await screen.findByText('Star schema only')).closest('tr')!;
    await user.click(within(starRow).getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Save new version' }));

    expect(await screen.findByText(/editing the conformance ruleset is the architect/)).toBeInTheDocument();
  });
});

describe('the shell', () => {
  it('offers Admin as a surface', async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} environment="local" initialRole="migration_architect" />);

    await user.click(screen.getByRole('button', { name: 'Admin' }));

    expect(await screen.findByText('Conformance rules')).toBeInTheDocument();
  });

  it('maps /admin to Admin', () => {
    expect(surfaceFromPath('/admin')).toBe('admin');
  });

  it('offers Migration Architect as a role', () => {
    render(<App api={fakeApi()} environment="local" />);
    expect(screen.getByRole('option', { name: 'Migration Architect' })).toBeInTheDocument();
  });
});
