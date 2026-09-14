/**
 * Entra ID sign-in — story S11.1.1. `lib/entra.ts`'s own module docstring discloses this
 * as real but never run against a live tenant; these tests exercise its real logic
 * (config gating, cache-first lookup before an interactive popup, sign-out) against a
 * mocked `@azure/msal-browser`, the seam that stands in for a live tenant.
 */

import { type AccountInfo, InteractionRequiredAuthError } from '@azure/msal-browser';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const initialize = vi.fn().mockResolvedValue(undefined);
const getAllAccounts = vi.fn();
const loginPopup = vi.fn();
const logoutPopup = vi.fn();
const acquireTokenSilent = vi.fn();
const acquireTokenPopup = vi.fn();

vi.mock('@azure/msal-browser', async () => {
  const actual = await vi.importActual<typeof import('@azure/msal-browser')>('@azure/msal-browser');
  return {
    ...actual,
    PublicClientApplication: vi.fn().mockImplementation(() => ({
      initialize,
      getAllAccounts,
      loginPopup,
      logoutPopup,
      acquireTokenSilent,
      acquireTokenPopup,
    })),
  };
});

const account = {
  username: 'a.mehta@client.example',
  name: 'A Mehta',
  homeAccountId: 'x',
} as unknown as AccountInfo;

beforeEach(() => {
  vi.clearAllMocks();
  initialize.mockResolvedValue(undefined);
  getAllAccounts.mockReturnValue([]);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

async function loadEntra() {
  return import('../lib/entra');
}

describe('isEntraConfigured', () => {
  it('is false when neither variable is set (the honest default today)', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', '');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', '');
    const { isEntraConfigured } = await loadEntra();
    expect(isEntraConfigured()).toBe(false);
  });

  it('is false with only one of the two set', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', 'client-id');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', '');
    const { isEntraConfigured } = await loadEntra();
    expect(isEntraConfigured()).toBe(false);
  });

  it('is true once both are set', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', 'client-id');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', 'tenant-id');
    const { isEntraConfigured } = await loadEntra();
    expect(isEntraConfigured()).toBe(true);
  });
});

describe('currentAccount', () => {
  it('is null when Entra is not configured -- never touches MSAL at all', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', '');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', '');
    const { currentAccount } = await loadEntra();
    expect(await currentAccount()).toBeNull();
    expect(initialize).not.toHaveBeenCalled();
  });

  it('returns the cached account when MSAL already has one', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', 'client-id');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', 'tenant-id');
    getAllAccounts.mockReturnValue([account]);
    const { currentAccount } = await loadEntra();
    expect(await currentAccount()).toBe(account);
  });

  it('is null when MSAL has no cached account', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', 'client-id');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', 'tenant-id');
    getAllAccounts.mockReturnValue([]);
    const { currentAccount } = await loadEntra();
    expect(await currentAccount()).toBeNull();
  });
});

describe('signIn', () => {
  it('runs an interactive popup and returns the resulting session', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', 'client-id');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', 'tenant-id');
    loginPopup.mockResolvedValue({ account, accessToken: 'a-fresh-token' });
    const { signIn } = await loadEntra();

    const session = await signIn();

    expect(session).toEqual({ account, accessToken: 'a-fresh-token' });
    expect(loginPopup).toHaveBeenCalledWith(
      expect.objectContaining({ scopes: ['api://client-id/access_as_user'] }),
    );
  });

  it('uses the configured scope override when one is set', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', 'client-id');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', 'tenant-id');
    vi.stubEnv('VITE_ENTRA_API_SCOPE', 'api://custom-scope/access_as_user');
    loginPopup.mockResolvedValue({ account, accessToken: 'a-fresh-token' });
    const { signIn } = await loadEntra();

    await signIn();

    expect(loginPopup).toHaveBeenCalledWith(
      expect.objectContaining({ scopes: ['api://custom-scope/access_as_user'] }),
    );
  });
});

describe('signOut', () => {
  it('does nothing when Entra is not configured', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', '');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', '');
    const { signOut } = await loadEntra();
    await signOut();
    expect(logoutPopup).not.toHaveBeenCalled();
  });

  it('signs out the cached account when one exists', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', 'client-id');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', 'tenant-id');
    getAllAccounts.mockReturnValue([account]);
    const { signOut } = await loadEntra();

    await signOut();

    expect(logoutPopup).toHaveBeenCalledWith({ account });
  });

  it('does not call logoutPopup when there is no cached account', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', 'client-id');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', 'tenant-id');
    getAllAccounts.mockReturnValue([]);
    const { signOut } = await loadEntra();

    await signOut();

    expect(logoutPopup).not.toHaveBeenCalled();
  });
});

describe('acquireToken', () => {
  it('returns a silently-acquired token when one is available', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', 'client-id');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', 'tenant-id');
    acquireTokenSilent.mockResolvedValue({ accessToken: 'silent-token' });
    const { acquireToken } = await loadEntra();

    expect(await acquireToken(account)).toBe('silent-token');
    expect(acquireTokenPopup).not.toHaveBeenCalled();
  });

  it('falls back to an interactive popup only when silent acquisition needs interaction', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', 'client-id');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', 'tenant-id');
    acquireTokenSilent.mockRejectedValue(new InteractionRequiredAuthError('consent_required'));
    acquireTokenPopup.mockResolvedValue({ accessToken: 'popup-token' });
    const { acquireToken } = await loadEntra();

    expect(await acquireToken(account)).toBe('popup-token');
  });

  it('propagates any other error without falling back to a popup', async () => {
    vi.stubEnv('VITE_ENTRA_CLIENT_ID', 'client-id');
    vi.stubEnv('VITE_ENTRA_TENANT_ID', 'tenant-id');
    acquireTokenSilent.mockRejectedValue(new Error('network down'));
    const { acquireToken } = await loadEntra();

    await expect(acquireToken(account)).rejects.toThrow('network down');
    expect(acquireTokenPopup).not.toHaveBeenCalled();
  });
});
