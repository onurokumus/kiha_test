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
