/* ==========================================================================
   Screener range-filter grid (P4). No framework -- this mirrors
   screener.screen.compile_ranges() in JS so the query can update live as
   someone types, without a round trip per keystroke. The Python function is
   the actual source of truth (it's what runs the real query); this only has
   to produce the same string.

   The compiled string is the ONLY thing that reaches the server -- it's
   written into the existing #q field and submitted exactly like a
   hand-typed query, so /screener and /results need no new parameters.
   ========================================================================== */
(function () {
  "use strict";

  var grid = document.getElementById("range-grid");
  var qInput = document.getElementById("q");
  if (!grid || !qInput) return;   // only present on /screener

  var rangeSection = document.getElementById("range-section");
  var advancedToggle = document.querySelector("[data-advanced-toggle]");

  function compile() {
    var rows = Array.prototype.slice.call(grid.querySelectorAll(".range-row"));
    rows.sort(function (a, b) { return a.dataset.metric.localeCompare(b.dataset.metric); });
    var clauses = [];
    rows.forEach(function (row) {
      var name = row.dataset.metric;
      var lo = row.querySelector(".range-min").value.trim();
      var hi = row.querySelector(".range-max").value.trim();
      if (lo) clauses.push(name + " >= " + lo);
      if (hi) clauses.push(name + " <= " + hi);
    });
    return clauses.join(" and ");
  }

  function sync() {
    if (qInput.hidden) qInput.value = compile();
  }

  grid.addEventListener("input", sync);

  grid.addEventListener("click", function (e) {
    var rm = e.target.closest(".range-row-remove");
    if (!rm) return;
    rm.closest(".range-row").remove();
    sync();
  });

  // ---- "+ Add metric" menu ----------------------------------------------
  var addToggle = document.querySelector("[data-add-metric-toggle]");
  var addMenu = document.querySelector("[data-add-metric-menu]");

  function addRow(key, label, minPlaceholder, maxPlaceholder) {
    if (grid.querySelector('.range-row[data-metric="' + key + '"]')) return;
    var row = document.createElement("div");
    row.className = "range-row";
    row.dataset.metric = key;
    var labelEl = document.createElement("span");
    labelEl.className = "range-row-label";
    labelEl.textContent = label;
    var min = document.createElement("input");
    min.type = "text"; min.inputMode = "decimal"; min.className = "range-min";
    min.placeholder = minPlaceholder || "min";
    var max = document.createElement("input");
    max.type = "text"; max.inputMode = "decimal"; max.className = "range-max";
    max.placeholder = maxPlaceholder || "max";
    var remove = document.createElement("button");
    remove.type = "button"; remove.className = "range-row-remove";
    remove.setAttribute("aria-label", "Remove " + label + " filter");
    remove.textContent = "✕";
    row.append(labelEl, min, max, remove);
    grid.appendChild(row);
  }

  if (addToggle && addMenu) {
    addToggle.addEventListener("click", function (e) {
      e.preventDefault();
      addMenu.hidden = !addMenu.hidden;
    });
    addMenu.addEventListener("click", function (e) {
      var item = e.target.closest("[data-add-metric]");
      if (!item) return;
      e.preventDefault();
      addRow(item.dataset.addMetric, item.dataset.label,
             item.dataset.minPlaceholder, item.dataset.maxPlaceholder);
      addMenu.hidden = true;
    });
    document.addEventListener("click", function (e) {
      if (!addMenu.hidden && !addToggle.contains(e.target) && !addMenu.contains(e.target)) {
        addMenu.hidden = true;
      }
    });
  }

  // ---- Advanced (text DSL) toggle ---------------------------------------
  // Same show/hide pattern as the browse Filters panel: links/inputs always
  // exist, this only flips which editor is visible.
  function setAdvanced(open) {
    qInput.hidden = !open;
    if (rangeSection) rangeSection.hidden = open;
    if (advancedToggle) {
      advancedToggle.setAttribute("aria-expanded", String(open));
      advancedToggle.textContent = open ? "Back to ranges" : "Advanced: edit as text";
    }
    if (open) {
      qInput.focus();
    } else {
      sync();
    }
  }

  if (advancedToggle) {
    advancedToggle.addEventListener("click", function (e) {
      e.preventDefault();
      setAdvanced(qInput.hidden);
    });
  }

  // Example chips show the exact query that ran -- some use `or`, which a
  // range grid can't express, so they always jump to the text view.
  document.querySelectorAll("[data-example-q]").forEach(function (chip) {
    chip.addEventListener("click", function () {
      qInput.value = chip.dataset.exampleQ;
      setAdvanced(true);
      if (window.htmx) window.htmx.trigger(qInput.closest("form"), "submit");
    });
  });

  sync();
})();
