/* ==========================================================================
   Price chart island — TradingView Lightweight Charts.

   Why this library: it's ~45 KB, canvas-rendered, and purpose-built for
   financial series. Chart.js is general-purpose and struggles at this point
   density; D3 means hand-building crosshairs, tooltips and range handling.

   Why an island: the library is only loaded on pages that actually draw a
   chart, and only once that chart scrolls into view. The landing page and
   every other route pay nothing for it.
   ========================================================================== */
(function () {
  "use strict";

  // cdnjs dropped this library from its catalog at some point after this was
  // written (confirmed 2026-08-19: the old cdnjs URL 404s). jsDelivr mirrors
  // every npm version indefinitely, so pin there instead of chasing whatever
  // host has it this month.
  var LIB = "https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js";
  var libPromise = null;

  function loadLib() {
    if (window.LightweightCharts) return Promise.resolve();
    if (libPromise) return libPromise;
    libPromise = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = LIB;
      s.onload = resolve;
      s.onerror = function () { reject(new Error("chart library failed to load")); };
      document.head.appendChild(s);
    });
    return libPromise;
  }

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function fmtPrice(v) {
    return v == null ? "—" : v.toLocaleString(undefined, {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  function init(root) {
    var ticker = root.dataset.chart;
    var legend = root.querySelector("[data-chart-legend]");
    var canvas = root.querySelector("[data-chart-canvas]");
    var status = root.querySelector("[data-chart-status]");
    var buttons = root.querySelectorAll("[data-range]");
    var chart, line, controller;

    function setStatus(msg) {
      if (status) { status.textContent = msg || ""; status.hidden = !msg; }
    }

    function build() {
      chart = LightweightCharts.createChart(canvas, {
        height: 320,
        autoSize: true,
        layout: {
          background: { type: "solid", color: "transparent" },
          textColor: css("--text-2"),
          fontFamily: css("--font-mono") || "monospace",
          fontSize: 11
        },
        grid: {
          vertLines: { visible: false },
          horzLines: { color: css("--border-subtle") }
        },
        rightPriceScale: { borderColor: css("--border") },
        timeScale: { borderColor: css("--border"), fixLeftEdge: true, fixRightEdge: true },
        crosshair: {
          mode: LightweightCharts.CrosshairMode.Magnet,
          vertLine: { color: css("--text-3"), width: 1, style: 2, labelBackgroundColor: css("--surface-3") },
          horzLine: { color: css("--text-3"), width: 1, style: 2, labelBackgroundColor: css("--surface-3") }
        },
        handleScale: false,
        handleScroll: false
      });

      line = chart.addAreaSeries({
        lineColor: css("--accent"),
        topColor: css("--accent-bg"),
        bottomColor: "transparent",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false
      });

      // Crosshair drives the legend, so the number under the cursor is always
      // the number being read. Without this a chart is decorative.
      chart.subscribeCrosshairMove(function (param) {
        if (!legend) return;
        if (!param.time || !param.seriesData || !param.seriesData.get(line)) {
          legend.textContent = legend.dataset.summary || "";
          return;
        }
        var v = param.seriesData.get(line);
        legend.textContent = param.time + "   " + fmtPrice(v.value);
      });
    }

    function load(range) {
      setStatus("Loading…");
      if (controller) controller.abort();
      controller = new AbortController();

      return fetch("/api/chart/" + encodeURIComponent(ticker) + "?range=" + range,
                   { signal: controller.signal })
        .then(function (r) {
          if (!r.ok) throw new Error(r.status === 404 ? "No price history" : "Couldn't load prices");
          return r.json();
        })
        .then(function (d) {
          if (!chart) build();
          line.setData(d.points.map(function (p) { return { time: p[0], value: p[1] }; }));
          chart.timeScale().fitContent();

          if (legend) {
            var pct = d.change_pct;
            var arrow = pct >= 0 ? "▲" : "▼";
            var summary = fmtPrice(d.last) + "   " + arrow + " " +
                          Math.abs(pct).toFixed(2) + "%  over " + d.range;
            legend.dataset.summary = summary;
            legend.textContent = summary;
            legend.style.color = pct >= 0 ? css("--up") : css("--down");
          }
          setStatus("");
        })
        .catch(function (err) {
          if (err.name === "AbortError") return;
          setStatus(err.message);
        });
    }

    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        buttons.forEach(function (o) { o.setAttribute("aria-pressed", "false"); });
        b.setAttribute("aria-pressed", "true");
        load(b.dataset.range);
      });
    });

    var active = root.querySelector('[data-range][aria-pressed="true"]');
    loadLib()
      .then(function () { return load(active ? active.dataset.range : "1y"); })
      .catch(function () { setStatus("Chart unavailable offline"); });
  }

  // Only pay for the library once a chart is actually about to be seen.
  function observe() {
    document.querySelectorAll("[data-chart]").forEach(function (root) {
      if (root.dataset.chartReady) return;
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting) {
            io.disconnect();
            root.dataset.chartReady = "1";
            init(root);
          }
        });
      }, { rootMargin: "200px" });
      io.observe(root);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", observe);
  } else {
    observe();
  }
})();
