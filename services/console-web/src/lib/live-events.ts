/**
 * Live updates over server-sent events -- story S10.1.2's own "live updates over
 * server-sent events for queues and boards; p95 screen update within 2 seconds of the
 * event."
 *
 * One `EventSource` for the whole console, opened once at the shell (`App.tsx`), not one
 * per screen -- a browser holds a low, shared limit on concurrent connections to one
 * origin, and every screen that wants "an event just happened, re-read" is content with
 * the identical signal. `useLiveTick` exposes that signal as a plain counter: a screen
 * that already re-fetches on some other counter changing (`nonce`, `queueNonce`, ...)
 * only has to add this one to the same dependency array to pick up live updates too --
 * no new fetch plumbing, the same "increment a number, the existing effect re-runs"
 * shape every screen already has for its own manual refresh.
 *
 * Deliberately a plain counter, not the event payload itself: a screen that wants to know
 * *what* changed already reads it back from its own API call once it re-fetches (the
 * identical fact, freshly read, is more trustworthy than trusting a push payload to
 * describe it completely) -- the stream's own job is only to say "something changed,
 * now", the same distinction `ADR 0010`'s own "board and queue updates" language draws
 * between a live signal and the state it invalidates.
 *
 * `EventSource` cannot carry this console's own `X-Astra-Principal`/`X-Astra-Roles`
 * headers (no browser lets it) -- `GET /v1/events:stream` is deliberately open for
 * exactly this reason (see its own docstring); nothing behind this hook depends on
 * identity.
 */

import { useEffect, useRef, useState } from 'react';

export function useLiveTick(): number {
  const [tick, setTick] = useState(0);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof EventSource === 'undefined') return;

    const source = new EventSource('/v1/events:stream');
    sourceRef.current = source;
    source.onmessage = () => setTick((value) => value + 1);
    // Every real event type is its own SSE `event:` name (see routes_events_stream.py),
    // not the default "message" -- a bare `onmessage` never fires for them, only for a
    // frame with no `event:` line at all, which this stream never sends. A generic
    // listener that does not care *which* type happened is simpler than naming every
    // `EventType` here and would silently miss a new one this platform adds later.
    const onAny = () => setTick((value) => value + 1);
    for (const type of EVENT_TYPES) source.addEventListener(type, onAny);

    return () => {
      for (const type of EVENT_TYPES) source.removeEventListener(type, onAny);
      source.close();
      sourceRef.current = null;
    };
  }, []);

  return tick;
}

/** Mirrors `astra_graph.events.EventType` (`services/graph-svc/src/astra_graph/events.
 * py`) -- listed rather than derived, the same "the console has no session to ask the
 * server with before it has rendered anything" reasoning `lib/roles.ts`'s own
 * `ARTIZENT_ROLE_VALUES` already gives for its own hand-transcribed list. */
const EVENT_TYPES = [
  'estate.node.upserted',
  'estate.edge.upserted',
  'estate.node.retired',
  'estate.edge.retired',
  'estate.source.drift',
  'estate.pattern.retired',
  'estate.mu.accepted',
  'estate.mu.promoted',
  'estate.adoption.captured',
  'estate.site.decommissioned',
] as const;
