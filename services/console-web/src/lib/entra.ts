/**
 * Entra ID sign-in — story S11.1.1, spec §18.1: "Users sign in with Entra ID; roles are
 * mapped from Entra groups". `App.tsx`'s own docstring already named this as a stated
 * stub: the console currently sends a role a person *picks* (`X-Astra-Principal`/
 * `X-Astra-Roles`), and says plainly on screen that this is not sign-in. This module is
 * the real replacement for that picker — real MSAL sign-in, a real access token acquired
 * for graph-svc's own API — but the console never maps groups to roles itself: the token
 * is opaque here and graph-svc's own `entra.py` does the verification and the group-to-
 * role mapping, the same "identity gets checked once, at the edge" shape the server side
 * already has.
 *
 * **Disclosed, not yet connected — exactly like `entra.py` on the server.** `@azure/msal-
 * browser` is a real dependency and this module's own logic (config gating, token-cache
 * lookups before a popup, sign-out) is covered by tests against a mocked MSAL client. It
 * has never run against a live Entra tenant, because `VITE_ENTRA_CLIENT_ID`/
 * `VITE_ENTRA_TENANT_ID` are unset in every environment this project has today — the
 * honest default, under which `isEntraConfigured()` is `false` and nothing in this module
 * is reachable from the UI. `App.tsx` renders "Sign in with Microsoft" only when it is
 * `true`; the existing "Acting as" selector remains the only identity affordance until
 * then, byte-for-byte unchanged.
 */

import {
  type AccountInfo,
  type Configuration,
  InteractionRequiredAuthError,
  PublicClientApplication,
} from '@azure/msal-browser';

export interface EntraSession {
  account: AccountInfo;
  /** What `Identity.bearerToken` (lib/api.ts) is built from. */
  accessToken: string;
}

function clientId(): string | undefined {
  return import.meta.env.VITE_ENTRA_CLIENT_ID || undefined;
}

function tenantId(): string | undefined {
  return import.meta.env.VITE_ENTRA_TENANT_ID || undefined;
}

/** The one gate every function below is built around: both must be set, or this whole
 * module stays inert. */
export function isEntraConfigured(): boolean {
  return Boolean(clientId() && tenantId());
}

function apiScope(): string {
  const configured = import.meta.env.VITE_ENTRA_API_SCOPE;
  if (configured) return configured;
  return `api://${clientId()}/access_as_user`;
}

let msalInstance: PublicClientApplication | undefined;
let initialised: Promise<void> | undefined;

/** Constructed lazily — never at module load — so an unconfigured deployment (today's
 * only exercised state) never touches MSAL at all, not even to build a config object. */
function client(): PublicClientApplication {
  if (!isEntraConfigured()) {
    throw new Error('Entra ID is not configured (VITE_ENTRA_CLIENT_ID/VITE_ENTRA_TENANT_ID)');
  }
  if (!msalInstance) {
    const configuration: Configuration = {
      auth: {
        clientId: clientId()!,
        authority: `https://login.microsoftonline.com/${tenantId()}`,
        redirectUri: window.location.origin,
      },
      cache: {
        // Session, not local: the identical "a per-viewer, per-tab convenience, not
        // durable state" posture this console already takes for browser storage
        // elsewhere (locale, deep-link state) — signing out of one tab should not
        // silently sign out every other tab a person has this console open in, but a
        // closed browser should not leave a live session behind either.
        cacheLocation: 'sessionStorage',
      },
    };
    msalInstance = new PublicClientApplication(configuration);
  }
  return msalInstance;
}

async function ensureInitialised(instance: PublicClientApplication): Promise<void> {
  if (!initialised) {
    initialised = instance.initialize();
  }
  await initialised;
}

/** The signed-in account, if MSAL's own cache already has one — checked on mount so a
 * page reload does not force a fresh interactive sign-in every time. */
export async function currentAccount(): Promise<AccountInfo | null> {
  if (!isEntraConfigured()) return null;
  const instance = client();
  await ensureInitialised(instance);
  const accounts = instance.getAllAccounts();
  return accounts[0] ?? null;
}

/** Interactive sign-in. Called only from a real user gesture (a button click) — MSAL's
 * own popup flow requires one, the same restriction a browser places on any popup. */
export async function signIn(): Promise<EntraSession> {
  const instance = client();
  await ensureInitialised(instance);
  const result = await instance.loginPopup({ scopes: [apiScope()] });
  return { account: result.account, accessToken: result.accessToken };
}

export async function signOut(): Promise<void> {
  if (!isEntraConfigured()) return;
  const instance = client();
  await ensureInitialised(instance);
  const account = instance.getAllAccounts()[0];
  if (account) {
    await instance.logoutPopup({ account });
  }
}

/** A fresh access token for the signed-in account — silently, from MSAL's own cache or a
 * refresh, falling back to an interactive popup only when silent acquisition genuinely
 * cannot proceed (consent changed, the refresh token expired). Called before every API
 * request that should carry one, the same "check, don't assume" posture a bearer token
 * needs since it is time-limited in a way the session-long "Acting as" role never was. */
export async function acquireToken(account: AccountInfo): Promise<string> {
  const instance = client();
  await ensureInitialised(instance);
  try {
    const result = await instance.acquireTokenSilent({ scopes: [apiScope()], account });
    return result.accessToken;
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      const result = await instance.acquireTokenPopup({ scopes: [apiScope()] });
      return result.accessToken;
    }
    throw error;
  }
}
