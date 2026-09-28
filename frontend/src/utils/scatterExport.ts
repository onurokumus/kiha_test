import type { DatasheetDataPoint, ScatterDataPoint } from '../types';

const HEADERS = [
  'type', 'id', 'test', 'point_id', 'name', 'label', 'start_s', 'end_s', 'selected',
  'x_variable', 'x', 'y_variable', 'y', 'x_min', 'x_max', 'y_min', 'y_max',
];

function cell(value: string | number | boolean | null | undefined): string {
  if (value == null) return '';
  if (typeof value === 'number' && !Number.isFinite(value)) {
    throw new Error('The scatter contains a non-finite value. Wait for valid points before exporting.');
  }
  const text = String(value);
  return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
}

function pointId(value: number): number {
  if (Number.isInteger(value) && !Number.isSafeInteger(value)) {
    throw new Error('A point ID exceeds the browser exact integer range. Export its source data instead.');
  }
  return value;
}

function endpoints(mean: number, error?: [number, number], minimum?: number, maximum?: number): [number | null, number | null] {
  return [minimum ?? (error ? mean - error[0] : null), maximum ?? (error ? mean + error[1] : null)];
}

/** One original TP per row, before visual clustering and independently of zoom.
 * X/Y retain source variable names and supplied values without extra rounding.
 * Datasheet rows are raw reference coordinates, not TP means. */
export function buildScatterCsv(
  points: readonly ScatterDataPoint[], datasheet: readonly DatasheetDataPoint[],
  xLabel: string, yLabel: string,
): string {
  const rows = [HEADERS.join(',')];
  for (const point of points) {
    rows.push([
      'test_point', point.id, point.test, pointId(point.tp.id), point.name, point.label,
      point.tp.start_s, point.tp.end_s, point.isSelected, xLabel, point.x, yLabel, point.y,
      ...endpoints(point.x, point.xError, point.xMin, point.xMax),
      ...endpoints(point.y, point.yError, point.yMin, point.yMax),
    ].map(cell).join(','));
  }
  for (const point of datasheet) {
    rows.push([
      'datasheet', point.id, point.zone, pointId(point.pointId), '', '', '', '', '',
      xLabel, point.x, yLabel, point.y, '', '', '', '',
    ].map(cell).join(','));
  }
  return rows.join('\r\n') + '\r\n';
}

function checkCanceled(signal?: AbortSignal) {
  if (signal?.aborted) throw new DOMException('Export canceled.', 'AbortError');
}

function download(blob: Blob, requestedName: string, extension: 'csv' | 'png', signal?: AbortSignal) {
  checkCanceled(signal);
  let stem = '';
  for (const character of requestedName.replace(/\.(csv|png)$/i, '').trim()) {
    if (stem.length + character.length > 210) break;
    stem += character.charCodeAt(0) < 32 || '<>:"/\\|?*'.includes(character) ? '_' : character;
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = (stem.replace(/[. ]+$/, '') || 'scatter') + '.' + extension;
  link.style.display = 'none';
  let timer = 0;
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    window.clearTimeout(timer);
    window.removeEventListener('pagehide', release);
    URL.revokeObjectURL(url);
  };
  try {
    document.body.appendChild(link);
    link.click();
    window.addEventListener('pagehide', release, { once: true });
    timer = window.setTimeout(release, 30_000);
  } catch (error) {
    release();
    throw error;
  } finally {
    link.remove();
  }
}

export async function downloadScatterCsv(
  points: readonly ScatterDataPoint[], datasheet: readonly DatasheetDataPoint[],
  xLabel: string, yLabel: string, filename: string, signal?: AbortSignal,
): Promise<void> {
  checkCanceled(signal);
  const csv = buildScatterCsv(points, datasheet, xLabel, yLabel);
  // Excel recognizes UTF-8 variable names and labels with the BOM.
  download(new Blob(['\uFEFF', csv], { type: 'text/csv;charset=utf-8' }), filename, 'csv', signal);
}

const STYLE_PROPERTIES = [
  'font-family', 'font-size', 'font-weight', 'font-style', 'font-stretch',
  'font-variant', 'letter-spacing', 'text-anchor', 'dominant-baseline',
  'fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-opacity',
  'stroke-dasharray', 'stroke-dashoffset', 'stroke-linecap', 'stroke-linejoin',
  'opacity', 'visibility', 'display', 'color', 'clip-path',
];

/** Snapshot synchronously: asynchronous font/image work must never mix axes,
 * points, or styling from a later view into the export. */
function snapshotSvg(source: SVGSVGElement): SVGSVGElement {
  const clone = source.cloneNode(true) as SVGSVGElement;
  const originals = [source, ...source.querySelectorAll<SVGElement>('*')];
  const copies = [clone, ...clone.querySelectorAll<SVGElement>('*')];
  originals.forEach((element, index) => {
    const style = window.getComputedStyle(element);
    for (const property of STYLE_PROPERTIES) {
      let value = style.getPropertyValue(property);
      // Computed fragment references become document-absolute in Chromium.
      // Those URLs must point inside the serialized image instead.
      if (property === 'clip-path') value = value.replace(/url\(["']?[^)"']*#([^)"']+)["']?\)/g, 'url(#$1)');
      if (value) copies[index].style.setProperty(property, value);
    }
    copies[index].style.removeProperty('transition');
    copies[index].style.removeProperty('filter');
  });
  // Files always use the original light chart palette. Normalize the detached
  // clone only, so async font/image work never changes the live theme or view.
  const paint = (selector: string, property: string, value: string) => {
    clone.querySelectorAll<SVGElement>(selector).forEach(element => element.style.setProperty(property, value));
  };
  paint('.recharts-cartesian-axis-line, .recharts-cartesian-axis-tick-line', 'stroke', '#b2bed0');
  // Computed styles are copied per element, including Recharts' nested tspans.
  paint('.recharts-cartesian-axis text, .recharts-cartesian-axis text *', 'fill', '#626f83');
  paint('[data-range-axis="x"], [data-range-axis="x"] line', 'stroke', '#405994');
  paint('[data-range-axis="y"], [data-range-axis="y"] line', 'stroke', '#806b20');
  paint('.datasheet-scatter-series .recharts-scatter-line, .datasheet-scatter-series .recharts-scatter-line path', 'stroke', '#806b20');
  paint('[data-scatter-hover-target="datasheet"]', 'fill', '#f7f8fa');
  paint('[data-scatter-hover-target="datasheet"]', 'stroke', '#806b20');
  clone.querySelectorAll<SVGElement>('[data-export-fill], [data-export-stroke]').forEach(element => {
    for (const property of ['fill', 'stroke']) {
      const color = element.getAttribute(`data-export-${property}`);
      if (color) element.style.setProperty(property, color);
    }
  });
  clone.querySelectorAll('.recharts-tooltip-cursor, animate, animateTransform, set').forEach(node => node.remove());
  clone.querySelectorAll<SVGGElement>('[data-scatter-hover-target="point"]').forEach(group => {
    const dot = group.querySelector<SVGCircleElement>('circle[fill]:not([fill="none"])');
    if (!dot) return;
    // The first filled circle is the real point. Other circles are transient
    // hover rings/highlights; its base r attribute retains selection sizing.
    group.querySelectorAll('circle').forEach(circle => { if (circle !== dot) circle.remove(); });
    dot.style.filter = 'none';
  });
  clone.querySelectorAll<SVGGElement>('[data-scatter-hover-target="cluster"]').forEach(group => {
    const dot = group.querySelector('circle');
    const count = Number(group.querySelector('text')?.textContent);
    if (!dot || !Number.isFinite(count) || count < 2) return;
    dot.setAttribute('r', String(Math.min(10 + Math.log10(count) * 5, 20)));
    dot.style.setProperty('fill', '#2e5c8a');
    dot.style.setProperty('stroke', '#f7f8fa');
    dot.style.setProperty('stroke-width', '1');
  });
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  return clone;
}

interface FontAsset { rule: string; source: string; url: string }

/** SVG loaded as an image cannot fetch a document's external web font. Embed
 * the same-origin font already used by the page, retaining native metrics. */
function fontAssets(svg: SVGSVGElement): FontAsset[] {
  const families = new Set([svg, ...svg.querySelectorAll<SVGElement>('text')]
    .flatMap(element => element.style.fontFamily.toLowerCase().split(',').map(name => name.trim().replace(/["']/g, ''))));
  const assets: FontAsset[] = [];
  for (const sheet of Array.from(document.styleSheets)) {
    let rules: CSSRuleList;
    try { rules = sheet.cssRules; } catch { continue; }
    for (const rule of Array.from(rules)) {
      if (rule.type !== CSSRule.FONT_FACE_RULE) continue;
      const face = rule as CSSFontFaceRule;
      if (!families.has(face.style.fontFamily.toLowerCase().replace(/["']/g, '').trim())) continue;
      const source = /url\(\s*["']?([^"')]+)["']?\s*\)/.exec(face.style.getPropertyValue('src'))?.[1];
      if (!source) continue;
      const url = new URL(source, sheet.href ?? document.baseURI);
      if (url.origin !== window.location.origin) continue;
      assets.push({ rule: face.cssText, source, url: url.href });
    }
  }
  return assets;
}

async function embedFonts(svg: SVGSVGElement, assets: FontAsset[], signal?: AbortSignal) {
  const rules: string[] = [];
  for (const asset of assets) {
    checkCanceled(signal);
    const response = await fetch(asset.url, { signal });
    if (!response.ok) throw new Error('The plot font could not be loaded. Try exporting again.');
    const blob = await response.blob();
    checkCanceled(signal);
    const bytes = new Uint8Array(await blob.arrayBuffer());
    if (bytes.length > 4_000_000) throw new Error('The plot font exceeds the export size limit.');
    let binary = '';
    for (let offset = 0; offset < bytes.length; offset += 8192) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
    }
    const data = 'data:' + (blob.type || 'font/ttf') + ';base64,' + btoa(binary);
    rules.push(asset.rule.replace(asset.source, data));
  }
  checkCanceled(signal);
  if (rules.length) {
    const style = document.createElementNS('http://www.w3.org/2000/svg', 'style');
    style.textContent = rules.join('\n');
    svg.insertBefore(style, svg.firstChild);
  }
}

function imageFromUrl(url: string, signal?: AbortSignal): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const clean = () => {
      image.onload = null; image.onerror = null;
      signal?.removeEventListener('abort', abort);
    };
    const abort = () => {
      clean(); image.src = '';
      reject(new DOMException('Export canceled.', 'AbortError'));
    };
    image.onload = () => { clean(); resolve(image); };
    image.onerror = () => { clean(); reject(new Error('The scatter image could not be rendered. Try exporting again.')); };
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) { abort(); return; }
    image.src = url;
  });
}

/** Opaque 2x PNG of the current native SVG, with no toolbar or extra report text. */
export async function downloadScatterPng(
  svg: SVGSVGElement, filename: string, signal?: AbortSignal,
): Promise<void> {
  checkCanceled(signal);
  const width = svg.width.baseVal.value, height = svg.height.baseVal.value;
  const pixelWidth = Math.ceil(width * 2), pixelHeight = Math.ceil(height * 2);
  if (!svg.isConnected || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    throw new Error('Wait for the scatter plot to finish drawing before exporting.');
  }
  if (pixelWidth > 8192 || pixelHeight > 8192 || pixelWidth * pixelHeight > 16_000_000) {
    throw new Error('This PNG exceeds the image size limit. Reduce the plot size and try again.');
  }
  const snapshot = snapshotSvg(svg);
  snapshot.setAttribute('width', String(width));
  snapshot.setAttribute('height', String(height));
  snapshot.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
  const fonts = fontAssets(snapshot);
  await embedFonts(snapshot, fonts, signal);
  const serialized = new XMLSerializer().serializeToString(snapshot);
  const url = URL.createObjectURL(new Blob([serialized], { type: 'image/svg+xml;charset=utf-8' }));
  const canvas = document.createElement('canvas');
  try {
    const image = await imageFromUrl(url, signal);
    checkCanceled(signal);
    canvas.width = pixelWidth; canvas.height = pixelHeight;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('The browser could not create the PNG drawing surface.');
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, pixelWidth, pixelHeight);
    context.drawImage(image, 0, 0, pixelWidth, pixelHeight);
    const blob = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(result => result ? resolve(result) : reject(new Error(
        'The browser could not encode the scatter PNG. Reduce the plot size and try again.',
      )), 'image/png');
    });
    download(blob, filename, 'png', signal);
  } finally {
    URL.revokeObjectURL(url);
    canvas.width = 1; canvas.height = 1;
  }
}
