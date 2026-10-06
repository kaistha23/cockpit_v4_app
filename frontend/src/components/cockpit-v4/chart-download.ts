/**
 * Taking a chart away from where you are looking at it.
 *
 * Export lived in a panel of links at the foot of the answer. A reader
 * who wanted the delinquency chart -- the third of four, halfway up a
 * long thread -- had to scroll past the thing they wanted, find a list of
 * generic labels, and work out which link was which. So the control is
 * now on the figure.
 *
 * WHAT IS DOWNLOADED IS THE SERVER'S CHART, not a picture of the page.
 * `export.py` renders the same payload the screen renders, against the
 * same published axis, so the file and the screen agree by construction.
 * Scraping the DOM instead would produce a fourth rendering -- one that
 * inherits the current theme, the current zoom and whatever the reader
 * happened to be hovering -- and none of that belongs in a document that
 * goes into a credit pack.
 *
 * PNG is rasterised FROM that SVG for the same reason: it is the same
 * drawing at a different resolution, not a second drawing.
 */

/** How many device pixels per SVG unit. Three is legible in a slide. */
export const RASTER_SCALE = 3;

/** The PNG beside an SVG of the same chart. */
export function pngName(svgName: string): string {
  return svgName.replace(/\.svg$/i, "") + ".png";
}

/**
 * The size a raster of this drawing should be.
 *
 * Read off the SVG's own `width`/`height` so the PNG has the drawing's
 * aspect ratio rather than the browser window's. A drawing that does not
 * declare one is given a sensible page rather than refused.
 */
export function rasterSize(svg: string, scale = RASTER_SCALE): {
  width: number;
  height: number;
} {
  const width = Number(/\bwidth="(\d+(?:\.\d+)?)"/.exec(svg)?.[1]);
  const height = Number(/\bheight="(\d+(?:\.\d+)?)"/.exec(svg)?.[1]);
  const usableW = Number.isFinite(width) && width > 0 ? width : 720;
  const usableH = Number.isFinite(height) && height > 0 ? height : 360;
  return { width: Math.round(usableW * scale), height: Math.round(usableH * scale) };
}

/**
 * An SVG document as a data URL an <img> will load.
 *
 * `encodeURIComponent` rather than `btoa`: the labels are the server's own
 * strings and a Saudi book's chart carries Arabic sector names, which
 * `btoa` throws on.
 */
export function svgDataUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/**
 * Rasterise an SVG document.
 *
 * Kept here rather than in the component so the component stays a
 * component. Rejects rather than resolving an empty image: a download
 * that silently produces a blank PNG is worse than one that says it
 * could not.
 */
export async function rasterise(svg: string, scale = RASTER_SCALE): Promise<Blob> {
  const { width, height } = rasterSize(svg, scale);
  const image = new Image();
  image.decoding = "sync";
  await new Promise<void>((resolve, reject) => {
    image.onload = () => resolve();
    image.onerror = () => reject(new Error("This chart could not be drawn."));
    image.src = svgDataUrl(svg);
  });
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("This browser cannot produce a PNG.");
  context.drawImage(image, 0, 0, width, height);
  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/png"));
  if (!blob) throw new Error("This chart could not be saved as a PNG.");
  return blob;
}

/**
 * Save a blob the browser already holds.
 *
 * The object URL is revoked on the NEXT TICK rather than immediately:
 * Safari has not started reading it when the click handler returns, and
 * revoking synchronously produces a download that never happens and says
 * nothing about it.
 */
export function save(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
