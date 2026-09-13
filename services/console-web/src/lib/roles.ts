/**
 * The client/Artizent split -- spec §2.4's own Organisation column, mirrored here since
 * the console has no server round trip just to ask "is this role Artizent's own" (story
 * S10.1.1, opening F10.1/E10). The six values are `graph-svc`'s own `roles.
 * ARTIZENT_ROLES`, transcribed by hand rather than fetched, the identical "the console
 * has no session to ask the server with before it has rendered anything" reasoning the
 * role/principal headers themselves already rest on.
 *
 * Extracted from `RegressionMonitor.tsx`'s own identical inline list (S7.7.1's own
 * "every Artizent role" export gate) rather than duplicated a second time -- that
 * screen now imports this constant too.
 */
export const ARTIZENT_ROLE_VALUES = [
  'programme_manager',
  'migration_architect',
  'semantic_model_engineer',
  'migration_engineer',
  'parity_engineer',
  'platform_engineer',
] as const;

export function isArtizentRole(roles: readonly string[]): boolean {
  return roles.some((role) => (ARTIZENT_ROLE_VALUES as readonly string[]).includes(role));
}
