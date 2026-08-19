/* ==========================================================================
   Site chrome: theme, density, motion. No framework.

   The no-flash init lives inline in base.html <head> and runs BEFORE first
   paint. This file only handles the toggles afterwards, so it can load
   deferred without causing a flash of the wrong theme.
   ========================================================================== */
(function () {
  "use strict";

  var KEYS = { theme: "ui.theme", density: "ui.density", motion: "ui.motion" };
  var root = document.documentElement;

  function store(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }

  function setPref(attr, key, value) {
    root.setAttribute(attr, value);
    store(key, value);
  }

  // ---- theme ------------------------------------------------------------
  function currentTheme() {
    return root.getAttribute("data-theme") ||
      (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  }

  function toggleTheme() {
    var next = currentTheme() === "dark" ? "light" : "dark";
    setPref("data-theme", KEYS.theme, next);
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      btn.setAttribute("aria-label", "Switch to " + (next === "dark" ? "light" : "dark") + " theme");
      var icon = btn.querySelector("[data-theme-icon]");
      if (icon) icon.textContent = next === "dark" ? "☾" : "☀";
    });
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-theme-toggle]");
    if (t) { e.preventDefault(); toggleTheme(); return; }

    var d = e.target.closest("[data-set-density]");
    if (d) { e.preventDefault(); setPref("data-density", KEYS.density, d.dataset.setDensity); return; }

    var m = e.target.closest("[data-set-motion]");
    if (m) { e.preventDefault(); setPref("data-motion", KEYS.motion, m.dataset.setMotion); }
  });

  // ---- search palette placeholder --------------------------------------
  // Wired properly in build step 2 (R2). For now Cmd/Ctrl-K focuses the hero
  // search if it's on the page, otherwise navigates to /search. Shipping the
  // shortcut early means the muscle memory is right from day one.
  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      var field = document.querySelector("[data-search-input]");
      if (field) { field.focus(); field.select(); }
      else { window.location.href = "/search"; }
    }
  });

  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-search-trigger]")) {
      e.preventDefault();
      var field = document.querySelector("[data-search-input]");
      if (field) { field.focus(); field.select(); }
      else { window.location.href = "/search"; }
    }
  });
})();
