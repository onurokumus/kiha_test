// Set the palette before CSS/app startup, including when the settings API is slow.
(function () {
  var saved;
  try { saved = localStorage.getItem('ptt.theme.v1'); } catch (_) { /* Storage may be blocked. */ }
  var theme = saved === 'light' || saved === 'dark'
    ? saved : window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.documentElement.style.backgroundColor = theme === 'dark' ? '#101824' : '#f7f8fa';
})();
