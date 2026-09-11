export const COMPONENT_KINDS = ['propeller', 'motor', 'esc'] as const;
export type ComponentKind = typeof COMPONENT_KINDS[number];
export type ComponentIds = Record<ComponentKind, string | null>;
export interface HardwareComponent { id: string; kind: ComponentKind; name: string; created_at: string }
export interface ComponentCatalog { version: 1; components: HardwareComponent[] }
export const COMPONENT_LABELS: Record<ComponentKind, string> = { propeller: 'Propeller', motor: 'Electric motor', esc: 'ESC' };
export const componentIds = (value?: Partial<ComponentIds> | null): ComponentIds => ({
  propeller: value?.propeller ?? null, motor: value?.motor ?? null, esc: value?.esc ?? null,
});
export const sameComponents = (a?: Partial<ComponentIds> | null, b?: Partial<ComponentIds> | null) =>
  COMPONENT_KINDS.every((key) => (a?.[key] ?? null) === (b?.[key] ?? null));
export function validComponentIds(value: unknown): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return Object.keys(record).every((key) => COMPONENT_KINDS.includes(key as ComponentKind)) &&
    COMPONENT_KINDS.every((key) => record[key] == null || (typeof record[key] === 'string' &&
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(record[key])));
}

export interface ComponentSet {
  id: string;
  name: string;
  components: ComponentIds;
  rpm_column: string | null;
  motor_temperature_column: string | null;
  motor_temperature_unit: 'C' | 'F' | 'K';
  power_column: string | null;
  power_unit: 'W' | 'kW';
}
export interface ComponentAssignments {
  component_sets?: ComponentSet[];
  components?: Partial<ComponentIds> | null;
  component_rpm_column?: string | null;
}
export const MAX_COMPONENT_SETS = 16;
// getRandomValues also works on the deployed plain-HTTP origin, where
// randomUUID is unavailable. Set RFC 9562 v4 version/variant bits explicitly.
function newSetId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
export const newComponentSet = (name: string, id: string = newSetId()): ComponentSet => ({
  id, name, components: componentIds(), rpm_column: null,
  motor_temperature_column: null, motor_temperature_unit: 'C', power_column: null, power_unit: 'W',
});
/** Read stored metadata defensively without repairing or replacing it. Only an
 * absent canonical field permits legacy projection; invalid data stays explicit. */
export function readComponentSets(value: ComponentAssignments): { sets: ComponentSet[]; error: string | null } {
  if (Object.prototype.hasOwnProperty.call(value, 'component_sets')) {
    if (!validComponentSets(value.component_sets)) return { sets: [], error: 'Invalid component settings' };
    return { sets: value.component_sets.map(set => ({ ...set, components: componentIds(set.components) })), error: null };
  }
  if ((value.components != null && !validComponentIds(value.components)) ||
      (value.component_rpm_column != null && (typeof value.component_rpm_column !== 'string' || !value.component_rpm_column.length))) {
    return { sets: [], error: 'Invalid component settings' };
  }
  return { sets: [{ ...newComponentSet('Set 1', 'legacy'), components: componentIds(value.components),
    rpm_column: value.component_rpm_column ?? null }], error: null };
}
export const componentSets = (value: ComponentAssignments): ComponentSet[] => readComponentSets(value).sets;
export const sameComponentSets = (a: ComponentSet[], b: ComponentSet[]) => a.length === b.length &&
  a.every((set, index) => {
    const other = b[index];
    return set.id === other.id && set.name === other.name && sameComponents(set.components, other.components) &&
      set.rpm_column === other.rpm_column && set.motor_temperature_column === other.motor_temperature_column &&
      set.motor_temperature_unit === other.motor_temperature_unit && set.power_column === other.power_column && set.power_unit === other.power_unit;
  });
export function componentSetsError(sets: ComponentSet[]): string {
  if (sets.length > MAX_COMPONENT_SETS) return `Use at most ${MAX_COMPONENT_SETS} component sets.`;
  const names = new Set<string>();
  const ids = new Set<string>();
  const hardware = new Set<string>();
  for (const set of sets) {
    const name = set.name.normalize('NFC').trim();
    if (!name || [...name].length > 120 || [...name].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) return 'Each set needs a single-line name of 1–120 characters.';
    if (names.has(name.toLocaleLowerCase())) return 'Give each component set a different name.';
    if (ids.has(set.id)) return 'Each component set must have a unique ID.';
    names.add(name.toLocaleLowerCase()); ids.add(set.id);
    for (const kind of COMPONENT_KINDS) {
      const id = set.components[kind];
      if (id && hardware.has(id)) return 'Each physical component can belong to only one set in a test.';
      if (id) hardware.add(id);
    }
  }
  return '';
}
export function validComponentSets(value: unknown): value is ComponentSet[] {
  if (!Array.isArray(value) || value.length > MAX_COMPONENT_SETS) return false;
  if (!value.every(set => set && typeof set === 'object' && !Array.isArray(set) &&
    typeof set.id === 'string' && (set.id === 'legacy' || /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(set.id)) &&
    typeof set.name === 'string' && validComponentIds(set.components) &&
    ['rpm_column', 'motor_temperature_column', 'power_column'].every(key => set[key] === null || (typeof set[key] === 'string' && set[key].length > 0)) &&
    ['C', 'F', 'K'].includes(set.motor_temperature_unit) && ['W', 'kW'].includes(set.power_unit))) return false;
  return !componentSetsError(value);
}
/** Preserve the wire representation so old resumable sessions keep their identity. */
export const componentAssignmentFields = (value?: ComponentAssignments) => value?.component_sets !== undefined
  ? { component_sets: componentSets(value).map(set => ({ ...set, name: set.name.normalize('NFC').trim() })) }
  : { components: componentIds(value?.components) };
export const sameComponentAssignments = (a?: ComponentAssignments, b?: ComponentAssignments) =>
  a?.component_sets !== undefined || b?.component_sets !== undefined
    ? a?.component_sets !== undefined && b?.component_sets !== undefined && sameComponentSets(a.component_sets, b.component_sets)
    : sameComponents(a?.components, b?.components);
export const componentSetsSummary = (value: ComponentAssignments, catalog: HardwareComponent[]) => {
  const read = readComponentSets(value);
  if (read.error) return read.error;
  return read.sets.filter(set => value.component_sets !== undefined || COMPONENT_KINDS.some(kind => set.components[kind]))
    .map(set => {
      const hardware = COMPONENT_KINDS.filter(kind => set.components[kind]).map(kind =>
        `${COMPONENT_LABELS[kind]}: ${catalog.find(item => item.id === set.components[kind])?.name ?? 'Unavailable component'}`).join(' · ');
      return `${set.name}: ${hardware || 'Unassigned'}`;
    }).join(' | ');
};
