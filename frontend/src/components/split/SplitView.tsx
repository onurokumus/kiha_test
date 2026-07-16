import { useEffect, useRef, useState } from 'react';
import {
  autoSplit,
  fetchSplitCandidates,
  fetchTestPoints,
  putTestPoints,
  uploadTestPoints,
} from '../../services/api';
import { IdCandidate, TestInfo, TestMeta, TestPoint, TestPointsFile } from '../../types';
import SplitPlot, { effectiveEnd, TimeRange } from './SplitPlot';

interface Props {
  test: string;
  meta: TestMeta;
  columns: string[]; // plottable columns (no time column)
  tests: TestInfo[];
  onTestChange: (test: string) => void;
}

/** Split editor: define/adjust test points over the full test.
 *  Auto-split proposes TPs from an ID-like column; nothing persists until
 *  Save (PUT /testpoints with the full TestPointsFile wrapper). */
export default function SplitView({ test, meta, columns, tests, onTestChange }: Props) {
  const [tps, setTps] = useState<TestPoint[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [range, setRange] = useState<TimeRange>(null);
  const [dirty, setDirty] = useState(false);
  const [candidates, setCandidates] = useState<IdCandidate[]>([]);
  const [candCol, setCandCol] = useState('');
  const [ignoreZero, setIgnoreZero] = useState(true);
  const [minLen, setMinLen] = useState(1.0);
  const [status, setStatus] = useState('');
  const [displayCol, setDisplayCol] = useState(columns[0] || '');
  const fileRef = useRef<HTMLInputElement>(null);

  const dataStart = meta.t_start ?? 0;
  const dataEnd = dataStart + meta.duration_s;

  // load saved test points + ID-column candidates
  useEffect(() => {
    let dead = false;
    fetchTestPoints(test)
      .then((f) => {
        if (dead) return;
        setTps(f.test_points);
        setDirty(false);
      })
      .catch((e) => !dead && console.error(e));
    fetchSplitCandidates(test)
      .then((c) => {
        if (dead) return;
        setCandidates(c);
        if (c.length) setCandCol(c[0].col);
      })
      .catch((e) => !dead && console.error(e));
    setSelectedId(null);
    setRange(null);
    return () => {
      dead = true;
    };
  }, [test]);

  useEffect(() => {
    setDisplayCol((prev) => (columns.includes(prev) ? prev : columns[0] || ''));
  }, [columns]);

  const patchTp = (id: number, patch: Partial<TestPoint>) => {
    setTps((list) => list.map((tp) => (tp.id === id ? { ...tp, ...patch } : tp)));
    setDirty(true);
  };

  const addTp = () => {
    const [r0, r1] = range ?? [dataStart, dataEnd];
    const w = r1 - r0;
    const id = tps.reduce((m, tp) => Math.max(m, tp.id), 0) + 1;
    const tp: TestPoint = {
      id,
      name: `TP-${String(id).padStart(2, '0')}`,
      label: '',
      start_s: round3(r0 + 0.3 * w),
      end_s: round3(r0 + 0.7 * w),
      start_idx: null,
      end_idx: null,
      notes: '',
    };
    setTps((l) => [...l, tp]);
    setSelectedId(id);
    setDirty(true);
  };

  const removeTp = (id: number) => {
    setTps((l) => l.filter((tp) => tp.id !== id));
    if (selectedId === id) setSelectedId(null);
    setDirty(true);
  };

  const runAutoSplit = async () => {
    if (!candCol) return;
    if (tps.length && !confirm(`Replace ${tps.length} existing test points?`)) return;
    setStatus('splitting…');
    try {
      const result = await autoSplit(test, candCol, ignoreZero, minLen);
      setTps(result);
      setSelectedId(null);
      setDirty(true);
      setStatus(`auto-split: ${result.length} test points from ${candCol} — unsaved`);
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    }
  };

  const save = async () => {
    const fs = meta.fs_hz;
    const bad = tps.find((tp) => tp.end_s !== null && tp.end_s <= tp.start_s);
    if (bad) {
      setStatus(`cannot save: ${bad.name} has end ≤ start`);
      return;
    }
    const sorted = [...tps].sort((a, b) => a.start_s - b.start_s);
    const withIdx = sorted.map((tp) => ({
      ...tp,
      start_idx: Math.round((tp.start_s - dataStart) * fs),
      end_idx: tp.end_s !== null ? Math.round((tp.end_s - dataStart) * fs) : null,
    }));
    const payload: TestPointsFile = {
      version: 1,
      test,
      source_file: meta.source_file ?? '',
      fs_hz: fs,
      test_points: withIdx,
    };
    setStatus('saving…');
    try {
      await putTestPoints(test, payload);
      setTps(withIdx);
      setDirty(false);
      setStatus(`saved ${withIdx.length} test points`);
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    }
  };

  const download = () => {
    const payload: TestPointsFile = {
      version: 1,
      test,
      source_file: meta.source_file ?? '',
      fs_hz: meta.fs_hz,
      test_points: tps,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json',
    });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${test}.testpoints.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const handleUpload = async (f: File) => {
    setStatus('uploading…');
    try {
      await uploadTestPoints(test, f);
      const file = await fetchTestPoints(test);
      setTps(file.test_points);
      setDirty(false);
      setStatus(`loaded ${file.test_points.length} test points from file`);
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    }
  };

  const zoomTo = (tp: TestPoint) => {
    const end = effectiveEnd(tp, tps, dataEnd);
    const pad = Math.max((end - tp.start_s) * 0.15, 0.5);
    setRange([tp.start_s - pad, end + pad]);
    setSelectedId(tp.id);
  };

  return (
    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8, overflowY: 'auto', padding: 12 }}>
      {/* toolbar */}
      <div className="panel" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, color: '#909090' }}>test:</span>
        <select
          className="input" style={{ width: 150 }}
          value={test} onChange={(e) => onTestChange(e.target.value)}
        >
          {tests.map((t) => (
            <option key={t.name} value={t.name} disabled={t.status !== 'ready'}>
              {t.name}{t.status !== 'ready' ? ` (${t.status})` : ''}
            </option>
          ))}
        </select>
        <span style={{ color: '#555' }}>|</span>
        <span className="section-title" style={{ margin: 0 }}>Auto-split</span>
        <select
          className="input" style={{ width: 160 }}
          value={candCol} onChange={(e) => setCandCol(e.target.value)}
        >
          {candidates.map((c) => (
            <option key={c.col} value={c.col}>
              {c.col} ({c.n_unique} values)
            </option>
          ))}
          {candidates.length === 0 && <option value="">no ID-like columns</option>}
        </select>
        <label style={{ fontSize: 11, display: 'flex', gap: 4, alignItems: 'center' }}>
          <input type="checkbox" checked={ignoreZero}
                 onChange={(e) => setIgnoreZero(e.target.checked)} />
          ignore 0
        </label>
        <label style={{ fontSize: 11, display: 'flex', gap: 4, alignItems: 'center' }}>
          min len (s)
          <input className="input" type="number" step="0.5" min="0"
                 style={{ width: 60 }}
                 value={minLen}
                 onChange={(e) => {
                   const v = Number(e.target.value);
                   setMinLen(Number.isFinite(v) && v >= 0 ? v : 0);
                 }} />
        </label>
        <button className="btn" disabled={!candCol} onClick={runAutoSplit}>
          auto-split
        </button>
        <span style={{ color: '#555' }}>|</span>
        <span style={{ fontSize: 11, color: '#909090' }}>plot:</span>
        <select
          className="input" style={{ width: 160 }}
          value={displayCol} onChange={(e) => setDisplayCol(e.target.value)}
        >
          {columns.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={addTp}>+ new TP</button>
        <button className="btn" onClick={() => fileRef.current?.click()}>
          load file
        </button>
        <button className="btn" onClick={download} disabled={tps.length === 0}>
          download
        </button>
        <button
          className={'btn-toggle' + (dirty ? ' active' : '')}
          onClick={save}
          disabled={!dirty}
        >
          save{dirty ? ' *' : ''}
        </button>
        <input
          ref={fileRef} type="file" accept=".json"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleUpload(f);
            e.target.value = '';
          }}
        />
      </div>
      {status && <div style={{ fontSize: 11, color: '#569cd6', padding: '0 4px' }}>{status}</div>}

      {displayCol && (
        <SplitPlot
          test={test}
          cols={[displayCol]}
          range={range}
          onRangeChange={setRange}
          tps={tps}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onChangeTp={patchTp}
          dataStart={dataStart}
          dataEnd={dataEnd}
        />
      )}

      {/* TP table */}
      <div className="panel">
        <div className="section-title">
          Test points <span className="badge">{tps.length}</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr 90px 90px 60px 1fr 100px', gap: 4, fontSize: 11 }}>
          <span style={{ color: '#909090' }}>name</span>
          <span style={{ color: '#909090' }}>label</span>
          <span style={{ color: '#909090' }}>start (s)</span>
          <span style={{ color: '#909090' }}>end (s)</span>
          <span style={{ color: '#909090' }}>open</span>
          <span style={{ color: '#909090' }}>notes</span>
          <span />
          {[...tps].sort((a, b) => a.start_s - b.start_s).map((tp) => {
            const sel = tp.id === selectedId;
            return (
              <FragmentRow key={tp.id} tp={tp} sel={sel}
                onPatch={(p) => patchTp(tp.id, p)}
                onZoom={() => zoomTo(tp)}
                onRemove={() => removeTp(tp.id)}
                onSelect={() => setSelectedId(sel ? null : tp.id)} />
            );
          })}
        </div>
        {tps.length === 0 && (
          <div style={{ color: '#909090', fontSize: 11, padding: 8 }}>
            no test points — auto-split from an ID column or “+ new TP”
          </div>
        )}
      </div>
    </div>
  );
}

function FragmentRow({ tp, sel, onPatch, onZoom, onRemove, onSelect }: {
  tp: TestPoint;
  sel: boolean;
  onPatch: (p: Partial<TestPoint>) => void;
  onZoom: () => void;
  onRemove: () => void;
  onSelect: () => void;
}) {
  const cellStyle = {
    background: sel ? '#1e3a52' : undefined,
    borderRadius: 2,
  };
  return (
    <>
      <input className="input" style={cellStyle} value={tp.name}
             onFocus={onSelect}
             onChange={(e) => onPatch({ name: e.target.value })} />
      <input className="input" style={cellStyle} value={tp.label}
             onFocus={onSelect}
             onChange={(e) => onPatch({ label: e.target.value })} />
      <input className="input" style={cellStyle} type="number" step="0.01"
             value={tp.start_s}
             onFocus={onSelect}
             onChange={(e) => {
               const v = Number(e.target.value);
               if (Number.isFinite(v)) onPatch({ start_s: v });
             }}
             onBlur={() => {
               if (tp.end_s !== null && tp.start_s >= tp.end_s)
                 onPatch({ start_s: round3(tp.end_s - 0.01) });
             }} />
      <input className="input" style={cellStyle} type="number" step="0.01"
             value={tp.end_s ?? ''}
             disabled={tp.end_s === null}
             onFocus={onSelect}
             onChange={(e) => {
               const v = Number(e.target.value);
               if (Number.isFinite(v)) onPatch({ end_s: v });
             }}
             onBlur={() => {
               if (tp.end_s !== null && tp.end_s <= tp.start_s)
                 onPatch({ end_s: round3(tp.start_s + 0.01) });
             }} />
      <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <input type="checkbox" checked={tp.end_s === null}
               title="open end: TP runs until next TP or end of data"
               onChange={(e) =>
                 onPatch({ end_s: e.target.checked ? null : tp.start_s + 5 })} />
      </label>
      <input className="input" style={cellStyle} value={tp.notes ?? ''}
             onFocus={onSelect}
             onChange={(e) => onPatch({ notes: e.target.value })} />
      <span style={{ display: 'flex', gap: 4 }}>
        <button className="btn" onClick={onZoom} title="zoom to test point">🔍</button>
        <button className="btn" onClick={onRemove} title="delete">✕</button>
      </span>
    </>
  );
}

const round3 = (v: number) => Math.round(v * 1000) / 1000;
