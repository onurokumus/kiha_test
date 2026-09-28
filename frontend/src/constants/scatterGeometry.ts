export const SCATTER_MARGIN = { top: 14, right: 18, bottom: 14, left: 8 };

// Explicit axis sizes shared by rendering, clustering and navigation.
export const Y_AXIS_WIDTH = 82;
export const X_AXIS_HEIGHT = 44;

export const PLOT_INSET = {
  left: SCATTER_MARGIN.left + Y_AXIS_WIDTH,
  right: SCATTER_MARGIN.right,
  top: SCATTER_MARGIN.top,
  bottom: SCATTER_MARGIN.bottom + X_AXIS_HEIGHT,
};

// Total pixels the plot area is inset horizontally / vertically from the container.
export const PLOT_INSET_X = PLOT_INSET.left + PLOT_INSET.right;
export const PLOT_INSET_Y = PLOT_INSET.top + PLOT_INSET.bottom;
