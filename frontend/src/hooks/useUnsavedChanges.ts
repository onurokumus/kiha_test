import { useCallback, useEffect, useRef } from 'react';

const DEFAULT_MESSAGE = 'You have unsaved changes. Discard them and continue?';

interface UseUnsavedChangesOptions {
  isDirty: boolean;
  onDirtyChange?: (isDirty: boolean) => void;
  /**
   * When enabled, requestContextChange asks before running its action.
   * Browser/tab closing is protected independently whenever isDirty is true.
   */
  confirmOnContextChange?: boolean;
  message?: string;
}

interface ContextChangeOptions {
  /** Override confirmOnContextChange for this one transition. */
  confirm?: boolean;
  /** Reset local drafts before the context is changed. */
  onDiscard?: () => void;
}

/**
 * Shared unsaved-work protection for editor-like views.
 *
 * The browser controls the text shown for `beforeunload`. In-app context
 * changes can use `requestContextChange` to show the supplied message.
 */
export function useUnsavedChanges({
  isDirty,
  onDirtyChange,
  confirmOnContextChange = false,
  message = DEFAULT_MESSAGE,
}: UseUnsavedChangesOptions) {
  const dirtyChangeRef = useRef(onDirtyChange);

  useEffect(() => {
    dirtyChangeRef.current = onDirtyChange;
  }, [onDirtyChange]);

  useEffect(() => {
    onDirtyChange?.(isDirty);
  }, [isDirty, onDirtyChange]);

  // Do not leave a parent-owned dirty flag set after this view is gone.
  useEffect(
    () => () => {
      dirtyChangeRef.current?.(false);
    },
    []
  );

  useEffect(() => {
    if (!isDirty) return;

    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };

    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [isDirty]);

  const confirmContextChange = useCallback(
    (shouldConfirm = confirmOnContextChange): boolean =>
      !isDirty || !shouldConfirm || window.confirm(message),
    [confirmOnContextChange, isDirty, message]
  );

  const requestContextChange = useCallback(
    (changeContext: () => void, options: ContextChangeOptions = {}): boolean => {
      const shouldConfirm = options.confirm ?? confirmOnContextChange;
      if (!confirmContextChange(shouldConfirm)) return false;

      if (isDirty) {
        options.onDiscard?.();
        // The view can unmount before its state update commits.
        dirtyChangeRef.current?.(false);
      }
      changeContext();
      return true;
    },
    [confirmContextChange, confirmOnContextChange, isDirty]
  );

  return { confirmContextChange, requestContextChange };
}
