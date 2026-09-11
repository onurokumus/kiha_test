import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import styles from './PlotFilterDialog.module.css';

/** Mount on demand from the plot menu; native modal keeps dense filter fields
 * outside grid clipping and restores keyboard focus to the menu opener. */
export function PlotFilterDialog({ label, onClose, children }: {
  label: string; onClose: () => void; children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  useEffect(() => {
    const element = dialog.current;
    const returnTo = opener.current;
    element?.showModal();
    return () => { element?.close(); if (returnTo?.isConnected) returnTo.focus({ preventScroll: true }); };
  }, []);
  return createPortal(<dialog ref={dialog} className={styles.dialog}
    aria-label={`Filter settings for ${label}`} onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <div className={styles.heading}>
      <strong>Filter · {label}</strong>
      <button type="button" onClick={onClose} aria-label="Close filter settings">×</button>
    </div>
    <p>Changes apply to this plot as you edit.</p>
    <div className={styles.fields}>{children}</div>
  </dialog>, document.body);
}
