import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AnalysisSession } from '../../services/analysisSession';
import { fetchAnalysisSources, fetchTests } from '../../services/api';
import {
  AnalysisSource,
  resolveSessionSources,
  SessionRecovery,
} from '../../services/sessionSources';
import {
  downloadSessionFile,
  MAX_SESSION_FILE_BYTES,
  parseSessionFile,
  SessionFile,
} from '../../services/sessionFiles';
import { TestInfo } from '../../types';
import styles from './SessionControls.module.css';

interface Props {
  onBegin: () => Promise<boolean>;
  getSession: () => AnalysisSession;
  saveDisabledReason: string | null;
  onApply: (
    input: AnalysisSession,
    recovery: SessionRecovery,
    sources: AnalysisSource[],
    tests: TestInfo[]
  ) => void;
}
interface Preview {
  file: SessionFile;
  recovery: SessionRecovery;
  reconnect: boolean;
}

export function SessionControls(props: Props) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button
        className="btn"
        ref={trigger}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={async () => {
          if (await props.onBegin()) setOpen(true);
        }}
      >
        Sessions
      </button>
      {open &&
        createPortal(
          <SessionDialog
            {...props}
            close={() => {
              setOpen(false);
              trigger.current?.focus();
            }}
          />,
          document.body
        )}
    </>
  );
}

function SessionDialog({
  getSession,
  saveDisabledReason,
  onApply,
  close: onClose,
}: Props & { close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const request = useRef(0);
  const close = () => {
    ++request.current;
    // The trigger is inert until the native modal closes.
    dialog.current?.close();
    onClose();
  };
  const [name, setName] = useState('Analysis session');
  const [file, setFile] = useState<SessionFile | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  useEffect(() => {
    dialog.current?.showModal();
    const generation = request;
    return () => {
      ++generation.current;
    };
  }, []);
  const check = async (input: SessionFile, reconnect = false, applying = false) => {
    const token = ++request.current;
    setBusy(true);
    setError('');
    setStatus('Checking source compatibility…');
    try {
      const [catalog, tests] = await Promise.all([fetchAnalysisSources(), fetchTests()]);
      if (token !== request.current) return;
      const recovery = resolveSessionSources(input.session, catalog.sources, reconnect);
      const next = { file: input, recovery, reconnect };
      if (applying && preview && JSON.stringify(preview.recovery) === JSON.stringify(recovery)) {
        onApply(input.session, recovery, catalog.sources, tests);
        close();
        return;
      }
      setPreview(next);
      setStatus(
        applying
          ? 'Sources changed since preview. Review the updated result before opening.'
          : 'Compatibility checked. Review before opening.'
      );
    } catch (cause) {
      if (token === request.current) {
        setStatus('');
        setError(
          cause instanceof Error ? cause.message : 'Could not check sources. Retry preview.'
        );
      }
    } finally {
      if (token === request.current) setBusy(false);
    }
  };
  const read = async (selected: File) => {
    const token = ++request.current;
    setFile(null);
    setPreview(null);
    setError('');
    setStatus('Reading session file…');
    setBusy(true);
    try {
      if (selected.size > MAX_SESSION_FILE_BYTES)
        throw new Error('Session files must be 2 MB or smaller.');
      const input = parseSessionFile(await selected.text());
      if (token !== request.current) return;
      setFile(input);
      await check(input);
    } catch (cause) {
      if (token === request.current) {
        setBusy(false);
        setStatus('');
        setError(cause instanceof Error ? cause.message : 'Could not read this file.');
      }
    }
  };
  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="session-title"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
    >
      <div className={styles.heading}>
        <h2 id="session-title">Analysis sessions</h2>
        <button onClick={close}>Close</button>
      </div>
      <p>
        Save the current analysis view to a JSON file. Reopen it with the same datasets available in
        this library. Files contain settings and source references; test samples stay in the
        library.
      </p>
      <fieldset>
        <legend>Save current view</legend>
        <label>
          Session name{' '}
          <input
            className="input"
            value={name}
            maxLength={120}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <button
          disabled={!!saveDisabledReason || !name.trim()}
          title={saveDisabledReason ?? undefined}
          onClick={() => {
            try {
              downloadSessionFile(name, getSession());
              setError('');
              setStatus('Session download started. Keep the JSON file to reopen this view.');
            } catch (cause) {
              setError(cause instanceof Error ? cause.message : 'Could not save the session file.');
            }
          }}
        >
          Save session file
        </button>
        {saveDisabledReason && <p>{saveDisabledReason}</p>}
      </fieldset>
      <fieldset>
        <legend>Open a saved view</legend>
        <label>
          Session file{' '}
          <input
            type="file"
            accept=".json,application/json"
            onChange={(event) => {
              const selected = event.target.files?.[0];
              event.target.value = '';
              if (selected) void read(selected);
            }}
          />
        </label>
        {file && (
          <>
            <p>
              <strong>{file.name}</strong>
              {file.savedAt && <> · saved {new Date(file.savedAt).toLocaleString()}</>}
            </p>
            <button disabled={busy} onClick={() => void check(file, preview?.reconnect)}>
              Refresh preview
            </button>
          </>
        )}
        {preview && (
          <div className={styles.preview} aria-label="Session preview">
            <p>
              {
                { tp: 'Test-point time', full: 'Full-test time', spectrum: 'Spectrum', xy: 'XY' }[
                  preview.recovery.session.viewMode
                ]
              }{' '}
              ·{' '}
              {
                { single: 'Single plot', quad: '2 × 2', nine: '3 × 3' }[
                  preview.recovery.session.plotDensity
                ]
              }
              {preview.recovery.session.expandedPlot !== null &&
                ` · Plot ${preview.recovery.session.expandedPlot + 1} maximized`}
            </p>
            <p>
              Tests:{' '}
              {preview.recovery.session.sources?.map((source) => source.name).join(', ') ||
                'None linked'}{' '}
              · {preview.recovery.session.selections.length} selected test points (
              {preview.recovery.session.selections.filter((point) => point.hidden).length} hidden)
            </p>
            <p>Plots: {preview.recovery.session.plotConfigs.join(', ') || 'Default variables'}</p>
            {preview.recovery.messages.length ? (
              <ul>
                {preview.recovery.messages.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            ) : (
              <p>All saved source references are compatible.</p>
            )}
            {preview.recovery.legacy && (
              <button disabled={busy} onClick={() => void check(preview.file, true)}>
                Reconnect legacy references by name
              </button>
            )}
            <p>
              Opening replaces the current analysis view and its automatic recovery after loading.
              Save the current view first if you want to keep it.
            </p>
            <button
              disabled={busy || preview.recovery.legacy}
              onClick={() => void check(preview.file, preview.reconnect, true)}
            >
              {preview.recovery.needsReview ? 'Open with available sources' : 'Open session'}
            </button>
          </div>
        )}
      </fieldset>
      {status && <p role="status">{status}</p>}
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </dialog>
  );
}
