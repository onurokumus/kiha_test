import type uPlot from 'uplot';

export type PlotHoverMode = 'current' | 'all' | 'none';

/** Keep the earlier on/off preference when upgrading to three choices. */
export function parsePlotHoverMode(value: string | null): PlotHoverMode {
  if (value === 'current' || value === 'all' || value === 'none') return value;
  return value === 'false' ? 'none' : 'all';
}

export interface PlotHoverMember {
  plot: uPlot;
  xAxis: string;
  yAxis?: string;
  render: () => void;
  hide: () => void;
}

/** Coordinates, never ranges/data, are shared. Unlike variables use relative
 * pointer positions; equal axis identities share actual scientific values. */
export function createPlotHoverGroup() {
  const members = new Set<PlotHoverMember>();
  let mode: PlotHoverMode = 'all', frame = 0;
  let active: PlotHoverMember | null = null;
  const clear = () => {
    cancelAnimationFrame(frame); frame = 0; active = null;
    members.forEach(member => {
      member.hide();
      if (member.plot.root.isConnected) member.plot.setCursor({ left: -1, top: -1 }, false);
    });
  };
  const move = (source: PlotHoverMember) => {
    if (mode === 'none') return;
    active = source;
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (mode === 'none' || !active?.plot.root.isConnected) { clear(); return; }
      const origin = active, cursor = origin.plot.cursor;
      const bounds = origin.plot.over.getBoundingClientRect();
      const left = cursor.left ?? -1, top = cursor.top ?? -1;
      if (left < 0 || top < 0 || left > bounds.width || top > bounds.height || !bounds.width || !bounds.height) {
        clear(); return;
      }
      const x = origin.plot.posToVal(left, 'x'), y = origin.plot.posToVal(top, 'y');
      members.forEach(member => {
        if (mode === 'current' && member !== origin) { member.hide(); return; }
        const plot = member.plot, rect = plot.over.getBoundingClientRect();
        if (!plot.root.isConnected || rect.width <= 0 || rect.height <= 0) { member.hide(); return; }
        if (member !== origin) {
          const targetX = member.xAxis === origin.xAxis ? plot.valToPos(x, 'x') : left / bounds.width * rect.width;
          const targetY = member.yAxis && member.yAxis === origin.yAxis
            ? plot.valToPos(y, 'y') : top / bounds.height * rect.height;
          // fireHook=false also avoids republishing native cursor sync.
          // Scales, data, selection and zoom handlers stay untouched.
          plot.setCursor(targetX < 0 || targetX > rect.width || targetY < 0 || targetY > rect.height
            ? { left: -1, top: -1 } : { left: targetX, top: targetY }, false);
        }
        member.render();
      });
    });
  };
  return {
    clear, move,
    register(member: PlotHoverMember) {
      members.add(member);
      return () => { clear(); members.delete(member); };
    },
    setMode(value: PlotHoverMode) { mode = value; clear(); },
  };
}

export type PlotHoverGroup = ReturnType<typeof createPlotHoverGroup>;
