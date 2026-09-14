/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ASTRA_ENV?: string;
  /** Story S11.1.1: this console's own Entra ID app registration. Both set is what turns
   * on "Sign in with Microsoft" (`lib/entra.ts`, `App.tsx`); either unset -- today's only
   * exercised value, since no tenant has given this project an app registration yet --
   * keeps the existing "Acting as" selector as the only identity affordance. */
  readonly VITE_ENTRA_CLIENT_ID?: string;
  readonly VITE_ENTRA_TENANT_ID?: string;
  /** The scope graph-svc's own app registration exposes for a signed-in user's access
   * token (`api://<VITE_ENTRA_CLIENT_ID>/access_as_user` is the conventional default a
   * real tenant's admin sets up when exposing an API) -- overridable because the exact
   * scope name is that admin's own choice, not this console's. */
  readonly VITE_ENTRA_API_SCOPE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
