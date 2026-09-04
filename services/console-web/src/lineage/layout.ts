/**
 * A deterministic force-directed layout.
 *
 * §15.3.2 asks for force-directed on this screen, and S1.4.2's purpose is to let a model
 * engineer *challenge a grouping* — which means the picture has to be one they can come
 * back to. So the simulation is deterministic in two ways:
 *
 * - **Seeded from the node ids**, not from `Math.random`. The same graph lays out the same
 *   way on every machine, every reload and every export. A layout that moved every time
 *   would make "the cluster on the left" a meaningless thing to say in a review.
 * - **Run to a fixed iteration count and then rendered**, rather than animated. Nothing
 *   settles differently because a laptop was busy, and the PNG export is the same picture
 *   the reviewer was looking at.
 *
 * The model is Fruchterman–Reingold: repulsion of k²/d between every pair, attraction of
 * d²/k along each edge, and a cooling temperature capping how far a node may move in one
 * step. `k` is the natural spacing for the node count and the canvas area, so the layout
 * self-scales — which a fixed repulsion constant does not. That distinction was not
 * academic: with a constant, a graph where every workbook links to every other collapsed
 * into an unreadable knot, and "every workbook defines the same calculation shape" is a
 * real estate rather than a contrived one.
 *
 * Edge attraction is divided by each endpoint's degree. Without that, a node with two
 * hundred links is dragged two hundred times harder than one with two, and any dense
 * cluster implodes to a point.
 *
 * No library. Complexity is O(n²) per iteration, which is deliberate rather than lazy: the
 * server caps a lineage scope at 250 workbooks, so the worst case is a few hundred nodes
 * and the naive loop finishes well inside a second. Barnes–Hut would be the right answer at
 * ten thousand nodes, and the wrong amount of code at this size.
 */

export interface LayoutNode {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
}

export interface LayoutLink {
  source: string;
  target: string;
  /** 0–1. A stronger link pulls harder, so shared lineage reads as proximity. */
  strength: number;
}

export interface LayoutOptions {
  width?: number;
  height?: number;
  iterations?: number;
  /** Multiplies the natural spacing. Above 1 the graph breathes; below it, it packs. */
  spread?: number;
  /** Attraction along edges, relative to repulsion. */
  attraction?: number;
}

export const DEFAULTS: Required<LayoutOptions> = {
  width: 960,
  height: 640,
  iterations: 300,
  spread: 0.9,
  attraction: 1,
};

/**
 * A small deterministic hash, used to place a node before the first iteration.
 *
 * Any stable function of the id would do; this one is FNV-1a because it is four lines and
 * spreads adjacent ids (`wb-0001`, `wb-0002`) to unrelated positions, which matters when
 * the ids are sequential and a poor hash would start every node in a line.
 */
export function seed(id: string): number {
  let hash = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    hash ^= id.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

export function layout(
  ids: string[],
  links: LayoutLink[],
  options: LayoutOptions = {},
): Map<string, LayoutNode> {
  const { width, height, iterations, spread, attraction } = { ...DEFAULTS, ...options };

  const centreX = width / 2;
  const centreY = height / 2;
  const radius = Math.min(width, height) * 0.38;

  // Start on a circle rather than at random points in the box: a ring has no initial
  // clusters to bias the result, and every node begins the same distance from the centre.
  const nodes: LayoutNode[] = ids.map((id, index) => {
    const angle = (index / Math.max(1, ids.length)) * Math.PI * 2;
    const jitter = seed(id);
    return {
      id,
      x: centreX + Math.cos(angle) * radius * (0.75 + jitter * 0.5),
      y: centreY + Math.sin(angle) * radius * (0.75 + jitter * 0.5),
      vx: 0,
      vy: 0,
    };
  });

  const index = new Map(nodes.map((node) => [node.id, node]));
  if (nodes.length === 0) return index;

  const edges = links
    .map((link) => ({
      source: index.get(link.source),
      target: index.get(link.target),
      strength: link.strength,
    }))
    .filter((edge): edge is { source: LayoutNode; target: LayoutNode; strength: number } =>
      Boolean(edge.source && edge.target),
    );

  // The natural distance between two nodes if the graph were spread evenly over the canvas.
  // Everything below is expressed relative to it, which is what makes the layout scale with
  // the graph rather than with a constant somebody tuned once against one estate.
  const k = spread * Math.sqrt((width * height) / nodes.length);

  const degree = new Map<string, number>();
  for (const edge of edges) {
    degree.set(edge.source.id, (degree.get(edge.source.id) ?? 0) + 1);
    degree.set(edge.target.id, (degree.get(edge.target.id) ?? 0) + 1);
  }

  for (let step = 0; step < iterations; step += 1) {
    // A node may not move further than this in one step. Cooling from a tenth of k to
    // nearly nothing is what makes the result settle rather than oscillate.
    const temperature = k * (1 - step / iterations) * 0.1;

    for (const node of nodes) {
      node.vx = 0;
      node.vy = 0;
    }

    for (let i = 0; i < nodes.length; i += 1) {
      const a = nodes[i]!;
      for (let j = i + 1; j < nodes.length; j += 1) {
        const b = nodes[j]!;
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let distance = Math.hypot(dx, dy);
        if (distance < 0.01) {
          // Two nodes exactly on top of each other have no direction to separate along.
          // Nudging by a function of the ids keeps that deterministic.
          dx = seed(a.id) - 0.5 || 0.01;
          dy = seed(b.id) - 0.5 || 0.01;
          distance = Math.hypot(dx, dy);
        }
        const force = (k * k) / distance;
        const fx = (dx / distance) * force;
        const fy = (dy / distance) * force;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }
    }

    for (const edge of edges) {
      const dx = edge.target.x - edge.source.x;
      const dy = edge.target.y - edge.source.y;
      const distance = Math.hypot(dx, dy) || 0.01;
      const force = ((distance * distance) / k) * edge.strength * attraction;
      const fx = (dx / distance) * force;
      const fy = (dy / distance) * force;
      const sourceDegree = Math.max(1, degree.get(edge.source.id) ?? 1);
      const targetDegree = Math.max(1, degree.get(edge.target.id) ?? 1);
      edge.source.vx += fx / sourceDegree;
      edge.source.vy += fy / sourceDegree;
      edge.target.vx -= fx / targetDegree;
      edge.target.vy -= fy / targetDegree;
    }

    for (const node of nodes) {
      const speed = Math.hypot(node.vx, node.vy) || 1;
      const limited = Math.min(speed, temperature);
      node.x += (node.vx / speed) * limited;
      node.y += (node.vy / speed) * limited;
    }
  }

  fit(nodes, width, height);
  return index;
}

/**
 * Scale the settled layout into the canvas.
 *
 * Without this the picture's size depends on how tangled the graph was — a tight cluster
 * would render as a dot in the middle of an empty box, and a sparse one would run off the
 * edges. Fitting means the same screen area is used whatever the graph.
 */
function fit(nodes: LayoutNode[], width: number, height: number): void {
  if (nodes.length === 0) return;
  const margin = 32;
  const xs = nodes.map((node) => node.x);
  const ys = nodes.map((node) => node.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  const scale = Math.min((width - margin * 2) / spanX, (height - margin * 2) / spanY);

  for (const node of nodes) {
    node.x = margin + (node.x - minX) * scale;
    node.y = margin + (node.y - minY) * scale;
  }

  // Centre what is left over, so a wide graph is not pinned to the top-left.
  const usedWidth = spanX * scale;
  const usedHeight = spanY * scale;
  const offsetX = (width - margin * 2 - usedWidth) / 2;
  const offsetY = (height - margin * 2 - usedHeight) / 2;
  for (const node of nodes) {
    node.x += offsetX;
    node.y += offsetY;
  }
}
