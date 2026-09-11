/** Keep the plain-text limits/normalization aligned with backend/app/test_notes.py. */
export const MAX_DESCRIPTION_LENGTH = 1000;
export const MAX_NOTES_LENGTH = 20_000;

export const normalizeTestText = (value: string): string => value.replace(/\r\n?/g, '\n');
export const textLength = (value: string): number => Array.from(normalizeTestText(value)).length;

export function testTextError(value: string, limit: number, label: string): string {
  // Permit line breaks and tabs, but reject nonprinting control characters.
  if (Array.from(value).some((character) => {
    const code = character.codePointAt(0)!;
    return (code < 32 && ![9, 10, 13].includes(code)) ||
      (code >= 127 && code <= 159) || (code >= 0xd800 && code <= 0xdfff);
  })) return `${label} contains an unsupported control character.`;
  return textLength(value) > limit ? `${label} must be ${limit.toLocaleString()} characters or fewer.` : '';
}
