/**
 * The console shell's Entra ID sign-in affordance — story S11.1.1.
 *
 * `lib/entra.ts` is mocked wholesale here (its own real logic is tested in
 * `entra.test.tsx`) so these tests are about what `App.tsx` does with it: nothing
 * renders differently from before this story when Entra is unconfigured, a "Sign in
 * with Microsoft" button appears when it is, and a bearer token acquired through it
 * reaches the API layer.
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { AccountInfo } from '@azure/msal-browser';

import { App } from '../App';
import type { Identity } from '../lib/api';
import { fakeApi } from './fixtures';

const isEntraConfigured = vi.fn(() => false);
const currentAccount = vi.fn().mockResolvedValue(null);
const signIn = vi.fn();
const signOut = vi.fn().mockResolvedValue(undefined);
const acquireToken = vi.fn();

vi.mock('../lib/entra', () => ({
  isEntraConfigured: () => isEntraConfigured(),
  currentAccount: () => currentAccount(),
  signIn: () => signIn(),
  signOut: () => signOut(),
  acquireToken: (account: unknown) => acquireToken(account),
}));

const account = {
  username: 'a.mehta@client.example',
  name: 'A Mehta',
  homeAccountId: 'x',
} as unknown as AccountInfo;

beforeEach(() => {
  vi.clearAllMocks();
  isEntraConfigured.mockReturnValue(false);
  currentAccount.mockResolvedValue(null);
});

describe('Entra unconfigured (the honest default today)', () => {
  it('renders the pre-existing "not signed in" text, unchanged', async () => {
    render(<App api={fakeApi()} environment="local" initialRole="programme_manager" />);
    await screen.findByRole('region', { name: 'Family count calibration' });

    expect(screen.getByText('not signed in')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sign in with Microsoft' })).not.toBeInTheDocument();
  });

  it('never calls currentAccount', async () => {
    render(<App api={fakeApi()} environment="local" initialRole="programme_manager" />);
    await screen.findByRole('region', { name: 'Family count calibration' });
    expect(currentAccount).not.toHaveBeenCalled();
  });
});

describe('Entra configured, signed out', () => {
  beforeEach(() => {
    isEntraConfigured.mockReturnValue(true);
  });

  it('shows a "Sign in with Microsoft" button instead of the stub text', async () => {
    render(<App api={fakeApi()} environment="local" initialRole="programme_manager" />);
    await screen.findByRole('region', { name: 'Family count calibration' });

    expect(screen.getByRole('button', { name: 'Sign in with Microsoft' })).toBeInTheDocument();
    expect(screen.queryByText('not signed in')).not.toBeInTheDocument();
  });

  it('signing in shows the signed-in name and a Sign out button', async () => {
    const user = userEvent.setup();
    signIn.mockResolvedValue({ account, accessToken: 'a-fresh-token' });
    render(<App api={fakeApi()} environment="local" initialRole="programme_manager" />);
    await screen.findByRole('region', { name: 'Family count calibration' });

    await user.click(screen.getByRole('button', { name: 'Sign in with Microsoft' }));

    expect(await screen.findByText('A Mehta')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument();
  });

  it('signing out returns to the sign-in button', async () => {
    const user = userEvent.setup();
    signIn.mockResolvedValue({ account, accessToken: 'a-fresh-token' });
    render(<App api={fakeApi()} environment="local" initialRole="programme_manager" />);
    await screen.findByRole('region', { name: 'Family count calibration' });
    await user.click(screen.getByRole('button', { name: 'Sign in with Microsoft' }));
    await screen.findByRole('button', { name: 'Sign out' });

    await user.click(screen.getByRole('button', { name: 'Sign out' }));

    expect(await screen.findByRole('button', { name: 'Sign in with Microsoft' })).toBeInTheDocument();
    expect(signOut).toHaveBeenCalled();
  });
});

describe('Entra configured, an existing session in MSAL\'s own cache', () => {
  it('picks it up on mount without an interactive prompt', async () => {
    isEntraConfigured.mockReturnValue(true);
    currentAccount.mockResolvedValue(account);
    acquireToken.mockResolvedValue('a-cached-token');

    render(<App api={fakeApi()} environment="local" initialRole="programme_manager" />);
    await screen.findByRole('region', { name: 'Family count calibration' });

    expect(await screen.findByText('A Mehta')).toBeInTheDocument();
    expect(signIn).not.toHaveBeenCalled();
  });
});

describe('the bearer token reaches the API layer', () => {
  it('is carried on Identity once signed in', async () => {
    isEntraConfigured.mockReturnValue(true);
    signIn.mockResolvedValue({ account, accessToken: 'a-fresh-token' });
    const user = userEvent.setup();

    let seenIdentity: Identity | undefined;
    const api = fakeApi();
    const originalEstate = api.estate.bind(api);
    api.estate = (query, identity) => {
      seenIdentity = identity;
      return originalEstate(query, identity);
    };

    render(<App api={api} environment="local" initialRole="programme_manager" />);
    await screen.findByRole('region', { name: 'Family count calibration' });
    await user.click(screen.getByRole('button', { name: 'Sign in with Microsoft' }));
    await screen.findByText('A Mehta');

    await user.selectOptions(screen.getByLabelText(/Acting as/), 'client_infosec_reviewer');
    await waitFor(() => expect(seenIdentity?.bearerToken).toBe('a-fresh-token'));
  });
});
