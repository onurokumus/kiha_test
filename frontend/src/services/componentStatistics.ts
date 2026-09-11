import { getJson } from './api';
import type { ComponentIds, HardwareComponent } from '../utils/components';

export interface UsageSummary {
  running_seconds: number; stopped_seconds: number; missing_rpm_seconds: number; gap_seconds: number;
  running_samples: number; stopped_samples: number; missing_rpm_samples: number; gap_samples: number;
  mean_rpm: number | null; sd_rpm: number | null; min_rpm: number | null; max_rpm: number | null;
  ranges_seconds: number[]; fs_hz?: number; n_rows?: number; first_time_s?: number; last_time_s?: number;
}
export interface UsageSource {
  name: string; status: string; source_id: string | null; component_ids: Partial<ComponentIds>;
  rpm_column: string | null; warnings: string[]; issue: string | null; summary: UsageSummary | null;
  association_revision?: number; rpm_revision?: number; time_source?: string;
}
export interface ComponentStatistics {
  version: 1; method: string; policy: string; generated_at: string; rpm_range_lower_bounds: number[];
  components: Array<HardwareComponent & { assigned_tests: number; included_tests: number; summary: UsageSummary }>;
  sources: UsageSource[];
}
export async function fetchComponentStatistics(signal?: AbortSignal): Promise<ComponentStatistics> {
  const result = await getJson<ComponentStatistics>('/component-statistics', signal);
  if (!result || result.version !== 1 || result.policy !== 'active-tests-positive-rpm' ||
      !Array.isArray(result.components) || !Array.isArray(result.sources) || !Array.isArray(result.rpm_range_lower_bounds)) {
    throw new Error('Unsupported component statistics response. Update the application and retry.');
  }
  return result;
}
