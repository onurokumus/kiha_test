import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

// Exercise the real hook with independent known aggregates. No DOM test shim.
const source = (await readFile(new URL('../src/hooks/useScatterFilter.ts', import.meta.url), 'utf8'))
  .replace("'react'", JSON.stringify(import.meta.resolve('react')));
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
const { useScatterFilter } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const point = (test, id, label) => ({ id: `${test}:${id}`, test, name: `TP ${id}`, label, tp: { id }, x: id, y: id });
const data = [point('a', 1, 'run'), point('a', 2, 'idle'), point('a', 3, 'missing'), point('b', 1, 'run')];
const stat = (mean, min, max) => ({ mean, min, max });
const cache = {
  a: {
    speed: { 1: stat(5, 0, 10), 2: stat(10, 8, 12), 3: stat(null, null, null) },
    load: { 1: stat(20, 10, 30), 2: stat(40, 35, 45), 3: stat(20, 10, 30) },
  },
  b: { speed: { 1: stat(15, 10, 20) } },
};
const range = (mode = 'mean', min = null, max = null, column = 'speed', id = column) => ({ mode, min, max, column, id });
function run(parameterFilters = [], tpKeys = [], labels = [], stats = cache) {
  let result;
  function Harness() {
    result = useScatterFilter(data, stats, { a: ['speed', 'load'], b: ['speed'] }, { tpKeys, labels, parameterFilters });
    return null;
  }
  renderToStaticMarkup(createElement(Harness));
  return { ...result, ids: result.applyFilters(data).map(point => point.id) };
}

test('empty and unbounded rows preserve all points and request no stats', () => {
  for (const rows of [[], [range()], [range('any')], [range('mean', 0, 1, '')]]) {
    const result = run(rows);
    assert.deepEqual(result.ids, ['a:1', 'a:2', 'a:3', 'b:1']);
    assert.equal(result.hasActiveFilters, false);
    assert.deepEqual(result.filterColumns, []);
  }
});
test('inclusive/equal and open bounds use each aggregate', () => {
  for (const [mode, min, max, expected] of [
    ['mean', 5, 10, ['a:1', 'a:2']], ['min', 0, 8, ['a:1', 'a:2']],
    ['max', 10, 12, ['a:1', 'a:2']], ['any', 10, 10, ['a:1', 'a:2', 'b:1']],
    ['mean', 5, 5, ['a:1']], ['min', 0, 0, ['a:1']], ['max', 10, 10, ['a:1']],
    ['mean', 10, null, ['a:2', 'b:1']], ['min', 10, null, ['b:1']],
    ['max', 12, null, ['a:2', 'b:1']], ['any', 12, null, ['a:2', 'b:1']],
    ['mean', null, 5, ['a:1']], ['min', null, 0, ['a:1']],
    ['max', null, 10, ['a:1']], ['any', null, 0, ['a:1']],
  ]) assert.deepEqual(run([range(mode, min, max)]).ids, expected, mode);
});
test('range overlap is an envelope test, not exact sample membership', () => {
  assert.deepEqual(run([range('any', 4, 6)]).ids, ['a:1']);
  assert.deepEqual(run([range('any', 21, 30)]).ids, []);
});
test('reversed bounds reject even when statistics are pending', () => {
  for (const mode of ['mean', 'min', 'max', 'any']) {
    assert.deepEqual(run([range(mode, 9, 8)]).ids, [], mode);
    assert.deepEqual(run([range(mode, 9, 8)], [], [], {}).ids, [], `${mode} pending`);
  }
});
test('null and missing TP stats are excluded', () => {
  for (const mode of ['mean', 'min', 'max', 'any']) {
    assert.deepEqual(run([range(mode, -100, 100)]).ids, ['a:1', 'a:2', 'b:1']);
    assert.deepEqual(run([range(mode, -100, 100)], [], [], { ...cache, a: { speed: { 1: cache.a.speed[1] } } }).ids, ['a:1', 'b:1']);
  }
});
test('missing columns exclude while unloaded existing columns pass provisionally', () => {
  assert.deepEqual(run([range('mean', 10, 30, 'load')]).ids, ['a:1', 'a:3']);
  assert.deepEqual(run([range('mean', 10, 30, 'load')], [], [], {}).ids, ['a:1', 'a:2', 'a:3']);
  assert.deepEqual(run([range('mean', 5, 5)], [], [], { a: cache.a }).ids, ['a:1', 'b:1']);
});
test('TP, label and parameter selections combine without widening', () => {
  assert.deepEqual(run([range('mean', 0, 20), range('max', null, 30, 'load')], ['a:1', 'a:2', 'b:1'], ['run']).ids, ['a:1']);
  assert.deepEqual(run([], ['a:1', 'a:2'], ['run', 'idle']).ids, ['a:1', 'a:2']);
  assert.deepEqual(run([], [], ['unknown']).ids, []);
});
test('required columns deduplicate active rows and ignore incomplete rows', () => {
  const result = run([range('mean', 0, null), range('max', null, 20, 'speed', 'second'),
    range('mean', 10, 30, 'load'), range('mean', null, null, 'unused'), range('mean', 0, 1, '')]);
  assert.deepEqual(result.filterColumns, ['speed', 'load']);
  assert.deepEqual(result.ids, ['a:1']);
});
