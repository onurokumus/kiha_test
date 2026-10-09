import { useEffect, useState } from 'react';
import { usePlotAppearance } from '../../hooks/usePlotAppearance';
import { resetPlotAppearance, setPlotAppearance } from '../../utils/plotAppearance';
import { parseFiniteNumber } from '../../utils/numericField';
import { NumericField } from './NumericField';
import styles from './PlotAppearanceControls.module.css';

const percentage = (scale: number) => String(Number((scale * 100).toPrecision(12)));

export function PlotAppearanceControls() {
  const { lineScale, pointScale } = usePlotAppearance();
  const [lineDraft, setLineDraft] = useState(() => percentage(lineScale));
  const [pointDraft, setPointDraft] = useState(() => percentage(pointScale));
  useEffect(() => { setLineDraft(percentage(lineScale)); }, [lineScale]);
  useEffect(() => { setPointDraft(percentage(pointScale)); }, [pointScale]);
  const update = (key: 'lineScale' | 'pointScale', draft: string) => {
    (key === 'lineScale' ? setLineDraft : setPointDraft)(draft);
    const value = parseFiniteNumber(draft);
    if (value !== null && value >= 50 && value <= 300) setPlotAppearance({ [key]: value / 100 });
  };
  return <section className={styles.section} aria-label="Plot appearance">
    <div className={styles.controls}>
      <label className={styles.control}>
        <span>Line</span>
          <NumericField min={50} max={300} value={lineDraft} unit="%" style={{ width: 92 }}
            aria-label="Line thickness" title="50–300%; default 100%. Applies to all line plots."
            onChange={event => update('lineScale', event.target.value)}
            onBlur={() => setLineDraft(percentage(lineScale))}
            onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); setLineDraft(percentage(lineScale)); } }} />
      </label>
      <label className={styles.control}>
        <span>Scatter</span>
          <NumericField min={50} max={300} value={pointDraft} unit="%" style={{ width: 92 }}
            aria-label="Scatter size" title="50–300%; default 100%. Applies to XY and overview markers."
            onChange={event => update('pointScale', event.target.value)}
            onBlur={() => setPointDraft(percentage(pointScale))}
            onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); setPointDraft(percentage(pointScale)); } }} />
      </label>
      <button type="button" className={styles.reset} aria-label="Reset appearance" title="Reset sizes"
        onClick={() => {
          resetPlotAppearance(); setLineDraft('100'); setPointDraft('100');
        }} disabled={lineScale === 1 && pointScale === 1 && lineDraft === '100' && pointDraft === '100'}>
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 6a5 5 0 1 1 0 5M3 2v4h4" /></svg>
      </button>
    </div>
  </section>;
}
