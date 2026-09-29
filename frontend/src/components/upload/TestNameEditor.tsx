import { useEffect, useId, useRef, useState } from 'react';
import styles from './UploadView.module.css';

interface Props {
  name: string;
  names: string[];
  disabled: boolean;
  onRename: (name: string, newName: string) => Promise<void>;
}

export default function TestNameEditor({ name, names, disabled, onRename }: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const submitting = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const errorId = useId();
  const target = draft.trim();
  const validation = !target ? 'Enter a test name.'
    : !/^[A-Za-z0-9._-]+$/.test(target) || !/[A-Za-z0-9]/.test(target)
      ? "Use letters, digits, periods, underscores or hyphens."
      : target !== name && names.includes(target) ? 'A test with this name already exists.' : '';
  const problem = error || validation;

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const cancel = () => {
    if (submitting.current) return;
    setEditing(false);
    setDraft(name);
    setError('');
    requestAnimationFrame(() => buttonRef.current?.focus());
  };

  const save = async () => {
    if (disabled || submitting.current || validation || target === name) return;
    submitting.current = true;
    setSaving(true);
    setError('');
    try {
      await onRename(name, target);
      setEditing(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      requestAnimationFrame(() => inputRef.current?.focus());
    } finally {
      submitting.current = false;
      setSaving(false);
    }
  };

  return editing ? (
    <form className={styles.renameForm} aria-label={`Rename test ${name}`}
      onSubmit={(event) => { event.preventDefault(); void save(); }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') { event.preventDefault(); cancel(); }
      }}>
      <input ref={inputRef} className="input" aria-label={`New name for ${name}`}
        value={draft} disabled={saving} autoComplete="off" spellCheck={false}
        aria-invalid={problem ? true : undefined} aria-describedby={problem ? errorId : undefined}
        onChange={(event) => { setDraft(event.target.value); setError(''); }} />
      <div className={styles.renameActions}>
        <button type="submit" className="btn" disabled={disabled || saving || !!validation || target === name}>
          {saving ? 'Saving…' : 'Save name'}
        </button>
        <button type="button" className="btn" disabled={saving} onClick={cancel}>Cancel rename</button>
      </div>
      {problem && <p id={errorId} className={styles.renameError} role="alert">{problem}</p>}
    </form>
  ) : (
    <div className={styles.testNameRow}>
      <span className={styles.testName} title={name}>{name}</span>
      <button ref={buttonRef} id={`rename-test-${encodeURIComponent(name)}`}
        className={styles.renameButton} type="button" disabled={disabled}
        aria-label={`Rename test ${name}`} title={disabled ? 'Renaming is unavailable while processing' : 'Rename test'}
        onClick={() => { setDraft(name); setError(''); setEditing(true); }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15z" />
        </svg>
      </button>
    </div>
  );
}
