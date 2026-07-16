import { CSSProperties } from 'react';

export const SelectStyle: CSSProperties = {
  background: '#3c3c3c',
  color: '#e0e0e0',
  border: '1px solid #555',
  padding: '4px 8px',
  borderRadius: 3,
  fontSize: 12,
};

export const noSelect: CSSProperties = {
  userSelect: 'none',
  WebkitUserSelect: 'none',
  MozUserSelect: 'none',
  msUserSelect: 'none',
};

export const buttonStyle: CSSProperties = {
  background: '#3c3c3c',
  color: '#e0e0e0',
  border: '1px solid #555',
  padding: '3px 10px',
  borderRadius: 3,
  cursor: 'pointer',
  fontSize: 11,
};
