import styles from './FilterDisplayToggle.module.css';

/** Display-only control: its state must never be part of a DSP request key. */
export function FilterDisplayToggle({ variable, showOriginal, onChange }: {
  variable: string;
  showOriginal: boolean;
  onChange: (show: boolean) => void;
}) {
  return (
    <button type="button" className={styles.toggle}
      aria-label={`Show original alongside filtered for ${variable}`}
      aria-pressed={showOriginal}
      title={showOriginal
        ? 'Original: thin dashed trace. Filtered: solid trace. Click to show filtered only.'
        : 'Show original stored data alongside the filtered trace. Does not change the filter.'}
      onClick={() => onChange(!showOriginal)}>
      <span className={styles.swatch} aria-hidden="true" />Original
    </button>
  );
}
