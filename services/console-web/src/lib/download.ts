/**
 * Triggering a client-side save of a blob this console already fetched with its own
 * identity headers (story S10.2.1's own PDF/PPTX export) -- a plain `<a href>` cannot
 * carry those headers (see `lib/api.ts`'s own `getBlob`), so the file is fetched first
 * and this function only handles turning the resulting bytes into a save the browser's
 * own download manager sees, the standard "object URL plus a synthetic click" pattern.
 */

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}
