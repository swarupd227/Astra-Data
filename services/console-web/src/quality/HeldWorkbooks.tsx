/**
 * The left pane: workbooks under the threshold.
 *
 * §15.3.2 asks the queue to list them, and "which of my workbooks is stuck" is a fair
 * question — but the work is construct-first, so this pane is a list rather than the
 * table. Each row says how far below the threshold it is and how many gaps are left,
 * because one workbook with a single remaining construct is nearly free to release and one
 * with nine is not.
 */

import type { HeldWorkbook, QueueResponse } from '../lib/api';

interface Props {
  queue: QueueResponse | null;
  loading: boolean;
  reharvesting: boolean;
  onReharvest: (workbook: HeldWorkbook) => void;
}

export function HeldWorkbooks({
  queue,
  loading,
  reharvesting,
  onReharvest,
}: Props): JSX.Element {
  if (loading && !queue) return <p className="empty">Reading…</p>;
  if (!queue || queue.held.length === 0) {
    return (
      <p className="empty">
        No workbook is held.
        <br />
        Everything parsed above the threshold.
      </p>
    );
  }

  return (
    <ul className="held-list">
      {queue.held.map((workbook) => (
        <li key={`${workbook.site}/${workbook.workbook_luid}`}>
          <div className="held-head">
            <span className="held-name" title={workbook.workbook_name}>
              {workbook.workbook_name}
            </span>
            <span className={`pill ${tone(workbook)}`}>{percent(workbook.parse_quality)}</span>
          </div>
          <div className="held-meta">
            {workbook.site} · {workbook.project}
          </div>
          <div className="held-meta">
            {workbook.unrecognised_constructs} unrecognised of {workbook.total}
            {workbook.ignorable > 0 && ` · ${workbook.ignorable} accepted`}
          </div>
          <button
            type="button"
            className="btn small"
            disabled={reharvesting}
            onClick={() => onReharvest(workbook)}
            title={`Re-harvest ${workbook.site} so its workbooks are re-parsed under the current grammar`}
          >
            {reharvesting ? 'Harvesting…' : 'Re-harvest site'}
          </button>
        </li>
      ))}
    </ul>
  );
}

function percent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}

function tone(workbook: HeldWorkbook): string {
  if (workbook.parse_quality === null) return 'idle';
  return workbook.parse_quality >= 0.9 ? 'warn' : 'bad';
}
