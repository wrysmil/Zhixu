/**
 * Entry detection for the offline static preview. It reads only the browser
 * query string, never the hash, so `?preview=1#/home` selects the preview root
 * while `#/home` alone keeps the real (backend-connected) shell.
 */
export function isPreviewEntry(search: string): boolean {
  return new URLSearchParams(search).get("preview") === "1";
}
