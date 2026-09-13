/**
 * A minimal `EventSource` test double for story S10.1.2's own `useLiveTick`
 * (`lib/live-events.ts`) -- jsdom implements no real `EventSource`, which is also why
 * `useLiveTick` itself guards on `typeof EventSource === 'undefined'` and quietly does
 * nothing when it is absent (every screen test that never installs this fake keeps
 * exercising the real, absent-EventSource code path, unaffected by this story).
 *
 * Tracks every constructed instance so a test can reach the most recent one and fire a
 * named event at it directly, the same "grab what was just constructed" shape this
 * codebase's other fakes already use (e.g. `fixtures.ts`'s own `fakeApi().recorded`).
 */

export class FakeEventSource {
  static instances: FakeEventSource[] = [];

  url: string;
  closed = false;
  onmessage: ((event: MessageEvent) => void) | null = null;
  private listeners = new Map<string, Set<(event: MessageEvent) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }

  removeEventListener(type: string, listener: (event: MessageEvent) => void): void {
    this.listeners.get(type)?.delete(listener);
  }

  close(): void {
    this.closed = true;
  }

  /** Simulate the server pushing one named event down this connection. */
  emit(type: string, data: unknown = {}): void {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
    if (type === 'message') this.onmessage?.(event);
  }

  static reset(): void {
    FakeEventSource.instances = [];
  }

  static latest(): FakeEventSource {
    const instance = FakeEventSource.instances.at(-1);
    if (!instance) throw new Error('no FakeEventSource was constructed');
    return instance;
  }
}
