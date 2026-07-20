// Statuses during which a test's files are being written by a background job:
// the request body is still streaming ('receiving'), the CSV->parquet ingest
// runs ('ingesting'), or an edit rebuild runs ('rebuilding'). A test in one of
// these is polled live and blocks mutating actions.
//
// Mirrors the backend BUSY_STATUSES (app/status.py) — keep the two in sync.
export const BUSY_STATUSES = ['receiving', 'ingesting', 'rebuilding'] as const;

export function isBusyStatus(status: string): boolean {
  return (BUSY_STATUSES as readonly string[]).includes(status);
}
