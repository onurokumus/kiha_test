import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const compile = source => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const moduleUrl = source => `data:text/javascript;base64,${Buffer.from(compile(source)).toString('base64')}`;
const storeUrl = moduleUrl(await readFile(new URL('../src/utils/plotAppearance.ts', import.meta.url), 'utf8'));
const source = await readFile(new URL('../src/utils/uplotAppearance.ts', import.meta.url), 'utf8');
const { lineAppearancePlugin } = await import(moduleUrl(source.replace("'./plotAppearance'", JSON.stringify(storeUrl))));
const pointSource = await readFile(new URL('../src/utils/uplotPointAppearance.ts', import.meta.url), 'utf8');
const { pointAppearancePlugin } = await import(moduleUrl(pointSource.replace("'./plotAppearance'", JSON.stringify(storeUrl))));
const { setPlotAppearance, resetPlotAppearance } = await import(storeUrl);

function fixture(widths) {
  const plot = {
    series: [{}, ...widths.map((width, index) => ({ width, show: index !== 1, _paths: {} }))],
    redraws: 0,
    redraw(rebuildPaths, recalcAxes) {
      assert.equal(rebuildPaths, false);
      assert.equal(recalcAxes, false);
      this.redraws++;
    },
    batch(callback) { callback(); },
    setScale() { assert.fail('Appearance must not change axis bounds'); },
    setData() { assert.fail('Appearance must not replace scientific samples'); },
  };
  const plugin = lineAppearancePlugin();
  plugin.hooks.init(plot);
  plugin.hooks.ready(plot);
  return { plot, destroy: () => plugin.hooks.destroy(plot) };
}

test('line scale applies saved preference before drawing and always derives from original widths', () => {
  setPlotAppearance({ lineScale: 2 });
  const { plot, destroy } = fixture([1, 1.5, 2, 0]);
  assert.deepEqual(plot.series.slice(1).map(series => series.width), [2, 3, 4, 0]);
  assert.equal(plot.redraws, 0);
  setPlotAppearance({ lineScale: 0.5 });
  assert.deepEqual(plot.series.slice(1).map(series => series.width), [0.5, 0.75, 1, 0]);
  assert.equal(plot.series[2].show, false);
  assert.equal(plot.series[1]._paths, null);
  assert.equal(plot.redraws, 1);
  resetPlotAppearance();
  assert.deepEqual(plot.series.slice(1).map(series => series.width), [1, 1.5, 2, 0]);
  destroy();
});

test('scatter-only changes do not redraw line plots and destroyed plots stop subscribing', () => {
  resetPlotAppearance();
  const first = fixture([1]), second = fixture([2]);
  setPlotAppearance({ pointScale: 3 });
  assert.equal(first.plot.redraws, 0);
  assert.equal(second.plot.redraws, 0);
  first.destroy();
  setPlotAppearance({ lineScale: 1.75 });
  assert.equal(first.plot.series[1].width, 1);
  assert.equal(second.plot.series[1].width, 3.5);
  second.destroy();
  resetPlotAppearance();
});

test('XY point diameter and stroke scale together without changing cursor position, axes or visibility', () => {
  setPlotAppearance({ pointScale: 2 });
  const plot = {
    series: [{}, { width: 1, points: { size: 5, width: 1 }, show: false, _paths: {} }],
    cursor: { left: 34, top: 19 },
    redraws: 0,
    redraw(rebuildPaths, recalcAxes) {
      assert.equal(rebuildPaths, false);
      assert.equal(recalcAxes, false);
      this.redraws++;
    },
    batch(callback) { callback(); },
    setCursor(cursor, fire) { assert.deepEqual(cursor, this.cursor); assert.equal(fire, false); },
    setScale() { assert.fail('Appearance must not change axis bounds'); },
    setData() { assert.fail('Appearance must not replace scientific samples'); },
  };
  const plugin = pointAppearancePlugin();
  plugin.hooks.init(plot);
  plugin.hooks.ready(plot);
  assert.deepEqual(plot.series[1].points, { size: 10, width: 2 });
  assert.equal(plot.series[1].width, 2);
  assert.equal(plot.redraws, 0);
  setPlotAppearance({ lineScale: 3 });
  assert.equal(plot.redraws, 0);
  setPlotAppearance({ pointScale: 0.5 });
  assert.deepEqual(plot.series[1].points, { size: 2.5, width: 0.5 });
  assert.equal(plot.series[1].width, 0.5);
  assert.equal(plot.series[1].show, false);
  assert.equal(plot.series[1]._paths, null);
  assert.equal(plot.redraws, 1);
  plugin.hooks.destroy(plot);
  resetPlotAppearance();
  assert.equal(plot.series[1].points.size, 2.5);
});
