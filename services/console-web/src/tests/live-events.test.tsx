/**
 * `useLiveTick` -- story S10.1.2's own live-updates signal. See `lib/live-events.ts`'s
 * own docstring for why it is a plain counter, not the event payload itself, and why one
 * `EventSource` is opened at the shell rather than per screen.
 */

import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { useLiveTick } from '../lib/live-events';
import { FakeEventSource } from './fake-event-source';

function Probe(): JSX.Element {
  const tick = useLiveTick();
  return <span data-testid="tick">{tick}</span>;
}

describe('useLiveTick', () => {
  const realEventSource = globalThis.EventSource;

  afterEach(() => {
    FakeEventSource.reset();
    globalThis.EventSource = realEventSource as typeof EventSource;
  });

  it('does nothing when the browser has no EventSource (the real jsdom default)', () => {
    // @ts-expect-error -- deliberately absent, the same environment every other screen
    // test already renders in without a fake installed.
    globalThis.EventSource = undefined;
    render(<Probe />);

    expect(screen.getByTestId('tick').textContent).toBe('0');
  });

  it('opens exactly one connection, to the stream route', () => {
    // @ts-expect-error -- test double stands in for the real browser API
    globalThis.EventSource = FakeEventSource;
    render(<Probe />);

    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.latest().url).toBe('/v1/events:stream');
  });

  it('increments the tick when a real event type arrives', () => {
    // @ts-expect-error -- test double stands in for the real browser API
    globalThis.EventSource = FakeEventSource;
    render(<Probe />);

    act(() => {
      FakeEventSource.latest().emit('estate.node.upserted', { subject: 'wb_1' });
    });

    expect(screen.getByTestId('tick').textContent).toBe('1');
  });

  it('increments again for a second, different event type', () => {
    // @ts-expect-error -- test double stands in for the real browser API
    globalThis.EventSource = FakeEventSource;
    render(<Probe />);

    act(() => {
      FakeEventSource.latest().emit('estate.node.upserted', {});
      FakeEventSource.latest().emit('estate.mu.accepted', {});
    });

    expect(screen.getByTestId('tick').textContent).toBe('2');
  });

  it('closes the connection on unmount', () => {
    // @ts-expect-error -- test double stands in for the real browser API
    globalThis.EventSource = FakeEventSource;
    const { unmount } = render(<Probe />);
    const source = FakeEventSource.latest();

    unmount();

    expect(source.closed).toBe(true);
  });
});
