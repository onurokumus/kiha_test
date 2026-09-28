import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const moduleUrl = async (path, imports = {}) => {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [name, replacement] of Object.entries(imports)) source = source.replace(name, replacement);
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
  });
  return 'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64');
};
const geometryUrl = await moduleUrl('../src/constants/scatterGeometry.ts');
const { PLOT_INSET, PLOT_INSET_X, PLOT_INSET_Y } = await import(geometryUrl);
const {
  boundsViewport, validScatterViewport, withinScatterLimits, scatterPlotAnchor,
  scatterWheelFactor, zoomScatterViewport, panScatterViewport,
} = await import(await moduleUrl('../src/utils/scatterViewport.ts', {
  '../constants/scatterGeometry': geometryUrl,
}));

const base = [-100, 300, 0.001, 0.005];
const close = (actual, expected, tolerance = 1e-12) => {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, index) => assert.ok(
    Math.abs(value - expected[index]) <= tolerance * Math.max(Math.abs(expected[index]), 1e-12),
    `${index}: ${value} != ${expected[index]}`,
  ));
};

test('wheel normalization makes pixel, line and page events equivalent', () => {
  close([scatterWheelFactor(48, 0, 480), scatterWheelFactor(3, 1, 480)],
    [scatterWheelFactor(0.1, 2, 480), scatterWheelFactor(0.1, 2, 480)]);
  assert.ok(scatterWheelFactor(2, 0, 480) < scatterWheelFactor(48, 0, 480));
  close([scatterWheelFactor(10, 0, 480) ** 10], [scatterWheelFactor(100, 0, 480)]);
});

test('opposite wheel events and toolbar factors restore both ranges without drift', () => {
  for (const anchor of [{ x: 0, y: 1 }, { x: 0.37, y: 0.82 }, { x: 1, y: 0 }]) {
    let view = base;
    for (let repeat = 0; repeat < 100; repeat++) {
      view = zoomScatterViewport(view, scatterWheelFactor(-60, 0, 500), base, anchor);
      view = zoomScatterViewport(view, scatterWheelFactor(60, 0, 500), base, anchor);
    }
    close(view, base);
  }
  close(zoomScatterViewport(zoomScatterViewport(base, .8, base), 1 / .8, base), base);
});

test('zoom keeps the exact data anchor under the cursor, including reversed screen Y', () => {
  const anchor = { x: .2, y: .9 };
  const next = zoomScatterViewport(base, .7, base, anchor);
  close([next[0] + (next[1] - next[0]) * anchor.x, next[2] + (next[3] - next[2]) * anchor.y],
    [base[0] + (base[1] - base[0]) * anchor.x, base[2] + (base[3] - base[2]) * anchor.y]);
});

test('anchors use actual axis insets and ignore margins, labels and collapsed charts', () => {
  const rect = { left: 110, top: 37, width: 760, height: 430 };
  const left = rect.left + PLOT_INSET.left, top = rect.top + PLOT_INSET.top;
  const right = rect.left + rect.width - PLOT_INSET.right;
  const bottom = rect.top + rect.height - PLOT_INSET.bottom;
  assert.deepEqual(scatterPlotAnchor(left, top, rect), { x: 0, y: 1 });
  assert.deepEqual(scatterPlotAnchor(right, bottom, rect), { x: 1, y: 0 });
  assert.deepEqual(scatterPlotAnchor((left + right) / 2, (top + bottom) / 2, rect), { x: .5, y: .5 });
  for (const [x, y] of [[left - 1, top], [right + 1, top], [left, top - 1], [left, bottom + 1], [NaN, top]])
    assert.equal(scatterPlotAnchor(x, y, rect), null);
  assert.equal(scatterPlotAnchor(left, top, { ...rect, width: PLOT_INSET_X }), null);
  assert.equal(scatterPlotAnchor(left, top, { ...rect, height: PLOT_INSET_Y }), null);
});

test('wheel spikes are bounded and invalid/zero events are inert', () => {
  const maximum = scatterWheelFactor(1e300, 2, 500);
  assert.ok(Number.isFinite(maximum) && maximum > 1 && maximum < 2);
  close([maximum * scatterWheelFactor(-1e300, 2, 500)], [1]);
  for (const args of [[NaN, 0, 500], [Infinity, 0, 500], [0, 0, 500], [1, 3, 500], [1, 0, 0], [1, 0, Infinity]])
    assert.equal(scatterWheelFactor(...args), null);
});

test('pan retains scale and sign conventions and rejects malformed/extreme movement', () => {
  const view = boundsViewport({ xMin: -100, xMax: 300, yMin: .001, yMax: .005 });
  const moved = panScatterViewport(view, 20, -.0005, base);
  close(moved, [-120, 280, .0015, .0055]);
  close(panScatterViewport(moved, -20, .0005, base), base);
  for (const delta of [NaN, Infinity, -Infinity, 1e300]) {
    assert.equal(panScatterViewport(view, delta, 0, base), null);
    assert.equal(panScatterViewport(view, 0, delta, base), null);
  }
  assert.equal(panScatterViewport([0, 0, 0, 1], 1, 1, base), null);
});

test('guards reject collapsed/overflowing views without suppressing small engineering units', () => {
  for (const view of [[0, 0, 0, 1], [1, 0, 0, 1], [0, 1, NaN, 2], [-1e308, 1e308, 0, 1], [1e15, 1e15 + .125, 0, 1]])
    assert.equal(validScatterViewport(view), false, view.join(','));
  for (const view of [[1e-12, 2e-12, -4e-15, -2e-15], [1e9, 1e9 + .01, 0, 1], [-2e100, -1e100, 1e90, 2e90]]) {
    assert.equal(validScatterViewport(view), true);
    assert.ok(zoomScatterViewport(view, .8, view));
  }
});

test('repeated zoom stops at numeric limits and invalid box views cannot change either axis', () => {
  let view = base, stopped = false;
  for (let repeat = 0; repeat < 200; repeat++) {
    const next = zoomScatterViewport(view, .5, base);
    if (next === null) { stopped = true; break; }
    assert.equal(withinScatterLimits(next, base), true);
    view = next;
  }
  assert.equal(stopped, true);
  assert.equal(withinScatterLimits([0, 1e-20, .001, .005], base), false);
  assert.equal(withinScatterLimits([-1e20, 1e20, .001, .005], base), false);
  assert.equal(withinScatterLimits([1e12, 1e12 + 400, .001, .005], base), false);
  assert.equal(withinScatterLimits([0, 10, .002, .003], base), true);
  for (const factor of [0, -1, NaN, Infinity, 1e300])
    assert.equal(zoomScatterViewport(base, factor, base), null);
  assert.equal(zoomScatterViewport(base, .8, base, { x: -1, y: .5 }), null);
  assert.equal(zoomScatterViewport(base, .8, base, { x: .5, y: NaN }), null);
});
