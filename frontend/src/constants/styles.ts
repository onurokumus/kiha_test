import { CSSProperties } from 'react';

export const SelectStyle: CSSProperties = {
  background: '#f8f9fc',
  color: '#202c42',
  border: '1px solid #dfe4ec',
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
  background: '#ffffff',
  color: '#202c42',
  border: '1px solid #dfe4ec',
  padding: '3px 10px',
  borderRadius: 5,
  minHeight: 30,
  fontFamily: 'Manrope, Segoe UI, sans-serif',
  cursor: 'pointer',
  fontSize: 11,
};
