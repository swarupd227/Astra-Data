/**
 * The console shell -- story S10.1.1, opening F10.1/E10.
 *
 * Tested through: every §15.1 role lands on its own real screen at the bare root path;
 * client roles see only their own derived surfaces in the nav while Artizent roles see
 * every surface; changing "Acting as" re-lands on the new role's own surface and its
 * own nav; the environment chip is visibly distinct per value; and browser back/forward
 * keeps the rendered surface in sync with the URL.
 */

import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import { App } from '../App';
import { fakeApi } from './fixtures';

beforeEach(() => {
  window.history.replaceState(null, '', '/');
});

describe('role landing pages (§15.1)', () => {
  const cases: { role: string; region: string }[] = [
    { role: 'programme_manager', region: 'Family count calibration' },
    { role: 'semantic_model_engineer', region: 'Model families' },
    { role: 'migration_engineer', region: 'Exception Desk' },
    { role: 'parity_engineer', region: 'Parity Dashboard' },
    { role: 'platform_engineer', region: 'Held workbooks' },
    { role: 'client_data_owner', region: 'Families for review' },
    { role: 'client_report_owner', region: 'G3 Gate Card' },
    { role: 'client_licence_admin', region: 'Decommission Tracker' },
    { role: 'client_infosec_reviewer', region: 'Sites and projects' },
  ];

  for (const { role, region } of cases) {
    it(`lands ${role} on their own real screen`, async () => {
      render(<App api={fakeApi()} environment="local" initialRole={role} />);
      expect(await screen.findByRole('region', { name: region })).toBeInTheDocument();
    });
  }

  it('falls back to Estate Explorer for a role §15.1 never names', async () => {
    render(<App api={fakeApi()} environment="local" initialRole="migration_architect" />);
    expect(await screen.findByRole('region', { name: 'Sites and projects' })).toBeInTheDocument();
  });
});

describe('navigation generated from role', () => {
  it('shows every surface to an Artizent role', async () => {
    render(<App api={fakeApi()} environment="local" initialRole="programme_manager" />);
    await screen.findByRole('region', { name: 'Family count calibration' });

    expect(screen.getByRole('button', { name: 'Decommission Tracker' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Model Proposal' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'G3 Acceptance' })).toBeInTheDocument();
  });

  it('shows a client role only its own landing surface and its own gated surfaces', async () => {
    render(<App api={fakeApi()} environment="local" initialRole="client_report_owner" />);
    await screen.findByRole('region', { name: 'G3 Gate Card' });

    expect(screen.getByRole('button', { name: 'G3 Acceptance' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Decommission Tracker' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Parity Dashboard' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Programme Board' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Model Proposal' })).not.toBeInTheDocument();
  });

  it('shows a single-surface client role exactly one tab', async () => {
    render(<App api={fakeApi()} environment="local" initialRole="client_data_owner" />);
    await screen.findByRole('region', { name: 'Families for review' });

    const nav = screen.getByRole('navigation', { name: 'Surfaces' });
    expect(nav.querySelectorAll('button')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Model Proposal' })).toBeInTheDocument();
  });

  it('a direct URL to a surface not in the nav still renders it', async () => {
    render(<App api={fakeApi()} environment="local" initialRole="client_data_owner" initialSurface="parity" />);
    expect(await screen.findByRole('region', { name: 'Parity Dashboard' })).toBeInTheDocument();
  });
});

describe('changing "Acting as" re-lands on the new role', () => {
  it('switches surface and nav together', async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} environment="local" initialRole="programme_manager" />);
    await screen.findByRole('region', { name: 'Family count calibration' });

    await user.selectOptions(screen.getByLabelText(/Acting as/), 'client_licence_admin');

    expect(await screen.findByRole('region', { name: 'Decommission Tracker' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Programme Board' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Decommission Tracker' })).toBeInTheDocument();
  });
});

describe('the environment chip is visibly distinct (§15.6)', () => {
  it.each([
    ['prod', 'prod'],
    ['test', 'test'],
    ['dev', 'dev'],
    ['local', 'local'],
  ])('renders %s with data-env=%s', async (environment, expected) => {
    const { container } = render(
      <App api={fakeApi()} environment={environment} initialRole="programme_manager" />,
    );
    await screen.findByRole('region', { name: 'Family count calibration' });

    const chip = container.querySelector('.env-chip');
    expect(chip).not.toBeNull();
    expect(chip?.getAttribute('data-env')).toBe(expected);
  });
});

describe('browser back and forward', () => {
  it('keeps the rendered surface in sync with the URL', async () => {
    render(<App api={fakeApi()} environment="local" initialRole="programme_manager" />);
    await screen.findByRole('region', { name: 'Family count calibration' });

    window.history.replaceState(null, '', '/lineage');
    act(() => {
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    expect(await screen.findByRole('region', { name: 'Lineage graph' })).toBeInTheDocument();
  });
});
