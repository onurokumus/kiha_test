import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/utils/scatterExport.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
const { buildScatterCsv, downloadScatterCsv, downloadScatterPng } = await import(
  'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64')
);
const point = (overrides = {}) => ({
  x: 2.125, y: -4.25, xError: [1.125, .375], yError: [.75, 4.25],
  id: 'test:7', test: 'test', name: 'TP 7', label: 'steady', color: '#263685', isSelected: false,
  tp: { id: 7, name: 'TP 7', label: 'steady', start_s: 10.25, end_s: null },
  ...overrides,
});
function parseCsv(text) {
  const rows = []; let row = [], field = '', quoted = false;
  for (let index = 0; index < text.length; index++) {
    const character = text[index];
    if (character === '"') {
      if (quoted && text[index + 1] === '"') { field += '"'; index++; }
      else quoted = !quoted;
    } else if (character === ',' && !quoted) { row.push(field); field = ''; }
    else if (character === '\r' && text[index + 1] === '\n' && !quoted) {
      row.push(field); rows.push(row); row = []; field = ''; index++;
    } else field += character;
  }
  assert.equal(quoted, false);
  return rows;
}
function records(text) {
  const [headers, ...rows] = parseCsv(text);
  return rows.map(row => {
    assert.equal(row.length, headers.length);
    return Object.fromEntries(headers.map((header, index) => [header, row[index]]));
  });
}
test('exports coincident TPs separately with identity, axis names and absolute ranges', () => {
  const rows = records(buildScatterCsv([
    point(), point({ id: 'other:7', test: 'other', isSelected: true }),
  ], [], 'Thrust [N]', 'Torque [N m]'));
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map(row => [row.type, row.id, row.test, row.point_id, row.selected]),
    [['test_point', 'test:7', 'test', '7', 'false'], ['test_point', 'other:7', 'other', '7', 'true']]);
  assert.deepEqual([rows[0].x_variable, rows[0].x, rows[0].y_variable, rows[0].y],
    ['Thrust [N]', '2.125', 'Torque [N m]', '-4.25']);
  assert.deepEqual([rows[0].x_min, rows[0].x_max, rows[0].y_min, rows[0].y_max], ['1', '2.5', '-5', '0']);
  assert.equal(rows[0].end_s, '');
});
test('source extrema retain precision instead of reconstructing a tiny endpoint from a large mean', () => {
  const mean = 1000000.123456, minimum = .000001;
  const row = records(buildScatterCsv([point({ x: mean, xError: [mean - minimum, 1], xMin: minimum, xMax: 1000001.123456 })], [], 'X', 'Y'))[0];
  assert.equal(Number(row.x_min), minimum);
  assert.equal(row.x_min, '0.000001');
  assert.equal(row.x_max, '1000001.123456');
});
test('CSV roundtrips commas, quotes, newlines, Unicode and supplied numeric precision', () => {
  const value = .12345678901234566;
  const identity = '測定,"alpha"\r\nline';
  const row = records(buildScatterCsv([point({ test: identity, name: identity, label: identity, x: value })],
    [], '軸,"X"\n[N]', 'Y'))[0];
  for (const field of ['test', 'name', 'label']) assert.equal(row[field], identity);
  assert.equal(row.x_variable, '軸,"X"\n[N]');
  assert.equal(Number(row.x), value);
});
test('raw datasheet rows retain identity and order without invented TP statistics', () => {
  const rows = records(buildScatterCsv([], [
    { id: 'ds:1', zone: 'Reference', pointId: 45, x: 1.25, y: 3.5, isDatasheet: true },
    { id: 'ds:2', zone: 'Reference', pointId: 47.5, x: 2, y: 6, isDatasheet: true },
  ], 'X', 'Y'));
  assert.deepEqual(rows.map(row => [row.type, row.id, row.test, row.point_id]),
    [['datasheet', 'ds:1', 'Reference', '45'], ['datasheet', 'ds:2', 'Reference', '47.5']]);
  for (const row of rows) for (const key of ['start_s', 'end_s', 'selected', 'x_min', 'x_max', 'y_min', 'y_max'])
    assert.equal(row[key], '');
});
test('missing ranges stay blank, filtered input is respected, and malformed numbers never become data', () => {
  const input = [point({ id: 'test:99', xError: undefined, yError: undefined })];
  const before = structuredClone(input);
  const rows = records(buildScatterCsv(input, [], 'X', 'X'));
  assert.deepEqual(input, before);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].id, 'test:99');
  for (const key of ['x_min', 'x_max', 'y_min', 'y_max']) assert.equal(rows[0][key], '');
  for (const x of [NaN, Infinity, -Infinity])
    assert.throws(() => buildScatterCsv([point({ x })], [], 'X', 'Y'), /non-finite/);
  assert.equal(records(buildScatterCsv([], [], 'X', 'Y')).length, 0);
});
test('aborted exports do not touch the DOM or produce a download', async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(downloadScatterCsv([point()], [], 'X', 'Y', 'scatter', controller.signal), { name: 'AbortError' });
  await assert.rejects(downloadScatterPng(null, 'scatter', controller.signal), { name: 'AbortError' });
});
test('PNG rejects disconnected, invalid and oversized charts before allocating DOM resources', async () => {
  const svg = (width, height, isConnected = true) => ({
    width: { baseVal: { value: width } }, height: { baseVal: { value: height } }, isConnected,
  });
  await assert.rejects(downloadScatterPng(svg(100, 100, false), 'scatter'), /finish drawing/);
  await assert.rejects(downloadScatterPng(svg(0, 100), 'scatter'), /finish drawing/);
  await assert.rejects(downloadScatterPng(svg(NaN, 100), 'scatter'), /finish drawing/);
  await assert.rejects(downloadScatterPng(svg(5000, 100), 'scatter'), /size limit/);
  await assert.rejects(downloadScatterPng(svg(2100, 2100), 'scatter'), /size limit/);
});


test('decimal IDs remain numeric while unsafe legacy integer IDs cannot silently round', () => {
  const decimal = point({ tp: { id: 9.125, start_s: 0, end_s: 1 } });
  assert.equal(records(buildScatterCsv([decimal], [], 'X', 'Y'))[0].point_id, '9.125');
  assert.throws(() => buildScatterCsv([point({ tp: { id: 9007199254740992 } })], [], 'X', 'Y'), /exact integer range/);
  assert.throws(() => buildScatterCsv([], [{ id: 'ds', zone: 'ds', pointId: 9007199254740992, x: 1, y: 1 }], 'X', 'Y'), /exact integer range/);
});
