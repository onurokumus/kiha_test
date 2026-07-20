// Single source of truth for the main Recharts scatter's geometry (bug 4.7).
//
// SCATTER_MARGIN is the chart's `margin` prop. PLOT_INSET is the pixel inset of
// the actual plotting area inside the chart container — which is NOT the same as
// the margin: Recharts renders the y-axis (line + tick labels, ~15 px) to the
// RIGHT of margin.left, so the left inset is margin.left + that axis width.
//
// Every pixel<->data conversion must use THESE (zoom-at-cursor in
// useMainPlotZoom, clustering geometry in MainScatterPlot). Previously the
// derived numbers (70 = left+right inset, 50 = top+bottom inset, and the 50/10
// cursor offsets) were hand-copied as magic literals in three places, so a
// margin tweak silently broke zoom-at-cursor and clustering.
export const SCATTER_MARGIN = { top: 10, right: 20, bottom: 40, left: 35 };

// Rendered y-axis width added by Recharts beyond margin.left.
const Y_AXIS_WIDTH = 15;

export const PLOT_INSET = {
  left: SCATTER_MARGIN.left + Y_AXIS_WIDTH, // 50
  right: SCATTER_MARGIN.right, // 20
  top: SCATTER_MARGIN.top, // 10
  bottom: SCATTER_MARGIN.bottom, // 40
};

// Total pixels the plot area is inset horizontally / vertically from the container.
export const PLOT_INSET_X = PLOT_INSET.left + PLOT_INSET.right; // 70
export const PLOT_INSET_Y = PLOT_INSET.top + PLOT_INSET.bottom; // 50
