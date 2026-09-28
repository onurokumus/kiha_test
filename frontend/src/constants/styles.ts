import { CSSProperties } from 'react';

export const SelectStyle: CSSProperties = {
  background: 'var(--input-bg, #f8f9fc)',
  color: 'var(--text, #202c42)',
  border: '1px solid var(--border, #dfe4ec)',
  padding: '4px 8px',
  borderRadius: 5,
  minHeight: 30,
  fontFamily: 'Manrope, Segoe UI, sans-serif',
  fontSize: 12,
};

export const noSelect: CSSProperties = {
  userSelect: 'none',
  WebkitUserSelect: 'none',
  MozUserSelect: 'none',
  msUserSelect: 'none',
};

export const buttonStyle: CSSProperties = {
  background: 'var(--surface, #ffffff)',
  color: 'var(--text, #202c42)',
  border: '1px solid var(--border, #dfe4ec)',
  padding: '3px 10px',
  borderRadius: 5,
  minHeight: 30,
  fontFamily: 'Manrope, Segoe UI, sans-serif',
  cursor: 'pointer',
  fontSize: 11,
};
