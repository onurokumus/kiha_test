import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/utils/testPointExport.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
const { indexTestPoints, patchTestPoint, draftTestPointRange } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
);
const meta = { t_start: 100, fs_hz: 10, n_rows: 100 };
const point = { id: 1, name: 'TP-01', label: 'mode=1', start_s: 100.04, end_s: 100.96,
  start_idx: 1, end_idx: 9, notes: '' };

test('native proposal rows survive display/draft/export and a save roundtrip on uneven timestamps', () => {
  const [indexed] = indexTestPoints([point], meta);
  assert.equal(indexed.start_idx, 1);
  assert.equal(indexed.end_idx, 9);
  assert.deepEqual(draftTestPointRange(indexed, [indexed], 100), { start_idx: 1, end_idx: 9 });
  assert.deepEqual(indexTestPoints([indexed], meta), [indexed]);
});

test('editing one time boundary clears only its native index; unrelated edits and same-value blur preserve both', () => {
  const renamed = patchTestPoint(point, { name: 'Renamed', notes: 'Note' });
  assert.equal(renamed.start_idx, 1);
  assert.equal(renamed.end_idx, 9);
  assert.deepEqual(patchTestPoint(point, { start_s: point.start_s }), point);
  const changedStart = indexTestPoints([patchTestPoint(point, { start_s: 100.3 })], meta)[0];
  assert.equal(changedStart.start_idx, 3);
  assert.equal(changedStart.end_idx, 9);
  const changedEnd = indexTestPoints([patchTestPoint(point, { end_s: 102 })], meta)[0];
  assert.equal(changedEnd.start_idx, 1);
  assert.equal(changedEnd.end_idx, 20);
});

test('new and invalid anchors use nominal conversion; open ends use next TP or data end', () => {
  const newPoint = { ...point, start_idx: null, end_idx: null };
  assert.deepEqual(indexTestPoints([newPoint], meta).map(p => [p.start_idx, p.end_idx]), [[0, 10]]);
  const invalid = { ...point, start_idx: -2, end_idx: 200 };
  assert.deepEqual(indexTestPoints([invalid], meta).map(p => [p.start_idx, p.end_idx]), [[0, 10]]);
  const open = indexTestPoints([patchTestPoint(point, { end_s: null })], meta)[0];
  assert.equal(open.end_idx, null);
  const next = { ...point, id: 2, start_s: 104, start_idx: 40 };
  assert.deepEqual(draftTestPointRange(open, [open, next], 100), { start_idx: 1, end_idx: 40 });
  assert.deepEqual(draftTestPointRange(open, [open], 100), { start_idx: 1, end_idx: 100 });
});
