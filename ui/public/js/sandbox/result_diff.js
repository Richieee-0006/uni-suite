/**
 * Semantic responsive Expected vs. Actual Result Diff table component.
 * - Uses native HTML5 <table>, <caption>, <thead>, <tbody>, <th scope="col|row">.
 * - Non-color-only ✓ / ✕ / + / − markers (WCAG 1.4.1).
 * - Responsive stacking on narrow containers via @container + data-label attributes.
 * - Strict Trusted Types 'none' safe: pure DOM node creation without inline styles.
 */

function createEl(tag, className, attrs) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v !== undefined && v !== null) {
        node.setAttribute(k, String(v));
      }
    }
  }
  return node;
}

function clearEl(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function formatCellValue(val) {
  if (val === null || val === undefined) {
    return "NULL";
  }
  if (typeof val === "number") {
    if (!Number.isFinite(val)) {
      return String(val);
    }
    return Number.isInteger(val) ? String(val) : Number(val.toPrecision(6)).toString();
  }
  if (typeof val === "boolean") {
    return val ? "TRUE" : "FALSE";
  }
  return String(val);
}

function cellsMatch(a, b, tol = 1e-4) {
  if (a === null && b === null) {
    return true;
  }
  if (a === undefined && b === undefined) {
    return true;
  }
  if (typeof a === "number" && typeof b === "number") {
    if (Number.isNaN(a) && Number.isNaN(b)) {
      return true;
    }
    return Math.abs(a - b) <= tol;
  }
  return String(a) === String(b);
}

export class SandboxResultDiff {
  constructor(container) {
    if (!container) {
      throw new Error("SandboxResultDiff requires a mount container.");
    }
    this.container = container;
    this.container.classList.add("sbx-diff-container");
    this.render(null);
  }

  render(tableDiff) {
    clearEl(this.container);

    if (
      !tableDiff ||
      !Array.isArray(tableDiff.columns) ||
      tableDiff.columns.length === 0
    ) {
      const empty = createEl("p", "sbx-empty-note");
      empty.textContent =
        "Tento běh nevytvořil žádný tabulkový výstup pro porovnání.";
      this.container.appendChild(empty);
      return;
    }

    const columns = tableDiff.columns.map(String);
    const expectedRows = Array.isArray(tableDiff.expected_rows)
      ? tableDiff.expected_rows
      : [];
    const actualRows = Array.isArray(tableDiff.actual_rows)
      ? tableDiff.actual_rows
      : [];
    const rowMatches = Array.isArray(tableDiff.row_matches)
      ? tableDiff.row_matches
      : [];
    const totalRows = Math.max(expectedRows.length, actualRows.length);
    const mismatchCount = rowMatches.filter((m) => !m).length;
    const isMatched = Boolean(tableDiff.matched);

    // Non-color-only summary bar.
    const summary = createEl(
      "div",
      isMatched ? "sbx-diff-summary sbx-diff-summary-ok" : "sbx-diff-summary sbx-diff-summary-fail",
      { role: "status" },
    );
    const badge = createEl("span", "sbx-diff-badge");
    badge.textContent = isMatched ? "✓ Shoda" : "✕ Rozdíl";
    const summaryText = createEl("span", "sbx-diff-summary-text");
    summaryText.textContent = isMatched
      ? `Výstup odpovídá očekávání (${actualRows.length} řádků, ${columns.length} sloupců).`
      : `Nalezeno ${mismatchCount} rozdílných řádků (očekáváno ${expectedRows.length}, získáno ${actualRows.length}).`;
    summary.appendChild(badge);
    summary.appendChild(summaryText);
    this.container.appendChild(summary);

    const scrollRegion = createEl("div", "sbx-diff-scroll", {
      role: "region",
      tabindex: "0",
      "aria-label": "Porovnání tabulkového výstupu (Očekáváno vs. Skutečnost)",
    });

    const table = createEl("table", "sbx-diff-table");
    const caption = createEl("caption", "visually-hidden");
    caption.textContent = "Porovnání očekávaných a skutečných řádků tabulky";
    table.appendChild(caption);

    const thead = createEl("thead");
    const headRow = createEl("tr");
    const statusTh = createEl("th", "sbx-diff-col-status", { scope: "col" });
    statusTh.textContent = "Stav";
    headRow.appendChild(statusTh);

    for (const col of columns) {
      const th = createEl("th", "sbx-diff-col", { scope: "col" });
      th.textContent = col;
      headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = createEl("tbody");
    for (let i = 0; i < totalRows; i += 1) {
      const hasExp = i < expectedRows.length;
      const hasAct = i < actualRows.length;
      const matched = Boolean(rowMatches[i]);

      let rowClass = "sbx-diff-row-match";
      let markerText = `✓ #${i + 1}`;
      if (!hasAct && hasExp) {
        rowClass = "sbx-diff-row-missing";
        markerText = `− #${i + 1} chybí`;
      } else if (hasAct && !hasExp) {
        rowClass = "sbx-diff-row-extra";
        markerText = `+ #${i + 1} navíc`;
      } else if (!matched) {
        rowClass = "sbx-diff-row-mismatch";
        markerText = `✕ #${i + 1} rozdíl`;
      }

      const tr = createEl("tr", rowClass);
      const rowTh = createEl("th", "sbx-diff-row-status", { scope: "row" });
      rowTh.textContent = markerText;
      tr.appendChild(rowTh);

      const expRow = hasExp ? expectedRows[i] : [];
      const actRow = hasAct ? actualRows[i] : [];

      for (let c = 0; c < columns.length; c += 1) {
        const td = createEl("td", "sbx-diff-cell", {
          "data-label": columns[c],
        });
        const expVal = expRow[c];
        const actVal = actRow[c];

        if (!hasAct && hasExp) {
          const missSpan = createEl("span", "sbx-diff-val-expected");
          missSpan.textContent = `oček. ${formatCellValue(expVal)}`;
          td.appendChild(missSpan);
        } else if (hasAct && !hasExp) {
          const extraSpan = createEl("span", "sbx-diff-val-actual");
          extraSpan.textContent = formatCellValue(actVal);
          td.appendChild(extraSpan);
        } else if (cellsMatch(actVal, expVal)) {
          td.textContent = formatCellValue(actVal);
        } else {
          td.classList.add("sbx-diff-cell-mismatch");
          const actEl = createEl("span", "sbx-diff-val-actual");
          actEl.textContent = `✕ ${formatCellValue(actVal)}`;
          const expEl = createEl("span", "sbx-diff-val-expected");
          expEl.textContent = `✓ ${formatCellValue(expVal)}`;
          td.appendChild(actEl);
          td.appendChild(expEl);
        }
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }

    table.appendChild(tbody);
    scrollRegion.appendChild(table);
    this.container.appendChild(scrollRegion);
  }

  destroy() {
    clearEl(this.container);
  }
}
