import { FC } from 'react';
import { TestInfo } from '../../types';

/**
 * The <option> list shared by every ready-test picker (SelectedPointsPanel,
 * SplitView, EditView): non-ready tests are disabled and get a "(status)"
 * suffix. Rendered INSIDE each view's own <select> so per-view select styling
 * (SelectStyle vs .input, widths, labels) is untouched — this only de-dupes the
 * identical option-rendering + disabled logic (bug 6.6).
 */
export const TestOptions: FC<{ tests: TestInfo[] }> = ({ tests }) => (
  <>
    {tests.map((t) => (
      <option key={t.name} value={t.name} disabled={t.status !== 'ready'}>
        {t.name}
        {t.status !== 'ready' ? ` (${t.status})` : ''}
      </option>
    ))}
  </>
);
