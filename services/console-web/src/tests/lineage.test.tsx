/**
 * The Lineage View, S1.4.2.
 *
 * The layout is tested as a function — it is the part where "force-directed" could quietly
 * become "differently arranged every time", which would make the screen useless for the
 * thing it is for. The screen itself is tested through what a model engineer can see and
 * do: filter node types, move the strength threshold, highlight a family, export.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../App';
import { lineageJson } from '../lineage/export';
import { LineageView, visibleLinks } from '../lineage/LineageView';
import { layout, seed } from '../lineage/layout';
import type { Identity } from '../lib/api';
import { ApiError } from '../lib/api';
import { FAMILY, fakeApi, lineageResponse } from './fixtures';

const ENGINEER: Identity = {
  principal: 'user:model@artizent.example',
  roles: ['semantic_model_engineer'],
};

function renderView(api = fakeApi()) {
  return { api, ...render(<LineageView api={api} identity={ENGINEER} />) };
}

// ------------------------------------------------------------------- the layout

describe('the force layout', () => {
  const ids = ['wb1', 'wb2', 'wb3', 't1', 't2'];
  const links = [{ source: 'wb1', target: 'wb2', strength: 0.9 }];

  it('is deterministic: the same graph lays out the same way every time', () => {
    const first = layout(ids, links);
    const second = layout(ids, links);

    for (const id of ids) {
      expect(first.get(id)!.x).toBeCloseTo(second.get(id)!.x, 10);
      expect(first.get(id)!.y).toBeCloseTo(second.get(id)!.y, 10);
    }
  });

  it('does not depend on the order the nodes arrive in', () => {
    const forwards = layout(ids, links);
    const backwards = layout([...ids].reverse(), links);

    // Positions differ (the ring seeding is index-based), but the *shape* must not: two
    // workbooks joined by a strong link stay close together whichever order they arrived.
    const near = (map: Map<string, { x: number; y: number }>) =>
      Math.hypot(map.get('wb1')!.x - map.get('wb2')!.x, map.get('wb1')!.y - map.get('wb2')!.y);
    const far = (map: Map<string, { x: number; y: number }>) =>
      Math.hypot(map.get('wb1')!.x - map.get('t2')!.x, map.get('wb1')!.y - map.get('t2')!.y);

    expect(near(forwards)).toBeLessThan(far(forwards));
    expect(near(backwards)).toBeLessThan(far(backwards));
  });

  it('pulls strongly linked nodes closer than weakly linked ones', () => {
    const positions = layout(
      ['a', 'b', 'c'],
      [
        { source: 'a', target: 'b', strength: 1 },
        { source: 'a', target: 'c', strength: 0.05 },
      ],
    );

    const ab = Math.hypot(
      positions.get('a')!.x - positions.get('b')!.x,
      positions.get('a')!.y - positions.get('b')!.y,
    );
    const ac = Math.hypot(
      positions.get('a')!.x - positions.get('c')!.x,
      positions.get('a')!.y - positions.get('c')!.y,
    );
    expect(ab).toBeLessThan(ac);
  });

  it('fits the picture to the canvas whatever the graph', () => {
    for (const graph of [ids, ['only']]) {
      const positions = layout(graph, []);
      for (const point of positions.values()) {
        expect(point.x).toBeGreaterThanOrEqual(0);
        expect(point.x).toBeLessThanOrEqual(960);
        expect(point.y).toBeGreaterThanOrEqual(0);
        expect(point.y).toBeLessThanOrEqual(640);
        expect(Number.isFinite(point.x)).toBe(true);
      }
    }
  });

  it('separates two nodes that start on top of each other', () => {
    // No links, so nothing pulls them apart except repulsion — and repulsion needs a
    // direction, which is where a naive implementation divides by zero.
    const positions = layout(['a', 'b'], []);
    const distance = Math.hypot(
      positions.get('a')!.x - positions.get('b')!.x,
      positions.get('a')!.y - positions.get('b')!.y,
    );
    expect(distance).toBeGreaterThan(1);
  });

  it('seeds from the id, so adjacent ids do not start in a line', () => {
    const seeds = ['wb-0001', 'wb-0002', 'wb-0003'].map(seed);
    expect(new Set(seeds).size).toBe(3);
    for (const value of seeds) {
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThanOrEqual(1);
    }
  });

  it('spreads a densely linked graph instead of collapsing it', () => {
    // Every pair linked — which is a real estate, not a contrived one: workbooks that all
    // define the same calculation shape link to each other. With a fixed repulsion
    // constant this drew an unreadable knot in the middle of an empty canvas.
    const many = Array.from({ length: 40 }, (_, i) => `wb${i}`);
    const complete = many.flatMap((a, i) =>
      many.slice(i + 1).map((b) => ({ source: a, target: b, strength: 0.2 })),
    );

    const positions = [...layout(many, complete).values()];
    const xs = positions.map((p) => p.x);
    const ys = positions.map((p) => p.y);

    // The middle 80% must occupy a real part of the canvas, not a dot.
    const spread = (values: number[]) => {
      const sorted = [...values].sort((a, b) => a - b);
      return sorted[Math.floor(sorted.length * 0.9)]! - sorted[Math.floor(sorted.length * 0.1)]!;
    };
    expect(spread(xs)).toBeGreaterThan(200);
    expect(spread(ys)).toBeGreaterThan(150);
  });

  it('scales with the node count rather than a tuned constant', () => {
    // The claim is that spacing follows the node count and the canvas area, so twenty
    // nodes and two hundred both use the space — rather than one filling it and the other
    // collapsing because a repulsion constant was tuned against a single estate.
    const spans = [20, 200].map((count) => {
      const ids = Array.from({ length: count }, (_, i) => `n${i}`);
      const positions = [...layout(ids, []).values()];
      const width =
        Math.max(...positions.map((p) => p.x)) - Math.min(...positions.map((p) => p.x));
      const height =
        Math.max(...positions.map((p) => p.y)) - Math.min(...positions.map((p) => p.y));
      // Fitting preserves the aspect ratio, so at least one dimension fills the canvas.
      return Math.max(width / (960 - 64), height / (640 - 64));
    });

    for (const filled of spans) expect(filled).toBeGreaterThan(0.95);
  });

  it('handles an empty graph without throwing', () => {
    expect(layout([], []).size).toBe(0);
  });
});

// -------------------------------------------------------------------- the screen

describe('the graph', () => {
  it('draws the workbooks, tables and fields in scope', async () => {
    renderView();

    const canvas = await screen.findByRole('img', { name: /Lineage graph/ });
    expect(canvas).toBeInTheDocument();
    // Datasources are hidden by default, so six of the seven nodes are drawn.
    expect(canvas.getAttribute('aria-label')).toContain('6 elements');
  });

  it('draws a shared-lineage link wider the stronger it is', async () => {
    renderView();
    await screen.findByRole('img', { name: /Lineage graph/ });

    const lines = [...document.querySelectorAll('.shared line')];
    expect(lines).toHaveLength(1); // only the 0.71 link clears the 0.15 default
    const width = Number(lines[0]!.getAttribute('stroke-width'));
    expect(width).toBeGreaterThan(3);
  });

  it('says what a link is made of', async () => {
    renderView();
    await screen.findByRole('img', { name: /Lineage graph/ });

    const title = document.querySelector('.shared line title')!.textContent!;
    expect(title).toContain('0.71 shared lineage');
    expect(title).toContain('tables 0.90');
    expect(title).toContain('2 calculation shapes');
    expect(title).toContain('computed');
  });

  it('says whether the strengths are the Cartographer’s or its own', async () => {
    renderView();

    expect(await screen.findByText(/computed by the same §12.1 formula/)).toBeInTheDocument();
  });

  it('credits the Cartographer when the edges are in the graph', async () => {
    renderView(fakeApi(undefined, lineageResponse({ shared_lineage_origin: 'graph' })));

    expect(await screen.findByText(/Cartographer’s SHARES_LINEAGE edges/)).toBeInTheDocument();
  });
});

describe('the node type filter', () => {
  it('hides and shows a type without another request', async () => {
    const user = userEvent.setup();
    const { api } = renderView();
    await screen.findByRole('img', { name: /Lineage graph/ });
    const before = api.calls.lineage.length;

    await user.click(screen.getByRole('button', { name: /^Table\s*2$/ }));

    await waitFor(() =>
      expect(
        screen.getByRole('img', { name: /Lineage graph/ }).getAttribute('aria-label'),
      ).toContain('4 elements'),
    );
    expect(api.calls.lineage).toHaveLength(before);
  });

  it('starts with datasources hidden, because they are a hop not a grouping', async () => {
    renderView();
    await screen.findByRole('img', { name: /Lineage graph/ });

    expect(screen.getByRole('button', { name: /Datasource/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });
});

describe('the strength threshold', () => {
  it('does not move the picture', async () => {
    // Sweeping the threshold is the thing a model engineer does on this screen. Re-running
    // the simulation for it cost seconds and, worse, rearranged the layout they were
    // reading. Found by using the screen.
    const user = userEvent.setup();
    renderView();
    await screen.findByRole('img', { name: /Lineage graph/ });

    const snapshot = () =>
      [...document.querySelectorAll('.nodes circle')]
        .map((c) => `${c.getAttribute('cx')},${c.getAttribute('cy')}`)
        .join(' ');
    const before = snapshot();

    const slider = screen.getByRole('slider');
    await user.clear?.(slider).catch(() => undefined);
    fireEvent.change(slider, { target: { value: '0.5' } });

    await waitFor(() => expect(document.querySelectorAll('.shared line')).toHaveLength(1));
    expect(snapshot()).toBe(before);
  });

  it('drops links below it', () => {
    const data = lineageResponse();
    const base = {
      site: null,
      project: null,
      family: null,
      hidden: new Set<string>(),
      colourBy: 'type',
    };

    expect(visibleLinks(data, { ...base, minStrength: 0 })).toHaveLength(2);
    expect(visibleLinks(data, { ...base, minStrength: 0.5 })).toHaveLength(1);
    expect(visibleLinks(data, { ...base, minStrength: 0.9 })).toHaveLength(0);
  });

  it('is on the screen as a slider', async () => {
    renderView();
    const slider = await screen.findByRole('slider');
    expect(slider).toHaveValue('0.15');
  });
});

describe('families', () => {
  it('says why there are none rather than showing an empty list', async () => {
    renderView();

    const note = (await screen.findByText(/No families yet/)).closest('p');
    expect(note).toHaveTextContent('the Cartographer proposes them by clustering');
    expect(note).toHaveTextContent('E3/F3.2');
  });

  it('highlights a family’s members and dims everything else', async () => {
    const user = userEvent.setup();
    renderView(fakeApi(undefined, lineageResponse({ families: [FAMILY] })));
    await screen.findByRole('img', { name: /Lineage graph/ });

    await user.click(screen.getByRole('button', { name: /mf_risk_positions/ }));

    await waitFor(() => {
      const groups = [...document.querySelectorAll('.nodes > g')];
      const dimmed = groups.filter((g) => Number(g.getAttribute('opacity')) < 0.5);
      // wb1 and wb2 are members; the third workbook and the tables and fields are not.
      expect(dimmed.length).toBeGreaterThan(0);
      expect(groups.length - dimmed.length).toBe(FAMILY.members.length);
    });
  });
});

describe('colour', () => {
  it('offers Migration Unit state by name, disabled, with the reason', async () => {
    renderView();

    const option = await screen.findByRole('button', { name: /Migration Unit state/ });
    expect(option).toBeDisabled();
    expect(screen.getByText(/state machine begins when the Cartographer/)).toBeInTheDocument();
  });

  it('colours by parse status when asked', async () => {
    const user = userEvent.setup();
    renderView();
    await screen.findByRole('img', { name: /Lineage graph/ });

    await user.click(screen.getByRole('button', { name: 'Parse status' }));

    await waitFor(() => {
      const fills = [...document.querySelectorAll('.nodes circle')].map((c) =>
        c.getAttribute('fill'),
      );
      expect(fills).toContain('var(--ok)');
      expect(fills).toContain('var(--bad)'); // the 0.83 workbook
    });
  });
});

describe('export', () => {
  beforeEach(() => {
    // jsdom has neither, and both are the whole mechanism of a browser download.
    URL.createObjectURL = vi.fn(() => 'blob:test');
    URL.revokeObjectURL = vi.fn();
  });

  it('exports the graph as JSON, with the components of every link', async () => {
    const user = userEvent.setup();
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    renderView();
    await screen.findByRole('img', { name: /Lineage graph/ });

    await user.click(screen.getByRole('button', { name: 'Export JSON' }));

    await waitFor(() => expect(clicked).toHaveBeenCalled());
    expect(await screen.findByText(/Exported the graph as JSON/)).toBeInTheDocument();
    clicked.mockRestore();
  });

  it('carries the formula and the origin, so a reader can check the numbers', () => {
    const body = JSON.parse(lineageJson(lineageResponse(), { min_strength: 0.15 }));

    expect(body.similarity.formula).toContain('0.5·J(tables)');
    expect(body.similarity.weights).toEqual({
      tables: 0.5,
      fields: 0.3,
      calc_shapes: 0.2,
      spec_ref: '§12.1',
    });
    expect(body.similarity.origin_meaning).toContain('nothing has clustered yet');
    expect(body.shared_lineage[0].jaccard_tables).toBe(0.9);
  });

  it('names the export after its scope', () => {
    const body = JSON.parse(
      lineageJson(lineageResponse({ scope: { ...lineageResponse().scope, site: 'RQA' } }), {}),
    );
    expect(body.scope.site).toBe('RQA');
  });

  it('offers a PNG export', async () => {
    renderView();
    expect(await screen.findByRole('button', { name: 'Export PNG' })).toBeEnabled();
  });
});

describe('failure and emptiness', () => {
  it('says the lineage could not be read', async () => {
    const api = fakeApi();
    api.lineage = async () => {
      throw new ApiError(503, 'unavailable', 'graph store is not ready');
    };

    render(<LineageView api={api} identity={ENGINEER} />);

    expect(await screen.findByText(/graph store is not ready/)).toBeInTheDocument();
  });

  it('explains an empty scope', async () => {
    renderView(fakeApi(undefined, lineageResponse({ nodes: [], edges: [], shared_lineage: [] })));

    expect(await screen.findByText(/Nothing in scope/)).toBeInTheDocument();
  });

  it('says when it chose a site for you, and which', async () => {
    // An unscoped estate too large for one graph narrows to its biggest site rather than
    // taking an alphabetical slice of several. The screen has to say so, or the reader
    // believes they are looking at the whole estate.
    renderView(fakeApi(undefined, lineageResponse({ auto_scoped_to: 'rqa' })));

    expect(await screen.findByText(/the estate is larger than one graph can carry/)).
      toBeInTheDocument();
    expect(screen.getByText('rqa')).toBeInTheDocument();
  });

  it('warns when the scope was capped', async () => {
    renderView(fakeApi(undefined, lineageResponse({ truncated: true })));

    expect(await screen.findByText(/Capped at 250 workbooks/)).toBeInTheDocument();
  });
});

describe('the shell', () => {
  it('offers both Estate surface screens', () => {
    render(<App api={fakeApi()} environment="local" />);

    expect(screen.getByRole('button', { name: 'Estate Explorer' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Lineage View' })).toBeInTheDocument();
  });

  it('switches between them', async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} environment="local" />);

    await user.click(screen.getByRole('button', { name: 'Lineage View' }));

    expect(await screen.findByRole('region', { name: 'Lineage graph' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Workbooks' })).not.toBeInTheDocument();
  });
});
