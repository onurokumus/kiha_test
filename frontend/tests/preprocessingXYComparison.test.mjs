import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

async function moduleUrl(path, imports = {}) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [specifier, url] of Object.entries(imports)) source = source.replace(`'${specifier}'`, `'${url}'`);
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
  });
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`;
}
const apiUrl = `data:text/javascript;base64,${Buffer.from('export const getJson = (path, signal) => globalThis.__xyComparisonGet(path, signal);').toString('base64')}`;
const rangeUrl = await moduleUrl('../src/utils/timePlotRanges.ts');
const { validatePreprocessingXYComparison, fetchPreprocessingXYComparison, comparisonXYCursorValues } =
  await import(await moduleUrl('../src/services/preprocessingXYComparison.ts', { './api': apiUrl, '../utils/timePlotRanges': rangeUrl }));
const source = { id: 'fixture-id', revision: 'revision-one', name: 'A&B flight' };
const counts = { finite_pairs: 2, missing_pairs: 1, native_finite_pairs: 2, native_missing_pairs: 1 };
const fixture = (patch = {}) => ({ source, x: 'Torque, raw [N·m]', y: 'Thrust [N]', mode: 'raw', stride: 1,
  n_raw: 3, n_sampled: 3, i0: 7, i1: 10, indices: [7, 8, 9], fallback_indices: [], t: [.7, .8, .9],
  original: { x: [10, null, 20], y: [30, null, 40] }, filtered: { x: [9, null, 20.00000001], y: [32, null, 37] },
  range: { start: 0, end: 3 }, summary: { original: counts, filtered: counts }, gaps: [], warnings: [], ...patch });

test('XY query preserves arbitrary axes, exact saved revision, interval and abort signal', async () => {
  const controller = new AbortController();
  let captured;
  globalThis.__xyComparisonGet = async (path, signal) => { captured = { path, signal }; return fixture(); };
  try {
    const result = await fetchPreprocessingXYComparison(source.name, source, fixture().x, fixture().y, [.7, 1], controller.signal);
    const url = new URL(captured.path, 'http://fixture');
    assert.equal(url.pathname, '/tests/A%26B%20flight/preprocess/compare/xy');
    for (const [key, value] of Object.entries({ x: fixture().x, y: fixture().y, source_id: source.id,
      source_revision: source.revision, t0: '0.7', t1: '1', max_points: '6000' })) assert.equal(url.searchParams.get(key), value);
    assert.equal(captured.signal, controller.signal);
    assert.deepEqual(result, fixture());
    await fetchPreprocessingXYComparison(source.name, source, fixture().x, fixture().y, null);
    assert.equal(new URL(captured.path, 'http://fixture').searchParams.has('t0'), false);
  } finally { delete globalThis.__xyComparisonGet; }
});

test('XY rejects stale sources, envelope data, mismatched rows, mispaired nulls and false counts', () => {
  for (const patch of [
    { source: { ...source, revision: 'different' } }, { source: { ...source, id: 'different' } },
    { source: { ...source, name: 'renamed' } }, { x: 'wrong' }, { y: 'wrong' }, { mode: 'envelope' },
    { indices: [7, 9, 8] }, { indices: [7, 8, 10] }, { indices: [7, 8] }, { indices: [7, 8, 8] },
    { t: [.7, .7, .9] }, { t: [.7, null, .9] }, { n_sampled: 2 }, { stride: 2 },
    { original: { x: [10, null, 20], y: [30, 40, 50] } },
    { filtered: { x: [9, null, Infinity], y: [32, null, 37] } },
    { fallback_indices: [99] }, { summary: { original: { ...counts, finite_pairs: 1 }, filtered: counts } },
    { summary: { original: counts, filtered: { ...counts, native_missing_pairs: 2 } } },
  ]) assert.throws(() => validatePreprocessingXYComparison(fixture(patch), source, fixture().x, fixture().y), /does not match/);
  assert.equal(validatePreprocessingXYComparison(fixture(), source, fixture().x, fixture().y).original.x[1], null);
});

test('shared sparse fallback rows remain real paired samples and both count populations are explicit', () => {
  const sampledCounts = { ...counts, native_finite_pairs: 10, native_missing_pairs: 2 };
  const data = fixture({ mode: 'sampled', stride: 4, n_raw: 12, i0: 7, i1: 19,
    indices: [7, 11, 13], fallback_indices: [13],
    summary: { original: sampledCounts, filtered: sampledCounts } });
  validatePreprocessingXYComparison(data, source, data.x, data.y);
  assert.throws(() => validatePreprocessingXYComparison({ ...data, fallback_indices: [] }, source, data.x, data.y), /does not match/);
});

test('XY cursor reads original and filtered at one row and retains tiny differences without null-as-zero', () => {
  const data = fixture();
  assert.deepEqual(comparisonXYCursorValues(data, 0), { index: 7, time: .7,
    original: { x: 10, y: 30 }, filtered: { x: 9, y: 32 }, difference: { x: -1, y: 2 } });
  assert.deepEqual(comparisonXYCursorValues(data, 1).difference, { x: null, y: null });
  assert.equal(comparisonXYCursorValues(data, 2).difference.x, 20.00000001 - 20);
  for (const index of [null, -1, 3, .5]) assert.equal(comparisonXYCursorValues(data, index), null);
  const overflow = fixture({ original: { x: [-1e308, null, 20], y: [30, null, 40] },
    filtered: { x: [1e308, null, 20], y: [32, null, 37] } });
  assert.equal(comparisonXYCursorValues(overflow, 0).difference.x, null);
});

test('time on either or both axes keeps native values and permits an empty interval', () => {
  for (const [x, y] of [['TIME', 'Thrust [N]'], ['Torque, raw [N·m]', 'TIME'], ['TIME', 'TIME']]) {
    const data = fixture({ x, y });
    validatePreprocessingXYComparison(data, source, x, y);
  }
  const emptyCounts = { finite_pairs: 0, missing_pairs: 0, native_finite_pairs: 0, native_missing_pairs: 0 };
  const empty = fixture({ i0: 7, i1: 7, n_raw: 0, n_sampled: 0, indices: [], t: [],
    original: { x: [], y: [] }, filtered: { x: [], y: [] }, summary: { original: emptyCounts, filtered: emptyCounts } });
  validatePreprocessingXYComparison(empty, source, empty.x, empty.y);
});
