/**
 * Tenant & Access -- story S11.1.2, opens F11.1.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { TenantAccess } from '../tenant-access/TenantAccess';
import { executionSafetyPolicy, fakeApi, svidRecord } from './fixtures';

const ARTIZENT_IDENTITY = { principal: 'user:p.eng@artizent.example', roles: ['platform_engineer'] };
const INFOSEC_IDENTITY = { principal: 'user:infosec@client.example', roles: ['client_infosec_reviewer'] };

describe('the agent catalog', () => {
  it('lists every declared agent, including the not-yet-built Arbiter', async () => {
    render(<TenantAccess api={fakeApi()} identity={ARTIZENT_IDENTITY} />);

    for (const id of ['harvester', 'cartographer', 'modeller', 'transpiler', 'compositor', 'arbiter', 'mender', 'steward']) {
      expect(await screen.findByText(id)).toBeInTheDocument();
    }
    expect(screen.getByText('not yet built')).toBeInTheDocument();
  });

  it("shows the Transpiler's own real narrowed scope on view", async () => {
    const user = userEvent.setup();
    render(<TenantAccess api={fakeApi()} identity={ARTIZENT_IDENTITY} />);
    await screen.findByText('transpiler');

    const row = screen.getByText('transpiler').closest('tr');
    expect(row).not.toBeNull();
    await user.click(within(row!).getByRole('button', { name: 'View charter' }));

    expect(await screen.findByText(/modify Pattern\.promotion_state/)).toBeInTheDocument();
    expect(screen.getByText(/CalculatedField, ExceptionCase, Measure/)).toBeInTheDocument();
  });

  it('shows unrestricted agents as unrestricted, not narrowed', async () => {
    render(<TenantAccess api={fakeApi()} identity={ARTIZENT_IDENTITY} />);
    const row = (await screen.findByText('harvester')).closest('tr');
    expect(within(row!).getByText('unrestricted')).toBeInTheDocument();
  });
});

describe('SVID records', () => {
  it('shows an honest empty state when none have been issued', async () => {
    render(<TenantAccess api={fakeApi()} identity={ARTIZENT_IDENTITY} />);
    expect(await screen.findByText('No SVID has been issued yet.')).toBeInTheDocument();
  });

  it('lists a real issued SVID with its own status', async () => {
    const api = fakeApi();
    api.seedSvids([svidRecord()]);
    render(<TenantAccess api={api} identity={ARTIZENT_IDENTITY} />);

    expect(await screen.findByText('spiffe://astra-data.internal/agent/transpiler/run/run-1')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
  });
});

describe("revoke (the platform engineer's own action)", () => {
  it('is hidden for a role that is not the platform engineer', async () => {
    const api = fakeApi();
    api.seedSvids([svidRecord()]);
    render(<TenantAccess api={api} identity={INFOSEC_IDENTITY} />);

    await screen.findByText('Active');
    expect(screen.queryByRole('button', { name: 'Revoke' })).not.toBeInTheDocument();
  });

  it('revokes a real SVID with a real reason', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.seedSvids([svidRecord()]);
    render(<TenantAccess api={api} identity={ARTIZENT_IDENTITY} />);
    await screen.findByText('Active');

    await user.click(screen.getByRole('button', { name: 'Revoke' }));
    await user.type(screen.getByLabelText('Reason'), 'rotated off a decommissioned host');
    await user.click(screen.getByRole('button', { name: 'Confirm revoke' }));

    expect(await screen.findByText('Revoked')).toBeInTheDocument();
    expect(api.recorded).toContainEqual(
      expect.objectContaining({ kind: 'REVOKE_SVID', reason: 'rotated off a decommissioned host' }),
    );
  });

  it('will not confirm with too short a reason', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.seedSvids([svidRecord()]);
    render(<TenantAccess api={api} identity={ARTIZENT_IDENTITY} />);
    await screen.findByText('Active');

    await user.click(screen.getByRole('button', { name: 'Revoke' }));
    await user.type(screen.getByLabelText('Reason'), 'short');

    expect(screen.getByRole('button', { name: 'Confirm revoke' })).toBeDisabled();
  });
});

describe("execution safety (story S11.2.1's own tenant policy)", () => {
  it('shows the honest default when no workspace is named production', async () => {
    render(<TenantAccess api={fakeApi()} identity={ARTIZENT_IDENTITY} />);
    expect(await screen.findByText(/none named/)).toBeInTheDocument();
  });

  it('shows a real, previously saved list of production workspaces', async () => {
    const api = fakeApi();
    api.seedExecutionSafetyPolicy(executionSafetyPolicy({ production_workspaces: ['prod'], version: 3 }));
    render(<TenantAccess api={api} identity={ARTIZENT_IDENTITY} />);
    expect(await screen.findByText('prod', { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/version 3/)).toBeInTheDocument();
  });

  it('hides Edit for a role that is not the platform engineer', async () => {
    render(<TenantAccess api={fakeApi()} identity={INFOSEC_IDENTITY} />);
    await screen.findByText(/none named/);
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
  });

  it('saves a real, edited list of production workspaces', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<TenantAccess api={api} identity={ARTIZENT_IDENTITY} />);
    await screen.findByText(/none named/);

    await user.click(screen.getByRole('button', { name: 'Edit' }));
    const input = screen.getByLabelText('Production workspaces (comma-separated)');
    await user.clear(input);
    await user.type(input, 'prod, prod-eu');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText(/prod, prod-eu/)).toBeInTheDocument();
    expect(api.recorded).toContainEqual(
      expect.objectContaining({ kind: 'SAVE_EXECUTION_SAFETY_POLICY', reason: 'prod,prod-eu' }),
    );
  });
});
