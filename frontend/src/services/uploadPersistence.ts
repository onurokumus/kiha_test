/** Minimal browser-side resume metadata.
 *
 * The File itself is deliberately never copied into localStorage/IndexedDB:
 * multi-GB blobs would exhaust browser storage. After a refresh the user must
 * reselect the original file; resumableUpload verifies every committed chunk
 * hash before trusting the server-side partial file.
 */

import { uploaderNameError } from './uploaderAttribution';

export interface UploadResumeRecord {
  version: 1;
  uploadId: string;
  testName: string;
  fileName: string;
  uploaderName?: string;
  sizeBytes: number;
  lastModifiedMs: number;
  fsHz?: number;
  timeMode?: 'auto' | 'column' | 'generated';
  timeColumn?: string;
  chunkSize: number;
  totalChunks: number;
  createdAt: string;
}

const STORAGE_KEY = 'ptt.uploadSessions.v1';

function isRecord(value: unknown): value is UploadResumeRecord {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<UploadResumeRecord>;
  return (
    item.version === 1 &&
    typeof item.uploadId === 'string' &&
    !!item.uploadId &&
    typeof item.testName === 'string' &&
    !!item.testName &&
    typeof item.fileName === 'string' &&
    (item.uploaderName === undefined ||
      (typeof item.uploaderName === 'string' &&
        !uploaderNameError(item.uploaderName))) &&
    Number.isFinite(item.sizeBytes) &&
    (item.sizeBytes ?? -1) >= 0 &&
    Number.isFinite(item.lastModifiedMs) &&
    (item.fsHz === undefined ||
      (Number.isFinite(item.fsHz) && (item.fsHz ?? 0) > 0)) &&
    (item.timeMode === undefined ||
      item.timeMode === 'auto' ||
      item.timeMode === 'column' ||
      item.timeMode === 'generated') &&
    (item.timeColumn === undefined || typeof item.timeColumn === 'string') &&
    Number.isFinite(item.chunkSize) &&
    (item.chunkSize ?? 0) > 0 &&
    Number.isInteger(item.totalChunks) &&
    (item.totalChunks ?? -1) >= 0 &&
    typeof item.createdAt === 'string'
  );
}

export function loadUploadRecords(): UploadResumeRecord[] {
  try {
    const parsed: unknown = JSON.parse(
      window.localStorage.getItem(STORAGE_KEY) ?? '[]'
    );
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isRecord);
  } catch {
    return [];
  }
}

function writeUploadRecords(records: UploadResumeRecord[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
  } catch {
    // Storage can be disabled/full. The live transfer still works; only
    // refresh-resume metadata is unavailable for this browser.
  }
}

export function saveUploadRecord(record: UploadResumeRecord): void {
  const records = loadUploadRecords().filter(
    (item) => item.uploadId !== record.uploadId && item.testName !== record.testName
  );
  writeUploadRecords([...records, record]);
}

export function removeUploadRecord(uploadId: string): void {
  writeUploadRecords(
    loadUploadRecords().filter((item) => item.uploadId !== uploadId)
  );
}
