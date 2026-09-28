import type uPlot from 'uplot';
import { AXIS_STYLE } from '../constants/uplotTheme';

/** Fit titles to the canvas instead of centering them on the smaller data area. */
export function axisTitlesPlugin(title: string): uPlot.Plugin {
  const lines: string[] = [];
  const xLines: string[] = [];
  let xTitle = '';
  const lineHeight = 16;
  return {
    opts: (_plot, options) => {
      const context = document.createElement('canvas').getContext('2d')!;
      context.font = AXIS_STYLE.labelFont;
      const wrap = (text: string, available: number, result: string[]) => {
        let line = '';
        for (const character of text) {
          if (line && context.measureText(line + character).width > Math.max(16, available)) {
            result.push(line);
            line = '';
          }
          line += character;
        }
        if (line) result.push(line);
      };
      // Expanded charts reserve a 32px legend row in TimePlot.module.css.
      wrap(title, options.height - (options.legend?.show ? 32 : 0) - 8, lines);
      const axis = options.axes?.[1];
      if (axis) {
        axis.labelSize = lines.length * lineHeight;
      }
      const xAxis = options.axes?.[0];
      if (typeof xAxis?.label === 'string' && xAxis.label) {
        xTitle = xAxis.label;
        wrap(xTitle, options.width - lines.length * lineHeight - 8, xLines);
        xAxis.labelSize = xLines.length * lineHeight;
      }
    },
    hooks: {
      // Suppress only the native draw; retain the complete label for exports.
      drawClear: [plot => {
        plot.axes[1].label = '';
        if (xTitle) plot.axes[0].label = '';
      }],
      draw: [plot => {
        plot.axes[1].label = title;
        if (xTitle) plot.axes[0].label = xTitle;
      }],
      drawAxes: [plot => {
        const context = plot.ctx;
        const ratio = context.canvas.width / plot.width;
        context.save();
        context.scale(ratio, ratio);
        context.font = AXIS_STYLE.labelFont;
        context.fillStyle = AXIS_STYLE.stroke;
        context.textAlign = 'center';
        context.textBaseline = 'middle';
        const titleWidth = lines.length * lineHeight;
        xLines.forEach((line, index) => {
          context.fillText(line, (plot.width + titleWidth) / 2,
            plot.height - (xLines.length - index - 0.5) * lineHeight);
        });
        context.translate(titleWidth / 2, plot.height / 2);
        context.rotate(-Math.PI / 2);
        lines.forEach((line, index) => {
          context.fillText(line, 0, (index - (lines.length - 1) / 2) * lineHeight);
        });
        context.restore();
      }],
    },
  };
}
