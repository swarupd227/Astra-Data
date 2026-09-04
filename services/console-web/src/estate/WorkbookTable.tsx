/**
 * The centre pane: the workbook table.
 *
 * §15.3.2 lists tier, score, usage, family, train, state and C4 count. Four of those are
 * Migration Unit properties that nothing produces yet (E3), and the class mix needs the
 * Transpiler (E5). Rather than render seven columns of "—", the table shows what the
 * estate actually knows and the pane header says, once, which columns are waiting on what.
 *
 * Rows are selectable with the keyboard as well as the mouse: §15.6 requires full keyboard
 * operation, and a table whose rows are only clickable is the usual way that is lost.
 */

import type { Workbook } from '../lib/api';
import { Maybe, ParseStatus, UsageCell, count } from './format';

interface Props {
  workbooks: Workbook[];
  selectedId: string | null;
  sort: string;
  onSelect: (workbook: Workbook) => void;
  onSort: (sort: string) => void;
}

const COLUMNS: { key: string; label: string; sortable?: string; className?: string }[] = [
  { key: 'name', label: 'Workbook', sortable: 'name' },
  { key: 'project', label: 'Project' },
  { key: 'parse', label: 'Parse', sortable: 'parse_quality' },
  { key: 'views', label: 'Views 90d', sortable: 'usage', className: 'numeric' },
  { key: 'calcs', label: 'Calcs', sortable: 'calculations', className: 'numeric' },
  { key: 'tier', label: 'Tier' },
  { key: 'owner', label: 'Owner' },
];

export function WorkbookTable({
  workbooks,
  selectedId,
  sort,
  onSelect,
  onSort,
}: Props): JSX.Element {
  if (workbooks.length === 0) {
    return (
      <p className="empty">
        No workbooks match these filters.
        <br />
        Clear a facet, or widen the search.
      </p>
    );
  }

  return (
    <table className="estate">
      <caption className="visually-hidden">
        Workbooks in the selected scope, with parse status, usage and owner
      </caption>
      <thead>
        <tr>
          {COLUMNS.map((column) => (
            <th
              key={column.key}
              className={column.className}
              scope="col"
              aria-sort={
                column.sortable && sort === column.sortable ? 'ascending' : undefined
              }
            >
              {column.sortable ? (
                <button type="button" onClick={() => onSort(column.sortable as string)}>
                  {column.label}
                  {sort === column.sortable ? ' ▾' : ''}
                </button>
              ) : (
                column.label
              )}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {workbooks.map((workbook) => (
          <tr
            key={workbook.id}
            aria-selected={workbook.id === selectedId}
            className={workbook.withdrawn ? 'withdrawn' : undefined}
            tabIndex={0}
            onClick={() => onSelect(workbook)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onSelect(workbook);
              }
            }}
          >
            <td title={workbook.name}>
              {workbook.withdrawn && (
                <span className="pill idle" style={{ marginRight: 6 }}>
                  withdrawn
                </span>
              )}
              {workbook.name}
            </td>
            <td>
              <Maybe value={workbook.project} />
            </td>
            <td>
              <ParseStatus workbook={workbook} />
            </td>
            <td className="numeric">
              <UsageCell workbook={workbook} />
            </td>
            <td className="numeric">{count(workbook.calculated_fields)}</td>
            <td>
              {workbook.tier ? (
                <span className="pill idle">{workbook.tier.toLowerCase()}</span>
              ) : (
                <span className="faint" title="No tier declared. Assessment is E3's.">
                  —
                </span>
              )}
            </td>
            <td>
              <Maybe value={workbook.owner} title="Owner, resolved from the source" />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
