import { useSyncExternalStore } from 'react';

export type Theme = 'light' | 'dark';
// Keep the key in sync with public/theme.js, which runs before the first paint.
const THEME_KEY = 'ptt.theme.v1';
const listeners = new Set<() => void>();
let current: Theme = 'light';
let preference: Theme | null = null;
let initialized = false;

function readPreference(): Theme | null {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    return saved === 'light' || saved === 'dark' ? saved : null;
  } catch {
    return null;
  }
}

function applyTheme(theme: Theme) {
  current = theme;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.documentElement.style.backgroundColor = theme === 'dark' ? '#101824' : '#f7f8fa';
  document.querySelector('meta[name="theme-color"]')?.setAttribute(
    'content', theme === 'dark' ? '#101824' : '#f7f8fa',
  );
  listeners.forEach(listener => listener());
}

/** Theme is local UI state, independent of analysis sessions and server defaults. */
export function initializeTheme() {
  if (initialized) return;
  initialized = true;
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  preference = readPreference();
  applyTheme(preference ?? (system.matches ? 'dark' : 'light'));
  system.addEventListener('change', event => {
    if (!preference) applyTheme(event.matches ? 'dark' : 'light');
  });
  window.addEventListener('storage', event => {
    if (event.key !== THEME_KEY && event.key !== null) return;
    preference = readPreference();
    applyTheme(preference ?? (system.matches ? 'dark' : 'light'));
  });
}

export function toggleTheme() {
  preference = current === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem(THEME_KEY, preference); } catch { /* Still works for this tab. */ }
  applyTheme(preference);
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
};

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, () => current, () => 'light');
}
