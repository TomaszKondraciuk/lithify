// Runs in <head>, before the page is drawn: the theme chosen on this page (kept by this browser),
// so the page does not flash in the system's colours first. No choice, or no storage: the system's.
'use strict';
try {
  const theme = localStorage.getItem('lithify-theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
} catch (_) { /* storage blocked: the system's theme */ }
