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
    // Chart island (and anything else that painted from CSS vars once) can
    // recolor without a full reload.
    window.dispatchEvent(new CustomEvent("ui:theme", { detail: { theme: next } }));
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-theme-toggle]");
    if (t) { e.preventDefault(); toggleTheme(); return; }

    var d = e.target.closest("[data-set-density]");
    if (d) { e.preventDefault(); setPref("data-density", KEYS.density, d.dataset.setDensity); return; }

    var m = e.target.closest("[data-set-motion]");
    if (m) { e.preventDefault(); setPref("data-motion", KEYS.motion, m.dataset.setMotion); }
  });

  // ---- mood gauge: hover/tap/focus a colour to see what it means --------
  (function () {
    var caption = document.getElementById("mood-zone-caption");
    if (!caption) return;
    var arcs = document.querySelectorAll(".mood-arc");
    var defaultLabel = caption.dataset.defaultLabel;
    var defaultDesc = caption.dataset.defaultDesc;

    function setCaption(label, desc) {
      caption.textContent = "";
      var strong = document.createElement("strong");
      strong.textContent = label;
      caption.append(strong, " — " + desc);
    }

    function show(arc) {
      arcs.forEach(function (a) { a.classList.toggle("is-active", a === arc); });
      setCaption(arc.dataset.zoneLabel, arc.dataset.zoneDesc);
    }

    function reset() {
      arcs.forEach(function (a) { a.classList.remove("is-active"); });
      setCaption(defaultLabel, defaultDesc);
    }

    arcs.forEach(function (arc) {
      arc.addEventListener("mouseenter", function () { show(arc); });
      arc.addEventListener("focus", function () { show(arc); });
      arc.addEventListener("mouseleave", reset);
      arc.addEventListener("blur", reset);
      // Tap-to-pin on touch: a second tap on the same zone (or elsewhere)
      // returns to today's actual reading rather than staying stuck.
      arc.addEventListener("click", function (e) {
        e.preventDefault();
        if (arc.classList.contains("is-active")) reset(); else show(arc);
      });
    });
  })();

  // ---- bar charts: hover/tap/focus a bar for the exact figure -----------
  // Every value already renders as a real label with zero JS (bar_chart()
  // in web/app.py bakes it into the SVG), so this only adds the second
  // layer: click/tap/keyboard-focus a bar to show the EXACT underlying
  // number (no B/M/K rounding) in the caption paragraph that follows the
  // chart in its own panel. Same interaction shape as the mood gauge above
  // on purpose -- one habit, every chart on the site. Works for any future
  // bar_chart() output automatically; nothing here is specific to revenue.
  document.querySelectorAll(".chart").forEach(function (svg) {
    var panel = svg.closest(".panel") || svg.parentElement;
    var caption = panel && panel.querySelector("[data-chart-caption]");
    if (!caption) return;
    var bars = svg.querySelectorAll(".chart-bar");
    if (!bars.length) return;
    var defaultText = caption.textContent.trim();

    function show(bar) {
      bars.forEach(function (b) { b.classList.toggle("is-active", b === bar); });
      caption.textContent = "";
      var strong = document.createElement("strong");
      strong.textContent = bar.dataset.label + " (" + bar.dataset.value + ")";
      caption.append(strong, ": " + bar.dataset.exact + " exact");
    }

    function reset() {
      bars.forEach(function (b) { b.classList.remove("is-active"); });
      caption.textContent = defaultText;
    }

    bars.forEach(function (bar) {
      bar.addEventListener("mouseenter", function () { show(bar); });
      bar.addEventListener("focus", function () { show(bar); });
      bar.addEventListener("mouseleave", reset);
      bar.addEventListener("blur", reset);
      bar.addEventListener("click", function () {
        if (bar.classList.contains("is-active")) reset(); else show(bar);
      });
    });
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

  // ---- browse filters panel ---------------------------------------------
  // Links always exist in the markup (crawlers see every filter regardless
  // of panel state) -- this only toggles visibility, never builds the DOM.
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-filters-toggle]");
    if (!t) return;
    e.preventDefault();
    var panel = document.getElementById(t.getAttribute("aria-controls"));
    if (!panel) return;
    panel.hidden = !panel.hidden;
    t.setAttribute("aria-expanded", panel.hidden ? "false" : "true");
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
