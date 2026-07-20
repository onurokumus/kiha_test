import React, { useRef, useState } from 'react';
import { AppSettings, DEFAULT_SETTINGS, normalizeSettings } from '../../constants/settings';

interface SettingsViewProps {
  /** The SAVED (active) settings. */
  settings: AppSettings;
  /** Unsaved edits, hoisted to App so switching tabs doesn't lose them;
   *  null = no pending edits. */
  draft: AppSettings | null;
  onDraftChange: (draft: AppSettings | null) => void;
  /** Persist + apply (App diffs against the saved settings and applies only
   *  the changed fields). Nothing takes effect before this. */
  onSave: (next: AppSettings) => void;
  /** Union of every loaded test's columns — the pick lists. A saved preference
   *  naming a column no test currently has still shows, tagged "(not loaded)". */
  columns: string[];
}

/** One column preference dropdown: auto option + union columns, keeping a
 *  stored value that isn't currently loaded visible instead of dropping it. */
const ColSelect: React.FC<{
  value: string;
  onChange: (v: string) => void;
  columns: string[];
  autoLabel: string;
  width?: number;
}> = ({ value, onChange, columns, autoLabel, width = 180 }) => (
  <select
    className="input"
    style={{ width }}
    value={value}
    onChange={(e) => onChange(e.target.value)}
  >
    <option value="">{autoLabel}</option>
    {value && !columns.includes(value) && (
      <option value={value}>{value} (not loaded)</option>
    )}
    {columns.map((c) => (
      <option key={c} value={c}>
        {c}
      </option>
    ))}
  </select>
);

const Section: React.FC<{ title: string; hint?: string; children: React.ReactNode }> = ({
  title,
  hint,
  children,
}) => (
  <div className="panel" style={{ padding: 14 }}>
    <div style={{ fontSize: 13, fontWeight: 600, color: '#569cd6', marginBottom: 4 }}>
      {title}
    </div>
    {hint && (
      <div style={{ fontSize: 11, color: '#909090', marginBottom: 10 }}>{hint}</div>
    )}
    {children}
  </div>
);

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
    <span style={{ width: 150, color: '#c0c0c0', flexShrink: 0 }}>{label}</span>
    {children}
  </label>
);

/** Settings tab. All edits accumulate in a DRAFT; only the Save button
 *  persists and applies them (Revert discards). Export/Import move the whole
 *  settings object through a JSON file so users can share configurations —
 *  an import lands in the draft for review, it is NOT auto-saved. */
export const SettingsView: React.FC<SettingsViewProps> = ({
  settings,
  draft,
  onDraftChange,
  onSave,
  columns,
}) => {
  const view = draft ?? settings;
  const dirty = draft !== null;
  const importRef = useRef<HTMLInputElement>(null);
  const [importError, setImportError] = useState('');

  const edit = (patch: Partial<AppSettings>) => {
    setImportError('');
    onDraftChange({ ...view, ...patch });
  };

  const handleExport = () => {
    // Export what's on screen (draft included) — WYSIWYG.
    const blob = new Blob([JSON.stringify(view, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'ptt-settings.json';
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImportFile = async (file: File) => {
    try {
      const parsed: unknown = JSON.parse(await file.text());
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('not a settings object');
      }
      onDraftChange(normalizeSettings(parsed));
      setImportError('');
    } catch (e) {
      setImportError(
        `could not import ${file.name}: ${e instanceof Error ? e.message : String(e)}`
      );
    }
  };

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
      <div
        style={{
          maxWidth: 720,
          margin: '0 auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        {/* Save / share bar */}
        <div
          className="panel"
          style={{
            padding: '10px 14px',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            flexWrap: 'wrap',
            position: 'sticky',
            top: 0,
            zIndex: 5,
          }}
        >
          <button className="btn" onClick={() => onSave(view)} disabled={!dirty}>
            Save
          </button>
          <button className="btn" onClick={() => onDraftChange(null)} disabled={!dirty}>
            Revert
          </button>
          {dirty && (
            <span className="badge" style={{ color: '#dcdcaa' }}>
              unsaved changes — nothing applies until Save
            </span>
          )}
          <span style={{ flex: 1 }} />
          <button className="btn" onClick={handleExport} title="download these settings as a JSON file">
            ⬇ Export
          </button>
          <button
            className="btn"
            onClick={() => importRef.current?.click()}
            title="load settings from a JSON file (lands in the draft — review, then Save)"
          >
            ⬆ Import
          </button>
          <input
            ref={importRef}
            type="file"
            accept=".json,application/json"
            style={{ display: 'none' }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleImportFile(f);
              e.target.value = '';
            }}
          />
          <button
            className="btn"
            onClick={() => onDraftChange({ ...DEFAULT_SETTINGS })}
            title="fill the draft with defaults (Save to apply)"
          >
            Reset to defaults
          </button>
        </div>
        {importError && (
          <div style={{ color: '#f48771', fontSize: 11 }}>{importError}</div>
        )}

        <Section
          title="Scatter plot (left panel)"
          hint="Preferred axes on load. Auto picks the column pair shared by the most tests, so one narrow test can't hide the rest. A session pick from the axis dropdowns still overrides."
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Row label="X axis">
              <ColSelect
                value={view.scatterX}
                onChange={(v) => edit({ scatterX: v })}
                columns={columns}
                autoLabel="(auto — most shared)"
              />
            </Row>
            <Row label="Y axis">
              <ColSelect
                value={view.scatterY}
                onChange={(v) => edit({ scatterY: v })}
                columns={columns}
                autoLabel="(auto — most shared)"
              />
            </Row>
          </div>
        </Section>

        <Section
          title="3×3 grid — plotted columns"
          hint="Preferred column per grid cell — the plotted (Y) variable in EVERY view mode: vs-time (Test points / Full test), Spectrum, and XY. Auto slots fill from the selected test points (first-selected first), then the active test. Edit Plots changes still override for the session."
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: 8,
              maxWidth: 560,
            }}
          >
            {Array.from({ length: 9 }, (_, i) => (
              <ColSelect
                key={i}
                value={view.gridColumns[i] ?? ''}
                onChange={(v) => {
                  const next = [...view.gridColumns];
                  next[i] = v;
                  edit({ gridColumns: next });
                }}
                columns={columns}
                autoLabel={`(auto ${i + 1})`}
                width={170}
              />
            ))}
          </div>
        </Section>

        <Section
          title="3×3 grid — XY mode"
          hint="XY mode has its own per-cell pairing, independent of the section above: each cell plots Y vs X (what Edit Plots shows as '[Y] vs [X]'). Y auto = follow that cell's plotted column above; X auto = the first grid column. Per-cell session picks in Edit Plots still override."
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: 8,
              maxWidth: 620,
            }}
          >
            {Array.from({ length: 9 }, (_, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                  border: '1px solid #3c3c3c',
                  borderRadius: 3,
                  padding: 6,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ fontSize: 10, color: '#909090', width: 14 }}>Y</span>
                  <ColSelect
                    value={view.xyYCols[i] ?? ''}
                    onChange={(v) => {
                      const next = [...view.xyYCols];
                      next[i] = v;
                      edit({ xyYCols: next });
                    }}
                    columns={columns}
                    autoLabel={`(same as cell ${i + 1})`}
                    width={150}
                  />
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ fontSize: 10, color: '#909090', width: 14 }}>X</span>
                  <ColSelect
                    value={view.xyXCols[i] ?? ''}
                    onChange={(v) => {
                      const next = [...view.xyXCols];
                      next[i] = v;
                      edit({ xyXCols: next });
                    }}
                    columns={columns}
                    autoLabel="(auto)"
                    width={150}
                  />
                </div>
              </div>
            ))}
          </div>
        </Section>

        <Section
          title="Right panel defaults"
          hint="Initial state of the grid's mode bar, applied on Save and on every future load."
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Row label="View mode">
              <select
                className="input"
                style={{ width: 180 }}
                value={view.defaultViewMode}
                onChange={(e) =>
                  edit({
                    defaultViewMode: e.target.value as AppSettings['defaultViewMode'],
                  })
                }
              >
                <option value="tp">Test points</option>
                <option value="full">Full test</option>
                <option value="spectrum">Spectrum</option>
                <option value="xy">XY</option>
              </select>
            </Row>
            <Row label="Spectrum estimator">
              <select
                className="input"
                style={{ width: 180 }}
                value={view.specMode}
                onChange={(e) => edit({ specMode: e.target.value as 'fft' | 'welch' })}
              >
                <option value="fft">FFT magnitude</option>
                <option value="welch">Welch PSD</option>
              </select>
            </Row>
            <Row label="Spectrum log Y">
              <input
                type="checkbox"
                checked={view.specLogY}
                onChange={(e) => edit({ specLogY: e.target.checked })}
              />
            </Row>
            <Row label="Overlap clustering">
              <input
                type="checkbox"
                checked={view.clustering}
                onChange={(e) => edit({ clustering: e.target.checked })}
              />
            </Row>
          </div>
        </Section>

        <Section
          title="Upload"
          hint="Assumed sample rate for CSVs whose time column is unusable (missing, non-increasing, or too coarse) — the backend then generates a uniform time axis at this rate. Files with a good time column are unaffected."
        >
          <Row label="Fallback rate (Hz)">
            <input
              className="input"
              style={{ width: 120 }}
              type="number"
              min={1}
              placeholder="2048 (default)"
              value={view.uploadFsHz}
              onChange={(e) => edit({ uploadFsHz: e.target.value })}
            />
          </Row>
        </Section>
      </div>
    </div>
  );
};

export default SettingsView;
