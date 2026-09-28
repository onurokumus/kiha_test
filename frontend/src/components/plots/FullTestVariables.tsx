import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { TimePlotConfig } from '../../types';
import { fullTestVariableColor } from '../../utils/fullTestVariables';
import { SearchableSelect } from '../controls/SearchableSelect';
import styles from './FullTestVariables.module.css';

interface Props {
  configs: TimePlotConfig[];
  allConfigs: TimePlotConfig[];
  showingOverlay: boolean;
  onPrimaryChange?: (column: string) => void;
  onAdditionalColumnsChange?: (columns: string[]) => void;
}

/** The same swatches accompany selection and the folded-away trace legend. */
export function FullTestVariables({ configs, allConfigs,
  showingOverlay, onPrimaryChange, onAdditionalColumnsChange }: Props) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0, width: 300, maxHeight: 360 });
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  const primary = configs[0];
  const extras = configs.slice(1).map(config => config.key);
  const selected = new Set(configs.map(config => config.key));
  const options = allConfigs.map(config => ({ value: config.key, label: config.label,
    color: fullTestVariableColor(config.key, allConfigs), keywords: [config.key] }));
  const legendEntries = configs.map(config => ({ label: config.label,
    color: fullTestVariableColor(config.key, allConfigs) }));
  const canAdd = configs.length < 6 && allConfigs.some(config => !selected.has(config.key));
  const place = useCallback(() => {
    const bounds = trigger.current?.getBoundingClientRect();
    if (!bounds) return;
    const width = Math.min(320, document.documentElement.clientWidth - 16);
    const height = Math.min(370, document.documentElement.clientHeight - 16);
    const top = Math.max(8, Math.min(bounds.bottom + 6, document.documentElement.clientHeight - height - 8));
    setPosition({ left: Math.max(8, Math.min(bounds.left, document.documentElement.clientWidth - width - 8)),
      top, width, maxHeight: document.documentElement.clientHeight - top - 8 });
  }, []);
  const close = useCallback((focus = false) => {
    setOpen(false);
    if (focus) trigger.current?.focus({ preventScroll: true });
  }, []);
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      const target = event.target as Node;
      if (!panel.current?.contains(target) && !trigger.current?.contains(target)) close();
    };
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    document.addEventListener('pointerdown', outside);
    document.addEventListener('focusin', outside);
    panel.current?.focus({ preventScroll: true });
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('focusin', outside);
    };
  }, [open, place, close]);
  if (!primary) return null;
  return <div className={styles.controls} data-full-test-variables>
      <SearchableSelect value={primary.key} onChange={key => {
        onPrimaryChange?.(key);
      }} options={options.map(option => ({ ...option, disabled: extras.includes(option.value) }))}
        ariaLabel="Plot variable" title="Change the primary variable" searchPlaceholder="Search plot variables..."
        optionNoun="variable" appearance="title" className={styles.primary} />
      <SearchableSelect value="" onChange={key => {
        onAdditionalColumnsChange?.([...extras, key]);
        // SearchableSelect normally restores focus to + after choosing. At
        // capacity that button becomes disabled; keep focus on the adjacent,
        // persistent legend instead of dropping it when the menu unmounts.
        const canAddAfter = configs.length + 1 < 6 &&
          allConfigs.some(config => !selected.has(config.key) && config.key !== key);
        if (!canAddAfter) trigger.current?.focus({ preventScroll: true });
      }}
        options={options.filter(option => !selected.has(option.value))}
        ariaLabel="Add variable to plot" title={configs.length >= 6 ? 'Maximum 6 variables per plot' : 'Add variable to this plot'}
        searchPlaceholder="Search variables to add..." optionNoun="variable" appearance="plot" size="compact"
        className={styles.add} disabled={!canAdd}
        triggerContent={<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>} />
    <button ref={trigger} type="button" className={styles.legendButton}
      aria-label={`Variable legend for ${configs.map(config => config.label).join(', ')}`}
      aria-expanded={open} aria-controls={open ? id : undefined} aria-haspopup="dialog"
      data-tooltip={open ? undefined : legendEntries.map(entry => entry.label).join(' · ')}
      data-tooltip-legend={open ? undefined : JSON.stringify(legendEntries)}
      onClick={() => { if (open) close(); else { place(); setOpen(true); } }}>
      <span className={styles.dots} aria-hidden="true">{configs.slice(0, 3).map(config =>
        <i key={config.key} className={styles.dot} style={{ background: fullTestVariableColor(config.key, allConfigs) }} />)}</span>
      <span className={styles.count}>{configs.length}</span>
      <svg width="9" height="9" viewBox="0 0 12 12" fill="none" stroke="currentColor" aria-hidden="true"><path d="m2 4 4 4 4-4" /></svg>
    </button>
    {open && createPortal(<div ref={panel} id={id} className={styles.legend} style={position} role="dialog"
      tabIndex={-1} aria-label="Plot variables and colors" onKeyDown={event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); }
        if (event.key === 'Tab') {
          const buttons = panel.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)');
          const first = buttons?.[0];
          const last = buttons?.[buttons.length - 1];
          const leavesPanel = event.shiftKey
            ? event.target === panel.current || event.target === first
            : event.target === (last ?? panel.current);
          if (leavesPanel) {
            // This panel is portaled to the document end. Start native Tab
            // navigation at its trigger so adjacent plot actions retain their
            // usual order in both directions; do not trap focus in the panel.
            trigger.current?.focus({ preventScroll: true });
            close();
          }
        }
      }}>
      <div className={styles.heading}>{configs.length} variable{configs.length === 1 ? '' : 's'}</div>
      {configs.map((config, index) => <div key={config.key} className={styles.row}>
        <div className={styles.variable}>
          {!showingOverlay && <i className={styles.line} aria-hidden="true" style={{ borderColor: fullTestVariableColor(config.key, allConfigs) }} />}
          <span className={styles.name} title={config.label}>{config.label}</span>
        </div>
        {showingOverlay && <div className={styles.samples}>
          <span className={styles.sample}><i className={`${styles.line} ${styles.original}`} aria-hidden="true" style={{ borderColor: fullTestVariableColor(config.key, allConfigs) }} />Original</span>
          <span className={styles.sample}><i className={styles.line} aria-hidden="true" style={{ borderColor: fullTestVariableColor(config.key, allConfigs) }} />Filtered</span>
        </div>}
        {index > 0 && <button type="button" className={styles.remove} aria-label={`Remove ${config.label} from plot`}
          title={`Remove ${config.label}`} onClick={() => { onAdditionalColumnsChange?.(extras.filter(key => key !== config.key)); panel.current?.focus({ preventScroll: true }); }}>×</button>}
      </div>)}
      <p className={styles.hint}>Shared Y axis</p>
    </div>, document.body)}
  </div>;
}
