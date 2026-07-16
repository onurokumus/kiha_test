import { ScatterDataPoint } from '../types';

export interface ClusterPoint {
  points: ScatterDataPoint[];
  centerX: number;
  centerY: number;
  count: number;
}

export interface ClusteredData {
  clusters: ClusterPoint[];
  individualPoints: ScatterDataPoint[];
}

/**
 * Clusters points based on pixel distance.
 * Points within clusterRadius pixels are grouped together.
 *
 * @param data - Array of scatter data points with x, y coordinates
 * @param xMin - Minimum x value in current zoom
 * @param xMax - Maximum x value in current zoom
 * @param yMin - Minimum y value in current zoom
 * @param yMax - Maximum y value in current zoom
 * @param chartWidth - Chart width in pixels
 * @param chartHeight - Chart height in pixels
 * @param clusterRadius - Radius in pixels for clustering (default: 20)
 * @param minPointsForCluster - Minimum points to form a cluster (default: 3)
 */
export const clusterPoints = (
  data: ScatterDataPoint[],
  xMin: number,
  xMax: number,
  yMin: number,
  yMax: number,
  chartWidth: number,
  chartHeight: number,
  clusterRadius: number = 20,
  minPointsForCluster: number = 3
): ClusteredData => {
  // Early exit for small datasets
  if (data.length < minPointsForCluster * 2) {
    return {
      clusters: [],
      individualPoints: data,
    };
  }

  // Convert data coordinates to pixel coordinates
  const xRange = xMax - xMin;
  const yRange = yMax - yMin;

  const pointsWithPixels = data.map((point) => ({
    point,
    px: ((point.x - xMin) / xRange) * chartWidth,
    py: chartHeight - ((point.y - yMin) / yRange) * chartHeight,
  }));

  // Track which points have been clustered
  const clustered = new Set<number>();
  const clusters: ClusterPoint[] = [];

  // Simple clustering: for each unclustered point, find nearby points
  for (let i = 0; i < pointsWithPixels.length; i++) {
    if (clustered.has(i)) continue;

    const current = pointsWithPixels[i];
    const nearbyIndices: number[] = [i];

    // Find all points within cluster radius
    for (let j = i + 1; j < pointsWithPixels.length; j++) {
      if (clustered.has(j)) continue;

      const other = pointsWithPixels[j];
      const distance = Math.sqrt(
        Math.pow(current.px - other.px, 2) + Math.pow(current.py - other.py, 2)
      );

      if (distance <= clusterRadius) {
        nearbyIndices.push(j);
      }
    }

    // If enough points for a cluster, create it
    if (nearbyIndices.length >= minPointsForCluster) {
      // Mark all as clustered
      nearbyIndices.forEach((idx) => clustered.add(idx));

      // Calculate cluster center (average of all points in data space)
      const clusterPoints = nearbyIndices.map((idx) => pointsWithPixels[idx].point);
      const centerX = clusterPoints.reduce((sum, p) => sum + p.x, 0) / clusterPoints.length;
      const centerY = clusterPoints.reduce((sum, p) => sum + p.y, 0) / clusterPoints.length;

      clusters.push({
        points: clusterPoints,
        centerX,
        centerY,
        count: clusterPoints.length,
      });
    }
  }

  // Collect individual points (not clustered)
  const individualPoints = pointsWithPixels
    .filter((_, idx) => !clustered.has(idx))
    .map((p) => p.point);

  return {
    clusters,
    individualPoints,
  };
};

/**
 * Determines if clustering should be enabled based on zoom level and point count.
 *
 * @param pointCount - Total number of points
 * @param zoomLevel - Current zoom level (1 = no zoom, >1 = zoomed in)
 */
export const shouldEnableClustering = (pointCount: number, zoomLevel: number): boolean => {
  // Enable clustering if:
  // 1. More than 500 points AND zoom level < 4
  // 2. More than 1500 points AND zoom level < 8
  // 3. More than 3000 points always (unless heavily zoomed in)

  if (pointCount > 3000 && zoomLevel < 15) return true;
  if (pointCount > 1500 && zoomLevel < 8) return true;
  if (pointCount > 500 && zoomLevel < 4) return true;

  return false;
};

/**
 * Calculate zoom level from current bounds.
 */
export const calculateZoomLevel = (
  currentXMin: number,
  currentXMax: number,
  currentYMin: number,
  currentYMax: number,
  initialXMin: number,
  initialXMax: number,
  initialYMin: number,
  initialYMax: number
): number => {
  const initialXRange = initialXMax - initialXMin;
  const initialYRange = initialYMax - initialYMin;
  const currentXRange = currentXMax - currentXMin;
  const currentYRange = currentYMax - currentYMin;

  const xZoom = initialXRange / currentXRange;
  const yZoom = initialYRange / currentYRange;

  return Math.max(xZoom, yZoom);
};
