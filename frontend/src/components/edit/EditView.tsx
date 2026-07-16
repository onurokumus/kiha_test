import { useEffect, useState } from 'react';
import {
  deleteTest,
  editTest,
  patchUserMeta,
  renameTest,
} from '../../services/api';
import { EditOps, TestInfo, TestMeta } from '../../types';

interface Props {
  test: string;
  meta: TestMeta;
  tests: TestInfo[];
  onTestChange: (test: string) => void;
  /** A rebuild was scheduled — parent should poll and refresh when ready. */
  onRebuildStarted: () => void;
  /** Test was renamed (navigate to the new name) or deleted (empty string). */
  onTestGone: (newName: string) => void;
  /** user_meta saved — parent should refresh meta. */
  onMetaSaved: () => void;
}

interface MetaRow {
  key: string;
  value: string;
}

const NAN_POLICY_HELP: Record<string, string> = {
  keep_gaps: 'NaN stays as gaps (lines break)',
  zero_fill: 'replace NaN with 0',
  interpolate: 'linear interpolation across gaps',
};

/** Edit tab: free-form test metadata plus the destructive rebuild
 *  operations (column rename/drop, NaN policy, trim, test rename/delete).
 *  Rebuilds run server-side like an ingest; the test is unavailable
 *  until it flips back to ready. */
export default function EditView({ test, meta, tests, onTestChange, onRebuildStarted, onTestGone, onMetaSaved }: Props) {
  const [status, setStatus] = useState('');

  // -- free-form metadata --
  const [rows, setRows] = useState<MetaRow[]>([]);
  useEffect(() => {
    setRows(Object.entries(meta.user_meta ?? {}).map(([key, value]) => ({ key, value })));
  }, [test, meta.user_meta]);

  const saveMeta = async () => {
    const userMeta: Record<string, string> = {};
    rows.forEach((r) => {
      if (r.key.trim()) userMeta[r.key.trim()] = r.value;
    });
    try {
      await patchUserMeta(test, userMeta);
      setStatus('metadata saved');
      onMetaSaved();
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    }
  };

  // -- column operations --
  const dataColumns = meta.columns.filter((c) => c !== meta.time_column);
  const [renames, setRenames] = useState<Record<string, string>>({});
  const [drops, setDrops] = useState<Set<string>>(new Set());
  useEffect(() => {
    setRenames({});
    setDrops(new Set());
  }, [test, meta.columns]);

  // -- NaN policy / trim --
  const [nanPolicy, setNanPolicy] = useState(meta.nan_policy ?? 'keep_gaps');
  const tStart = meta.t_start ?? 0;
  const tEnd = tStart + meta.duration_s;
  const [trim0, setTrim0] = useState(String(tStart));
  const [trim1, setTrim1] = useState(String(tEnd));
  useEffect(() => {
    setNanPolicy(meta.nan_policy ?? 'keep_gaps');
    setTrim0(String(meta.t_start ?? 0));
    setTrim1(String((meta.t_start ?? 0) + meta.duration_s));
  }, [test, meta]);

  const runRebuild = async (ops: EditOps, what: string) => {
    if (!confirm(`${what}\n\nThis rewrites the test's data and pyramid; the test is unavailable until the rebuild finishes. Continue?`)) return;
    try {
      await editTest(test, ops);
      setStatus(`${what} — rebuilding…`);
      onRebuildStarted();
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    }
  };

  const applyColumns = () => {
    const rename: Record<string, string> = {};
    Object.entries(renames).forEach(([oldName, newName]) => {
      const trimmed = newName.trim();
      if (trimmed && trimmed !== oldName && !drops.has(oldName)) rename[oldName] = trimmed;
    });
    const drop = Array.from(drops);
    if (!Object.keys(rename).length && !drop.length) {
      setStatus('no column changes to apply');
      return;
    }
    const parts = [
      Object.keys(rename).length ? `rename ${Object.keys(rename).length} column(s)` : '',
      drop.length ? `drop ${drop.join(', ')}` : '',
    ].filter(Boolean);
    runRebuild({ rename, drop }, parts.join(' + '));
  };

  const applyNanPolicy = () => {
    if (nanPolicy === (meta.nan_policy ?? 'keep_gaps')) {
      setStatus('NaN policy unchanged');
      return;
    }
    runRebuild({ nan_policy: nanPolicy }, `apply NaN policy '${nanPolicy}'`);
  };

  const applyTrim = () => {
    const a = Number(trim0);
    const b = Number(trim1);
    if (!Number.isFinite(a) || !Number.isFinite(b) || b - a < 1) {
      setStatus('trim needs numeric t0 < t1 keeping at least 1 s');
      return;
    }
    if (a <= tStart + 1e-9 && b >= tEnd - 1e-9) {
      setStatus('trim range covers all data — nothing to cut');
      return;
    }
    runRebuild(
      { trim_t0: a, trim_t1: b },
      `trim to [${a}, ${b}] s (cuts ${(a - tStart + (tEnd - b)).toFixed(1)} s; test points outside are clipped)`
    );
  };

  // -- test rename / delete --
  const [newName, setNewName] = useState(test);
  useEffect(() => setNewName(test), [test]);

  const doRename = async () => {
    const target = newName.trim();
    if (!target || target === test) return;
    try {
      await renameTest(test, target);
      onTestGone(target);
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    }
  };

  const doDelete = async () => {
    if (!confirm(`Delete test '${test}'? It moves to the trash folder and can be restored server-side for a while.`)) return;
    try {
      await deleteTest(test);
      onTestGone('');
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    }
  };

  const nanTotal = Object.values(meta.nan_counts ?? {}).reduce((a, b) => a + b, 0);

  return (
    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8, overflowY: 'auto', padding: 12 }}>
      {status && <div style={{ fontSize: 11, color: '#569cd6', padding: '0 4px' }}>{status}</div>}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {/* test info + metadata */}
        <div className="panel" style={{ flex: '1 1 380px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div className="section-title" style={{ margin: 0 }}>Test</div>
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
          </div>
          <div style={{ fontSize: 11, color: '#909090' }}>
            {meta.n_rows.toLocaleString()} rows × {meta.n_columns} columns · {meta.fs_hz} Hz ·{' '}
            {meta.duration_s.toFixed(1)} s · source {meta.source_file || '—'}
            {meta.jitter_warning && <span style={{ color: '#dcdcaa' }}> · ⚠ time jitter &gt;1%</span>}
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input className="input" style={{ width: 200 }} value={newName}
                   onChange={(e) => setNewName(e.target.value)} />
            <button className="btn" onClick={doRename} disabled={!newName.trim() || newName.trim() === test}>
              rename test
            </button>
            <span style={{ flex: 1 }} />
            <button className="btn" style={{ borderColor: '#a04040', color: '#f48771' }} onClick={doDelete}>
              delete test
            </button>
          </div>

          <div className="section-title" style={{ marginTop: 8 }}>Metadata</div>
          <div style={{ fontSize: 10, color: '#909090' }}>
            free-form descriptors (prop, motor, ESC, ambient…) stored in meta.json
          </div>
          {rows.map((r, i) => (
            <div key={i} style={{ display: 'flex', gap: 6 }}>
              <input className="input" style={{ width: 140 }} placeholder="key" value={r.key}
                     onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} />
              <input className="input" placeholder="value" value={r.value}
                     onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
              <button className="btn" onClick={() => setRows(rows.filter((_, j) => j !== i))}>✕</button>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn" onClick={() => setRows([...rows, { key: '', value: '' }])}>+ field</button>
            <button className="btn" onClick={saveMeta}>save metadata</button>
          </div>
        </div>

        {/* NaN policy + trim */}
        <div className="panel" style={{ flex: '1 1 320px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div className="section-title">NaN policy</div>
          <div style={{ fontSize: 11, color: '#909090' }}>
            {nanTotal > 0
              ? `${nanTotal.toLocaleString()} missing values across ${Object.keys(meta.nan_counts ?? {}).length} column(s)`
              : 'no missing values in this test'}
            {' · current: '}{meta.nan_policy ?? 'keep_gaps'}
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <select className="input" style={{ width: 140 }} value={nanPolicy}
                    onChange={(e) => setNanPolicy(e.target.value)}>
              {Object.keys(NAN_POLICY_HELP).map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            <span style={{ fontSize: 10, color: '#909090' }}>{NAN_POLICY_HELP[nanPolicy]}</span>
            <span style={{ flex: 1 }} />
            <button className="btn" onClick={applyNanPolicy}>apply</button>
          </div>
          <div style={{ fontSize: 10, color: '#909090' }}>
            ('drop rows' is not offered — it would break the uniform sample rate)
          </div>

          <div className="section-title" style={{ marginTop: 8 }}>Trim</div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span style={{ fontSize: 11, color: '#909090' }}>keep</span>
            <input className="input" style={{ width: 90 }} type="number" step="0.1"
                   value={trim0} onChange={(e) => setTrim0(e.target.value)} />
            <span style={{ fontSize: 11, color: '#909090' }}>–</span>
            <input className="input" style={{ width: 90 }} type="number" step="0.1"
                   value={trim1} onChange={(e) => setTrim1(e.target.value)} />
            <span style={{ fontSize: 11, color: '#909090' }}>s (data: {tStart.toFixed(1)}–{tEnd.toFixed(1)})</span>
            <span style={{ flex: 1 }} />
            <button className="btn" onClick={applyTrim}>apply</button>
          </div>
        </div>
      </div>

      {/* column table */}
      <div className="panel">
        <div className="section-title">
          Columns <span className="badge">{dataColumns.length}</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 90px 60px', gap: 4, fontSize: 11, maxHeight: 300, overflowY: 'auto' }}>
          <span style={{ color: '#909090' }}>column</span>
          <span style={{ color: '#909090' }}>rename to</span>
          <span style={{ color: '#909090' }}>NaN count</span>
          <span style={{ color: '#909090' }}>drop</span>
          {dataColumns.map((c) => (
            <ColumnRow key={c} name={c}
              nanCount={meta.nan_counts?.[c] ?? 0}
              rename={renames[c] ?? ''}
              dropped={drops.has(c)}
              onRename={(v) => setRenames({ ...renames, [c]: v })}
              onDrop={(checked) => {
                const next = new Set(drops);
                if (checked) next.add(c);
                else next.delete(c);
                setDrops(next);
              }} />
          ))}
        </div>
        <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="btn" onClick={applyColumns}>apply column changes</button>
          <span style={{ fontSize: 10, color: '#909090' }}>
            time column '{meta.time_column}' is protected; units live in the column name (e.g. thrust_n)
          </span>
        </div>
      </div>
    </div>
  );
}

function ColumnRow({ name, nanCount, rename, dropped, onRename, onDrop }: {
  name: string;
  nanCount: number;
  rename: string;
  dropped: boolean;
  onRename: (v: string) => void;
  onDrop: (checked: boolean) => void;
}) {
  return (
    <>
      <span style={{ color: dropped ? '#666' : '#e0e0e0', textDecoration: dropped ? 'line-through' : 'none', lineHeight: '24px' }}>
        {name}
      </span>
      <input className="input" placeholder="(keep name)" value={rename}
             disabled={dropped} onChange={(e) => onRename(e.target.value)} />
      <span style={{ color: nanCount > 0 ? '#dcdcaa' : '#666', lineHeight: '24px' }}>
        {nanCount > 0 ? nanCount.toLocaleString() : '—'}
      </span>
      <input type="checkbox" checked={dropped} onChange={(e) => onDrop(e.target.checked)} />
    </>
  );
}
