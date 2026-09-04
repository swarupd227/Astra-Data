/**
 * The lineage mini-graph in the right pane (§15.3.2).
 *
 * **Layered, not force-directed.** §15.3.2 asks for a force-directed graph on the *Lineage
 * View*, which is a full screen for a family. This is the mini-graph in a 360-pixel pane,
 * and a force simulation there gives a different picture on every render — the same
 * workbook would look different each time you selected it, which makes it useless for
 * recognising anything. So nodes are laid out by their distance from the workbook, and the
 * layout is a pure function of the data: the same workbook always draws the same shape.
 *
 * It is also drawn as plain SVG with no graph library. The whole thing is one anchor, its
 * neighbours and their neighbours; a layout engine would be more dependency than drawing.
 */

import type { LineageNode, WorkbookDetail } from '../lib/api';

interface Props {
  detail: WorkbookDetail;
}

const WIDTH = 336;
const ROW_HEIGHT = 26;
const PADDING = 10;

/** Colour by what a node *is*. The palette is the pill palette, so a Worksheet is the same
 *  colour wherever it appears. */
const TONE: Record<string, string> = {
  Workbook: 'var(--accent)',
  Worksheet: 'var(--ok)',
  Dashboard: 'var(--ok)',
  Datasource: 'var(--warn)',
  Field: 'var(--text-faint)',
  CalculatedField: 'var(--bad)',
  Parameter: 'var(--text-muted)',
  Project: 'var(--text-faint)',
  Site: 'var(--text-faint)',
  User: 'var(--text-muted)',
};

//: More than this in one pane is a hairball. The count is shown so the reader knows the
//: picture is partial, and the Lineage View (a later story) is where the whole thing lives.
const MAX_PER_DEPTH = 6;

export function Lineage({ detail }: Props): JSX.Element {
  const anchorId = detail.workbook.id;
  const byDepth = new Map<number, LineageNode[]>();
  for (const node of detail.lineage.nodes) {
    if (node.id === anchorId) continue;
    const bucket = byDepth.get(node.depth) ?? [];
    bucket.push(node);
    byDepth.set(node.depth, bucket);
  }

  const depths = [...byDepth.keys()].sort((a, b) => a - b);
  const columns = depths.map((depth) => {
    const all = (byDepth.get(depth) ?? []).slice().sort((a, b) => {
      const byType = a.type.localeCompare(b.type);
      return byType !== 0 ? byType : (a.name ?? a.id).localeCompare(b.name ?? b.id);
    });
    return { depth, shown: all.slice(0, MAX_PER_DEPTH), hidden: all.length - MAX_PER_DEPTH };
  });

  const rows = Math.max(1, ...columns.map((column) => column.shown.length));
  const height = PADDING * 2 + rows * ROW_HEIGHT;
  const columnWidth = (WIDTH - 110) / Math.max(1, columns.length);
  const anchorX = 54;
  const anchorY = height / 2;

  const positioned = columns.flatMap((column, index) =>
    column.shown.map((node, row) => ({
      node,
      x: anchorX + 58 + index * columnWidth,
      y: PADDING + ROW_HEIGHT / 2 + row * ROW_HEIGHT + (rows - column.shown.length) * ROW_HEIGHT / 2,
    })),
  );
  const position = new Map(positioned.map((entry) => [entry.node.id, entry]));

  return (
    <div className="lineage">
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={`Lineage of ${String(detail.workbook.properties.name ?? 'workbook')}: ${detail.lineage.nodes.length} related elements`}
      >
        <g stroke="var(--border-strong)" strokeWidth="1" fill="none">
          {detail.lineage.edges.map((edge, index) => {
            const from = edge.from === anchorId ? { x: anchorX, y: anchorY } : position.get(edge.from);
            const to = edge.to === anchorId ? { x: anchorX, y: anchorY } : position.get(edge.to);
            if (!from || !to) return null;
            const midpoint = (from.x + to.x) / 2;
            return (
              <path
                key={`${edge.from}-${edge.to}-${index}`}
                d={`M ${from.x} ${from.y} C ${midpoint} ${from.y}, ${midpoint} ${to.y}, ${to.x} ${to.y}`}
              />
            );
          })}
        </g>

        <g>
          <circle cx={anchorX} cy={anchorY} r="6" fill="var(--accent)" />
          <text x={anchorX} y={anchorY - 10} textAnchor="middle" className="node-label">
            this workbook
          </text>
        </g>

        {positioned.map(({ node, x, y }) => (
          <g key={node.id}>
            <circle cx={x} cy={y} r="4" fill={TONE[node.type] ?? 'var(--text-faint)'} />
            <text x={x + 7} y={y + 3} className="node-label">
              {truncate(node.name ?? node.type, 18)}
            </text>
            <title>{`${node.type}: ${node.name ?? node.id}`}</title>
          </g>
        ))}
      </svg>

      <p className="faint" style={{ margin: 0, padding: '6px 8px', fontSize: 11.5 }}>
        {detail.lineage.nodes.length} elements within {detail.lineage.depth} hops
        {columns.some((column) => column.hidden > 0) &&
          ` · ${columns.reduce((sum, column) => sum + Math.max(0, column.hidden), 0)} not drawn`}
        {detail.lineage.truncated && ' · truncated by the element limit'}
      </p>
    </div>
  );
}

function truncate(value: string, length: number): string {
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}
