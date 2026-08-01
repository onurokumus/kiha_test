import uPlot from 'uplot';

export interface XRangeHighlight {
  start: number;
  end: number;
  color: string;
}

/** Draw translucent, color-coded x-range bars behind a uPlot's traces.
 *
 * The getter keeps the plugin current while the uPlot instance is reused.
 * Using drawClear also means the bars track local wheel zoom and drag pan
 * immediately, before the new server window is fetched. */
export function xRangeHighlightsPlugin(
  getHighlights: () => readonly XRangeHighlight[]
): uPlot.Plugin {
  return {
    hooks: {
      drawClear: (u) => {
        const xMin = u.scales.x.min;
        const xMax = u.scales.x.max;
        if (xMin == null || xMax == null || xMax <= xMin) return;

        const { ctx, bbox } = u;
        const plotLeft = bbox.left;
        const plotRight = bbox.left + bbox.width;
        const plotTop = bbox.top;
        const barHeight = bbox.height;
        const edgeWidth = Math.max(1, uPlot.pxRatio);

        ctx.save();
        ctx.beginPath();
        ctx.rect(plotLeft, plotTop, bbox.width, barHeight);
        ctx.clip();

        for (const highlight of getHighlights()) {
          if (
            !Number.isFinite(highlight.start) ||
            !Number.isFinite(highlight.end) ||
            highlight.end <= highlight.start ||
            highlight.end < xMin ||
            highlight.start > xMax
          ) {
            continue;
          }

          const start = Math.max(highlight.start, xMin);
          const end = Math.min(highlight.end, xMax);
          const left = Math.max(plotLeft, u.valToPos(start, 'x', true));
          const right = Math.min(plotRight, u.valToPos(end, 'x', true));
          if (!Number.isFinite(left) || !Number.isFinite(right) || right < left) continue;

          const width = Math.max(edgeWidth, right - left);
          ctx.fillStyle = highlight.color;
          ctx.globalAlpha = 0.14;
          ctx.fillRect(left, plotTop, width, barHeight);

          ctx.strokeStyle = highlight.color;
          ctx.globalAlpha = 0.9;
          ctx.lineWidth = edgeWidth;
          ctx.beginPath();
          ctx.moveTo(left, plotTop);
          ctx.lineTo(left, plotTop + barHeight);
          ctx.moveTo(Math.min(right, plotRight), plotTop);
          ctx.lineTo(Math.min(right, plotRight), plotTop + barHeight);
          ctx.stroke();
        }

        ctx.restore();
      },
    },
  };
}
