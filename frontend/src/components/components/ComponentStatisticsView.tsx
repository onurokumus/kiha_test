import { useEffect, useMemo, useState } from 'react';
import { isAbortError } from '../../services/api';
import { fetchComponentStatistics, type ComponentStatistics, type UsageSource } from '../../services/componentStatistics';
import { COMPONENT_KINDS, COMPONENT_LABELS, type ComponentKind } from '../../utils/components';
import styles from './ComponentStatisticsView.module.css';

const number = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString(undefined,
  value !== 0 && Math.abs(value) < .001 ? { maximumSignificantDigits: 3 } : { maximumFractionDigits: 3 });
const minutes = (seconds: number) => number(seconds / 60);
const range = (min: number | null, max: number | null) => min == null || max == null ? '—' : `${number(min)} – ${number(max)}`;

export default function ComponentStatisticsView({ onEditTest }: { onEditTest: (name: string) => void }) {
  const [data, setData] = useState<ComponentStatistics | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [generation, setGeneration] = useState(0);
  const [search, setSearch] = useState('');
  const [kind, setKind] = useState<ComponentKind | ''>('');
  const [selected, setSelected] = useState('');
  useEffect(() => {
    let current = true;
    const controller = new AbortController();
    setLoading(true); setError(''); setData(null);
    fetchComponentStatistics(controller.signal).then(result => {
      if (current) setData(result);
    }).catch(e => {
      if (current && !isAbortError(e)) setError(e instanceof Error ? e.message : String(e));
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [generation]);
  const items = useMemo(() => data?.components.filter(item => (!kind || item.kind === kind) &&
    `${item.name} ${item.id}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())) ?? [], [data, kind, search]);
  const component = data?.components.find(item => item.id === selected);
  const sources = data?.sources.filter(source => !component || source.component_ids[component.kind] === component.id) ?? [];

  const sourceRow = (source: UsageSource) => <tr key={source.name}>
    <th scope="row"><span>{source.name}</span><small>{source.rpm_column ? `RPM: ${source.rpm_column}` : 'RPM unassigned'}</small>
      <button className="btn" disabled={source.status !== 'ready'} onClick={() => onEditTest(source.name)}
        aria-label={`Edit component settings for ${source.name}`}>Edit settings</button></th>
    <td>{source.summary ? minutes(source.summary.running_seconds) : '—'}</td>
    <td>{source.summary ? range(source.summary.min_rpm, source.summary.max_rpm) : '—'}</td>
    <td>{source.issue ? <span className={styles.warning}>{source.issue}</span> : source.summary && <>
      <span>{number(source.summary.running_samples)} running / {number(source.summary.n_rows)} rows</span>
      <small>Stopped {minutes(source.summary.stopped_seconds)} min · Missing RPM {minutes(source.summary.missing_rpm_seconds)} min · Gaps {minutes(source.summary.gap_seconds)} min</small>
    </>}
      {(source.warnings.length > 0 || source.summary) && <details><summary>Source details{source.warnings.length ? ` · ${source.warnings.length} notice${source.warnings.length === 1 ? '' : 's'}` : ''}</summary>
        {source.warnings.map(warning => <p key={warning} className={styles.warning}>{warning}</p>)}
        <p>Dataset ID: {source.source_id ?? 'Unavailable (legacy)'}<br />Timing: {source.time_source ?? 'Unavailable'}</p>
        {source.summary && <p>Samples {number(source.summary.first_time_s)} – {number(source.summary.last_time_s)} s · {number(source.summary.fs_hz)} Hz<br />
          Mean {number(source.summary.mean_rpm)} RPM · Population SD {number(source.summary.sd_rpm)} RPM</p>}
      </details>}
    </td>
  </tr>;

  return <main className={styles.page} aria-label="Component statistics">
    <div className={styles.content}>
      <div className={styles.header}><div><h1>Component use</h1>
        <p>Measured runtime and shaft RPM across active tests, for each individual component.</p></div>
        <button className="btn" disabled={loading} onClick={() => setGeneration(value => value + 1)}>Refresh statistics</button>
      </div>
      <details className={styles.policy}><summary>Counting policy · active tests, RPM &gt; 0</summary>
        <p>Each finite positive RPM sample contributes 1 / sample rate seconds. Minutes = running seconds / 60.
          Zero and negative RPM are stopped; missing or infinite RPM and known acquisition gaps are excluded.
          Timestamp jumps add no runtime. The final observed sample contributes one sample period.</p>
        <p>Mean and population standard deviation are weighted by running seconds across tests with different sample rates.
          Ranges show measured operation, not component ratings. Every associated propeller, motor and ESC uses the test’s explicitly selected shaft RPM.</p>
        <p>Totals use complete current test rows once, independently of test points, plot selection and temporary filters.
          Saved data edits can change results. Trash and permanent deletion remove contributions; restore adds them back.
          These are current-library totals, not lifetime records. Refresh after changes from another window.</p>
      </details>
      {loading && <p role="status">Calculating component statistics…</p>}
      {error && <div role="alert" className={styles.warning}>Component statistics unavailable: {error}
        <button className="btn" onClick={() => setGeneration(value => value + 1)}>Retry statistics</button></div>}
      {data && <>
        <p className={styles.snapshot} role="status">Components: {data.components.length} · Active tests: {data.sources.length} · Excluded tests: {data.sources.filter(source => source.issue).length} · Refreshed {new Date(data.generated_at).toLocaleTimeString()}</p>
        {!data.components.length && <p>No components yet. Create and assign individual hardware in Uploads or Edit, then choose each test’s RPM column in Edit.</p>}
        {data.components.length > 0 && <>
          <div className={styles.filters}><label>Find component<input className="input" type="search" value={search} onChange={e => setSearch(e.target.value)} /></label>
            <label>Component type<select className="input" value={kind} onChange={e => setKind(e.target.value as ComponentKind | '')}>
              <option value="">All types</option>{COMPONENT_KINDS.map(value => <option key={value} value={value}>{COMPONENT_LABELS[value]}</option>)}
            </select></label></div>
          <div className={styles.tableScroll} role="region" aria-label="Component totals" tabIndex={0}><table>
            <caption>Runtime in minutes; RPM statistics include running samples only.</caption>
            <thead><tr><th scope="col">Component</th><th scope="col">Tests used / assigned</th><th scope="col">Runtime (min)</th><th scope="col">Mean RPM</th><th scope="col">SD RPM</th><th scope="col">RPM range</th></tr></thead>
            <tbody>{items.map(item => <tr key={item.id} data-component-id={item.id} aria-selected={selected === item.id}>
              <th scope="row"><button className={styles.nameButton} aria-pressed={selected === item.id}
                onClick={() => setSelected(selected === item.id ? '' : item.id)}>{item.name}</button><small>{COMPONENT_LABELS[item.kind]}</small></th>
              <td>{item.included_tests} / {item.assigned_tests}{item.included_tests < item.assigned_tests && <small className={styles.warning}>Incomplete coverage</small>}</td>
              <td>{item.included_tests ? minutes(item.summary.running_seconds) : '—'}</td><td>{number(item.summary.mean_rpm)}</td>
              <td>{number(item.summary.sd_rpm)}</td><td>{range(item.summary.min_rpm, item.summary.max_rpm)}</td>
            </tr>)}</tbody></table></div>
          {!items.length && <p>No components match this search.</p>}
        </>}
        {component && <section className={styles.ranges} aria-label="Operating ranges">
          <div className={styles.header}><h2>{component.name} · operating ranges</h2><button className="btn" onClick={() => setSelected('')}>Show all tests</button></div>
          <p>{COMPONENT_LABELS[component.kind]} · {component.id}</p>
          <div className={styles.buckets}>{data.rpm_range_lower_bounds.map((lower, index, bounds) => <div key={lower}>
            <span>{index === 0 ? '> 0' : number(lower)}{index + 1 < bounds.length ? ` to < ${number(bounds[index + 1])}` : '+'} RPM</span>
            <strong>{component.included_tests ? minutes(component.summary.ranges_seconds[index]) : '—'} min</strong>
          </div>)}</div>
          <p>Excluded from runtime: missing RPM {minutes(component.summary.missing_rpm_seconds)} min; acquisition gaps {minutes(component.summary.gap_seconds)} min.
            Stopped: {minutes(component.summary.stopped_seconds)} min. Tests without usable data have unknown runtime.</p>
        </section>}
        <section aria-label="Test contributions"><h2>{component ? `Tests assigned to ${component.name}` : 'Test contributions and coverage'}</h2>
          <p>Select a component above to inspect its ranges and sources. Edit settings to correct associations or select RPM.</p>
          {sources.length ? <div className={styles.tableScroll} role="region" aria-label="Source contributions" tabIndex={0}><table>
            <thead><tr><th scope="col">Test / RPM source</th><th scope="col">Runtime (min)</th><th scope="col">RPM range</th><th scope="col">Coverage</th></tr></thead>
            <tbody>{sources.map(sourceRow)}</tbody></table></div> : <p>No active tests{component ? ' assigned to this component' : ''}.</p>}
        </section>
      </>}
    </div>
  </main>;
}
