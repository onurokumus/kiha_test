/** Browser-local convenience value for self-reported upload attribution. */

export const MAX_UPLOADER_NAME_LENGTH = 80;

const STORAGE_KEY = 'ptt.uploaderName.v1';
const CONTROL_CHARACTER = /\p{Cc}/u;

export function normalizeUploaderName(value: string): string {
  return value.normalize('NFC').trim();
}

export function uploaderNameError(value: string): string {
  const normalized = normalizeUploaderName(value);
  if (!normalized) return 'Enter who is uploading these files.';
  if (CONTROL_CHARACTER.test(normalized)) {
    return 'Uploaded by cannot contain control characters.';
  }
  if ([...normalized].length > MAX_UPLOADER_NAME_LENGTH) {
    return `Uploaded by must be ${MAX_UPLOADER_NAME_LENGTH} characters or fewer.`;
  }
  return '';
}

export function loadRememberedUploaderName(): string {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY) ?? '';
    return uploaderNameError(value) ? '' : normalizeUploaderName(value);
  } catch {
    return '';
  }
}

export function rememberUploaderName(value: string): void {
  const normalized = normalizeUploaderName(value);
  if (uploaderNameError(normalized)) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, normalized);
  } catch {
    // Attribution still travels with the upload when storage is unavailable.
  }
}
