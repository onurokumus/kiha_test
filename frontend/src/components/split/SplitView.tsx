import { CSSProperties, useEffect, useRef, useState } from 'react';
import {
  autoSplit,
  exportCsvUrl,
  fetchSplitCandidates,
  fetchTestPoints,
  putTestPoints,
  uploadTestPoints,
} from '../../services/api';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import { IdCandidate, TestInfo, TestMeta, TestPoint, TestPointsFile } from '../../types';
import { round3 } from '../../utils/formatters';
import { SearchableSelect } from '../controls/SearchableSelect';
import { TestSelect } from '../controls/TestSelect';
import { useConfirm } from '../feedback/confirm';
import SplitPlot, { effectiveEnd, TimeRange } from './SplitPlot';

interface Props {
  test: string;
  meta: TestMeta;
  columns: string[]; // plottable columns (no time column)
  tests: TestInfo[];
  onTestChange: (test: string) => Promise<boolean | void>;
  /** Reports whether this view has test-point changes that are not saved. */
  onDirtyChange?: (isDirty: boolean) => void;
  /** Test-point definitions were persisted and dependent caches are stale. */
  onSaved?: () => void;
  /** Reports a save/upload that must finish before navigation. */
  onBusyChange?: (isBusy: boolean) => void;
}

function sameTestPoints(left: TestPoint[], right: TestPoint[]): boolean {
  const comparable = (points: TestPoint[]) =>
    [...points]
      .sort((a, b) => a.id - b.id)
      .map(({ id, name, label, start_s, end_s, notes }) => ({
        id,
        name,
        label,
        start_s,
        end_s,
        notes: notes ?? '',
      }));

  return JSON.stringify(comparable(left)) === JSON.stringify(comparable(right));
}

/** Split editor: define/adjust test points over the full test.
 *  Auto-split proposes TPs from an ID-like column; nothing persists until
 *  Save (PUT /testpoints with the full TestPointsFile wrapper). */
export default function SplitView({
  test,
  meta,
  columns,
  tests,
  onTestChange,
  onDirtyChange,
  onSaved,
  onBusyChange,
}: Props) {
  const confirmAction = useConfirm();
  const [tps, setTps] = useState<TestPoint[]>([]);
  const [savedTps, setSavedTps] = useState<TestPoint[] | null>(null);
  const [tpLoadState, setTpLoadState] = useState<'loading' | 'ready' | 'error'>(
    'loading'
  );
  const [tpLoadError, setTpLoadError] = useState('');
  const [tpLoadRetry, setTpLoadRetry] = useState(0);
  const [saving, setSaving] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [range, setRange] = useState<TimeRange>(null);
  const [candidates, setCandidates] = useState<IdCandidate[]>([]);
  const [candCol, setCandCol] = useState('');
  const [ignoreZero, setIgnoreZero] = useState(true);
  const [minLen, setMinLen] = useState(1.0);
  const [status, setStatus] = useState('');
  const [displayCol, setDisplayCol] = useState(columns[0] || '');
  const fileRef = useRef<HTMLInputElement>(null);

  const dataStart = meta.t_start ?? 0;
  const dataEnd = dataStart + meta.duration_s;
  const dirty = savedTps !== null && !sameTestPoints(tps, savedTps);

  // load saved test points + ID-column candidates
  useEffect(() => {
    let dead = false;
    setTps([]);
    setSavedTps(null);
    setTpLoadState('loading');
    setTpLoadError('');
    fetchTestPoints(test)
      .then((f) => {
        if (dead) return;
        setTps(f.test_points);
        setSavedTps(f.test_points);
        setTpLoadState('ready');
      })
      .catch((e) => {
        if (dead) return;
        console.error(e);
        setTpLoadError(e instanceof Error ? e.message : String(e));
        setTpLoadState('error');
      });
    fetchSplitCandidates(test)
      .then((c) => {
        if (dead) return;
        setCandidates(c);
        setCandCol(c[0]?.col ?? '');
      })
      .catch((e) => !dead && console.error(e));
    setSelectedId(null);
    setRange(null);
    return () => {
      dead = true;
    };
  }, [test, tpLoadRetry]);

  useEffect(() => {
    onBusyChange?.(saving);
    return () => onBusyChange?.(false);
  }, [onBusyChange, saving]);

  useEffect(() => {
    setDisplayCol((prev) => (columns.includes(prev) ? prev : columns[0] || ''));
  }, [columns]);

  const patchTp = (id: number, patch: Partial<TestPoint>) => {
    setTps((list) => list.map((tp) => (tp.id === id ? { ...tp, ...patch } : tp)));
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
  };

  const removeTp = (id: number) => {
    setTps((l) => l.filter((tp) => tp.id !== id));
    if (selectedId === id) setSelectedId(null);
  };

  const runAutoSplit = async () => {
    if (!candCol || tpLoadState !== 'ready' || saving) return;
    if (
      tps.length &&
      !(await confirmAction({
        title: `Replace ${tps.length} existing test points?`,
        description: `Auto-split will replace the ${tps.length} unsaved test-point definitions currently shown.`,
        detail: 'Nothing is written to the test until you select Save.',
        confirmLabel: 'Replace points',
        tone: 'warning',
      }))
    ) {
      return;
    }
    setStatus('splitting…');
    try {
      const result = await autoSplit(test, candCol, ignoreZero, minLen);
      setTps(result);
      setSelectedId(null);
      setStatus(`auto-split: ${result.length} test points from ${candCol} — unsaved`);
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    }
  };

  const save = async () => {
    if (!dirty || tpLoadState !== 'ready' || saving) return;
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
      setSaving(true);
      await putTestPoints(test, payload);
      setTps(withIdx);
      setSavedTps(withIdx);
      onSaved?.();
      setStatus(`saved ${withIdx.length} test points`);
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    } finally {
      setSaving(false);
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
    if (tpLoadState !== 'ready' || saving) return;
    setStatus('uploading…');
    try {
      setSaving(true);
      await uploadTestPoints(test, f);
      onSaved?.();
      const file = await fetchTestPoints(test);
      setTps(file.test_points);
      setSavedTps(file.test_points);
      setStatus(`loaded ${file.test_points.length} test points from file`);
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    } finally {
      setSaving(false);
    }
  };

  const zoomTo = (tp: TestPoint) => {
    const end = effectiveEnd(tp, tps, dataEnd);
    const pad = Math.max((end - tp.start_s) * 0.15, 0.5);
    setRange([tp.start_s - pad, end + pad]);
    setSelectedId(tp.id);
  };

  const discardChanges = (announce = true) => {
    if (savedTps !== null) {
      setTps(savedTps.map((tp) => ({ ...tp })));
    }
    setSelectedId(null);
    setRange(null);
    if (announce) setStatus('discarded unsaved test-point changes');
  };

  const { confirmContextChange } = useUnsavedChanges({
    isDirty: dirty,
    onDirtyChange,
    confirmOnContextChange: true,
    message: `Replace unsaved test-point changes for '${test}'?`,
  });

  const changeTest = async (nextTest: string) => {
    if (nextTest === test) return;
    if ((await onTestChange(nextTest)) === false) return;
    discardChanges(false);
  };

  return (
    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8, overflowY: 'auto', padding: 12 }}>
      {tpLoadState === 'loading' && (
        <div className="panel" role="status" aria-live="polite" style={{ color: '#9fc7df' }}>
          Loading saved test-point definitions…
        </div>
      )}
      {tpLoadState === 'error' && (
        <div
          className="panel"
          role="alert"
          style={{ display: 'flex', alignItems: 'center', gap: 10, color: '#f4a08e' }}
        >
          <span style={{ flex: 1 }}>
            Could not load saved test-point definitions. Editing is disabled to protect the
            existing file. {tpLoadError}
          </span>
          <TestSelect
            tests={tests}
            ariaLabel="Choose another test"
            value={test}
            onChange={changeTest}
            style={{ width: 190 }}
          />
          <button className="btn" onClick={() => setTpLoadRetry((attempt) => attempt + 1)}>
            Retry
          </button>
        </div>
      )}
      <fieldset
        disabled={tpLoadState !== 'ready' || saving}
        aria-busy={tpLoadState === 'loading' || saving}
        style={{
          display: 'flex',
          minWidth: 0,
          flexDirection: 'column',
          gap: 8,
          margin: 0,
          padding: 0,
          border: 0,
          pointerEvents: tpLoadState !== 'ready' || saving ? 'none' : undefined,
          opacity: tpLoadState !== 'ready' || saving ? 0.72 : 1,
        }}
      >
      {/* toolbar */}
      <div
        className="panel"
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 5,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexWrap: 'wrap',
        }}
      >
        <span style={{ fontSize: 11, color: '#909090' }}>test:</span>
        <TestSelect
          tests={tests}
          value={test}
          onChange={changeTest}
          ariaLabel="Active test"
          style={{ width: 180 }}
        />
        <span style={{ color: '#555' }}>|</span>
        <span className="section-title" style={{ margin: 0 }}>Auto-split</span>
        <SearchableSelect
          value={candCol}
          onChange={setCandCol}
          ariaLabel="Auto-split column"
          options={candidates.map((candidate) => ({
            value: candidate.col,
            label: candidate.col,
            description: `${candidate.n_unique.toLocaleString()} unique values`,
          }))}
          placeholder="No ID-like columns"
          searchPlaceholder="Search split columns..."
          optionNoun="column"
          disabled={candidates.length === 0}
          style={{ width: 190 }}
        />
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
        <SearchableSelect
          value={displayCol}
          onChange={setDisplayCol}
          ariaLabel="Plot column"
          options={columns.map((column) => ({ value: column, label: column }))}
          searchPlaceholder="Search plot columns..."
          optionNoun="signal"
          style={{ width: 190 }}
        />
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
          {saving ? 'saving…' : `save${dirty ? ' *' : ''}`}
        </button>
        <button className="btn" onClick={() => discardChanges()} disabled={!dirty}>
          reset
        </button>
        <input
          ref={fileRef} type="file" accept=".json"
          style={{ display: 'none' }}
          onChange={async (e) => {
            const f = e.target.files?.[0];
            if (f && (await confirmContextChange())) handleUpload(f);
            e.target.value = '';
          }}
        />
      </div>
      {status && <div style={{ fontSize: 11, color: '#569cd6', padding: '0 4px' }}>{status}</div>}

      {displayCol && (
        <SplitPlot
          key={`${test}:${displayCol}`}
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
        <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr 90px 90px 60px 1fr 130px', gap: 4, fontSize: 11 }}>
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
                exportHref={exportCsvUrl(test, {
                  t0: tp.start_s,
                  t1: effectiveEnd(tp, tps, dataEnd),
                })}
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
      </fieldset>
    </div>
  );
}

/** Numeric table cell that commits ONLY on blur. Editing start (s) live would
 *  re-sort the row under the cursor on every keystroke, and clearing the field
 *  (Number('') === 0) would teleport the row to the top mid-edit (bug 1.20).
 *  Holding the edit locally until blur keeps the committed value — and thus the
 *  sort order — stable while typing; an empty/invalid value reverts. */
function NumberCell({ value, disabled, style, onFocus, onCommit }: {
  value: number | null;
  disabled?: boolean;
  style?: CSSProperties;
  onFocus?: () => void;
  onCommit: (v: number) => void;
}) {
  const [text, setText] = useState(value === null ? '' : String(value));
  const [editing, setEditing] = useState(false);
  // resync from props whenever not actively editing (clamp/save/reset/switch TP)
  useEffect(() => {
    if (!editing) setText(value === null ? '' : String(value));
  }, [value, editing]);
  return (
    <input
      className="input"
      style={style}
      type="number"
      step="0.01"
      disabled={disabled}
      value={text}
      onFocus={() => {
        setEditing(true);
        onFocus?.();
      }}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        setEditing(false);
        const v = Number(text);
        if (text.trim() !== '' && Number.isFinite(v)) onCommit(v);
        else setText(value === null ? '' : String(value)); // revert empty/invalid
      }}
    />
  );
}

function FragmentRow({ tp, sel, exportHref, onPatch, onZoom, onRemove, onSelect }: {
  tp: TestPoint;
  sel: boolean;
  /** Window-export URL for the TP's CURRENT time range — unlike the saved-TP
   *  endpoint this also works for unsaved/edited rows. */
  exportHref: string;
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
      <NumberCell style={cellStyle} value={tp.start_s} onFocus={onSelect}
             onCommit={(v) =>
               onPatch({
                 start_s: tp.end_s !== null && v >= tp.end_s
                   ? round3(tp.end_s - 0.01)
                   : v,
               })} />
      <NumberCell style={cellStyle} value={tp.end_s} disabled={tp.end_s === null}
             onFocus={onSelect}
             onCommit={(v) =>
               onPatch({ end_s: v <= tp.start_s ? round3(tp.start_s + 0.01) : v })} />
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
        <a className="btn" href={exportHref} download
           style={{ textDecoration: 'none' }}
           title="download CSV of this TP's current time range">⬇</a>
        <button className="btn" onClick={onRemove} title="delete">✕</button>
      </span>
    </>
  );
}
