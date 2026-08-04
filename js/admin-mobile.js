/**
 * Makes the admin tables readable on a phone.
 *
 * On mobile the CSS turns every `.nd-table` row into a stacked card, which
 * needs each cell to carry its column name. Rather than editing the twenty-odd
 * places that build table HTML — and having to remember it in the next one —
 * the header text is copied onto the cells here, once, for every table.
 *
 * A MutationObserver rather than a one-off pass: the dashboard replaces table
 * bodies with innerHTML on every refresh, so labels stamped at load would be
 * thrown away by the first poll.
 */

function labelRows(table) {
  const headers = [...table.querySelectorAll('thead th')].map(th => th.textContent.trim());
  if (!headers.length) return;
  table.querySelectorAll('tbody tr').forEach(row => {
    [...row.children].forEach((cell, index) => {
      // A cell spanning the table is an empty/error message, not data; a label
      // in front of it would read as a column name for nothing.
      if (cell.colSpan > 1) return;
      const label = headers[index];
      if (label && cell.dataset.label !== label) cell.dataset.label = label;
    });
  });
}

export function initAdminMobileTables(root = document) {
  const run = () => root.querySelectorAll('.nd-table').forEach(labelRows);
  run();

  let queued = false;
  const observer = new MutationObserver(() => {
    // Coalesce: one refresh can rewrite several tables in the same tick, and
    // re-labelling inside the callback would retrigger the observer.
    if (queued) return;
    queued = true;
    // setTimeout, not requestAnimationFrame: rAF is paused in a background or
    // non-painting tab, and the labels would then only appear once the tab was
    // looked at -- by which time the table has already rendered unlabelled.
    setTimeout(() => {
      queued = false;
      run();
    }, 0);
  });

  const host = root.getElementById?.('admin-dashboard-view') || root.body || root;
  if (host) observer.observe(host, { childList: true, subtree: true });
}
