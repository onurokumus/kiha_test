import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/utils/plotHoverValues.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
const { sampleHoverRows, nearestXYDataIdx, formatHoverNumber, compactHoverLabels } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
);

test('aligned original/filtered envelopes report the cursor sample with their distinct identities', () => {
  const plot = { series: [{}, ...['original max', 'original min', 'filtered'].map(label => ({ label, stroke: '#d55e00' }))],
    data: [[0, 1, 2], [9, 10, 11], [2, null, 4], [5, 6, 7]], cursor: { idx: 1 } };
  assert.deepEqual(sampleHoverRows(plot, false).map(row => [row.label, ...row.values]),
    [['original max', '1', '10'], ['original min', '1', '—'], ['filtered', '1', '6']]);
  assert.equal(sampleHoverRows(plot, false)[0].color, '#d55e00');
});

test('faceted traces retain independent X samples and omit unavailable indices or hidden traces', () => {
  const plot = { series: [{}, { label: 'TP1', stroke: () => '#abc123' }, { label: 'TP2' },
      { label: 'hidden', show: false }, { label: 'outside' }],
    data: [null, [[0, 1], [2, 3]], [[0, 1.25], [5, 6]], [[0], [9]], [[0], [9]]],
    cursor: { idxs: [null, 1, 1, 0, null] } };
  assert.deepEqual(sampleHoverRows(plot, true).map(row => [row.label, ...row.values]),
    [['TP1', '1', '3'], ['TP2', '1.25', '6']]);
  assert.equal(sampleHoverRows(plot, true)[0].color, '#abc123');
});

function xy(pair, cursor = { left: 50, top: 20 }) {
  return { data: [null, pair], cursor, series: [{}, { facets: [{ scale: 'x' }, { scale: 'y' }] }],
    scales: { x: { min: 0, max: 10 }, y: { min: 0, max: 10 } },
    valToPos: (value, scale) => scale === 'x' ? value * 10 : 100 - value * 10 };
}

test('XY picking resolves unsorted/repeated X using Y distance in screen space', () => {
  assert.equal(nearestXYDataIdx(xy([[9, 5, 1, 5], [1, 2, 8, 8]]), 1), 3);
  assert.equal(nearestXYDataIdx(xy([[9, 5, 1, 5], [1, 2, 8, 8]], { left: 50, top: 80 }), 1), 1);
});

test('XY picking rejects missing/nonfinite/offscreen pairs and works after axis zoom', () => {
  const plot = xy([[5, 5, 5, 5, 4], [null, NaN, Infinity, 12, 7]]);
  assert.equal(nearestXYDataIdx(plot, 1), 4);
  plot.scales.y.min = 8;
  assert.equal(nearestXYDataIdx(plot, 1), null);
  assert.equal(nearestXYDataIdx(xy([[], []]), 1), null);
  assert.equal(nearestXYDataIdx(xy([[5], [8]], { left: -1, top: 20 }), 1), null);
});

test('readouts preserve tiny/large values and distinguish missing values from zero', () => {
  assert.equal(formatHoverNumber(0), '0');
  assert.equal(formatHoverNumber(null), '—');
  assert.equal(formatHoverNumber(undefined), '—');
  assert.equal(formatHoverNumber(NaN), '—');
  assert.equal(formatHoverNumber(Infinity), '—');
  assert.equal(formatHoverNumber(1.23456789e-12), '1.23457e-12');
  assert.equal(formatHoverNumber(1.23456789e15), '1.23457e+15');
});

test('compact labels omit repeated test/original context but preserve cross-test and overlay identities', () => {
  const rows = labels => labels.map(label => ({ label, values: [] }));
  assert.deepEqual(compactHoverLabels(rows(['TP-04 · ptt_demo_run_b · original'])), ['TP-04']);
  assert.deepEqual(compactHoverLabels(rows(['TP-01 · A · original', 'TP-01 · B · original'])),
    ['TP-01 · A', 'TP-01 · B']);
  assert.deepEqual(compactHoverLabels(rows(['TP-01 · A · original', 'TP-01 · A · filtered'])),
    ['TP-01 · original', 'TP-01 · filtered']);
  assert.deepEqual(compactHoverLabels(rows(['Steady · A · TP 1', 'Steady · A · TP 2'])),
    ['Steady · TP 1', 'Steady · TP 2']);
  assert.deepEqual(compactHoverLabels(rows(['original', 'Hz', 's', 'U', 'wind · measured'])),
    ['original', 'Hz', 's', 'U', 'wind · measured']);
});
