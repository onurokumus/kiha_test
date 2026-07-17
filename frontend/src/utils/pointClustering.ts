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
 * In PTT this is overlap disambiguation, not a perf optimization: TP scatters
 * are ~tens of points, but two TPs flown at the same condition land on the
 * same pixel and become indistinguishable without the cluster badge.
 *
 * @param data - Array of scatter data points with x, y coordinates
 * @param xMin - Minimum x value in current zoom
 * @param xMax - Maximum x value in current zoom
 * @param yMin - Minimum y value in current zoom
 * @param yMax - Maximum y value in current zoom
 * @param chartWidth - Chart width in pixels
 * @param chartHeight - Chart height in pixels
 * @param clusterRadius - Radius in pixels for clustering (default: 20)
 * @param minPointsForCluster - Minimum points to form a cluster (default: 2)
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
  minPointsForCluster: number = 2
): ClusteredData => {
  // Too few points to ever form a cluster
  if (data.length < minPointsForCluster) {
    return {
      clusters: [],
      individualPoints: data,
    };
  }

  // Convert data coordinates to pixel coordinates. A zero range (constant
  // column => every point shares that coordinate) must not divide to NaN —
  // those points genuinely stack, which is exactly when clustering matters.
  const xRange = xMax - xMin || 1;
  const yRange = yMax - yMin || 1;

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
 * Determines if clustering should be enabled.
 *
 * FMS gated this on point count (>500) and zoom level because clustering was
 * a perf optimization for 10k-point datasets. PTT scatters are small, so it
 * is always on when there is anything to overlap: clustering is pixel-based,
 * so zooming in still separates near-neighbors naturally, while truly
 * coincident points keep their count badge at ANY zoom (no zoom cutoff —
 * identical points never separate, a cutoff would re-hide them). The user
 * toggle in App is the only off switch.
 */
export const shouldEnableClustering = (pointCount: number): boolean => {
  return pointCount >= 2;
};
