/**
 * The graph itself: SVG, laid out by the deterministic simulation in `layout.ts`.
 *
 * Two kinds of edge are drawn, and telling them apart is most of what makes the picture
 * readable:
 *
 * - **Structural** (workbook → table, workbook → field): thin, pale, no weight. These say
 *   what a workbook uses.
 * - **Shared lineage** (workbook ↔ workbook): width proportional to §12.1's strength.
 *   These are the evidence a family was grouped on, and they are what S1.4.2 exists for,
 *   so they are drawn over the structural ones and never hidden behind them.
 *
 * The SVG is the export surface too — the PNG is this element serialised — so nothing is
 * drawn with HTML overlays or CSS backgrounds that would not survive the trip.
 */

import { forwardRef, useMemo } from 'react';

import type { LineageResponse } from '../lib/api';
import type { LineageFilters } from './LineageView';
import { layout } from './layout';

const WIDTH = 960;
const HEIGHT = 640;

/** One colour per node type, from the same palette the rest of the console uses. */
const TYPE_COLOUR: Record<string, string> = {
  Workbook: 'var(--accent)',
  Datasource: 'var(--warn)',
  Table: 'var(--ok)',
  Field: 'var(--text-faint)',
  CalculatedField: 'var(--bad)',
};

/** A stable palette for families, so the same family is the same colour across reloads. */
const FAMILY_COLOURS = [
  'var(--accent)',
  'var(--ok)',
  'var(--warn)',
  'var(--bad)',
  'var(--text-muted)',
];

const RADIUS: Record<string, number> = {
  Workbook: 7,
  Datasource: 5,
  Table: 5.5,
  Field: 3.5,
  CalculatedField: 4,
};

interface Props {
  data: LineageResponse;
  filters: LineageFilters;
  highlighted: Set<string> | null;
  selected: string | null;
  onSelect: (id: string | null) => void;
}

export const LineageCanvas = forwardRef<SVGSVGElement, Props>(function LineageCanvas(
  { data, filters, highlighted, selected, onSelect },
  ref,
) {
  const visible = useMemo(
    () => data.nodes.filter((node) => !filters.hidden.has(node.type)),
    [data.nodes, filters.hidden],
  );
  const visibleIds = useMemo(() => new Set(visible.map((node) => node.id)), [visible]);

  const structural = useMemo(
    () =>
      data.edges.filter(
        (edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target),
      ),
    [data.edges, visibleIds],
  );

  // Every link between visible nodes, whatever its strength. The layout is computed from
  // *all* the evidence; the threshold only decides what is drawn.
  const linked = useMemo(
    () =>
      data.shared_lineage.filter(
        (link) => visibleIds.has(link.source) && visibleIds.has(link.target),
      ),
    [data.shared_lineage, visibleIds],
  );

  const shared = useMemo(
    () => linked.filter((link) => link.strength >= filters.minStrength),
    [linked, filters.minStrength],
  );

  // Deliberately *not* a function of the threshold. Re-running the simulation when the
  // slider moves costs seconds on a dense graph and, worse, makes the picture jump — so
  // the one thing a model engineer is doing (sweeping the threshold to see which links
  // survive) would rearrange the very layout they are reading.
  const positions = useMemo(
    () =>
      layout(
        visible.map((node) => node.id),
        [
          // A shared-lineage link pulls hardest: it is the relationship the screen is
          // about, and the picture should put strongly related workbooks near each other.
          ...linked.map((link) => ({ ...link, strength: link.strength * 3 })),
          ...structural.map((edge) => ({
            source: edge.source,
            target: edge.target,
            strength: 0.5,
          })),
        ],
        { width: WIDTH, height: HEIGHT },
      ),
    [visible, linked, structural],
  );

  const familyOf = useMemo(() => {
    const map = new Map<string, number>();
    data.families.forEach((family, index) => {
      for (const member of family.members) map.set(member, index);
    });
    return map;
  }, [data.families]);

  const colourOf = (node: LineageResponse['nodes'][number]): string => {
    switch (filters.colourBy) {
      case 'parse_status':
        if (node.type !== 'Workbook') return 'var(--text-faint)';
        if (node.parse_quality === null || node.parse_quality === undefined)
          return 'var(--idle)';
        return node.parse_quality >= 0.98 ? 'var(--ok)' : 'var(--bad)';
      case 'family': {
        const index = familyOf.get(node.id);
        return index === undefined
          ? 'var(--text-faint)'
          : FAMILY_COLOURS[index % FAMILY_COLOURS.length]!;
      }
      default:
        return TYPE_COLOUR[node.type] ?? 'var(--text-faint)';
    }
  };

  const dimmed = (id: string): boolean => {
    if (!highlighted) return false;
    return !highlighted.has(id);
  };

  return (
    <svg
      ref={ref}
      className="lineage-canvas"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label={`Lineage graph: ${visible.length} elements, ${shared.length} shared-lineage links`}
      onClick={(event) => event.target === event.currentTarget && onSelect(null)}
    >
      <g className="structural">
        {structural.map((edge, index) => {
          const from = positions.get(edge.source);
          const to = positions.get(edge.target);
          if (!from || !to) return null;
          return (
            <line
              key={`s-${edge.source}-${edge.target}-${index}`}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              stroke="var(--border-strong)"
              strokeWidth={0.5}
              opacity={dimmed(edge.source) && dimmed(edge.target) ? 0.15 : 0.4}
            />
          );
        })}
      </g>

      <g className="shared">
        {shared.map((link) => {
          const from = positions.get(link.source);
          const to = positions.get(link.target);
          if (!from || !to) return null;
          const faded = dimmed(link.source) || dimmed(link.target);
          return (
            <line
              key={`l-${link.source}-${link.target}`}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              stroke={link.origin === 'graph' ? 'var(--accent)' : 'var(--text-muted)'}
              // Width is the strength. 0.15 is barely a line, 1.0 is a band — the
              // difference has to be visible at a glance or the weight says nothing.
              strokeWidth={0.8 + link.strength * 5}
              opacity={faded ? 0.12 : 0.55}
              strokeLinecap="round"
            >
              <title>
                {`${link.strength.toFixed(2)} shared lineage — tables ${link.jaccard_tables.toFixed(2)}, fields ${link.jaccard_fields.toFixed(2)}, ${link.shared_calc_shapes} calculation shapes (${link.origin})`}
              </title>
            </line>
          );
        })}
      </g>

      <g className="nodes">
        {visible.map((node) => {
          const point = positions.get(node.id);
          if (!point) return null;
          const isSelected = selected === node.id;
          const faded = dimmed(node.id);
          return (
            <g key={node.id} opacity={faded ? 0.18 : 1}>
              <circle
                cx={point.x}
                cy={point.y}
                r={(RADIUS[node.type] ?? 4) * (isSelected ? 1.6 : 1)}
                fill={colourOf(node)}
                stroke={isSelected ? 'var(--text)' : 'none'}
                strokeWidth={isSelected ? 1.5 : 0}
                onClick={(event) => {
                  event.stopPropagation();
                  onSelect(isSelected ? null : node.id);
                }}
                style={{ cursor: 'pointer' }}
              >
                <title>{`${node.type}: ${node.name}`}</title>
              </circle>
              {(node.type === 'Workbook' || isSelected) && (
                <text
                  x={point.x + (RADIUS[node.type] ?? 4) + 3}
                  y={point.y + 3}
                  fill="var(--text)"
                  fontSize={8.5}
                  fontFamily="var(--font-sans)"
                >
                  {truncate(node.name, 20)}
                </text>
              )}
            </g>
          );
        })}
      </g>
    </svg>
  );
});

function truncate(value: string, length: number): string {
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}
