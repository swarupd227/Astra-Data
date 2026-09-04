/**
 * How estate values are rendered, in one place.
 *
 * Formatting lives here rather than inline so a parse-quality figure looks the same in the
 * table, the tree and the detail pane. Spec §15.2: "every number on every screen is a
 * query over the Estate Graph" — if the same number renders three ways, a reader has to
 * work out whether they are the same number.
 */

import type { Workbook } from '../lib/api';

/** Grey rather than zero. A workbook nobody has parsed has no score, which is not 0%. */
export function percent(value: number | null): string {
  return value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`;
}

export function count(value: number | null): string {
  return value === null || value === undefined ? '—' : value.toLocaleString('en-GB');
}

export type Tone = 'ok' | 'warn' | 'bad' | 'idle';

export function parseQualityTone(workbook: Workbook): Tone {
  if (workbook.parse_quality === null) return 'idle';
  if (workbook.parse_quality >= 1) return 'ok';
  if (!workbook.held) return 'ok';
  return workbook.parse_quality >= 0.9 ? 'warn' : 'bad';
}

/**
 * The parse-status cell. It carries the number *and* whether the workbook is held,
 * because §4.1.4's threshold is what decides whether work can proceed and a bare
 * percentage makes every reader do that comparison in their head.
 */
export function ParseStatus({ workbook }: { workbook: Workbook }): JSX.Element {
  const tone = parseQualityTone(workbook);
  if (workbook.parse_quality === null) {
    return <span className="pill idle">not parsed</span>;
  }
  return (
    <span className={`pill ${tone}`} title={workbook.held ? 'Below the 98% threshold: held for review (spec §4.1.4)' : 'Clear to advance'}>
      {percent(workbook.parse_quality)}
      {workbook.held ? ' · held' : ''}
    </span>
  );
}

export function UsageCell({ workbook }: { workbook: Workbook }): JSX.Element {
  if (workbook.views_90d === null) return <span className="faint">—</span>;
  return (
    <span title={`${count(workbook.distinct_viewers_90d)} distinct viewers over 90 days`}>
      {count(workbook.views_90d)}
    </span>
  );
}

/** An absent value is a visible dash, never an empty cell that reads as a rendering bug. */
export function Maybe({ value, title }: { value: string | null; title?: string }): JSX.Element {
  if (!value) return <span className="faint" title={title}>—</span>;
  return <span title={title ?? value}>{value}</span>;
}
