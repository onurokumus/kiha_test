import { useEffect, useRef, useState } from 'react';
import {
  deleteFormulaRecipe,
  deleteTest,
  editTest,
  fetchFormulaRecipes,
  patchUserMeta,
  previewFormulas,
  renameTest,
  saveFormulaRecipe,
} from '../../services/api';
import { useUnsavedChanges } from '../../hooks/useUnsavedChanges';
import {
  DerivedVariableProvenance,
  EditOps,
  FormulaPreview,
  FormulaRecipe,
  FormulaSpec,
  TestInfo,
  TestMeta,
} from '../../types';
import { SearchableSelect } from '../controls/SearchableSelect';
import { TestSelect } from '../controls/TestSelect';
import { useConfirm } from '../feedback/confirm';

interface Props {
  test: string;
  meta: TestMeta;
  tests: TestInfo[];
  onTestChange: (test: string) => Promise<boolean | void>;
  /** A rebuild was scheduled — parent should poll and refresh when ready. */
  onRebuildStarted: () => void;
  /** Test was renamed (navigate to the new name) or deleted (empty string). */
  onTestGone: (newName: string) => void;
  /** user_meta saved — parent should refresh meta. */
  onMetaSaved: () => void;
  /** Reports whether any edit draft has not been saved or applied. */
  onDirtyChange?: (isDirty: boolean) => void;
  /** Reports an accepted mutation request that must finish before navigation. */
  onBusyChange?: (isBusy: boolean) => void;
}

interface MetaRow {
  key: string;
  value: string;
}

interface TrimDraft {
  start: string;
  end: string;
}

interface FormulaDraft extends FormulaSpec {
  id: number;
}

interface FormulaNotice {
  kind: 'info' | 'error' | 'success';
  text: string;
}

interface FormulaDraftIssue {
  text: string;
  draftIndex?: number;
  field?: 'name' | 'expression';
}

const FORMULA_INSERTS = [
  { label: '+', text: ' + ' },
  { label: '−', text: ' - ' },
  { label: '×', text: ' * ' },
  { label: '÷', text: ' / ' },
  { label: 'power', text: ' ** ' },
  { label: 'sqrt', text: 'sqrt()', caret: 5 },
  { label: 'abs', text: 'abs()', caret: 4 },
  { label: 'min', text: 'min(, )', caret: 4 },
  { label: 'max', text: 'max(, )', caret: 4 },
  { label: 'IF', text: 'IF(, , )', caret: 3 },
] as const;

// Keep the editor aligned with the API's defensive batch ceiling. Normal
// workflows are expected to use roughly 10-20 rows, but larger saved recipes
// remain editable without being truncated.
const MAX_FORMULA_DRAFTS = 64;

let nextFormulaDraftId = 1;

function createFormulaDraft(spec?: FormulaSpec): FormulaDraft {
  return {
    id: nextFormulaDraftId++,
    name: spec?.name ?? '',
    expression: spec?.expression ?? '',
    replace: Boolean(spec?.replace),
  };
}

function activeFormulaSpecs(drafts: FormulaDraft[]): FormulaSpec[] {
  return drafts
    .filter((draft) => draft.name.trim() || draft.expression.trim())
    .map((draft) => ({
      name: draft.name.trim(),
      expression: draft.expression.trim(),
      ...(draft.replace ? { replace: true } : {}),
    }));
}

function formulaReferences(expression: string): string[] {
  const references: string[] = [];
  const pattern = /\{([^{}]+)\}/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(expression)) !== null) {
    const name = match[1].trim();
    if (name && !references.includes(name)) references.push(name);
  }
  return references;
}

function formulaDraftIssue(
  drafts: FormulaDraft[],
  columns: string[],
  timeColumn: string
): FormulaDraftIssue | null {
  const formulas = drafts.flatMap((draft, draftIndex) => {
    const name = draft.name.trim();
    const expression = draft.expression.trim();
    return name || expression
      ? [{ name, expression, replace: Boolean(draft.replace), draftIndex }]
      : [];
  });
  if (!formulas.length) {
    return {
      text: 'Add at least one equation.',
      draftIndex: 0,
      field: 'name',
    };
  }
  if (formulas.length > MAX_FORMULA_DRAFTS) {
    return {
      text: `An equation batch cannot exceed ${MAX_FORMULA_DRAFTS} equations.`,
    };
  }

  const names = new Set<string>();
  const targetPositions = new Map(
    formulas.map((formula, index) => [formula.name, index])
  );
  for (const [index, formula] of formulas.entries()) {
    const prefix = `Equation ${formula.draftIndex + 1}`;
    if (!formula.name) {
      return {
        text: `${prefix}: enter a result column.`,
        draftIndex: formula.draftIndex,
        field: 'name',
      };
    }
    if (!formula.expression) {
      return {
        text: `${prefix}: enter an expression.`,
        draftIndex: formula.draftIndex,
        field: 'expression',
      };
    }
    if (formula.name === timeColumn) {
      return {
        text: `${prefix}: the time column '${timeColumn}' is protected.`,
        draftIndex: formula.draftIndex,
        field: 'name',
      };
    }
    if (names.has(formula.name)) {
      return {
        text: `${prefix}: result name '${formula.name}' appears more than once.`,
        draftIndex: formula.draftIndex,
        field: 'name',
      };
    }
    if (columns.includes(formula.name) && !formula.replace) {
      return {
        text: `${prefix}: '${formula.name}' already exists. Enable replace to overwrite it.`,
        draftIndex: formula.draftIndex,
        field: 'name',
      };
    }
    const references = formulaReferences(formula.expression);
    if (references.includes(formula.name)) {
      return {
        text: `${prefix}: '${formula.name}' cannot reference itself. Saved equations must be reproducible.`,
        draftIndex: formula.draftIndex,
        field: 'expression',
      };
    }
    const laterTarget = references.find(
      (reference) => (targetPositions.get(reference) ?? -1) > index
    );
    if (laterTarget) {
      return {
        text: `${prefix}: '${formula.name}' uses later result '${laterTarget}'. Move '${laterTarget}' earlier.`,
        draftIndex: formula.draftIndex,
        field: 'expression',
      };
    }
    names.add(formula.name);
  }
  return null;
}

function formatPreviewValue(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return 'NaN';
  const magnitude = Math.abs(value);
  if ((magnitude > 0 && magnitude < 0.001) || magnitude >= 1_000_000) {
    return value.toExponential(3);
  }
  return Number(value.toPrecision(7)).toLocaleString();
}

function appliedEquations(meta: TestMeta): DerivedVariableProvenance[] {
  const value = meta.derived_variables;
  const records: DerivedVariableProvenance[] = Array.isArray(value)
    ? value
    : value && typeof value === 'object'
      ? Object.entries(value).map(([name, record]) => ({ ...record, name }))
      : [];
  const columns = new Set(meta.columns);
  const seen = new Set<string>();
  return records.flatMap((record) => {
    if (
      !record ||
      typeof record.name !== 'string' ||
      typeof record.expression !== 'string' ||
      !columns.has(record.name) ||
      seen.has(record.name)
    ) {
      return [];
    }
    seen.add(record.name);
    const dependencies = Array.isArray(record.dependencies)
      ? record.dependencies.filter(
          (dependency): dependency is string => typeof dependency === 'string'
        )
      : [];
    const missingDependencies = dependencies.filter(
      (dependency) => !columns.has(dependency)
    );
    return [
      {
        ...record,
        dependencies,
        missing_dependencies: missingDependencies,
      },
    ];
  });
}

function dependentEquationNames(
  target: string,
  equations: DerivedVariableProvenance[]
): string[] {
  const affected = new Set([target]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const equation of equations) {
      if (
        affected.has(equation.name) ||
        !(equation.dependencies ?? []).some((dependency) => affected.has(dependency))
      ) {
        continue;
      }
      affected.add(equation.name);
      changed = true;
    }
  }
  return equations
    .map((equation) => equation.name)
    .filter((name) => name !== target && affected.has(name));
}

function formatFormulaTimestamp(value?: string | null): string {
  if (!value) return '';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleString();
}

function metaRows(userMeta: TestMeta['user_meta']): MetaRow[] {
  return Object.entries(userMeta ?? {}).map(([key, value]) => ({
    key,
    value,
  }));
}

function sameMetaRows(left: MetaRow[], right: MetaRow[]): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

const NAN_POLICY_HELP: Record<string, string> = {
  keep_gaps: 'NaN stays as gaps (lines break)',
  zero_fill: 'replace NaN with 0',
  interpolate: 'linear interpolation across gaps',
};

/** Edit tab: free-form test metadata plus the destructive rebuild
 *  operations (column rename/drop, NaN policy, trim, test rename/delete).
 *  Rebuilds run server-side like an ingest; the test is unavailable
 *  until it flips back to ready. */
export default function EditView({
  test,
  meta,
  tests,
  onTestChange,
  onRebuildStarted,
  onTestGone,
  onMetaSaved,
  onDirtyChange,
  onBusyChange,
}: Props) {
  const confirmAction = useConfirm();
  const [status, setStatus] = useState('');
  const [pendingAction, setPendingAction] = useState('');
  const columnSignature = meta.columns.join('\u0000');
  const firstColumn = meta.columns[0] ?? '';

  useEffect(() => {
    onBusyChange?.(Boolean(pendingAction));
    return () => onBusyChange?.(false);
  }, [onBusyChange, pendingAction]);

  // -- derived-variable equations + reusable recipes --
  const [formulaDrafts, setFormulaDrafts] = useState<FormulaDraft[]>(() => [
    createFormulaDraft(),
  ]);
  const [activeFormulaId, setActiveFormulaId] = useState(
    formulaDrafts[0].id
  );
  const [selectedVariable, setSelectedVariable] = useState(
    meta.columns[0] ?? ''
  );
  const [formulaPreview, setFormulaPreview] =
    useState<FormulaPreview | null>(null);
  const [formulaNotice, setFormulaNotice] =
    useState<FormulaNotice | null>(null);
  const formulaRowListRef = useRef<HTMLDivElement | null>(null);
  const nameRefs = useRef<Record<number, HTMLInputElement | null>>({});
  const expressionRefs = useRef<Record<number, HTMLInputElement | null>>({});
  const moveUpRefs = useRef<Record<number, HTMLButtonElement | null>>({});
  const moveDownRefs = useRef<Record<number, HTMLButtonElement | null>>({});
  const removeRefs = useRef<Record<number, HTMLButtonElement | null>>({});
  const [recipes, setRecipes] = useState<FormulaRecipe[]>([]);
  const [selectedRecipe, setSelectedRecipe] = useState('');
  const [recipeName, setRecipeName] = useState('');
  const [editingDerivedName, setEditingDerivedName] = useState<string | null>(null);
  const savedEquations = appliedEquations(meta);

  useEffect(() => {
    let cancelled = false;
    fetchFormulaRecipes()
      .then((result) => {
        if (!cancelled) setRecipes(result.recipes);
      })
      .catch((error) => {
        if (!cancelled) {
          setFormulaNotice({
            kind: 'error',
            text: `Could not load formula recipes: ${
              error instanceof Error ? error.message : String(error)
            }`,
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const blank = createFormulaDraft();
    setFormulaDrafts([blank]);
    setActiveFormulaId(blank.id);
    setSelectedVariable(firstColumn);
    setEditingDerivedName(null);
    setFormulaPreview(null);
    setFormulaNotice(null);
  }, [test, columnSignature, firstColumn]);

  // -- free-form metadata --
  const [rows, setRows] = useState<MetaRow[]>([]);
  const [savedRows, setSavedRows] = useState<MetaRow[]>([]);
  useEffect(() => {
    const nextRows = metaRows(meta.user_meta);
    setRows(nextRows);
    setSavedRows(nextRows);
  }, [test, meta.user_meta]);

  const saveMeta = async () => {
    if (pendingAction) return;
    const userMeta: Record<string, string> = {};
    rows.forEach((r) => {
      if (r.key.trim()) userMeta[r.key.trim()] = r.value;
    });
    try {
      setPendingAction('Saving metadata');
      await patchUserMeta(test, userMeta);
      const nextRows = Object.entries(userMeta).map(([key, value]) => ({
        key,
        value,
      }));
      setRows(nextRows);
      setSavedRows(nextRows);
      setStatus('metadata saved');
      onMetaSaved();
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    } finally {
      setPendingAction('');
    }
  };

  // -- column operations --
  const dataColumns = meta.columns.filter((c) => c !== meta.time_column);
  const [renames, setRenames] = useState<Record<string, string>>({});
  const [drops, setDrops] = useState<Set<string>>(new Set());
  useEffect(() => {
    setRenames({});
    setDrops(new Set());
  }, [test, columnSignature]);

  // -- NaN policy / trim --
  const [nanPolicy, setNanPolicy] = useState(meta.nan_policy ?? 'keep_gaps');
  const [savedNanPolicy, setSavedNanPolicy] = useState(
    meta.nan_policy ?? 'keep_gaps'
  );
  const tStart = meta.t_start ?? 0;
  const tEnd = tStart + meta.duration_s;
  const [trim0, setTrim0] = useState(String(tStart));
  const [trim1, setTrim1] = useState(String(tEnd));
  const [savedTrim, setSavedTrim] = useState<TrimDraft>({
    start: String(tStart),
    end: String(tEnd),
  });
  useEffect(() => {
    const nextNanPolicy = meta.nan_policy ?? 'keep_gaps';
    const nextTrim = {
      start: String(meta.t_start ?? 0),
      end: String((meta.t_start ?? 0) + meta.duration_s),
    };
    setNanPolicy(nextNanPolicy);
    setSavedNanPolicy(nextNanPolicy);
    setTrim0(nextTrim.start);
    setTrim1(nextTrim.end);
    setSavedTrim(nextTrim);
  }, [test, meta.nan_policy, meta.t_start, meta.duration_s]);

  const formulaSpecs = activeFormulaSpecs(formulaDrafts);
  const formulaIssue = formulaDraftIssue(
    formulaDrafts,
    meta.columns,
    meta.time_column
  );
  const formulaValidation = formulaIssue?.text ?? '';
  const editingOriginal = editingDerivedName
    ? savedEquations.find((record) => record.name === editingDerivedName)
    : undefined;
  const formulasDirty = editingOriginal
    ? formulaSpecs.length !== 1 ||
      formulaSpecs[0].name !== editingOriginal.name ||
      formulaSpecs[0].expression !== editingOriginal.expression
    : formulaSpecs.length > 0;
  const cascadingDependents = Array.from(
    new Set(
      formulaSpecs.flatMap((spec) =>
        dependentEquationNames(spec.name, savedEquations)
      )
    )
  ).filter((name) => !formulaSpecs.some((spec) => spec.name === name));

  const clearFormulaDrafts = () => {
    const blank = createFormulaDraft();
    setFormulaDrafts([blank]);
    setActiveFormulaId(blank.id);
    setEditingDerivedName(null);
    setSelectedRecipe('');
    setRecipeName('');
    setFormulaPreview(null);
    setFormulaNotice(null);
  };

  const editAppliedEquation = async (record: DerivedVariableProvenance) => {
    if (pendingAction) return;
    if (
      formulasDirty &&
      !(await confirmAction({
        title: `Edit saved equation '${record.name}'?`,
        description: 'The current unsaved equation draft will be replaced.',
        detail: 'Applied test data will not change until you preview and rebuild.',
        confirmLabel: 'Load equation',
        tone: 'warning',
      }))
    ) {
      return;
    }

    const draft = createFormulaDraft({
      name: record.name,
      expression: record.expression,
      replace: true,
    });
    const dependents = dependentEquationNames(record.name, savedEquations);
    const missing = record.missing_dependencies ?? [];
    setFormulaDrafts([draft]);
    setActiveFormulaId(draft.id);
    setEditingDerivedName(record.name);
    setSelectedRecipe('');
    setRecipeName('');
    setFormulaPreview(null);
    setFormulaNotice({
      kind: missing.length ? 'error' : 'info',
      text: missing.length
        ? `Loaded '${record.name}'. Repair missing reference${
            missing.length === 1 ? '' : 's'
          }: ${missing.join(', ')}.`
        : dependents.length
          ? `Loaded '${record.name}'. Applying it will also recalculate: ${dependents.join(
              ', '
            )}.`
          : `Loaded '${record.name}' for editing. Its result name is locked.`,
    });
    window.requestAnimationFrame(() => {
      const input = expressionRefs.current[draft.id];
      input?.focus();
      input?.select();
    });
  };

  const updateFormulaDraft = (
    id: number,
    patch: Partial<Omit<FormulaDraft, 'id'>>
  ) => {
    setFormulaDrafts((current) =>
      current.map((draft) => (draft.id === id ? { ...draft, ...patch } : draft))
    );
    setFormulaPreview(null);
    setFormulaNotice(null);
  };

  const focusFormulaName = (id: number, resetListScroll = false) => {
    window.requestAnimationFrame(() => {
      if (resetListScroll && formulaRowListRef.current) {
        formulaRowListRef.current.scrollTop = 0;
        formulaRowListRef.current.scrollLeft = 0;
      }
      const input = nameRefs.current[id];
      input?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      input?.focus();
    });
  };

  const showFormulaValidation = (): boolean => {
    if (!formulaIssue) return false;
    setFormulaNotice({ kind: 'error', text: formulaIssue.text });
    const draft = formulaDrafts[formulaIssue.draftIndex ?? 0];
    if (!draft) return true;
    setActiveFormulaId(draft.id);
    window.requestAnimationFrame(() => {
      const input =
        formulaIssue.field === 'expression'
          ? expressionRefs.current[draft.id]
          : nameRefs.current[draft.id];
      input?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      input?.focus();
    });
    return true;
  };

  const addFormulaDraft = () => {
    if (formulaDrafts.length >= MAX_FORMULA_DRAFTS) {
      setFormulaNotice({
        kind: 'error',
        text: `An equation batch cannot exceed ${MAX_FORMULA_DRAFTS} rows.`,
      });
      return;
    }
    const draft = createFormulaDraft();
    setFormulaDrafts((current) => [...current, draft]);
    setActiveFormulaId(draft.id);
    setFormulaPreview(null);
    setFormulaNotice(null);
    focusFormulaName(draft.id);
  };

  const removeFormulaDraft = (id: number) => {
    const removedIndex = formulaDrafts.findIndex((draft) => draft.id === id);
    if (removedIndex < 0) return;
    const remaining = formulaDrafts.filter((draft) => draft.id !== id);
    const nextDrafts = remaining.length ? remaining : [createFormulaDraft()];
    const nextActive = nextDrafts[Math.min(removedIndex, nextDrafts.length - 1)];
    const preservedActive = remaining.find(
      (draft) => draft.id === activeFormulaId
    );
    setFormulaDrafts(nextDrafts);
    setActiveFormulaId(preservedActive?.id ?? nextActive.id);
    delete nameRefs.current[id];
    delete expressionRefs.current[id];
    delete moveUpRefs.current[id];
    delete moveDownRefs.current[id];
    delete removeRefs.current[id];
    setFormulaPreview(null);
    setFormulaNotice(null);
    if (preservedActive) {
      window.requestAnimationFrame(() => {
        const button = removeRefs.current[nextActive.id];
        button?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        button?.focus();
      });
    } else {
      focusFormulaName(nextActive.id);
    }
  };

  const moveFormulaDraft = (id: number, offset: -1 | 1) => {
    const index = formulaDrafts.findIndex((draft) => draft.id === id);
    const targetIndex = index + offset;
    if (index < 0 || targetIndex < 0 || targetIndex >= formulaDrafts.length) {
      return;
    }
    const reordered = [...formulaDrafts];
    [reordered[index], reordered[targetIndex]] = [
      reordered[targetIndex],
      reordered[index],
    ];
    setFormulaDrafts(reordered);
    setActiveFormulaId(id);
    setFormulaPreview(null);
    setFormulaNotice(null);
    window.requestAnimationFrame(() => {
      const canContinue =
        offset === -1
          ? targetIndex > 0
          : targetIndex < formulaDrafts.length - 1;
      const button = canContinue
        ? offset === -1
          ? moveUpRefs.current[id]
          : moveDownRefs.current[id]
        : offset === -1
          ? moveDownRefs.current[id]
          : moveUpRefs.current[id];
      button?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      button?.focus();
    });
  };

  const insertFormulaText = (text: string, caretOffset = text.length) => {
    const targetId =
      formulaDrafts.find((draft) => draft.id === activeFormulaId)?.id ??
      formulaDrafts[0]?.id;
    if (targetId === undefined) return;

    const input = expressionRefs.current[targetId];
    const draft = formulaDrafts.find((candidate) => candidate.id === targetId);
    if (!draft) return;
    const start = input?.selectionStart ?? draft.expression.length;
    const end = input?.selectionEnd ?? start;
    const nextExpression =
      draft.expression.slice(0, start) + text + draft.expression.slice(end);
    updateFormulaDraft(targetId, { expression: nextExpression });
    setActiveFormulaId(targetId);

    window.requestAnimationFrame(() => {
      const nextInput = expressionRefs.current[targetId];
      if (!nextInput) return;
      const caret = start + caretOffset;
      nextInput.focus();
      nextInput.setSelectionRange(caret, caret);
    });
  };

  const runFormulaPreview = async () => {
    if (pendingAction) return;
    if (showFormulaValidation()) return;
    try {
      setPendingAction('Previewing equations');
      const result = await previewFormulas(test, formulaSpecs);
      const cascadedCount = Math.max(
        0,
        result.formulas.length - formulaSpecs.length
      );
      setFormulaPreview(result);
      setFormulaNotice({
        kind: 'success',
        text: `${result.formulas.length} equation${
          result.formulas.length === 1 ? '' : 's'
        } validated on ${result.sample_size} sample rows.${
          cascadedCount
            ? ` Includes ${cascadedCount} saved dependent equation${
                cascadedCount === 1 ? '' : 's'
              }.`
            : ''
        }`,
      });
    } catch (error) {
      setFormulaPreview(null);
      setFormulaNotice({
        kind: 'error',
        text: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setPendingAction('');
    }
  };

  const loadRecipe = async () => {
    const recipe = recipes.find((candidate) => candidate.name === selectedRecipe);
    if (!recipe) return;
    if (
      formulasDirty &&
      !(await confirmAction({
        title: `Load recipe '${recipe.name}'?`,
        description: 'The current unsaved equation draft will be replaced by this recipe.',
        detail: 'Saved recipes and test data will not be changed.',
        confirmLabel: 'Load recipe',
        tone: 'warning',
      }))
    ) {
      return;
    }
    const nextDrafts = recipe.formulas.length
      ? recipe.formulas.map(createFormulaDraft)
      : [createFormulaDraft()];
    setFormulaDrafts(nextDrafts);
    setActiveFormulaId(nextDrafts[0].id);
    setEditingDerivedName(null);
    setRecipeName(recipe.name);
    setFormulaPreview(null);
    setFormulaNotice({
      kind: 'info',
      text: `Loaded '${recipe.name}'. Preview it against this test before applying.`,
    });
    focusFormulaName(nextDrafts[0].id, true);
  };

  const saveRecipe = async () => {
    if (pendingAction) return;
    const name = recipeName.trim();
    if (!name) {
      setFormulaNotice({ kind: 'error', text: 'Enter a recipe name.' });
      return;
    }
    if (showFormulaValidation()) return;
    const replacesRecipe = recipes.some((recipe) => recipe.name === name);
    if (
      replacesRecipe &&
      !(await confirmAction({
        title: `Replace recipe '${name}'?`,
        description: 'The saved equation set will be replaced by the current draft.',
        confirmLabel: 'Replace recipe',
        tone: 'warning',
      }))
    ) {
      return;
    }
    try {
      setPendingAction(`Saving recipe '${name}'`);
      const saved = await saveFormulaRecipe(name, formulaSpecs);
      setRecipes((current) =>
        [...current.filter((recipe) => recipe.name !== saved.name), saved].sort(
          (left, right) => left.name.localeCompare(right.name)
        )
      );
      setSelectedRecipe(saved.name);
      setRecipeName(saved.name);
      setFormulaNotice({
        kind: 'success',
        text: `Saved recipe '${saved.name}'.`,
      });
    } catch (error) {
      setFormulaNotice({
        kind: 'error',
        text: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setPendingAction('');
    }
  };

  const removeRecipe = async () => {
    if (pendingAction || !selectedRecipe) return;
    if (
      !(await confirmAction({
        title: `Delete recipe '${selectedRecipe}'?`,
        description: 'This removes the saved recipe. The equation draft currently shown is unchanged.',
        confirmLabel: 'Delete recipe',
        tone: 'danger',
      }))
    ) {
      return;
    }
    const name = selectedRecipe;
    try {
      setPendingAction(`Deleting recipe '${name}'`);
      await deleteFormulaRecipe(name);
      setRecipes((current) =>
        current.filter((recipe) => recipe.name !== name)
      );
      setSelectedRecipe('');
      if (recipeName === name) setRecipeName('');
      setFormulaNotice({
        kind: 'success',
        text: `Deleted recipe '${name}'. The equation draft is unchanged.`,
      });
    } catch (error) {
      setFormulaNotice({
        kind: 'error',
        text: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setPendingAction('');
    }
  };

  const runRebuild = async (
    ops: EditOps,
    what: string,
    onApplied: () => void,
    discardedDrafts: string[] = [],
    additionalDetail = ''
  ) => {
    if (pendingAction) return;
    const draftWarning = discardedDrafts.length
      ? ` Unsaved ${discardedDrafts.join(', ')} drafts will also be discarded.`
      : '';
    if (
      !(await confirmAction({
        title: 'Rebuild test data?',
        description: `${what}.${draftWarning}`,
        detail: `This rewrites the test's data and pyramid. The test will be unavailable until the rebuild finishes.${
          additionalDetail ? ` ${additionalDetail}` : ''
        }`,
        confirmLabel: 'Rebuild test',
        tone: 'warning',
      }))
    ) {
      return;
    }
    try {
      setPendingAction(what);
      await editTest(test, ops);
      onApplied();
      setStatus(`${what} — rebuilding…`);
      onRebuildStarted();
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    } finally {
      setPendingAction('');
    }
  };

  const applyFormulas = () => {
    if (showFormulaValidation()) return;
    const replacements = formulaSpecs.filter((formula) => formula.replace).length;
    const summary = editingDerivedName
      ? `update saved derived variable '${editingDerivedName}'`
      : [
          `materialize ${formulaSpecs.length} derived variable${
            formulaSpecs.length === 1 ? '' : 's'
          }`,
          replacements
            ? `replace ${replacements} existing column${
                replacements === 1 ? '' : 's'
              }`
            : '',
        ]
          .filter(Boolean)
          .join(' + ');
    runRebuild(
      { formulas: formulaSpecs },
      summary,
      resetDrafts,
      [
        metadataDirty ? 'metadata' : '',
        renameDirty ? 'test-name' : '',
        nanPolicyDirty ? 'NaN-policy' : '',
        trimDirty ? 'trim' : '',
        columnsDirty ? 'column-change' : '',
      ].filter(Boolean),
      cascadingDependents.length
        ? `Saved dependent equations will be recalculated in dependency order: ${cascadingDependents.join(
            ', '
          )}.`
        : ''
    );
  };

  const applyColumns = () => {
    const rename: Record<string, string> = {};
    Object.entries(renames).forEach(([oldName, newName]) => {
      const trimmed = newName.trim();
      if (trimmed && trimmed !== oldName && !drops.has(oldName)) rename[oldName] = trimmed;
    });
    const drop = Array.from(drops);
    if (!Object.keys(rename).length && !drop.length) {
      setStatus('no column changes to apply');
      return;
    }
    const parts = [
      Object.keys(rename).length ? `rename ${Object.keys(rename).length} column(s)` : '',
      drop.length ? `drop ${drop.join(', ')}` : '',
    ].filter(Boolean);
    runRebuild(
      { rename, drop },
      parts.join(' + '),
      resetDrafts,
      [
        metadataDirty ? 'metadata' : '',
        renameDirty ? 'test-name' : '',
        nanPolicyDirty ? 'NaN-policy' : '',
        trimDirty ? 'trim' : '',
        formulasDirty ? 'equation' : '',
      ].filter(Boolean)
    );
  };

  const applyNanPolicy = () => {
    if (nanPolicy === (meta.nan_policy ?? 'keep_gaps')) {
      setStatus('NaN policy unchanged');
      return;
    }
    runRebuild(
      { nan_policy: nanPolicy },
      `apply NaN policy '${nanPolicy}'`,
      resetDrafts,
      [
        metadataDirty ? 'metadata' : '',
        renameDirty ? 'test-name' : '',
        trimDirty ? 'trim' : '',
        columnsDirty ? 'column-change' : '',
        formulasDirty ? 'equation' : '',
      ].filter(Boolean)
    );
  };

  const applyTrim = () => {
    const a = Number(trim0);
    const b = Number(trim1);
    if (!Number.isFinite(a) || !Number.isFinite(b) || b - a < 1) {
      setStatus('trim needs numeric t0 < t1 keeping at least 1 s');
      return;
    }
    if (a <= tStart + 1e-9 && b >= tEnd - 1e-9) {
      setStatus('trim range covers all data — nothing to cut');
      return;
    }
    runRebuild(
      { trim_t0: a, trim_t1: b },
      `trim to [${a}, ${b}] s (cuts ${(a - tStart + (tEnd - b)).toFixed(1)} s; test points outside are clipped)`,
      resetDrafts,
      [
        metadataDirty ? 'metadata' : '',
        renameDirty ? 'test-name' : '',
        nanPolicyDirty ? 'NaN-policy' : '',
        columnsDirty ? 'column-change' : '',
        formulasDirty ? 'equation' : '',
      ].filter(Boolean)
    );
  };

  // -- test rename / delete --
  const [newName, setNewName] = useState(test);
  useEffect(() => setNewName(test), [test]);

  const metadataDirty = !sameMetaRows(rows, savedRows);
  const normalizedNewName = newName.trim();
  const renameDirty = normalizedNewName.length > 0 && normalizedNewName !== test;
  const nanPolicyDirty = nanPolicy !== savedNanPolicy;
  const trimValues = [Number(trim0), Number(trim1)];
  const savedTrimValues = [Number(savedTrim.start), Number(savedTrim.end)];
  const trimDirty =
    trimValues.every(Number.isFinite) && savedTrimValues.every(Number.isFinite)
      ? trimValues.some(
          (value, index) => Math.abs(value - savedTrimValues[index]) > 1e-9
        )
      : trim0.trim() !== savedTrim.start.trim() ||
        trim1.trim() !== savedTrim.end.trim();
  const columnsDirty =
    drops.size > 0 ||
    Object.entries(renames).some(
      ([oldName, value]) =>
        !drops.has(oldName) &&
        value.trim().length > 0 &&
        value.trim() !== oldName
    );
  const dirty =
    metadataDirty ||
    renameDirty ||
    nanPolicyDirty ||
    trimDirty ||
    columnsDirty ||
    formulasDirty;

  const resetDrafts = () => {
    setRows(savedRows.map((row) => ({ ...row })));
    setRenames({});
    setDrops(new Set());
    setNanPolicy(savedNanPolicy);
    setTrim0(savedTrim.start);
    setTrim1(savedTrim.end);
    setNewName(test);
    clearFormulaDrafts();
  };

  const { requestContextChange } = useUnsavedChanges({
    isDirty: dirty,
    onDirtyChange,
  });

  const changeTest = async (nextTest: string) => {
    if (nextTest === test) return;
    if ((await onTestChange(nextTest)) === false) return;
    resetDrafts();
  };

  const discardDrafts = () => {
    resetDrafts();
    setStatus('discarded unsaved edit drafts');
  };

  const doRename = async () => {
    if (pendingAction) return;
    const target = normalizedNewName;
    if (!target || target === test) return;
    const otherDrafts = [
      metadataDirty ? 'metadata' : '',
      nanPolicyDirty ? 'NaN-policy' : '',
      trimDirty ? 'trim' : '',
      columnsDirty ? 'column-change' : '',
      formulasDirty ? 'equation' : '',
    ].filter(Boolean);
    if (
      otherDrafts.length > 0 &&
      !(await confirmAction({
        title: `Rename '${test}' to '${target}'?`,
        description: `Unsaved ${otherDrafts.join(', ')} drafts will be discarded.`,
        detail: 'The saved test data remains available under the new name.',
        confirmLabel: 'Rename test',
        tone: 'warning',
      }))
    ) {
      return;
    }
    try {
      setPendingAction(`Renaming ${test}`);
      await renameTest(test, target);
      await requestContextChange(() => onTestGone(target), {
        confirm: false,
        onDiscard: resetDrafts,
      });
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    } finally {
      setPendingAction('');
    }
  };

  const doDelete = async () => {
    if (pendingAction) return;
    if (
      !(await confirmAction({
        title: `Delete test '${test}'?`,
        description: 'The test will move to the trash folder and can be restored server-side for a limited time.',
        detail: dirty
          ? 'All unsaved edit drafts will also be discarded.'
          : 'Analysis data for this test will no longer appear in the workspace.',
        confirmLabel: 'Move to trash',
        tone: 'danger',
      }))
    ) {
      return;
    }
    try {
      setPendingAction(`Deleting ${test}`);
      await deleteTest(test);
      await requestContextChange(() => onTestGone(''), {
        confirm: false,
        onDiscard: resetDrafts,
      });
    } catch (e) {
      setStatus(String(e instanceof Error ? e.message : e));
    } finally {
      setPendingAction('');
    }
  };

  const nanTotal = Object.values(meta.nan_counts ?? {}).reduce((a, b) => a + b, 0);

  return (
    <fieldset
      className="edit-view"
      disabled={Boolean(pendingAction)}
      aria-busy={Boolean(pendingAction)}
      style={{
        flex: 1,
        minWidth: 0,
        minInlineSize: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        overflowY: 'auto',
        margin: 0,
        padding: 12,
        border: 0,
      }}
    >
      {pendingAction && (
        <div role="status" aria-live="polite" style={{ fontSize: 11, color: '#9fc7df' }}>
          {pendingAction}…
        </div>
      )}
      {status && <div style={{ fontSize: 11, color: '#569cd6', padding: '0 4px' }}>{status}</div>}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {/* test info + metadata */}
        <div className="panel" style={{ flex: '1 1 380px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div className="section-title" style={{ margin: 0 }}>Test</div>
            <TestSelect
              tests={tests}
              value={test}
              onChange={changeTest}
              ariaLabel="Active test"
              style={{ width: 190 }}
            />
            <span style={{ flex: 1 }} />
            <button className="btn" onClick={discardDrafts} disabled={!dirty}>
              reset drafts
            </button>
          </div>
          <div style={{ fontSize: 11, color: '#909090' }}>
            {meta.n_rows.toLocaleString()} rows × {meta.n_columns} columns · {meta.fs_hz} Hz ·{' '}
            {meta.duration_s.toFixed(1)} s · source {meta.source_file || '—'}
            {(meta.missing_rows_inserted ?? 0) > 0 ? (
              <span style={{ color: '#dcdcaa' }}>
                {' '}· ⚠ {meta.missing_rows_inserted?.toLocaleString()} missing
                {' '}row{meta.missing_rows_inserted === 1 ? '' : 's'} preserved
                {' '}as NaN across {meta.time_gap_count?.toLocaleString()}
                {' '}time gap{meta.time_gap_count === 1 ? '' : 's'}
              </span>
            ) : (
              meta.jitter_warning && (
                <span style={{ color: '#dcdcaa' }}>
                  {' '}· ⚠ time jitter &gt;1%
                </span>
              )
            )}
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input className="input" style={{ width: 200 }} value={newName}
                   onChange={(e) => setNewName(e.target.value)} />
            <button className="btn" onClick={doRename} disabled={!newName.trim() || newName.trim() === test}>
              rename test
            </button>
            <span style={{ flex: 1 }} />
            <button className="btn" style={{ borderColor: '#a04040', color: '#f48771' }} onClick={doDelete}>
              delete test
            </button>
          </div>

          <div className="section-title" style={{ marginTop: 8 }}>Metadata</div>
          <div style={{ fontSize: 10, color: '#909090' }}>
            free-form descriptors (prop, motor, ESC, ambient…) stored in meta.json
          </div>
          {rows.map((r, i) => (
            <div key={i} style={{ display: 'flex', gap: 6 }}>
              <input className="input" style={{ width: 140 }} placeholder="key" value={r.key}
                     onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} />
              <input className="input" placeholder="value" value={r.value}
                     onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
              <button className="btn" onClick={() => setRows(rows.filter((_, j) => j !== i))}>✕</button>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn" onClick={() => setRows([...rows, { key: '', value: '' }])}>+ field</button>
            <button className="btn" onClick={saveMeta} disabled={!metadataDirty}>save metadata</button>
          </div>
        </div>

        {/* NaN policy + trim */}
        <div className="panel" style={{ flex: '1 1 320px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div className="section-title">NaN policy</div>
          <div style={{ fontSize: 11, color: '#909090' }}>
            {nanTotal > 0
              ? `${nanTotal.toLocaleString()} missing values across ${Object.keys(meta.nan_counts ?? {}).length} column(s)`
              : 'no missing values in this test'}
            {' · current: '}{meta.nan_policy ?? 'keep_gaps'}
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <select className="input" style={{ width: 140 }} value={nanPolicy}
                    onChange={(e) => setNanPolicy(e.target.value)}>
              {Object.keys(NAN_POLICY_HELP).map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            <span style={{ fontSize: 10, color: '#909090' }}>{NAN_POLICY_HELP[nanPolicy]}</span>
            <span style={{ flex: 1 }} />
            <button className="btn" onClick={applyNanPolicy} disabled={!nanPolicyDirty}>apply</button>
          </div>
          <div style={{ fontSize: 10, color: '#909090' }}>
            ('drop rows' is not offered — it would break the uniform sample rate)
          </div>

          <div className="section-title" style={{ marginTop: 8 }}>Trim</div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span style={{ fontSize: 11, color: '#909090' }}>keep</span>
            <input className="input" style={{ width: 90 }} type="number" step="0.1"
                   value={trim0} onChange={(e) => setTrim0(e.target.value)} />
            <span style={{ fontSize: 11, color: '#909090' }}>–</span>
            <input className="input" style={{ width: 90 }} type="number" step="0.1"
                   value={trim1} onChange={(e) => setTrim1(e.target.value)} />
            <span style={{ fontSize: 11, color: '#909090' }}>s (data: {tStart.toFixed(1)}–{tEnd.toFixed(1)})</span>
            <span style={{ flex: 1 }} />
            <button className="btn" onClick={applyTrim} disabled={!trimDirty}>apply</button>
          </div>
        </div>
      </div>

      {/* derived variables */}
      <div className="panel edit-formula-panel">
        <div className="edit-formula-heading">
          <div>
            <div className="section-title edit-formula-title">
              Derived variables
              <span className="badge">{formulaSpecs.length} drafted</span>
              <span className="badge">{savedEquations.length} applied</span>
            </div>
            <div className="edit-formula-subtitle">
              Build new columns or update equations saved with this test. Drafts
              run from top to bottom, so a later row can use an earlier result.
            </div>
          </div>
          <div className="edit-formula-recipe">
            <SearchableSelect
              ariaLabel="Saved formula recipe"
              value={selectedRecipe}
              onChange={(recipe) => {
                setSelectedRecipe(recipe);
                if (recipe) setRecipeName(recipe);
              }}
              options={[
                { value: '', label: 'Saved recipes...' },
                ...recipes.map((recipe) => ({
                  value: recipe.name,
                  label: recipe.name,
                  description: `${recipe.formulas.length} formula${
                    recipe.formulas.length === 1 ? '' : 's'
                  }`,
                })),
              ]}
              searchPlaceholder="Search recipes..."
              optionNoun="recipe"
              size="compact"
            />
            <button
              className="btn"
              onClick={loadRecipe}
              disabled={!selectedRecipe}
            >
              load
            </button>
            <input
              className="input"
              aria-label="Formula recipe name"
              placeholder="recipe name"
              value={recipeName}
              onChange={(event) => setRecipeName(event.target.value)}
            />
            <button
              className="btn"
              onClick={saveRecipe}
              disabled={!recipeName.trim() || !formulasDirty}
            >
              save recipe
            </button>
            <button
              className="btn edit-formula-delete-recipe"
              onClick={removeRecipe}
              disabled={!selectedRecipe}
              title="Delete the selected saved recipe"
            >
              delete
            </button>
          </div>
        </div>

        <section
          className="edit-formula-applied"
          aria-labelledby="edit-formula-applied-title"
        >
          <div className="edit-formula-applied-heading">
            <div>
              <div id="edit-formula-applied-title">Applied equations</div>
              <span>
                Saved automatically with this test when a derived variable is
                built.
              </span>
            </div>
            {editingDerivedName && (
              <span className="badge">editing {editingDerivedName}</span>
            )}
          </div>
          {savedEquations.length ? (
            <div className="edit-formula-applied-list" role="list">
              {savedEquations.map((record) => {
                const dependents = dependentEquationNames(
                  record.name,
                  savedEquations
                );
                const missingDependencies = record.missing_dependencies ?? [];
                const updatedAt = formatFormulaTimestamp(record.updated_at);
                const isEditing = editingDerivedName === record.name;
                return (
                  <div
                    className={`edit-formula-applied-row${
                      isEditing ? ' is-editing' : ''
                    }${missingDependencies.length ? ' has-warning' : ''}`}
                    key={record.name}
                    role="listitem"
                  >
                    <div className="edit-formula-applied-body">
                      <div className="edit-formula-applied-equation">
                        <span className="edit-formula-sr-only">
                          {record.name} equals {record.expression}
                        </span>
                        <code
                          className="edit-formula-applied-name"
                          aria-hidden="true"
                        >
                          {record.name}
                        </code>
                        <span aria-hidden="true">=</span>
                        <code
                          className="edit-formula-applied-expression"
                          aria-hidden="true"
                        >
                          {record.expression}
                        </code>
                      </div>
                      <div className="edit-formula-applied-meta">
                        <span>
                          {record.dependencies?.length
                            ? `uses ${record.dependencies.join(', ')}`
                            : 'uses constants only'}
                        </span>
                        {dependents.length > 0 && (
                          <span>
                            {dependents.length} dependent equation
                            {dependents.length === 1 ? '' : 's'}
                          </span>
                        )}
                        {updatedAt && <span>updated {updatedAt}</span>}
                        {missingDependencies.length > 0 && (
                          <span className="edit-formula-applied-warning">
                            missing {missingDependencies.join(', ')}
                          </span>
                        )}
                      </div>
                    </div>
                    <button
                      className="btn edit-formula-applied-edit"
                      onClick={() => void editAppliedEquation(record)}
                      disabled={Boolean(pendingAction) || isEditing}
                      aria-label={`Edit applied equation ${record.name}`}
                    >
                      {isEditing ? 'editing' : 'edit'}
                    </button>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="edit-formula-applied-empty">
              No equations have been applied to this test yet.
            </div>
          )}
        </section>

        <div className="edit-formula-insert-bar">
          <span className="edit-formula-insert-label">
            Insert into equation{' '}
            {Math.max(
              1,
              formulaDrafts.findIndex(
                (draft) => draft.id === activeFormulaId
              ) + 1
            )}
          </span>
          <SearchableSelect
            ariaLabel="Variable to insert"
            value={selectedVariable}
            onChange={setSelectedVariable}
            options={meta.columns.map((column) => ({ value: column, label: column }))}
            searchPlaceholder="Search variables..."
            optionNoun="variable"
            size="compact"
          />
          <button
            className="btn edit-formula-insert-variable"
            onClick={() => insertFormulaText(`{${selectedVariable}}`)}
            disabled={!selectedVariable}
          >
            insert {'{variable}'}
          </button>
          <span className="edit-formula-token-divider" aria-hidden="true" />
          <div className="edit-formula-tokens" aria-label="Formula shortcuts">
            {FORMULA_INSERTS.map((token) => (
              <button
                key={token.label}
                className="edit-formula-token"
                onClick={() =>
                  insertFormulaText(
                    token.text,
                    'caret' in token ? token.caret : token.text.length
                  )
                }
                title={`Insert ${token.text.trim()}`}
              >
                {token.label}
              </button>
            ))}
          </div>
        </div>

        <div className="edit-formula-help">
          References must use braces, for example{' '}
          <code>{'{torque_nm} * {rpm} * 2 * pi / 60'}</code>. Also supported:
          comparisons, <code>log</code>, <code>log10</code>, <code>exp</code>,{' '}
          <code>sin</code>, <code>cos</code>, <code>tan</code>,{' '}
          <code>clip(value, min, max)</code>, <code>min</code>, <code>max</code>,{' '}
          <code>IF(condition, true, false)</code>, and constants <code>pi</code>,{' '}
          <code>e</code>, <code>nan</code>.
        </div>

        <div className="edit-formula-batch-toolbar">
          <div>
            <strong>Equation batch</strong>
            <span aria-live="polite">
              {formulaDrafts.length} row{formulaDrafts.length === 1 ? '' : 's'}
            </span>
            <small>
              Designed for batches of 10–20; maximum {MAX_FORMULA_DRAFTS}.
              Put dependencies first.
            </small>
          </div>
          <button
            type="button"
            className="btn edit-formula-add"
            onClick={addFormulaDraft}
            disabled={
              Boolean(editingDerivedName) ||
              formulaDrafts.length >= MAX_FORMULA_DRAFTS
            }
            title={
              editingDerivedName
                ? 'Finish or cancel the applied-equation edit first'
                : formulaDrafts.length >= MAX_FORMULA_DRAFTS
                  ? `Maximum ${MAX_FORMULA_DRAFTS} equations per batch`
                  : 'Add another equation to this batch'
            }
          >
            + add equation
          </button>
        </div>

        <div
          ref={formulaRowListRef}
          className="edit-formula-row-list"
          role="list"
          aria-label="Equation batch editor"
        >
          <div className="edit-formula-row edit-formula-row-header" aria-hidden="true">
            <span>order</span>
            <span>result column</span>
            <span />
            <span>expression</span>
            <span>existing name</span>
            <span>actions</span>
          </div>
          {formulaDrafts.map((draft, index) => {
            const isSavedTarget = editingDerivedName === draft.name;
            return (
              <div
                key={draft.id}
                role="listitem"
                aria-label={`Equation ${index + 1} of ${formulaDrafts.length}`}
                className={`edit-formula-row${
                  draft.id === activeFormulaId ? ' is-active' : ''
                }`}
              >
                <span
                  className="edit-formula-order"
                  title="Equations execute in this order"
                >
                  {index + 1}
                </span>
                <input
                  ref={(input) => {
                    nameRefs.current[draft.id] = input;
                  }}
                  className={`input edit-formula-name${
                    isSavedTarget ? ' is-locked' : ''
                  }`}
                  aria-label={`Equation ${index + 1} result column`}
                  placeholder="new_column"
                  value={draft.name}
                  readOnly={isSavedTarget}
                  title={
                    isSavedTarget
                      ? 'The result name is locked while editing an applied equation. Rename it with the column controls.'
                      : undefined
                  }
                  onFocus={() => setActiveFormulaId(draft.id)}
                  onChange={(event) =>
                    updateFormulaDraft(draft.id, { name: event.target.value })
                  }
                />
                <span className="edit-formula-equals" aria-hidden="true">
                  =
                </span>
                <input
                  ref={(input) => {
                    expressionRefs.current[draft.id] = input;
                  }}
                  className="input edit-formula-expression"
                  aria-label={`Equation ${index + 1} expression`}
                  placeholder="{variable_a} / {variable_b}"
                  value={draft.expression}
                  onClick={() => setActiveFormulaId(draft.id)}
                  onFocus={() => setActiveFormulaId(draft.id)}
                  onSelect={() => setActiveFormulaId(draft.id)}
                  onChange={(event) =>
                    updateFormulaDraft(draft.id, {
                      expression: event.target.value,
                    })
                  }
                />
                <label
                  className={`edit-formula-replace${
                    isSavedTarget ? ' is-locked' : ''
                  }`}
                  title={
                    isSavedTarget
                      ? 'Applied equations always replace their materialized column'
                      : 'Required when the result uses an existing column name'
                  }
                >
                  <input
                    type="checkbox"
                    checked={isSavedTarget || Boolean(draft.replace)}
                    disabled={isSavedTarget}
                    onChange={(event) =>
                      updateFormulaDraft(draft.id, {
                        replace: event.target.checked,
                      })
                    }
                  />
                  replace
                </label>
                <div className="edit-formula-row-actions">
                  <button
                    ref={(button) => {
                      moveUpRefs.current[draft.id] = button;
                    }}
                    type="button"
                    className="btn edit-formula-move"
                    aria-label={`Move equation ${index + 1} up`}
                    title="Move equation earlier"
                    onClick={() => moveFormulaDraft(draft.id, -1)}
                    disabled={isSavedTarget || index === 0}
                  >
                    ↑
                  </button>
                  <button
                    ref={(button) => {
                      moveDownRefs.current[draft.id] = button;
                    }}
                    type="button"
                    className="btn edit-formula-move"
                    aria-label={`Move equation ${index + 1} down`}
                    title="Move equation later"
                    onClick={() => moveFormulaDraft(draft.id, 1)}
                    disabled={isSavedTarget || index === formulaDrafts.length - 1}
                  >
                    ↓
                  </button>
                  <button
                    ref={(button) => {
                      removeRefs.current[draft.id] = button;
                    }}
                    type="button"
                    className="btn edit-formula-remove"
                    aria-label={`Remove equation ${index + 1}`}
                    title={
                      isSavedTarget
                        ? 'Use cancel edit below to leave this applied equation unchanged'
                        : `Remove equation ${index + 1}`
                    }
                    onClick={() => removeFormulaDraft(draft.id)}
                    disabled={isSavedTarget}
                  >
                    ×
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        <div className="edit-formula-actions">
          {editingDerivedName && (
            <button className="btn" onClick={clearFormulaDrafts}>
              cancel edit
            </button>
          )}
          <button
            className="btn"
            onClick={runFormulaPreview}
            disabled={!formulasDirty}
          >
            validate &amp; preview
          </button>
          <button
            className="btn edit-formula-apply"
            onClick={applyFormulas}
            disabled={!formulasDirty}
          >
            apply &amp; rebuild
          </button>
          <span>
            {editingDerivedName
              ? 'The result name is locked. Preview and apply also recalculate saved dependent equations.'
              : 'Preview is read-only. Apply materializes full-resolution columns and rebuilds plot pyramids.'}
          </span>
        </div>

        {(formulaNotice || (formulasDirty && formulaValidation)) && (
          <div
            className={`edit-formula-notice ${
              formulaNotice?.kind === 'error' ||
              (!formulaNotice && formulaValidation)
                ? 'is-error'
                : formulaNotice?.kind === 'success'
                  ? 'is-success'
                  : ''
            }`}
            role={
              formulaNotice?.kind === 'error' ? 'alert' : 'status'
            }
            aria-live={
              formulaNotice?.kind === 'error'
                ? 'assertive'
                : formulaNotice
                  ? 'polite'
                  : 'off'
            }
          >
            {formulaNotice?.text || formulaValidation}
          </div>
        )}

        {formulaPreview && (
          <div className="edit-formula-preview" aria-label="Equation preview">
            <div className="edit-formula-preview-heading">
              Preview · {formulaPreview.sample_size} sampled rows
            </div>
            {formulaPreview.formulas.map((formula) => (
              <div className="edit-formula-preview-row" key={formula.name}>
                <div className="edit-formula-preview-name">
                  <code>{formula.name}</code>
                  {formula.replaces_existing && (
                    <span className="badge">replaces existing</span>
                  )}
                </div>
                <div className="edit-formula-preview-stats">
                  mean {formatPreviewValue(formula.stats.mean)} · range{' '}
                  {formatPreviewValue(formula.stats.min)} –{' '}
                  {formatPreviewValue(formula.stats.max)} ·{' '}
                  {formula.stats.nan_count.toLocaleString()} NaN
                </div>
                <div className="edit-formula-preview-values">
                  {formula.values.map((value, index) => (
                    <code
                      key={`${formula.name}-${
                        formulaPreview.row_indices[index] ?? index
                      }`}
                      title={`row ${
                        formulaPreview.row_indices[index] ?? index
                      } · t=${formatPreviewValue(
                        formulaPreview.time[index] ?? null
                      )}`}
                    >
                      {formatPreviewValue(value)}
                    </code>
                  ))}
                </div>
                <div className="edit-formula-preview-deps">
                  uses{' '}
                  {formula.dependencies.length
                    ? formula.dependencies.join(', ')
                    : 'constants only'}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* column table */}
      <div className="panel">
        <div className="section-title">
          Rename or remove existing columns{' '}
          <span className="badge">{dataColumns.length}</span>
        </div>
        <div style={{ marginBottom: 7, fontSize: 10, color: '#909090' }}>
          Type a new name beside any existing column. Leave it blank to keep the
          current name; check remove only when the column should be deleted.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 90px 60px', gap: 4, fontSize: 11, maxHeight: 300, overflowY: 'auto' }}>
          <span style={{ color: '#909090' }}>existing column name</span>
          <span style={{ color: '#909090' }}>new column name (optional)</span>
          <span style={{ color: '#909090' }}>missing</span>
          <span style={{ color: '#909090' }}>remove</span>
          {dataColumns.map((c) => (
            <ColumnRow key={c} name={c}
              nanCount={meta.nan_counts?.[c] ?? 0}
              rename={renames[c] ?? ''}
              dropped={drops.has(c)}
              onRename={(v) => setRenames({ ...renames, [c]: v })}
              onDrop={(checked) => {
                const next = new Set(drops);
                if (checked) next.add(c);
                else next.delete(c);
                setDrops(next);
              }} />
          ))}
        </div>
        <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="btn" onClick={applyColumns} disabled={!columnsDirty}>apply renames / removals</button>
          <span style={{ fontSize: 10, color: '#909090' }}>
            time column '{meta.time_column}' is protected; units live in the column name (e.g. thrust_n)
          </span>
        </div>
      </div>
    </fieldset>
  );
}

function ColumnRow({ name, nanCount, rename, dropped, onRename, onDrop }: {
  name: string;
  nanCount: number;
  rename: string;
  dropped: boolean;
  onRename: (v: string) => void;
  onDrop: (checked: boolean) => void;
}) {
  return (
    <>
      <span style={{ color: dropped ? '#666' : '#e0e0e0', textDecoration: dropped ? 'line-through' : 'none', lineHeight: '24px' }}>
        {name}
      </span>
      <input className="input" placeholder="enter a new name" value={rename}
             aria-label={`Rename column ${name}`}
             disabled={dropped} onChange={(e) => onRename(e.target.value)} />
      <span style={{ color: nanCount > 0 ? '#dcdcaa' : '#666', lineHeight: '24px' }}>
        {nanCount > 0 ? nanCount.toLocaleString() : '—'}
      </span>
      <input type="checkbox" checked={dropped} aria-label={`Remove column ${name}`}
             onChange={(e) => onDrop(e.target.checked)} />
    </>
  );
}
