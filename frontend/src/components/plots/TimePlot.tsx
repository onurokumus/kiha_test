import React, { useEffect, useRef, useState, useMemo } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { fetchFiltered, isAbortError } from '../../services/api';
import { FilterSpec, SelectedTestPoint, TimePlotConfig } from '../../types';
import { noSelect } from '../../constants/styles';
import { AXIS_STYLE, TP_SYNC_KEY } from '../../constants/uplotTheme';
import { xPanZoomPlugin } from '../../utils/uplotPanZoom';
import { FilterUi } from '../../constants/filters';
import { FilterRow } from '../controls/FilterRow';
import styles from './TimePlot.module.css';

const FILTER_COLOR = '#dcdcaa';

interface TimePlotProps {
  cfg: TimePlotConfig;
  selectedTPs: SelectedTestPoint[];
  hiddenTPs: Set<string>;
  isExpanded: boolean;
  onToggleExpand: () => void;
  zoomDomain: [number, number] | null;
  onZoomChange: (domain: [number, number]) => void;
  onZoomReset?: () => void;
  /** THIS plot's DSP filter — dashed overlay per TP, computed over each TP's
   *  own range in its own test. Mode bar broadcasts; expanded row edits one. */
  filterSpec?: FilterSpec | null;
  filterUi?: FilterUi;
  onFilterUiChange?: (patch: Partial<FilterUi>) => void;
  fs?: number | null;
  isEditMode?: boolean;
  allConfigs?: TimePlotConfig[];
  onConfigChange?: (newKey: string) => void;
}

interface OverlayTrace {
  label: string;
  color: string;
  t: number[];
  y: (number | null)[];
}

/** Filtered overlay of one TP: 1 segment (raw) or 2 (envelope min/max). */
interface FilteredTpOverlay {
  color: string;
  name: string;
  segs: { t: number[]; y: (number | null)[] }[];
  warning: boolean;
}

/** TP-overlay time plot: one line per selected test point, relative time
 *  from TP start. uPlot mode 2 (facets) — each series keeps its own time
 *  array, so TPs of different lengths overlay without resampling. */
export const TimePlot: React.FC<TimePlotProps> = ({
  cfg,
  selectedTPs,
  hiddenTPs,
  isExpanded,
  onToggleExpand,
  zoomDomain,
  onZoomChange,
  onZoomReset,
  filterSpec = null,
  filterUi,
  onFilterUiChange,
  fs = null,
  isEditMode = false,
  allConfigs = [],
  onConfigChange,
}) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [fovers, setFovers] = useState<FilteredTpOverlay[]>([]);
  const [ferror, setFerror] = useState('');
  const [fbusy, setFbusy] = useState(false);
  // per-cell filter row visibility, toggled by the ≈ header button
  const [showFilter, setShowFilter] = useState(false);

  const visibleTPs = selectedTPs.filter((s) => !hiddenTPs.has(s.id));
  const tpFingerprint = visibleTPs
    .map((s) => `${s.id}:${s.tp.start_s}:${s.endS}`)
    .join('|');

  const traces: OverlayTrace[] = useMemo(() => {
    const out: OverlayTrace[] = [];
    selectedTPs.forEach((s) => {
      if (hiddenTPs.has(s.id)) return;
      const trace = s.traces[cfg.key];
      if (!trace) return;
      const t: number[] = [];
      const y: (number | null)[] = [];
      trace.t.forEach((tv, i) => {
        if (tv === null) return; // time itself should never be NaN; skip if so
        t.push(tv);
        y.push(trace.y[i]);
      });
      if (t.length > 0)
        out.push({ label: `${s.name} · ${s.test}`, color: s.color, t, y });
    });
    return out;
  }, [selectedTPs, hiddenTPs, cfg.key]);

  // Track the chart area's box; uPlot needs explicit pixel dimensions.
  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setBox({ w: el.clientWidth, h: el.clientHeight });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Filtered overlay per visible TP: /filter over the TP's own absolute
  // range in its own test, shifted to relative time. Fetched once per
  // (spec, TP set, column) — zoom stays client-side like the raw traces.
  useEffect(() => {
    if (!filterSpec || visibleTPs.length === 0) {
      setFovers([]);
      setFerror('');
      return;
    }
    let dead = false;
    const controller = new AbortController();
    const px = chartRef.current?.clientWidth || 800;
    const timer = window.setTimeout(() => {
      setFbusy(true);
      Promise.all(
        visibleTPs.map(async (s): Promise<FilteredTpOverlay | null> => {
          try {
            const w = await fetchFiltered(
              s.test, [cfg.key], filterSpec, s.tp.start_s, s.endS, px,
              controller.signal
            );
            const sr = w.series[cfg.key];
            if (!sr) return null;
            const ys: (number | null)[][] =
              w.mode === 'envelope'
                ? [(sr as { max: (number | null)[] }).max, (sr as { min: (number | null)[] }).min]
                : [sr as (number | null)[]];
            const segs = ys.map((yArr) => {
              const t: number[] = [];
              const y: (number | null)[] = [];
              w.t.forEach((tv, i) => {
                if (tv === null) return;
                t.push(tv - s.tp.start_s);
                y.push(yArr[i]);
              });
              return { t, y };
            });
            return { color: s.color, name: s.name, segs, warning: !!w.boundary_warning };
          } catch (e) {
            if (isAbortError(e)) throw e;
            console.error(`filter failed for ${s.id}/${cfg.key}:`, e);
            setFerror(String(e instanceof Error ? e.message : e));
            return null;
          }
        })
      )
        .then((res) => {
          if (dead) return;
          const ok = res.filter((r): r is FilteredTpOverlay => r !== null);
          setFovers(ok);
          if (ok.length === visibleTPs.length) setFerror('');
        })
        .catch((e) => {
          if (!dead && !isAbortError(e)) {
            setFovers([]);
            setFerror(String(e instanceof Error ? e.message : e));
          }
        })
        .finally(() => !dead && setFbusy(false));
    }, 300);
    return () => {
      dead = true;
      window.clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterSpec, tpFingerprint, cfg.key]);

  useEffect(() => {
    const el = chartRef.current;
    plotRef.current?.destroy();
    plotRef.current = null;
    if (!el || traces.length === 0 || box.w < 40 || box.h < 40) return;

    const opts: uPlot.Options = {
      mode: 2,
      width: box.w,
      height: box.h,
      scales: {
        x: {
          time: false,
          ...(zoomDomain ? { range: [zoomDomain[0], zoomDomain[1]] as [number, number] } : {}),
        },
        y: {},
      },
      axes: [{ ...AXIS_STYLE }, { ...AXIS_STYLE, scale: 'y' }],
      legend: { show: isExpanded, live: true },
      cursor: {
        drag: { x: true, y: false },
        sync: { key: TP_SYNC_KEY, scales: ['x', null] },
      },
      plugins: [xPanZoomPlugin(onZoomChange)],
      series: [
        {},
        ...traces.map(
          (trace) =>
            ({
              label: trace.label,
              stroke: trace.color,
              width: 1.5,
              spanGaps: false,
              facets: [
                { scale: 'x', auto: true },
                { scale: 'y', auto: true },
              ],
            }) as uPlot.Series
        ),
        ...fovers.flatMap((f) =>
          f.segs.map(
            (_, i) =>
              ({
                label: `${f.name} filt${f.segs.length > 1 ? (i === 0 ? ' max' : ' min') : ''}`,
                stroke: f.color,
                width: 1.5,
                dash: [6, 4],
                spanGaps: false,
                facets: [
                  { scale: 'x', auto: true },
                  { scale: 'y', auto: true },
                ],
              }) as uPlot.Series
          )
        ),
      ],
      hooks: {
        setSelect: [
          (u) => {
            if (u.select.width > 10) {
              const t0 = u.posToVal(u.select.left, 'x');
              const t1 = u.posToVal(u.select.left + u.select.width, 'x');
              u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
              onZoomChange([t0, t1]);
            }
          },
        ],
      },
    };

    const data = [
      null,
      ...traces.map((tr) => [tr.t, tr.y]),
      ...fovers.flatMap((f) => f.segs.map((seg) => [seg.t, seg.y])),
    ] as unknown as uPlot.AlignedData;
    const u = new uPlot(opts, data, el);
    plotRef.current = u;

    // The legend renders inside the container and eats chart height.
    if (isExpanded) {
      const legend = u.root.querySelector('.u-legend') as HTMLElement | null;
      const legendH = legend?.offsetHeight ?? 0;
      if (legendH > 0) {
        u.setSize({ width: box.w, height: Math.max(60, box.h - legendH) });
      }
    }

    return () => {
      plotRef.current?.destroy();
      plotRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [traces, fovers, zoomDomain, box, isExpanded]);

  const containerClass = `${styles.plotContainer} ${
    isExpanded ? styles.plotContainerExpanded : styles.plotContainerCollapsed
  }`;

  const buttonClass = `${styles.expandButton} ${
    isExpanded ? styles.expandButtonExpanded : styles.expandButtonCollapsed
  }`;

  return (
    <div className={containerClass} style={{ ...noSelect }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 4,
          position: 'relative',
        }}
      >
        <div style={{ fontSize: 12, color: '#c0c0c0' }}>{cfg.label}</div>
        {showFilter && !isExpanded && filterUi && onFilterUiChange && (
          <div
            style={{
              position: 'absolute',
              left: 0,
              right: 58,
              top: -4,
              zIndex: 6,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              flexWrap: 'wrap',
              background: '#2d2d2d',
              border: '1px solid #3c3c3c',
              borderRadius: 3,
              padding: '2px 4px',
              boxShadow: '0 2px 8px rgba(0, 0, 0, 0.5)',
            }}
          >
            <FilterRow
              ui={filterUi}
              onChange={onFilterUiChange}
              fs={fs}
              title="filter for THIS plot only"
            />
          </div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          {fbusy && <span style={{ fontSize: 10, color: '#569cd6' }}>⟳</span>}
          {filterUi && onFilterUiChange && (
            <button
              onClick={() => setShowFilter((v) => !v)}
              className={buttonClass}
              title={
                ferror ||
                (filterSpec ? 'filter active — click to edit' : 'filter this plot')
              }
            >
              <span
                className={styles.expandButtonIcon}
                style={{
                  color: ferror ? '#f48771' : filterSpec ? FILTER_COLOR : undefined,
                }}
              >
                ≈
              </span>
            </button>
          )}
          <button onClick={onToggleExpand} className={buttonClass}>
            <span className={styles.expandButtonIcon}>{isExpanded ? '▪' : '▣'}</span>
          </button>
        </div>
        {isEditMode && !isExpanded && allConfigs.length > 0 && (
          <select
            value={cfg.key}
            onChange={(e) => onConfigChange?.(e.target.value)}
            style={{
              position: 'absolute',
              left: 0,
              top: -2,
              background: '#1e1e1e',
              color: '#e0e0e0',
              border: '1px solid #3c3c3c',
              borderRadius: 3,
              padding: '2px 6px',
              fontSize: 11,
              cursor: 'pointer',
              outline: 'none',
              fontFamily: 'Segoe UI, sans-serif',
              zIndex: 5,
            }}
          >
            {allConfigs.map((config) => (
              <option key={config.key} value={config.key}>
                {config.label}
              </option>
            ))}
          </select>
        )}
      </div>
      {isExpanded && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 4 }}>
          {filterUi && onFilterUiChange && (
            <>
              <span style={{ fontSize: 11, color: '#909090' }}>filter:</span>
              <FilterRow
                ui={filterUi}
                onChange={onFilterUiChange}
                fs={fs}
                title="filter for THIS plot only"
              />
            </>
          )}
          {fovers.length > 0 && <span className="badge">filtered overlay (dashed)</span>}
          {fovers.some((f) => f.warning) && (
            <span style={{ fontSize: 10, color: '#dcdcaa' }}>
              ⚠ range touches data edge — filter transients possible
            </span>
          )}
          {ferror && <span style={{ color: '#f48771', fontSize: 10 }}>{ferror}</span>}
        </div>
      )}
      <div
        ref={chartRef}
        style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}
        onDoubleClick={onZoomReset}
        title={traces.length === 0 ? 'Select test points on the scatter plot' : undefined}
      >
        {traces.length === 0 && (
          <div style={{ color: '#555', fontSize: 11, padding: 8 }}>no selection</div>
        )}
      </div>
    </div>
  );
};
