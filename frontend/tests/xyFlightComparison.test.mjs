import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/utils/xyFlightComparison.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
const { alignedXYPairs, xyFlightAxisLabel } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
);

test('independent native XY clouds preserve ordering, repeated X, and unrelated variable values', () => {
  const a = { x: [90, 30, 90, 20], y: [1, 4, 3, 2] };
  const b = { x: [31, 25, 31], y: [0.5, 1.5, 2.5] };
  const shiftedA = alignedXYPairs(a, 'rpm', 'torque', 'clock', -100);
  const shiftedB = alignedXYPairs(b, 'rpm', 'torque', 'time', -20);
  assert.equal(shiftedA.x, a.x);
  assert.equal(shiftedA.y, a.y);
  assert.equal(shiftedB.x, b.x);
  assert.equal(shiftedB.y, b.y);
});

test('time axes align independently without changing native data used by export provenance', () => {
  const a = { x: [100, 100.5, 101], y: [10, 30, 20] };
  const b = { x: [900, 900.25, 900.5, 900.75], y: [15, 19, 22, 25] };
  assert.deepEqual(alignedXYPairs(a, 'time', 'torque', 'time', -100),
    { x: [0, 0.5, 1], y: a.y });
  assert.deepEqual(alignedXYPairs(b, 'time', 'torque', 'time', -900),
    { x: [0, 0.25, 0.5, 0.75], y: b.y });
  assert.deepEqual(a.x, [100, 100.5, 101]);
  assert.deepEqual(b.x, [900, 900.25, 900.5, 900.75]);
  assert.deepEqual(alignedXYPairs({ x: [7, 2], y: [101, 102] }, 'rpm', 'time', 'time', -100),
    { x: [7, 2], y: [1, 2] });
});

test('same-column XY time pairs shift both coordinates and preserve stored-time zero offsets', () => {
  const pairs = { x: [101, 101.5], y: [101, 101.5] };
  assert.deepEqual(alignedXYPairs(pairs, 'clock', 'clock', 'clock', -98),
    { x: [3, 3.5], y: [3, 3.5] });
  assert.equal(alignedXYPairs(pairs, 'clock', 'clock', 'clock', 0).x, pairs.x);
  assert.equal(alignedXYPairs(pairs, 'clock', 'clock', 'clock', 0).y, pairs.y);
});

test('axis labels distinguish aligned time, stored variables and heterogeneous source columns', () => {
  assert.equal(xyFlightAxisLabel('rpm', 'RPM', ['time', 'time'], 'elapsed'), 'RPM');
  assert.equal(xyFlightAxisLabel('time', 'Time', ['time', 'time'], 'elapsed'), 'Time · aligned elapsed time (s)');
  assert.equal(xyFlightAxisLabel('time', 'Time', ['time', 'time'], 'stored'), 'Time · aligned stored time (s)');
  assert.equal(xyFlightAxisLabel('time', 'Time', ['time', 'clock'], 'elapsed'), 'Time (time aligned per flight)');
});
