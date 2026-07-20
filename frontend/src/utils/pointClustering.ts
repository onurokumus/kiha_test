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

  // Uniform-grid spatial index so neighbor lookup is O(n) instead of O(n²)
  // (perf 2.5): bucket every point into a `clusterRadius`-sized cell keyed
  // "col,row". Two points within `clusterRadius` (Euclidean) differ by at most
  // one cell on each axis, so a point's neighbors can only live in its own cell
  // or the 8 around it — the exact same radius test below still decides
  // membership, so the greedy output is byte-for-byte identical to the old
  // full O(n²) scan (NaN pixels floor to a "NaN,NaN" cell and their distance
  // test is NaN <= r === false, exactly as before → they stay individual).
  const cellSize = clusterRadius > 0 ? clusterRadius : 1;
  const grid = new Map<string, number[]>();
  const cellKey = (px: number, py: number) =>
    `${Math.floor(px / cellSize)},${Math.floor(py / cellSize)}`;
  for (let i = 0; i < pointsWithPixels.length; i++) {
    const { px, py } = pointsWithPixels[i];
    const key = cellKey(px, py);
    const bucket = grid.get(key);
    if (bucket) bucket.push(i);
    else grid.set(key, [i]);
  }

  // Track which points have been clustered
  const clustered = new Set<number>();
  const clusters: ClusterPoint[] = [];

  // Simple clustering: for each unclustered point, find nearby points
  for (let i = 0; i < pointsWithPixels.length; i++) {
    if (clustered.has(i)) continue;

    const current = pointsWithPixels[i];
    const nearbyIndices: number[] = [i];

    // Candidate neighbors from the 3×3 cell neighborhood only. Collect then
    // sort ascending so membership is visited in the same index order the old
    // linear `j = i + 1 …` scan used (cluster center is an order-independent
    // average, but keeping the order makes the two implementations identical).
    const cellX = Math.floor(current.px / cellSize);
    const cellY = Math.floor(current.py / cellSize);
    const candidates: number[] = [];
    for (let cx = cellX - 1; cx <= cellX + 1; cx++) {
      for (let cy = cellY - 1; cy <= cellY + 1; cy++) {
        const bucket = grid.get(`${cx},${cy}`);
        if (bucket) for (const j of bucket) if (j > i) candidates.push(j);
      }
    }
    candidates.sort((a, b) => a - b);

    for (const j of candidates) {
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
