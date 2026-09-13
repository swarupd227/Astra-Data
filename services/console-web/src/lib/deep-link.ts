/**
 * A tiny, shared query-string convenience so a screen's own current selection can be
 * linked to and reproduced from that link -- story S10.1.1's own "deep links to an MU,
 * a case, a gate card and a run are stable" AC.
 *
 * Deliberately its own module, not folded into `api.ts`: this is a browser/URL concern,
 * not an API concern, and every screen that wants a stable deep link reads and writes
 * through the same two functions rather than each inventing its own `URLSearchParams`
 * handling. Writes use `replaceState`, not `pushState` -- selecting a different item on
 * the same screen updates what a copied link points at without growing the browser's
 * own back/forward history one entry per click, the same "the URL reflects state, it
 * does not narrate every step" posture `App.tsx`'s own surface-switching already takes.
 */

export function getDeepLinkParam(name: string): string | null {
  if (typeof window === 'undefined') return null;
  return new URLSearchParams(window.location.search).get(name);
}

export function setDeepLinkParam(name: string, value: string | null): void {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  if (value) {
    url.searchParams.set(name, value);
  } else {
    url.searchParams.delete(name);
  }
  window.history.replaceState(null, '', `${url.pathname}${url.search}`);
}
