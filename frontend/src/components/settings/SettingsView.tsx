import React, { useLayoutEffect, useRef, useState } from 'react';
import { NumericField } from '../controls/NumericField';
import { numericError } from '../../utils/numericField';
import '../featureWorkspace.css';
import styles from './SettingsView.module.css';
import { AppSettings, DEFAULT_SETTINGS, normalizeSettings } from '../../constants/settings';
import { SearchableSelect, SearchableSelectOption } from '../controls/SearchableSelect';
import { useConfirm } from '../feedback/confirm';

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
  /** Publish the settings currently shown as the server-wide page baseline. */
  onMakeDefault: (next: AppSettings) => Promise<void>;
  /** Union of every loaded test's columns — the pick lists. A saved preference
   *  naming a column no test currently has still shows, tagged "(not loaded)". */
  columns: string[];
  /** XY preferences also allow each test's stored time column. */
  xyColumns: string[];
  /** Ready uploaded data zones that can supply datasheet rows. */
  zones: string[];
}

/** One column preference dropdown: auto option + union columns, keeping a
 *  stored value that isn't currently loaded visible instead of dropping it. */
const ColSelect: React.FC<{
  value: string;
  onChange: (v: string) => void;
  columns: string[];
  autoLabel: string;
  width?: number;
  ariaLabel?: string;
}> = ({ value, onChange, columns, autoLabel, width = 180, ariaLabel = 'Column preference' }) => {
  const options: SearchableSelectOption[] = [
    {
      value: '',
      label: autoLabel,
      description: 'Choose the best available signal',
      group: 'Automatic',
    },
    ...(value && !columns.includes(value)
      ? [
          {
            value,
            label: value,
            description: 'Saved preference - not currently loaded',
            group: 'Saved preference',
          },
        ]
      : []),
    ...columns.map((column) => ({
      value: column,
      label: column,
      group: 'Loaded signals',
    })),
  ];

  return (
    <SearchableSelect
      style={{ width, maxWidth: '100%', minWidth: 0 }}
      value={value}
      onChange={onChange}
      options={options}
      ariaLabel={ariaLabel}
      searchPlaceholder="Search signals..."
      optionNoun="signal"
      menuMinWidth={300}
    />
  );
};

/** Datasheet source selector. Keep a temporarily unavailable saved zone
 * visible so a server restart or re-upload cannot erase the preference. */
const ZoneSelect: React.FC<{
  value: string;
  onChange: (value: string) => void;
  zones: string[];
}> = ({ value, onChange, zones }) => {
  const options: SearchableSelectOption[] = [
    {
      value: '',
      label: '(none)',
      description: 'Do not draw a datasheet reference line',
      group: 'Datasheet',
    },
    ...(value && !zones.includes(value)
      ? [
          {
            value,
            label: value,
            description: 'Saved zone - not currently loaded',
            group: 'Saved preference',
          },
        ]
      : []),
    ...zones.map((zone) => ({
      value: zone,
      label: zone,
      group: 'Loaded zones',
    })),
  ];

  return (
    <SearchableSelect
      style={{ width: 260 }}
      value={value}
      onChange={onChange}
      options={options}
      ariaLabel="Datasheet zone"
      searchPlaceholder="Search data zones..."
      optionNoun="zone"
      menuMinWidth={320}
    />
  );
};

const Section: React.FC<{ title: string; hint?: string; advanced?: boolean; wide?: boolean; warning?: string; children: React.ReactNode }> = ({
  title, hint, advanced, wide, warning, children,
}) => advanced ? (
  <details className={`feature-disclosure ${styles.wide}`}>
    <summary>{title}{warning && <span className="feature-field-warning">{warning}</span>}</summary>
    <div className="feature-disclosure-body">
      {children}
      {hint && <details className="feature-help" style={{ marginTop: 14 }}><summary>How this works</summary><p>{hint}</p></details>}
    </div>
  </details>
) : (
  <section className={`panel feature-settings-section ${wide ? styles.wide : ''}`}>
    <h2>{title}</h2>
    {children}
    {hint && <details className="feature-help"><summary>How this works</summary><p>{hint}</p></details>}
  </section>
);

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <label className="settings-row">
    <span>{label}</span>
    {children}
  </label>
);

/** Settings tab. All edits accumulate in a DRAFT; Save persists locally, while
 *  Make default also publishes and applies the displayed draft. Revert
 *  discards. Export/Import move the whole
 *  settings object through a JSON file so users can share configurations —
 *  an import lands in the draft for review, it is NOT auto-saved. */
export const SettingsView: React.FC<SettingsViewProps> = ({
  settings,
  draft,
  onDraftChange,
  onSave,
  onMakeDefault,
  columns,
  xyColumns,
  zones,
}) => {
  const confirmAction = useConfirm();
  const view = draft ?? settings;
  const dirty = draft !== null;
  const importRef = useRef<HTMLInputElement>(null);
  const moreRef = useRef<HTMLDetailsElement>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [moreMaxHeight, setMoreMaxHeight] = useState<number>();
  const [importError, setImportError] = useState('');
  const [publishingDefault, setPublishingDefault] = useState(false);
  const [publishMessage, setPublishMessage] = useState('');
  const [publishError, setPublishError] = useState('');
  const rateError = numericError(view.uploadFsHz, { min: 0, exclusiveMin: true, allowEmpty: true });

  useLayoutEffect(() => {
    if (!moreOpen) return;
    const fitMenu = () => {
      const trigger = moreRef.current?.querySelector('summary');
      if (trigger) setMoreMaxHeight(Math.max(40, window.innerHeight - trigger.getBoundingClientRect().bottom - 18));
    };
    const dismissOutside = (event: PointerEvent) => {
      const menu = moreRef.current;
      if (!menu || !(event.target instanceof Node) || menu.contains(event.target)) return;
      // Publication confirmations own focus until they close; their trigger must
      // stay visible for the shared dialog's focus restoration.
      if (document.querySelector('[aria-modal="true"]')) return;
      menu.open = false;
      if (menu.contains(document.activeElement)) menu.querySelector('summary')?.focus({ preventScroll: true });
    };
    fitMenu();
    window.addEventListener('resize', fitMenu);
    window.addEventListener('scroll', fitMenu, true);
    document.addEventListener('pointerdown', dismissOutside, true);
    return () => {
      window.removeEventListener('resize', fitMenu);
      window.removeEventListener('scroll', fitMenu, true);
      document.removeEventListener('pointerdown', dismissOutside, true);
    };
  }, [moreOpen]);

  const replaceDraft = (next: AppSettings | null) => {
    setImportError('');
    setPublishMessage('');
    setPublishError('');
    onDraftChange(next);
  };

  const edit = (patch: Partial<AppSettings>) => {
    replaceDraft({ ...view, ...patch });
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
    setPublishMessage('');
    setPublishError('');
    try {
      const parsed: unknown = JSON.parse(await file.text());
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('not a settings object');
      }
      replaceDraft(normalizeSettings(parsed));
    } catch (e) {
      setImportError(
        `could not import ${file.name}: ${e instanceof Error ? e.message : String(e)}`
      );
    }
  };

  const handleMakeDefault = async () => {
    if (rateError) return;
    const confirmed = await confirmAction({
      title: 'Make these the page defaults?',
      description:
        'The settings currently shown will become the starting configuration for everyone.',
      detail:
        'They apply on the next page load for browsers without personal settings. Existing personal settings will not be overwritten.',
      confirmLabel: 'Make page defaults',
      tone: 'warning',
    });
    if (!confirmed) return;

    setPublishingDefault(true);
    setPublishMessage('');
    setPublishError('');
    try {
      await onMakeDefault(view);
      setPublishMessage('These settings are now the page defaults for everyone.');
    } catch (error) {
      setPublishError(
        `could not update page defaults: ${error instanceof Error ? error.message : String(error)}`
      );
    } finally {
      setPublishingDefault(false);
    }
  };

  return (
    <div className={`settings-page feature-settings ${styles.workspace}`}>
      <div className="feature-settings-content">
        <header className={styles.toolbar}>
          <h1>Workspace settings</h1>
          <span className={styles.draftStatus} aria-live="polite">
            {dirty ? 'Unsaved changes' : 'Saved settings'}
          </span>
          <div className={`settings-actions ${styles.actions}`}>
          <button className="btn" onClick={() => replaceDraft(null)} disabled={!dirty}>
            Revert
          </button>
          <button className="btn btn-primary" onClick={() => { if (!rateError) onSave(view); }} disabled={!dirty || !!rateError}>
            Save
          </button>
          <details className={styles.moreActions} ref={moreRef}
            onToggle={(event) => setMoreOpen(event.currentTarget.open)}
            onKeyDown={(event) => {
              if (event.key === 'Escape' && moreRef.current?.open) {
                event.stopPropagation();
                moreRef.current.open = false;
                moreRef.current.querySelector('summary')?.focus();
              }
            }}
            onBlur={(event) => {
              const next = event.relatedTarget as HTMLElement | null;
              if (next && !event.currentTarget.contains(next) && !next.closest('[role="dialog"], [role="alertdialog"]')) {
                event.currentTarget.open = false;
              }
            }}>
            <summary title="Import, export and defaults" aria-label="More settings actions">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3" cy="8" r="1.5" /><circle cx="8" cy="8" r="1.5" /><circle cx="13" cy="8" r="1.5" /></svg>
            </summary>
            <div className={styles.morePanel} style={{ maxHeight: moreMaxHeight }}>
          <button
            className="btn"
            onClick={handleExport}
            title="download these settings as a JSON file"
          >
            Export JSON
          </button>
          <button
            className="btn"
            onClick={() => importRef.current?.click()}
            title="load settings from a JSON file (lands in the draft — review, then Save)"
          >
            Import JSON
          </button>
          <div className={styles.menuDivider} />
          <button
            className="btn"
            onClick={handleMakeDefault}
            disabled={publishingDefault || !!rateError}
            title="save the settings shown here as the starting defaults for browsers without personal settings"
          >
            {publishingDefault ? 'Publishing...' : 'Make default for everyone'}
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
            onClick={() => replaceDraft({ ...DEFAULT_SETTINGS })}
            title="fill the draft with defaults (Save to apply)"
          >
            Reset to defaults
          </button>
            </div>
          </details>
        </div>
        </header>
        {importError && <div style={{ color: '#b84343', fontSize: 11 }}>{importError}</div>}
        <div className={styles.feedback} aria-live="polite">
          {publishMessage && <div style={{ color: '#237c66', fontSize: 11 }}>{publishMessage}</div>}
          {publishError && <div style={{ color: '#b84343', fontSize: 11 }}>{publishError}</div>}
        </div>

        <div className={styles.sections}>
        <Section
          title="Scatter axes"
          hint="Preferred axes on load. Auto picks the column pair shared by the most tests, so one narrow test can't hide the rest. A session pick from the axis dropdowns still overrides."
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Row label="X axis">
              <ColSelect
                value={view.scatterX}
                onChange={(v) => edit({ scatterX: v })}
                columns={columns}
                autoLabel="(auto — most shared)"
                ariaLabel="Preferred X axis"
              />
            </Row>
            <Row label="Y axis">
              <ColSelect
                value={view.scatterY}
                onChange={(v) => edit({ scatterY: v })}
                columns={columns}
                autoLabel="(auto — most shared)"
                ariaLabel="Preferred Y axis"
              />
            </Row>
          </div>
        </Section>

        <Section
          title="Default view"
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
                onChange={(e) => edit({ specMode: e.target.value as 'fft' | 'welch' | 'waterfall' })}
              >
                <option value="fft">FFT magnitude</option>
                <option value="welch">Welch PSD</option>
                  <option value="waterfall">Waterfall FFT</option>
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
          title="Scatter datasheet line"
          advanced
          hint="Upload the datasheet CSV through Uploads, choose its point-ID column as the time column, then select that uploaded zone here. For the active scatter axes, only rows with valid values in both matching columns are joined; missing or empty columns are ignored."
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Row label="Datasheet zone">
              <ZoneSelect
                value={view.datasheetZone}
                onChange={(value) => edit({ datasheetZone: value })}
                zones={zones}
              />
            </Row>
            <Row label="Show by default">
              <input
                type="checkbox"
                checked={view.datasheetVisible}
                onChange={(event) => edit({ datasheetVisible: event.target.checked })}
              />
            </Row>
          </div>
        </Section>

        <Section
          title="Grid columns"
          wide
          hint="Preferred signal per plot in Time, Spectrum and XY. Auto uses selected test points first, then the active test. Signal selections in plot titles override these defaults for the session."
        >
          <div
            className="settings-grid"
            style={{
              display: 'grid',
              gap: 8,
              maxWidth: 560,
            }}
          >
            {Array.from({ length: 9 }, (_, i) => (
              <div className="feature-grid-cell" key={i}>
                <span>Plot {i + 1}</span>
                <ColSelect
                value={view.gridColumns[i] ?? ''}
                onChange={(v) => {
                  const next = [...view.gridColumns];
                  next[i] = v;
                  edit({ gridColumns: next });
                }}
                columns={columns}
                autoLabel={`(auto ${i + 1})`}
                width={170}
                ariaLabel={`Preferred signal for grid cell ${i + 1}`}
              />
              </div>
            ))}
          </div>
        </Section>

        <Section
          title="XY pairings"
          advanced
          hint="Each XY plot has its own X and Y signals, including stored time in seconds. Y auto follows the plot's grid signal above; X auto uses the first grid signal. Session selections in plot titles override these defaults."
        >
          <div
            className="settings-grid settings-grid-xy"
            style={{
              display: 'grid',
              gap: 8,
              maxWidth: 620,
            }}
          >
            {Array.from({ length: 9 }, (_, i) => (
              <div
                key={i}
                className={styles.xyCell}
              >
                <span className={styles.cellLabel}>Plot {i + 1}</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ fontSize: 10, color: '#626f83', width: 14 }}>X</span>
                  <ColSelect
                    value={view.xyXCols[i] ?? ''}
                    onChange={(v) => {
                      const next = [...view.xyXCols];
                      next[i] = v;
                      edit({ xyXCols: next });
                    }}
                    columns={xyColumns}
                    autoLabel="(auto)"
                    width={150}
                    ariaLabel={`Preferred X signal for XY cell ${i + 1}`}
                  />
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ fontSize: 10, color: '#626f83', width: 14 }}>Y</span>
                  <ColSelect
                    value={view.xyYCols[i] ?? ''}
                    onChange={(v) => {
                      const next = [...view.xyYCols];
                      next[i] = v;
                      edit({ xyYCols: next });
                    }}
                    columns={xyColumns}
                    autoLabel={`(same as cell ${i + 1})`}
                    width={150}
                    ariaLabel={`Preferred Y signal for XY cell ${i + 1}`}
                  />
                </div>
              </div>
            ))}
          </div>
        </Section>

        <Section
          title="Upload sample rate"
          advanced
          warning={rateError ? 'Check rate' : undefined}
          hint="Default for the import setup's fallback-rate field. You can change it for each upload; generated-time mode makes that upload's selected rate authoritative."
        >
          <Row label="Default rate">
            <NumericField
              className="input"
              style={{ width: 120 }}
              min={0}
              exclusiveMin
              allowEmpty
              unit="Hz"
              aria-label="Default rate (Hz)"
              placeholder="2048 (default)"
              value={view.uploadFsHz}
              onChange={(e) => edit({ uploadFsHz: e.target.value })}
            />
          </Row>
        </Section>
        </div>
      </div>
    </div>
  );
};

export default SettingsView;
