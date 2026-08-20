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

  // Lightweight Charts' "business day" mode (plain 'YYYY-MM-DD' strings)
  // treats every provided point as one evenly-spaced slot, regardless of how
  // many real days separate it from the next one. That's fine for unbroken
  // daily data, but our 5y/max ranges are downsampled to one point a week or
  // month -- with real, uneven gaps between them (holidays, missing data) --
  // so business-day mode drew them as if evenly spaced, which is what made
  // the long ranges look compressed/warped instead of a clean timeline.
  // UTCTimestamp mode fixes that: positions and tick marks (including the
  // "just show the year" labels at wide zoom) are genuinely time-linear.
  function toUnixTime(iso) {
    return Math.floor(Date.parse(iso + "T00:00:00Z") / 1000);
  }

  function fmtPrice(v) {
    return v == null ? "—" : "$" + v.toLocaleString(undefined, {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  function fmtSignedMoney(v) {
    if (v == null) return "—";
    var sign = v >= 0 ? "+" : "-";
    return sign + "$" + Math.abs(v).toLocaleString(undefined, {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  function fmtSignedPct(v) {
    if (v == null) return "";
    var sign = v >= 0 ? "+" : "-";
    return "(" + sign + Math.abs(v).toFixed(2) + "%)";
  }

  function fmtDate(iso) {
    var d = new Date(iso + "T00:00:00Z");
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
  }

  // MAX can span many years once real price history goes back further than
  // today's ~1-year provider cap, and at that zoom the library's default
  // formatter mixes month/day ticks in with year ticks -- fine up close, but
  // "the big picture" is exactly what should read as a clean year-by-year
  // timeline. Force every tick to a bare year only for this range.
  function yearOnlyTickFormatter(time) {
    return String(new Date(time * 1000).getUTCFullYear());
  }

  function defaultTickFormatter(time, tickMarkType) {
    var d = new Date(time * 1000);
    var TT = LightweightCharts.TickMarkType;
    if (tickMarkType === TT.Year) return String(d.getUTCFullYear());
    if (tickMarkType === TT.Month) {
      return d.toLocaleDateString(undefined, { month: "short", timeZone: "UTC" });
    }
    if (tickMarkType === TT.DayOfMonth) {
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
    }
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  }

  // The actual bug, finally correctly diagnosed: fitContent() guarantees
  // every bar is *visible*, not that the bars *fill the panel*. With a
  // sparse series (13 monthly points on MAX, ~50 on 1y) in a wide box, the
  // library shows everything at a modest default bar spacing and leaves
  // the rest of the width empty -- rather than stretching bars to fill it.
  // That's independent of container size or resize timing, which is why
  // two rounds of "fix the resize race" didn't move it: the race wasn't
  // the bug. setVisibleLogicalRange maps a given bar-index range directly
  // and linearly across the *entire* current pixel width, which is what
  // "fill the box" actually requires. [-0.5, n-0.5] is the standard trick
  // for "show exactly these n bars, edge to edge, no matter how few there
  // are" -- half a bar of padding on each side keeps the first/last point
  // from sitting flush against the axis lines.
  function stretchToFill(chart, pointCount) {
    if (pointCount > 0) {
      chart.timeScale().setVisibleLogicalRange({ from: -0.5, to: pointCount - 0.5 });
    }
  }

  function init(root) {
    var ticker = root.dataset.chart;
    var priceEl = root.querySelector("[data-legend-price]");
    var changeEl = root.querySelector("[data-legend-change]");
    var dateEl = root.querySelector("[data-legend-date]");
    var status = root.querySelector("[data-chart-status]");
    var canvas = root.querySelector("[data-chart-canvas]");
    var buttons = root.querySelectorAll("[data-range]");
    var chart, line, controller;
    var firstValue = null;     // period-start price, so any hovered point can
                                // show "vs start of range" like the idle summary does
    var pointCount = 0;        // needed on every resize too, not just on load

    function setStatus(msg) {
      if (status) { status.textContent = msg || ""; status.hidden = !msg; }
    }

    function setLegend(price, change, changePct, dateLabel) {
      if (priceEl) priceEl.textContent = fmtPrice(price);
      if (changeEl) {
        changeEl.textContent = change == null ? ""
          : fmtSignedMoney(change) + " " + fmtSignedPct(changePct);
        changeEl.classList.toggle("up", change != null && change >= 0);
        changeEl.classList.toggle("down", change != null && change < 0);
      }
      if (dateEl) dateEl.textContent = dateLabel || "";
    }

    function build() {
      chart = LightweightCharts.createChart(canvas, {
        width: canvas.clientWidth,
        height: canvas.clientHeight,
        layout: {
          background: { type: "solid", color: "transparent" },
          textColor: css("--text-2"),
          fontFamily: css("--font-sans"),
          fontSize: 12
        },
        grid: {
          vertLines: { visible: false },
          horzLines: { color: css("--border-subtle") }
        },
        rightPriceScale: { visible: false },
        leftPriceScale: {
          visible: true, borderColor: css("--border"),
          scaleMargins: { top: 0.10, bottom: 0.08 }
        },
        timeScale: {
          borderColor: css("--border"), fixLeftEdge: true, fixRightEdge: true,
          timeVisible: false, secondsVisible: false,
          tickMarkFormatter: defaultTickFormatter
        },
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
        lastValueVisible: false,
        priceScaleId: "left"
      });

      // Deliberately not using the `autoSize` option. It installs its own
      // internal ResizeObserver that resizes the canvas but does NOT re-fit
      // the visible range afterwards -- it just grows the canvas and leaves
      // the new room empty, which is the exact bug this replaces. A second,
      // independent ResizeObserver calling fitContent() (the previous fix
      // here) doesn't reliably win that race either: two separate observers
      // on the same element fire in unspecified relative order, so ours
      // could run before the library's own internal resize has actually
      // applied the new width. Doing both steps ourselves, in one callback,
      // in a guaranteed order, removes the race entirely.
      new ResizeObserver(function (entries) {
        var box = entries[0].contentRect;
        if (box.width > 0 && box.height > 0) {
          chart.resize(box.width, box.height);
          stretchToFill(chart, pointCount);
        }
      }).observe(canvas);

      // Crosshair drives the legend, so the number under the cursor is always
      // the number being read, framed the same way as the idle state (change
      // vs. the start of the visible range) rather than switching metaphors.
      // param.time is a raw UTCTimestamp (seconds) in this mode, not an
      // object -- format it straight from that, in UTC, so the date shown
      // matches the trading day the point was stored under, not whatever the
      // viewer's local timezone rolls it into.
      chart.subscribeCrosshairMove(function (param) {
        if (!param.time || !param.seriesData || !param.seriesData.get(line)) {
          setLegend(idle.price, idle.change, idle.changePct, idle.dateLabel);
          return;
        }
        var v = param.seriesData.get(line).value;
        var change = firstValue != null ? v - firstValue : null;
        var changePct = firstValue ? (v / firstValue - 1) * 100 : null;
        var hoverDate = new Date(param.time * 1000).toLocaleDateString(
          undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
        setLegend(v, change, changePct, hoverDate);
      });
    }

    var idle = { price: null, change: null, changePct: null, dateLabel: "" };

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
          chart.applyOptions({ timeScale: {
            tickMarkFormatter: range === "max" ? yearOnlyTickFormatter : defaultTickFormatter
          } });
          firstValue = d.points.length ? d.points[0][1] : null;
          pointCount = d.points.length;
          line.setData(d.points.map(function (p) {
            return { time: toUnixTime(p[0]), value: p[1] };
          }));
          stretchToFill(chart, pointCount);

          idle = {
            price: d.last, change: d.change, changePct: d.change_pct,
            dateLabel: d.last_date ? fmtDate(d.last_date) + "  ·  " + d.range.toUpperCase() : "",
          };
          setLegend(idle.price, idle.change, idle.changePct, idle.dateLabel);
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
