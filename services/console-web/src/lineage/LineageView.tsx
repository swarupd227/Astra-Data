/**
 * The Lineage View (spec §15.3.2, story S1.4.2).
 *
 * A force-directed graph of workbooks, the tables and fields behind them, and how much any
 * two workbooks share — so a model engineer can see why the Cartographer grouped a family
 * and challenge it.
 *
 * The controls change what is *drawn*, not what is read. Hiding a node type, moving the
 * strength threshold and re-colouring are all local, so they respond immediately; only the
 * scope (site, project, family) costs a request. That distinction is the difference between
 * a graph somebody explores and one they wait on.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { Api, Identity, LineageResponse } from '../lib/api';
import { ApiError } from '../lib/api';
import { LineageCanvas } from './LineageCanvas';
import { download, exportName, lineageJson, svgToPng } from './export';

export interface LineageFilters {
  site: string | null;
  project: string | null;
  family: string | null;
  minStrength: number;
  hidden: Set<string>;
  colourBy: string;
}

const INITIAL: LineageFilters = {
  site: null,
  project: null,
  family: null,
  minStrength: 0.15,
  // Datasources are a hop, not a thing a model engineer groups on. Hidden by default so
  // the first view is workbooks, tables and fields — which is what §15.3.2 asks for.
  hidden: new Set(['Datasource']),
  colourBy: 'type',
};

interface Props {
  api: Api;
  identity: Identity;
}

export function LineageView({ api, identity }: Props): JSX.Element {
  const [filters, setFilters] = useState<LineageFilters>(INITIAL);
  const [data, setData] = useState<LineageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const canvas = useRef<SVGSVGElement>(null);

  const scope = useMemo(
    () => ({
      site: filters.site,
      project: filters.project,
      family: filters.family,
      min_strength: 0,
    }),
    [filters.site, filters.project, filters.family],
  );

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .lineage(scope, identity)
      .then((response) => {
        if (!live) return;
        setData(response);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(
          caught instanceof ApiError ? caught.message : 'The lineage could not be read.',
        );
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, scope]);

  const patch = useCallback((next: Partial<LineageFilters>) => {
    setFilters((current) => ({ ...current, ...next }));
  }, []);

  const toggleType = useCallback((type: string) => {
    setFilters((current) => {
      const hidden = new Set(current.hidden);
      if (hidden.has(type)) hidden.delete(type);
      else hidden.add(type);
      return { ...current, hidden };
    });
  }, []);

  const exportJson = useCallback(() => {
    if (!data) return;
    const body = lineageJson(data, {
      hidden_types: [...filters.hidden],
      min_strength: filters.minStrength,
      colour_by: filters.colourBy,
    });
    download(`${exportName('lineage', data.scope)}.json`, new Blob([body], {
      type: 'application/json',
    }));
    setNotice('Exported the graph as JSON, with every link’s components.');
  }, [data, filters]);

  const exportPng = useCallback(async () => {
    if (!canvas.current || !data) return;
    try {
      const background = getComputedStyle(document.body).backgroundColor || '#ffffff';
      const blob = await svgToPng(canvas.current, { background });
      download(`${exportName('lineage', data.scope)}.png`, blob);
      setNotice('Exported the picture as PNG.');
    } catch (caught: unknown) {
      setNotice(caught instanceof Error ? caught.message : 'The picture could not be exported.');
    }
  }, [data]);

  const familyMembers = useMemo(() => {
    if (!data || !selectedFamily(data, filters.family)) return null;
    return new Set(selectedFamily(data, filters.family)!.members);
  }, [data, filters.family]);

  const nodeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of data?.nodes ?? []) {
      counts.set(node.type, (counts.get(node.type) ?? 0) + 1);
    }
    return counts;
  }, [data]);

  const sites = useMemo(() => {
    const found = new Set<string>();
    for (const node of data?.nodes ?? []) {
      if (node.type === 'Workbook' && node.site) found.add(node.site);
    }
    return [...found].sort();
  }, [data]);

  return (
    <>
      {error && <div className="banner">{error}</div>}

      <div className="lineage-workspace">
        <section className="pane" aria-label="Lineage controls">
          <header className="pane-header">
            <h2>Scope</h2>
          </header>
          <div className="pane-body filters">
            <div className="filter-group">
              <h3>Site</h3>
              <button
                type="button"
                className="filter-option"
                aria-pressed={filters.site === null}
                onClick={() => patch({ site: null, family: null })}
              >
                <span>All sites</span>
              </button>
              {sites.map((site) => (
                <button
                  type="button"
                  key={site}
                  className="filter-option"
                  aria-pressed={filters.site === site}
                  onClick={() => patch({ site, family: null })}
                >
                  <span>{site}</span>
                </button>
              ))}
            </div>

            <div className="filter-group">
              <h3>Model family</h3>
              {data && data.families.length === 0 ? (
                <p className="pending-note">
                  <strong>No families yet</strong> — the Cartographer proposes them by
                  clustering the estate (E3/F3.2). The shared-lineage strengths below are the
                  evidence it will cluster on.
                </p>
              ) : (
                <>
                  <button
                    type="button"
                    className="filter-option"
                    aria-pressed={filters.family === null}
                    onClick={() => patch({ family: null })}
                  >
                    <span>All workbooks</span>
                  </button>
                  {(data?.families ?? []).map((family) => (
                    <button
                      type="button"
                      key={family.id}
                      className="filter-option"
                      aria-pressed={filters.family === family.id}
                      onClick={() => patch({ family: family.id })}
                    >
                      <span>{family.name}</span>
                      <span className="count">{family.size}</span>
                    </button>
                  ))}
                </>
              )}
            </div>

            <div className="filter-group">
              <h3>Node types</h3>
              {(data?.node_types ?? []).map((type) => (
                <button
                  type="button"
                  key={type}
                  className="filter-option"
                  aria-pressed={!filters.hidden.has(type)}
                  onClick={() => toggleType(type)}
                >
                  <span>{type}</span>
                  <span className="count">{nodeCounts.get(type) ?? 0}</span>
                </button>
              ))}
            </div>

            <div className="filter-group">
              <h3>Shared lineage</h3>
              <label className="slider" htmlFor="min-strength">
                Minimum strength
                <output>{filters.minStrength.toFixed(2)}</output>
              </label>
              <input
                id="min-strength"
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={filters.minStrength}
                onChange={(event) => patch({ minStrength: Number(event.target.value) })}
              />
              <p className="faint" style={{ fontSize: 11.5, margin: '4px 0 0' }}>
                {data?.shared_lineage_origin === 'graph'
                  ? 'Strengths are the Cartographer’s SHARES_LINEAGE edges.'
                  : 'Nothing has clustered yet, so strengths are computed by the same §12.1 formula the Cartographer uses.'}
              </p>
            </div>

            <div className="filter-group">
              <h3>Colour</h3>
              {(data?.colour_modes ?? []).map((mode) => (
                <button
                  type="button"
                  key={mode.key}
                  className="filter-option"
                  aria-pressed={filters.colourBy === mode.key}
                  disabled={!mode.available}
                  title={mode.reason ?? mode.note}
                  onClick={() => patch({ colourBy: mode.key })}
                >
                  <span>{mode.label}</span>
                </button>
              ))}
              {(data?.colour_modes ?? [])
                .filter((mode) => !mode.available)
                .map((mode) => (
                  <p className="pending-note" key={`${mode.key}-why`}>
                    <strong>{mode.label}</strong> — {mode.reason}
                  </p>
                ))}
            </div>
          </div>
        </section>

        <section className="pane" aria-label="Lineage graph">
          <div className="toolbar">
            <strong>Lineage View</strong>
            <span className="muted">
              {data
                ? `${data.workbook_count} workbooks · ${visibleLinks(data, filters).length} shared-lineage links`
                : '—'}
            </span>
            <span className="spacer" style={{ flex: 1 }} />
            <button type="button" className="btn small" onClick={exportPng} disabled={!data}>
              Export PNG
            </button>
            <button type="button" className="btn small" onClick={exportJson} disabled={!data}>
              Export JSON
            </button>
          </div>

          <div className="pane-body">
            {loading && !data ? (
              <p className="empty">Reading the lineage…</p>
            ) : data && data.nodes.length === 0 ? (
              <p className="empty">
                Nothing in scope.
                <br />
                Harvest a site, or widen the scope on the left.
              </p>
            ) : data ? (
              <LineageCanvas
                ref={canvas}
                data={data}
                filters={filters}
                highlighted={familyMembers}
                selected={selected}
                onSelect={setSelected}
              />
            ) : null}
          </div>

          <footer className="statusbar">
            {data?.auto_scoped_to && (
              <span>
                Showing <strong>{data.auto_scoped_to}</strong> — the estate is larger than
                one graph can carry, so this is its biggest site rather than a slice of
                several. Pick another on the left.
              </span>
            )}
            {data?.truncated && (
              <span className="over-budget">
                Capped at {data.scope.limit} workbooks — narrow the scope to see all of them
              </span>
            )}
            {notice && <span>{notice}</span>}
            <span className="spacer" />
            {data && <span>{Math.round(data.read_ms)} ms in the graph</span>}
          </footer>
        </section>
      </div>
    </>
  );
}

export function selectedFamily(
  data: LineageResponse,
  familyId: string | null,
): LineageResponse['families'][number] | undefined {
  return familyId ? data.families.find((family) => family.id === familyId) : undefined;
}

export function visibleLinks(
  data: LineageResponse,
  filters: LineageFilters,
): LineageResponse['shared_lineage'] {
  if (filters.hidden.has('Workbook')) return [];
  return data.shared_lineage.filter((link) => link.strength >= filters.minStrength);
}
