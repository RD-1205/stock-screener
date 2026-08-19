/* ==========================================================================
   Ticker belt behaviour.

   Two jobs, and the second one is the subtle part:

   1. Swap in the user's watchlist if they have one (localStorage, no account
      needed -- see R5.7).
   2. Refresh prices WITHOUT restarting the scroll.

   On (2): the obvious implementation re-renders the track's HTML on each poll.
   That resets the CSS animation, so the belt visibly jumps back to the start
   every refresh. Instead we mutate only the price and change text nodes in
   place -- the track keeps scrolling and the numbers change underneath it.

   Polling, not SSE or WebSocket: with 15-minute delayed quotes there is
   nothing to stream, and polling is a fraction of the operational complexity.
   ========================================================================== */
(function () {
  "use strict";

  var POLL_MS = 45000;
  var WATCHLIST_KEY = "watchlist";
  var tape = document.querySelector("[data-tape]");
  if (!tape) return;

  var track = tape.querySelector("[data-tape-track]");
  var sourceEl = tape.querySelector("[data-tape-source]");
  var setEl = tape.querySelector("[data-tape-set]");
  var timer = null;

  function watchlist() {
    try {
      var raw = localStorage.getItem(WATCHLIST_KEY);
      var list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list.filter(Boolean).slice(0, 30) : [];
    } catch (e) { return []; }
  }

  function symbols() {
    var out = [];
    track.querySelectorAll("[data-tape-item]").forEach(function (el) {
      var s = el.dataset.tapeItem;
      if (out.indexOf(s) === -1) out.push(s);
    });
    return out;
  }

  function fmt(v) {
    return v == null ? "—" : v.toLocaleString(undefined, {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  function paint(sym, q) {
    // Both the visible item and its aria-hidden duplicate carry the same
    // data-tape-item, so this updates the clone too and the loop stays
    // consistent as it wraps.
    track.querySelectorAll('[data-tape-item="' + sym + '"]').forEach(function (el) {
      var priceEl = el.querySelector("[data-tape-price]");
      var chgEl = el.querySelector("[data-tape-change]");
      if (!priceEl) return;

      var next = fmt(q.price);
      if (priceEl.textContent.trim() !== next) {
        var rising = (q.change_pct || 0) >= 0;
        el.classList.add(rising ? "flash-up" : "flash-down");
        setTimeout(function () {
          el.classList.remove("flash-up", "flash-down");
        }, 900);
      }
      priceEl.textContent = next;

      if (chgEl) {
        if (q.change_pct == null) {
          chgEl.textContent = "—";
          chgEl.className = "tape-chg dim";
        } else {
          var up = q.change_pct >= 0;
          chgEl.textContent = (up ? "▲" : "▼") + Math.abs(q.change_pct).toFixed(2) + "%";
          chgEl.className = "tape-chg " + (up ? "up" : "down");
        }
      }
    });
  }

  function refresh() {
    var syms = symbols();
    if (!syms.length) return;
    fetch("/api/quote?symbols=" + encodeURIComponent(syms.join(",")))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.quotes) return;
        Object.keys(d.quotes).forEach(function (sym) { paint(sym, d.quotes[sym]); });
        if (sourceEl && d.label) sourceEl.textContent = d.label;
      })
      .catch(function () { /* a decorative strip must never break the page */ });
  }

  function renderItem(sym, q) {
    var a = document.createElement("a");
    a.className = "tape-item";
    a.href = "/stocks/" + encodeURIComponent(sym);
    a.dataset.tapeItem = sym;
    var up = (q && q.change_pct != null) ? q.change_pct >= 0 : true;
    a.innerHTML =
      '<span class="tape-sym"></span>' +
      '<span class="tape-px" data-tape-price></span>' +
      '<span class="tape-chg ' + (q && q.change_pct != null ? (up ? "up" : "down") : "dim") +
      '" data-tape-change></span>';
    a.querySelector(".tape-sym").textContent = sym;
    a.querySelector("[data-tape-price]").textContent = q ? fmt(q.price) : "—";
    a.querySelector("[data-tape-change]").textContent =
      (q && q.change_pct != null)
        ? (up ? "▲" : "▼") + Math.abs(q.change_pct).toFixed(2) + "%"
        : "—";
    return a;
  }

  function useWatchlist(list) {
    fetch("/api/quote?symbols=" + encodeURIComponent(list.join(",")))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.quotes) return;
        var known = list.filter(function (s) { return d.quotes[s]; });
        if (!known.length) return;              // keep the default set

        track.innerHTML = "";
        for (var pass = 0; pass < 2; pass++) {
          known.forEach(function (sym) {
            var el = renderItem(sym, d.quotes[sym]);
            if (pass === 1) {
              el.setAttribute("aria-hidden", "true");
              el.setAttribute("tabindex", "-1");
            }
            track.appendChild(el);
          });
        }
        // Keep scroll speed constant regardless of how many symbols there are.
        tape.style.setProperty("--tape-duration", (known.length * 4) + "s");
        if (setEl) setEl.textContent = "Watchlist";
        if (sourceEl && d.label) sourceEl.textContent = d.label;
      })
      .catch(function () { /* fall back to the server-rendered set */ });
  }

  var list = watchlist();
  if (list.length) useWatchlist(list);

  // Don't poll a tab nobody is looking at.
  function start() { if (!timer) timer = setInterval(refresh, POLL_MS); }
  function stop() { clearInterval(timer); timer = null; }
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop(); else { refresh(); start(); }
  });
  start();

  window.tapeRefresh = refresh;               // exposed for tests/debugging
})();
