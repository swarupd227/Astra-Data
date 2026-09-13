/**
 * The "explain" affordance — story S10.1.2's own third AC: "every number on a screen has
 * an 'explain' affordance that opens the query or the events behind it."
 *
 * One small, generic component every screen's own figures attach to — a `metricKey` into
 * `explain.py`'s own registry (`GET /v1/explain/{metric_key}`), and, when the figure is
 * about one real subject (a workbook, a case, a train...), a `subjectId` that reads that
 * subject's own real recent history straight from the existing outbox read
 * (`GET /v1/events?subject=`, via `Api.subjectEvents` -- no new backend route needed for
 * that half). Nothing is fetched until a reader actually opens the panel, so an unopened
 * `<Explain>` costs nothing beyond the one small trigger button.
 *
 * Wiring this onto the next screen's own number is one import and one JSX attribute, not
 * new infrastructure -- see `explain.py`'s own module docstring for which figures this
 * story wired in this first pass.
 */

import { useCallback, useState } from 'react';

import { ApiError, type Api, type ExplainEntry, type Identity, type SubjectEvent } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
  /** A key into `explain.EXPLAIN_REGISTRY` (graph-svc). */
  metricKey: string;
  /** When this figure is about one real subject, its platform id -- the panel then also
   * shows that subject's own recent real events. */
  subjectId?: string;
  /** Screen-reader label for the trigger, since the visible glyph alone says nothing.
   * Defaults to a generic phrasing naming the metric. */
  label?: string;
}

export function Explain({ api, identity, metricKey, subjectId, label }: Props): JSX.Element {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [entry, setEntry] = useState<ExplainEntry | null>(null);
  const [events, setEvents] = useState<SubjectEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [explainEntry, subjectEvents] = await Promise.all([
        api.explain(metricKey, identity),
        subjectId ? api.subjectEvents(subjectId, identity) : Promise.resolve(null),
      ]);
      setEntry(explainEntry);
      setEvents(subjectEvents?.events ?? null);
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : 'This figure could not be explained.');
    } finally {
      setLoading(false);
    }
  }, [api, identity, metricKey, subjectId]);

  const toggle = useCallback(() => {
    setOpen((wasOpen) => {
      const nowOpen = !wasOpen;
      if (nowOpen && !entry && !loading) void load();
      return nowOpen;
    });
  }, [entry, loading, load]);

  return (
    <span className="explain">
      <button
        type="button"
        className="explain-trigger"
        aria-label={label ?? `Explain ${metricKey}`}
        aria-expanded={open}
        onClick={toggle}
      >
        ⓘ
      </button>
      {open && (
        <div className="explain-panel" role="region" aria-label="Explain this figure">
          {loading && <p className="empty">Reading the real query…</p>}
          {error && <div className="banner">{error}</div>}
          {entry && (
            <>
              <h4>{entry.title}</h4>
              <p className="faint mono">{entry.source}</p>
              <pre className="explain-text">{entry.text}</pre>
            </>
          )}
          {events && events.length > 0 && (
            <>
              <h4>Recent events for this subject</h4>
              <ul className="explain-events">
                {events.map((item) => (
                  <li key={item.sequence} className="mono">
                    {item.event.type} · {item.event.time}
                  </li>
                ))}
              </ul>
            </>
          )}
          {events && events.length === 0 && (
            <p className="faint">No events recorded for this subject.</p>
          )}
        </div>
      )}
    </span>
  );
}
