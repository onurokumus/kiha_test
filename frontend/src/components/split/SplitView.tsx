import { CSSProperties, useEffect, useRef, useState } from 'react';
import {
  testPointCsvUrl,
  fetchTestPoints,
  putTestPoints,
  uploadTestPoints,
} from '../../services/api';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import { AutoSplitProposal, TestInfo, TestMeta, TestPoint, TestPointsFile } from '../../types';
import { round3 } from '../../utils/formatters';
import { draftTestPointRange, indexTestPoints, patchTestPoint } from '../../utils/testPointExport';
import { TestSelect } from '../controls/TestSelect';
import { effectiveEnd, TimeRange } from './SplitPlot';
import SplitPlotStack from './SplitPlotStack';
import AutoSplitPanel from './AutoSplitPanel';

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
      .map(({ id, name, label, start_s, end_s, start_idx, end_idx, notes }) => ({
        id,
        name,
        label,
        start_s,
        end_s,
        start_idx,
        end_idx,
        notes: notes ?? '',
      }));

  return JSON.stringify(comparable(left)) === JSON.stringify(comparable(right));
}

/** Split editor: define/adjust test points over the full test.
 *  Auto-split previews intervals where all selected variables stay constant; nothing persists until
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
  const [autoSplitOpen, setAutoSplitOpen] = useState(false);
  const autoSplitTrigger = useRef<HTMLButtonElement>(null);
  const [status, setStatus] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const dataStart = meta.t_start ?? 0;
  const dataEnd = dataStart + meta.duration_s;
  const dirty = savedTps !== null && !sameTestPoints(tps, savedTps);
  const indexedTps = indexTestPoints(tps, meta);

  // Load saved test points. Auto-split candidates are loaded on demand.
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

  const patchTp = (id: number, patch: Partial<TestPoint>) => {
    setTps((list) => list.map((tp) => (tp.id === id ? patchTestPoint(tp, patch) : tp)));
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

  const closeAutoSplit = () => {
    setAutoSplitOpen(false);
    requestAnimationFrame(() => autoSplitTrigger.current?.focus({ preventScroll: true }));
  };

  const applyAutoSplit = (proposal: AutoSplitProposal) => {
    if (tpLoadState !== 'ready' || saving || proposal.test_points.length === 0) return;
    setTps(proposal.test_points);
    setSelectedId(null);
    setRange(null);
    setStatus(`Auto-split applied: ${proposal.test_points.length} test points from ${proposal.columns.join(', ')}. Review the plots, then Save to keep these changes.`);
    closeAutoSplit();
  };

  const save = async () => {
    if (!dirty || tpLoadState !== 'ready' || saving) return;
    const fs = meta.fs_hz;
    const bad = tps.find((tp) => tp.end_s !== null && tp.end_s <= tp.start_s);
    if (bad) {
      setStatus(`cannot save: ${bad.name} has end ≤ start`);
      return;
    }
    const withIdx = indexTestPoints(tps, meta);
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
        <button ref={autoSplitTrigger} className={'btn-toggle' + (autoSplitOpen ? ' active' : '')}
          aria-label="Configure auto-split" aria-expanded={autoSplitOpen}
          onClick={() => autoSplitOpen ? closeAutoSplit() : setAutoSplitOpen(true)}>
          Auto-split…
        </button>
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

      {autoSplitOpen && (
        <AutoSplitPanel
          key={JSON.stringify([test, meta.columns, meta.n_rows, meta.fs_hz, meta.t_start, meta.edited_at])}
          test={test} columns={columns} draft={tps}
          disabled={tpLoadState !== 'ready' || saving}
          onApply={applyAutoSplit} onClose={closeAutoSplit}
        />
      )}

      <SplitPlotStack
        key={test}
        test={test}
        columns={columns}
        range={range}
        onRangeChange={setRange}
        tps={tps}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onChangeTp={patchTp}
        dataStart={dataStart}
        dataEnd={dataEnd}
      />

      {/* TP table */}
      <div className="panel">
        <div className="section-title">
          Test points <span className="badge">{tps.length}</span>
        </div>
        <p style={{ fontSize: 11, color: '#909090', margin: '4px 0 8px' }}>
          CSV includes full-resolution stored data and test_point_id. Unsaved changes download
          as a draft; plot zoom and filters do not affect the export.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr 90px 90px 60px 1fr 130px', gap: 4, fontSize: 11 }}>
          <span style={{ color: '#909090' }}>name</span>
          <span style={{ color: '#909090' }}>label</span>
          <span style={{ color: '#909090' }}>start (s)</span>
          <span style={{ color: '#909090' }}>end (s)</span>
          <span style={{ color: '#909090' }}>open</span>
          <span style={{ color: '#909090' }}>notes</span>
          <span />
          {indexedTps.map((tp) => {
            const sel = tp.id === selectedId;
            const draft = draftTestPointRange(tp, indexedTps, meta.n_rows);
            const exportHref = !dirty
              ? testPointCsvUrl(test, tp.id)
              : draft ? testPointCsvUrl(test, tp.id, undefined, draft) : undefined;
            return (
              <FragmentRow key={tp.id} tp={tp} sel={sel}
                exportHref={saving ? undefined : exportHref}
                isDraft={dirty}
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
function NumberCell({ value, disabled, style, onFocus, onCommit, ariaLabel }: {
  value: number | null;
  disabled?: boolean;
  style?: CSSProperties;
  onFocus?: () => void;
  onCommit: (v: number) => void;
  ariaLabel?: string;
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
      aria-label={ariaLabel}
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

function FragmentRow({ tp, sel, exportHref, isDraft, onPatch, onZoom, onRemove, onSelect }: {
  tp: TestPoint;
  sel: boolean;
  /** Shared TP-export URL, optionally with explicit half-open draft rows. */
  exportHref?: string;
  isDraft: boolean;
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
             aria-label={`Name for TP ${tp.id}`}
             onFocus={onSelect}
             onChange={(e) => onPatch({ name: e.target.value })} />
      <input className="input" style={cellStyle} value={tp.label}
             onFocus={onSelect}
             onChange={(e) => onPatch({ label: e.target.value })} />
      <NumberCell style={cellStyle} value={tp.start_s} onFocus={onSelect}
             ariaLabel={`Start seconds for TP ${tp.id}`}
             onCommit={(v) =>
               onPatch({
                 start_s: tp.end_s !== null && v >= tp.end_s
                   ? round3(tp.end_s - 0.01)
                   : v,
               })} />
      <NumberCell style={cellStyle} value={tp.end_s} disabled={tp.end_s === null}
             ariaLabel={`End seconds for TP ${tp.id}`}
             onFocus={onSelect}
             onCommit={(v) =>
               onPatch({ end_s: v <= tp.start_s ? round3(tp.start_s + 0.01) : v })} />
      <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <input type="checkbox" checked={tp.end_s === null}
               aria-label={`Open end for TP ${tp.id}`}
               title="open end: TP runs until next TP or end of data"
               onChange={(e) =>
                 onPatch({ end_s: e.target.checked ? null : tp.start_s + 5 })} />
      </label>
      <input className="input" style={cellStyle} value={tp.notes ?? ''}
             onFocus={onSelect}
             onChange={(e) => onPatch({ notes: e.target.value })} />
      <span style={{ display: 'flex', gap: 4 }}>
        <button className="btn" onClick={onZoom} title="zoom to test point">🔍</button>
        <span style={{ alignSelf: 'center', color: '#909090' }} title={`Test-point ID ${tp.id}`}>#{tp.id}</span>
        <a className="btn" href={exportHref} download
           aria-label={`Download ${isDraft ? 'draft ' : ''}CSV for TP ${tp.id}`}
           aria-disabled={!exportHref || undefined}
           tabIndex={exportHref ? undefined : -1}
           style={{ textDecoration: 'none' }}
           title={!exportHref ? 'No samples in this range, or save in progress' :
             `Download ${isDraft ? 'unsaved draft' : 'saved TP'} CSV with test_point_id`}>⬇</a>
        <button className="btn" onClick={onRemove} title="delete">✕</button>
      </span>
    </>
  );
}
