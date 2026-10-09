import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/utils/plotAppearance.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
let instance = 0;
const fresh = () => import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}#${++instance}`);
const { parsePlotAppearance } = await fresh();

test('missing and malformed appearance preferences preserve current default geometry', () => {
  for (const raw of [null, '', '{broken', 'null', 'true', '[]', '42', '{}']) {
    assert.deepEqual(parsePlotAppearance(raw), { lineScale: 1, pointScale: 1 });
  }
});

test('appearance preferences bound and validate each size independently', () => {
  assert.deepEqual(parsePlotAppearance('{"lineScale":0.5,"pointScale":3}'), { lineScale: 0.5, pointScale: 3 });
  assert.deepEqual(parsePlotAppearance('{"lineScale":2,"pointScale":"3"}'), { lineScale: 2, pointScale: 1 });
  assert.deepEqual(parsePlotAppearance('{"lineScale":1e100,"pointScale":0}'), { lineScale: 1, pointScale: 1 });
  assert.deepEqual(parsePlotAppearance('{"lineScale":1.375,"pointScale":0.82}'), { lineScale: 1.375, pointScale: 0.82 });
});

function browser(t, saved, blocked = false) {
  const originalWindow = globalThis.window, originalStorage = globalThis.localStorage;
  const handlers = new Map();
  const values = new Map(saved ? [['ptt.plot-appearance.v1', saved]] : []);
  globalThis.window = { addEventListener: (name, fn) => handlers.set(name, fn) };
  globalThis.localStorage = {
    getItem(key) { if (blocked) throw Error('blocked'); return values.get(key) ?? null; },
    setItem(key, value) { if (blocked) throw Error('blocked'); values.set(key, value); },
  };
  t.after(() => {
    if (originalWindow === undefined) delete globalThis.window; else globalThis.window = originalWindow;
    if (originalStorage === undefined) delete globalThis.localStorage; else globalThis.localStorage = originalStorage;
  });
  return { values, storage: key => handlers.get('storage')({ key }) };
}

test('saved appearance loads once, updates all subscribers, and restores both defaults', async t => {
  const { values } = browser(t, '{"lineScale":2,"pointScale":1.5}');
  const store = await fresh();
  const initial = store.getPlotAppearance();
  assert.deepEqual(initial, { lineScale: 2, pointScale: 1.5 });
  assert.equal(store.getPlotAppearance(), initial, 'snapshot identity is stable');
  let notifications = 0;
  const unsubscribe = store.subscribePlotAppearance(() => notifications++);
  store.setPlotAppearance({ lineScale: 0.75 });
  assert.deepEqual(store.getPlotAppearance(), { lineScale: 0.75, pointScale: 1.5 });
  assert.deepEqual(JSON.parse(values.get(store.PLOT_APPEARANCE_KEY)), store.getPlotAppearance());
  assert.equal(notifications, 1);
  store.setPlotAppearance({ lineScale: 0.75 });
  assert.equal(notifications, 1, 'unchanged sizes do not redraw plots');
  store.resetPlotAppearance();
  assert.deepEqual(store.getPlotAppearance(), { lineScale: 1, pointScale: 1 });
  assert.equal(notifications, 2);
  unsubscribe();
  store.setPlotAppearance({ pointScale: 3 });
  assert.equal(notifications, 2, 'unmounted plots stop receiving notifications');
});

test('unavailable browser storage still permits immediate appearance updates and reset', async t => {
  browser(t, null, true);
  const store = await fresh();
  store.setPlotAppearance({ lineScale: 2.5, pointScale: 0.5 });
  assert.deepEqual(store.getPlotAppearance(), { lineScale: 2.5, pointScale: 0.5 });
  store.resetPlotAppearance();
  assert.deepEqual(store.getPlotAppearance(), { lineScale: 1, pointScale: 1 });
});

test('other tabs synchronize appearance changes, removals and malformed preferences', async t => {
  const { values, storage } = browser(t);
  const store = await fresh();
  store.getPlotAppearance();
  values.set(store.PLOT_APPEARANCE_KEY, '{"lineScale":3,"pointScale":2}');
  storage('unrelated');
  assert.equal(store.getPlotAppearance().lineScale, 1);
  storage(store.PLOT_APPEARANCE_KEY);
  assert.deepEqual(store.getPlotAppearance(), { lineScale: 3, pointScale: 2 });
  values.set(store.PLOT_APPEARANCE_KEY, 'broken');
  storage(store.PLOT_APPEARANCE_KEY);
  assert.deepEqual(store.getPlotAppearance(), { lineScale: 1, pointScale: 1 });
  store.setPlotAppearance({ pointScale: 2 });
  values.clear();
  storage(null);
  assert.deepEqual(store.getPlotAppearance(), { lineScale: 1, pointScale: 1 });
});
