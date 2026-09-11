import type { AnalysisSession } from './analysisSession';
import { emptyPlotViewports } from '../utils/plotViewport';

export interface SavedSourceReference {
  name: string;
  id: string | null;
  revision?: string;
  test_points: { id: number; revision: string }[];
}
export interface AnalysisSource extends SavedSourceReference {
  status: string;
  columns?: string[];
  error?: string;
}
export interface AnalysisSourceCatalog { version: 1; sources: AnalysisSource[] }

export function retainPendingSourceReferences(sources: AnalysisSource[], saved?: SavedSourceReference[]): AnalysisSource[] {
  return sources.map(source => {
    const previous = saved?.find(item => item.id && item.id === source.id);
    return source.status !== 'ready' && previous
      ? { ...source, revision: previous.revision, test_points: previous.test_points } : source;
  });
}

const pointKey = (key: string): [string, number] | null => {
  const at = key.lastIndexOf(':');
  const id = Number(key.slice(at + 1));
  return at > 0 && Number.isInteger(id) ? [key.slice(0, at), id] : null;
};

export function referencedNames(session: AnalysisSession): string[] {
  return [...new Set([session.currentTest, ...session.selections.map(s => s.test),
    ...session.filterState.tpKeys.map(key => pointKey(key)?.[0] ?? '')].filter(Boolean))];
}

/** Snapshot only referenced sources. Null IDs fail closed on the next recovery;
 * never promote an unknown source to a name-only legacy reference. */
export function captureSessionSources(session: AnalysisSession, sources: AnalysisSource[]): SavedSourceReference[] {
  return referencedNames(session).map(name => {
    const source = sources.find(item => item.name === name);
    return { name, id: source?.error ? null : source?.id ?? null,
      revision: source?.revision, test_points: source?.test_points ?? [] };
  });
}

export interface SessionRecovery {
  session: AnalysisSession;
  messages: string[];
  legacy: boolean;
  needsReview: boolean;
}

/** Shared resolver for automatic recovery and session-file previews/reopening.
 * All name-based state is remapped before any plots can mount. */
export function resolveSessionSources(
  saved: AnalysisSession, sources: AnalysisSource[], reconnectLegacy = false
): SessionRecovery {
  sources = retainPendingSourceReferences(sources, saved.sources);
  const messages: string[] = [];
  const names = referencedNames(saved);
  const legacy = saved.sources === undefined && names.length > 0;
  const mapping = new Map<string, AnalysisSource>();
  const changed = new Set<string>();
  for (const name of names) {
    const refs = saved.sources?.filter(source => source.name === name) ?? [];
    const reference = refs.length === 1 ? refs[0] : undefined;
    const matches = legacy && reconnectLegacy
      ? sources.filter(source => source.name === name)
      : reference?.id ? sources.filter(source => source.id === reference.id) : [];
    const current = matches.length === 1 ? matches[0] : undefined;
    if (!current || current.error || !current.id) {
      messages.push(`${name}: ${legacy && !reconnectLegacy ? 'legacy name-only reference needs reconnection' :
        current?.error || (matches.length > 1 ? 'dataset identity is ambiguous' :
          sources.some(source => source.name === name) ? 'saved identity does not match the available test' : 'test is missing or in the trash')}.`);
      continue;
    }
    mapping.set(name, current);
    if (current.status !== 'ready') messages.push(`${current.name}: ${current.status}; saved selections will wait for the test to become ready.`);
    if (name !== current.name) messages.push(`${name} → ${current.name}: recovered the same dataset after rename or restore.`);
    if (current.revision && reference?.revision && current.revision !== reference.revision) {
      changed.add(name);
      messages.push(`${current.name}: sample data or variables changed; saved axis ranges were reset. Analysis uses current data.`);
    }
  }
  if (legacy && reconnectLegacy) messages.push('Legacy references were reconnected by name at your request. Original dataset identity could not be verified.');
  const resolvePoint = (test: string, tpId: number): string | null => {
    const current = mapping.get(test);
    if (!current) return null;
    if (current.status !== 'ready') return current.name;
    const point = current.test_points?.find(p => p.id === tpId);
    const previous = saved.sources?.find(s => s.name === test)?.test_points.find(p => p.id === tpId);
    if (!point || (!legacy && (!previous || point.revision !== previous.revision))) {
      messages.push(`${current.name} TP ${tpId}: ${point ? 'saved interval changed' : 'test point is missing'}; selection skipped.`);
      return null;
    }
    return current.name;
  };
  const selections = saved.selections.flatMap(selection => {
    const test = resolvePoint(selection.test, selection.tpId);
    return test ? [{ ...selection, test }] : [];
  });
  const tpKeys = saved.filterState.tpKeys.flatMap(key => {
    const point = pointKey(key);
    if (!point) return [];
    const test = resolvePoint(...point);
    return test ? [`${test}:${point[1]}`] : [];
  });
  const currentTest = mapping.get(saved.currentTest)?.name ?? '';
  const sourceChanged = changed.size > 0 || mapping.size !== names.length || selections.length !== saved.selections.length;
  const session: AnalysisSession = { ...saved, currentTest, selections,
    plotViewports: sourceChanged ? emptyPlotViewports() : saved.plotViewports,
    // Parameter filters retain their explicit column names. Missing variables
    // remain visible in controls instead of silently widening the selection.
    filterState: { ...saved.filterState, tpKeys },
    mainZoom: sourceChanged ? null : saved.mainZoom,
    timeZoom: sourceChanged ? null : saved.timeZoom,
    fullRange: sourceChanged ? null : saved.fullRange,
    timeYRanges: sourceChanged ? [] : saved.timeYRanges.map(item => {
      if (!item) return null;
      try {
        const [column, points] = JSON.parse(item.context) as [string, string[]];
        const remapped = points.map(point => {
          const [name, ...rest] = JSON.parse(point) as [string, ...unknown[]];
          const mapped = mapping.get(name);
          if (!mapped) throw new Error('Unavailable Y context');
          return JSON.stringify([mapped.name, ...rest]);
        }).sort();
        return { ...item, context: JSON.stringify([column, remapped]) };
      } catch { return null; }
    }),
    // Persist positional choices even when defaults/preferences changed since
    // the workspace was captured. Missing slots are shown as unavailable.
    plotsUserEdited: saved.plotConfigs.length > 0 || saved.plotsUserEdited,
    axesUserSet: !!(saved.xAxis || saved.yAxis) || saved.axesUserSet,
  };
  const allColumns = new Set(sources.flatMap(source => source.columns ?? []));
  const gridSources = (saved.viewMode === 'tp' || (saved.viewMode === 'spectrum' && saved.specSource === 'tp') ||
    (saved.viewMode === 'xy' && saved.xySource === 'tp')) ? selections.map(s => s.test) : [];
  const gridColumns = new Set(sources.filter(source => gridSources.includes(source.name) || source.name === currentTest)
    .flatMap(source => source.columns ?? []));
  saved.plotConfigs.forEach((column, index) => {
    if (column && !gridColumns.has(column)) messages.push(`Plot ${index + 1}: ${column} is unavailable. Its slot and filter settings were retained; choose a variable in Edit Plots.`);
  });
  for (const column of [saved.xAxis, saved.yAxis, ...saved.filterState.parameterFilters.map(f => f.column)]) {
    if (column && !allColumns.has(column)) messages.push(`${column}: comparison variable is unavailable.`);
  }
  if (saved.filterState.tpKeys.length && !tpKeys.length) messages.push('No saved test-point filters could be recovered; the comparison now shows all available test points.');
  session.sources = captureSessionSources(session, sources);
  return { session, messages: [...new Set(messages)], legacy: legacy && !reconnectLegacy,
    needsReview: mapping.size !== names.length || selections.length !== saved.selections.length || tpKeys.length !== saved.filterState.tpKeys.length };
}
