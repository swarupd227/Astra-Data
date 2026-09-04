/**
 * Export to PNG and JSON (S1.4.2).
 *
 * **JSON is the graph, not the picture.** A model engineer challenging a family needs the
 * numbers — every shared-lineage link with its Jaccard components and whether the
 * Cartographer or the platform produced it. The export carries the §12.1 weights and the
 * origin alongside, so a spreadsheet built from it can be checked against the formula
 * rather than trusted.
 *
 * **PNG is the picture, rendered from the same SVG on screen.** Serialised, drawn to a
 * canvas and saved — no server round trip and no headless browser. The one trap is that a
 * canvas cannot resolve CSS variables from the document, so the SVG is serialised with its
 * computed colours inlined; without that the export comes out black on black in dark mode,
 * which is the sort of thing nobody notices until a client is sent one.
 */

import type { LineageResponse } from '../lib/api';

export function lineageJson(response: LineageResponse, filters: Record<string, unknown>): string {
  return JSON.stringify(
    {
      exported_at: new Date().toISOString(),
      scope: response.scope,
      view: filters,
      // Stated with the data: a reader can recompute any strength from its components.
      similarity: {
        formula: '0.5·J(tables) + 0.3·J(fields) + 0.2·shared_calc_shapes / max_calc_shapes',
        weights: response.weights,
        origin: response.shared_lineage_origin,
        origin_meaning:
          response.shared_lineage_origin === 'graph'
            ? 'SHARES_LINEAGE edges written by the Cartographer'
            : 'computed by this read from the same inputs; nothing has clustered yet',
      },
      workbook_count: response.workbook_count,
      truncated: response.truncated,
      families: response.families,
      nodes: response.nodes,
      edges: response.edges,
      shared_lineage: response.shared_lineage,
    },
    null,
    2,
  );
}

export function download(name: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/**
 * Copy the computed colour of every painted attribute onto the element itself.
 *
 * An SVG serialised out of the document loses the stylesheet, and every `var(--…)` with it.
 * Walking the tree once and inlining what the browser actually computed is what makes the
 * PNG look like the screen.
 */
export function inlineComputedStyles(source: SVGSVGElement): SVGSVGElement {
  const clone = source.cloneNode(true) as SVGSVGElement;
  const originals = [source, ...source.querySelectorAll('*')];
  const clones = [clone, ...clone.querySelectorAll('*')];

  originals.forEach((element, index) => {
    const target = clones[index];
    if (!(target instanceof SVGElement) && !(target instanceof HTMLElement)) return;
    const computed = window.getComputedStyle(element as Element);
    for (const property of ['fill', 'stroke', 'stroke-width', 'opacity', 'font-size', 'font-family']) {
      const value = computed.getPropertyValue(property);
      if (value) target.style.setProperty(property, value);
    }
  });
  return clone;
}

export interface PngOptions {
  /** Rendered at twice the on-screen size, so the export is legible in a document. */
  scale?: number;
  background?: string;
}

export async function svgToPng(
  svg: SVGSVGElement,
  { scale = 2, background = '#ffffff' }: PngOptions = {},
): Promise<Blob> {
  const clone = inlineComputedStyles(svg);
  const width = svg.viewBox.baseVal.width || svg.clientWidth || 960;
  const height = svg.viewBox.baseVal.height || svg.clientHeight || 640;
  clone.setAttribute('width', String(width));
  clone.setAttribute('height', String(height));
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');

  const markup = new XMLSerializer().serializeToString(clone);
  const source = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(markup)}`;

  const image = await loadImage(source);
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const context = canvas.getContext('2d');
  if (!context) throw new Error('this browser cannot render a canvas');

  // An opaque background, because a transparent PNG pasted into a light document shows
  // white labels on white.
  context.fillStyle = background;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);

  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('the picture could not be encoded'))),
      'image/png',
    );
  });
}

function loadImage(source: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.addEventListener('load', () => resolve(image));
    image.addEventListener('error', () => reject(new Error('the picture could not be drawn')));
    image.src = source;
  });
}

export function exportName(prefix: string, scope: LineageResponse['scope']): string {
  const parts = [prefix, scope.family ?? scope.project ?? scope.site ?? 'estate'];
  const stamp = new Date().toISOString().slice(0, 10);
  return `${parts.join('-').replace(/[^\w.-]+/g, '-').toLowerCase()}-${stamp}`;
}
