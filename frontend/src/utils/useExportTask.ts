import { useCallback, useEffect, useRef, useState } from 'react';
import { observeExport, type ExportProgress } from '../services/exportProgress';

type State = 'idle' | 'running' | 'canceling' | 'canceled' | 'completed' | 'failed';
interface Feedback { state: State; progress: ExportProgress; message: string }
const initial: Feedback = { state: 'idle', progress: { stage: '' }, message: '' };

/** One operation per dialog; an old completion cannot overwrite a new run. */
export function useExportTask() {
  const current = useRef<AbortController | null>(null);
  const [feedback, setFeedback] = useState(initial);
  useEffect(() => () => { current.current?.abort(); current.current = null; }, []);
  const reset = useCallback(() => {
    current.current?.abort(); current.current = null; setFeedback(initial);
  }, []);
  const cancel = () => {
    if (!current.current) return;
    setFeedback((previous) => ({ ...previous, state: 'canceling', progress: { stage: 'Canceling; waiting for the current block to finish' } }));
    current.current.abort();
  };
  const run = async (format: string, work: (signal: AbortSignal) => Promise<void>) => {
    if (current.current) return;
    const origin = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const controller = new AbortController(); current.current = controller;
    let latest: ExportProgress = { stage: `Preparing ${format}` };
    const stop = observeExport(controller.signal, (progress) => {
      latest = progress;
      if (current.current === controller) setFeedback((previous) => ({ ...previous, progress }));
    });
    setFeedback({ state: 'running', progress: latest, message: '' });
    try {
      await work(controller.signal);
      if (current.current === controller && !controller.signal.aborted) {
        setFeedback({ state: 'completed', progress: latest, message: `${format} completed. Download sent to your browser.` });
      }
    } catch (error) {
      if (current.current === controller && !controller.signal.aborted) {
        setFeedback({ state: 'failed', progress: latest, message: `Export failed. ${error instanceof Error ? error.message : 'Try again.'}` });
      }
    } finally {
      stop();
      if (current.current === controller) {
        current.current = null;
        if (controller.signal.aborted) setFeedback({ state: 'canceled', progress: latest,
          message: latest.cancellationUnconfirmed
            ? 'Download canceled. Server cleanup is not yet confirmed and may still be finishing.'
            : 'Export canceled. No file was downloaded.' });
        // Cancel disappears and download buttons re-enable on the next render.
        // Restore the initiating action if focus was lost with those controls.
        window.setTimeout(() => {
          if (!current.current && origin?.isConnected && origin.closest('dialog')?.open &&
              (document.activeElement === document.body || document.activeElement?.closest('[data-export-state]'))) origin.focus();
        }, 0);
      }
    }
  };
  return { ...feedback, busy: feedback.state === 'running' || feedback.state === 'canceling', run, cancel, reset };
}
