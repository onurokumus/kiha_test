import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import ts from 'typescript';
const nodeRequire = createRequire(import.meta.url);
const cache = new Map();
function load(path) {
  if (cache.has(path)) return cache.get(path).exports;
  const module = { exports: {} }; cache.set(path, module);
  const { outputText } = ts.transpileModule(readFileSync(path, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
  });
  new Function('require', 'module', 'exports', outputText)(specifier => specifier.startsWith('.')
    ? load(resolve(dirname(path), specifier + '.ts')) : nodeRequire(specifier), module, module.exports);
  return module.exports;
}
const { normalizeFullFlightComparison, nativeFlightRange, flightTimeOffset, nextFlightColor, comparisonViewports } =
  load(fileURLToPath(new URL('../src/utils/fullFlightComparison.ts', import.meta.url)));

test('elapsed alignment uses source start while manual shifts remain in displayed seconds', () => {
  assert.equal(flightTimeOffset('elapsed', 300, 12.5), -287.5);
  assert.equal(flightTimeOffset('stored', 300, 12.5), 12.5);
  assert.deepEqual(nativeFlightRange([2, 7], -100), [102, 107]);
  assert.deepEqual(nativeFlightRange([2, 7], 3), [-1, 4]);
  assert.equal(nativeFlightRange(null, -100), null);
});
test('comparison persistence rejects malformed entries without mistaking empty selection for legacy', () => {
  assert.equal(normalizeFullFlightComparison(undefined), null);
  assert.deepEqual(normalizeFullFlightComparison({ flights: [] }), { flights: [], timeBasis: 'elapsed' });
  const normalized = normalizeFullFlightComparison({ timeBasis: 'bad', flights: [null, {},
    { test: 'A', color: 'red', hidden: true, offset: Infinity },
    { test: 'A', color: '#123456' }, { test: 'B', offset: '-3', hidden: 1 }] });
  assert.deepEqual(normalized.flights.map(flight => [flight.test, flight.hidden, flight.offset]), [['A', true, 0], ['B', false, 0]]);
  assert.notEqual(normalized.flights[0].color, normalized.flights[1].color);
  assert.equal(normalized.timeBasis, 'elapsed');
});
test('adding a flight does not reuse a hidden flight color', () => {
  const first = nextFlightColor([]);
  const second = nextFlightColor([{ color: first, hidden: true }]);
  assert.notEqual(first, second);
  assert.equal(nextFlightColor([{ color: first }]), second);
});
test('first flight visibility toggle retains compatible Time Y and XY crops', () => {
  const token = ['stable-id', 'revision'];
  const time = { context: JSON.stringify(['full', token, 'rpm', ['torque']]), x: [102, 107], y: [10, 20] };
  const xy = { context: JSON.stringify(['xy', 'torque', 'rpm', 'full', [token, [102, 107]]]), x: [1000, 2000], y: [10, 20] };
  const foreign = { ...time, context: JSON.stringify(['full', ['other-id', 'revision'], 'rpm', []]) };
  const before = { full: [time, foreign], xy: [xy], spectrum: [null] };
  const next = comparisonViewports(before, token, 'stored');
  assert.deepEqual(JSON.parse(next.full[0].context), ['full', ['comparison', 'stored'], 'rpm', ['torque']]);
  assert.deepEqual(JSON.parse(next.xy[0].context)[4], [['comparison', 'stored'], [102, 107]]);
  assert.deepEqual(next.full[0].y, time.y);
  assert.deepEqual(next.xy[0].x, xy.x);
  assert.equal(next.full[1], foreign);
  assert.equal(next.spectrum, before.spectrum);
  assert.equal(JSON.parse(before.full[0].context)[1][0], 'stable-id');
});
