/* ==========================================================================
   Price chart island — vanilla canvas.

   TradingView Lightweight Charts kept right-aligning short series in a wide
   pane (half-empty chart) across multiple stretch/logical-range fixes. We
   only need one close series, a crosshair, and range tabs — so we draw it
   ourselves. X is index-linear across the full width: every series fills
   the pane by construction. No CDN, ~same interaction surface as before.
   ========================================================================== */
(function () {
  "use strict";

  var PAD = { top: 16, right: 12, bottom: 28, left: 56 };

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
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
    return d.toLocaleDateString(undefined, {
      year: "numeric", month: "short", day: "numeric", timeZone: "UTC"
    });
  }

  function fmtTick(iso, mode) {
    var d = new Date(iso + "T00:00:00Z");
    if (mode === "year") {
      return d.toLocaleDateString(undefined, { year: "numeric", timeZone: "UTC" });
    }
    if (mode === "month") {
      return d.toLocaleDateString(undefined, { month: "short", year: "2-digit", timeZone: "UTC" });
    }
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
  }

  function niceScale(lo, hi, target) {
    if (!(hi > lo)) { lo -= 1; hi += 1; }
    var span = hi - lo;
    var step = Math.pow(10, Math.floor(Math.log10(span / target)));
    var err = (span / target) / step;
    if (err >= 5) step *= 5;
    else if (err >= 2) step *= 2;
    var start = Math.floor(lo / step) * step;
    var ticks = [];
    for (var v = start; v <= hi + step * 0.5; v += step) {
      if (v >= lo - step * 0.01 && v <= hi + step * 0.01) ticks.push(v);
    }
    return ticks;
  }

  function init(root) {
    var ticker = root.dataset.chart;
    var priceEl = root.querySelector("[data-legend-price]");
    var changeEl = root.querySelector("[data-legend-change]");
    var dateEl = root.querySelector("[data-legend-date]");
    var status = root.querySelector("[data-chart-status]");
    var host = root.querySelector("[data-chart-canvas]");
    var buttons = root.querySelectorAll("[data-range]");

    var canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", ticker + " price chart");
    host.innerHTML = "";
    host.appendChild(canvas);
    var ctx = canvas.getContext("2d");

    var points = [];           // [{iso, value}]
    var firstValue = null;
    var currentRange = null;
    var hoverIndex = -1;
    var idle = { price: null, change: null, changePct: null, dateLabel: "" };
    var controller = null;
    var plot = { x0: 0, y0: 0, w: 0, h: 0, min: 0, max: 1 };

    function setStatus(msg) {
      if (status) { status.textContent = msg || ""; status.hidden = !msg; }
    }

    function setLegend(price, change, changePct, dateLabel) {
      if (priceEl) priceEl.textContent = fmtPrice(price);
      if (changeEl) {
        changeEl.textContent = change == null ? ""
          : fmtSignedMoney(change) + " " + fmtSignedPct(changePct);
        changeEl.classList.toggle("up", change != null && change > 0);
        changeEl.classList.toggle("down", change != null && change < 0);
      }
      if (dateEl) dateEl.textContent = dateLabel || "";
    }

    function markPressed(range) {
      buttons.forEach(function (b) {
        b.setAttribute("aria-pressed", b.dataset.range === range ? "true" : "false");
      });
      currentRange = range;
    }

    function applyAvailability(available) {
      if (!available) return;
      buttons.forEach(function (b) {
        var ok = available[b.dataset.range] !== false;
        b.disabled = !ok;
        b.setAttribute("aria-disabled", ok ? "false" : "true");
        b.title = ok ? "" : "Not enough price history for this range";
      });
    }

    function legendLabel(d) {
      if (!d.first_date || !d.last_date) return "";
      var span = fmtDate(d.first_date) + " → " + fmtDate(d.last_date);
      var tag = (d.range || "").toUpperCase();
      if (d.partial) tag += " · all available";
      return span + "  ·  " + tag + "  ·  " + (d.quote_label || "At close");
    }

    function xAt(i, n) {
      if (n <= 1) return plot.x0 + plot.w / 2;
      return plot.x0 + (i / (n - 1)) * plot.w;
    }

    function yAt(v) {
      var t = (v - plot.min) / (plot.max - plot.min || 1);
      return plot.y0 + plot.h * (1 - t);
    }

    function tickMode() {
      if (points.length < 2) return "day";
      var a = new Date(points[0].iso + "T00:00:00Z");
      var b = new Date(points[points.length - 1].iso + "T00:00:00Z");
      var days = (b - a) / 86400000;
      if (days > 800) return "year";
      if (days > 90) return "month";
      return "day";
    }

    function draw() {
      var dpr = window.devicePixelRatio || 1;
      var cssW = host.clientWidth;
      var cssH = host.clientHeight;
      if (cssW < 2 || cssH < 2) return;

      canvas.width = Math.floor(cssW * dpr);
      canvas.height = Math.floor(cssH * dpr);
      canvas.style.width = cssW + "px";
      canvas.style.height = cssH + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      var accent = css("--accent") || "#e0a83c";
      var accentBg = css("--accent-bg") || "rgba(224,168,60,.14)";
      var text2 = css("--text-2") || "#aaa";
      var text3 = css("--text-3") || "#777";
      var border = css("--border-subtle") || "rgba(255,255,255,.08)";
      var font = css("--font-sans") || "sans-serif";
      var surface = css("--surface-3") || "#222";

      ctx.clearRect(0, 0, cssW, cssH);

      plot.x0 = PAD.left;
      plot.y0 = PAD.top;
      plot.w = Math.max(1, cssW - PAD.left - PAD.right);
      plot.h = Math.max(1, cssH - PAD.top - PAD.bottom);

      if (!points.length) return;

      var vals = points.map(function (p) { return p.value; });
      var lo = Math.min.apply(null, vals);
      var hi = Math.max.apply(null, vals);
      var pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.02 || 1;
      plot.min = lo - pad;
      plot.max = hi + pad;

      var yTicks = niceScale(plot.min, plot.max, 6);
      ctx.font = "11px " + font;
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      yTicks.forEach(function (v) {
        var y = yAt(v);
        ctx.strokeStyle = border;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(plot.x0, y);
        ctx.lineTo(plot.x0 + plot.w, y);
        ctx.stroke();
        ctx.fillStyle = text2;
        ctx.fillText(v.toLocaleString(undefined, {
          minimumFractionDigits: 2, maximumFractionDigits: 2
        }), plot.x0 - 8, y);
      });

      var n = points.length;
      var mode = tickMode();
      var xCount = mode === "year" ? 4 : mode === "month" ? 6 : 5;
      var xIdxs = [];
      if (n === 1) xIdxs = [0];
      else {
        for (var t = 0; t < xCount; t++) {
          xIdxs.push(Math.round(t * (n - 1) / (xCount - 1)));
        }
      }
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = text3;
      var seen = {};
      xIdxs.forEach(function (i) {
        var label = fmtTick(points[i].iso, mode);
        if (seen[label] && mode === "year") return;
        seen[label] = 1;
        ctx.fillText(label, xAt(i, n), plot.y0 + plot.h + 8);
      });

      // Area fill under the line, edge to edge.
      ctx.beginPath();
      points.forEach(function (p, i) {
        var x = xAt(i, n), y = yAt(p.value);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.lineTo(xAt(n - 1, n), plot.y0 + plot.h);
      ctx.lineTo(xAt(0, n), plot.y0 + plot.h);
      ctx.closePath();
      ctx.fillStyle = accentBg;
      ctx.fill();

      ctx.beginPath();
      points.forEach(function (p, i) {
        var x = xAt(i, n), y = yAt(p.value);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = accent;
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.stroke();

      if (hoverIndex >= 0 && hoverIndex < n) {
        var hp = points[hoverIndex];
        var hx = xAt(hoverIndex, n);
        var hy = yAt(hp.value);
        ctx.strokeStyle = text3;
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(hx, plot.y0);
        ctx.lineTo(hx, plot.y0 + plot.h);
        ctx.moveTo(plot.x0, hy);
        ctx.lineTo(plot.x0 + plot.w, hy);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = accent;
        ctx.beginPath();
        ctx.arc(hx, hy, 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = surface;
        ctx.strokeStyle = accent;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(hx, hy, 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      }
    }

    function indexFromClientX(clientX) {
      var rect = canvas.getBoundingClientRect();
      var x = clientX - rect.left;
      if (points.length <= 1) return 0;
      var t = (x - plot.x0) / plot.w;
      t = Math.max(0, Math.min(1, t));
      return Math.round(t * (points.length - 1));
    }

    function showHover(i) {
      hoverIndex = i;
      if (i < 0 || i >= points.length) {
        setLegend(idle.price, idle.change, idle.changePct, idle.dateLabel);
        draw();
        return;
      }
      var p = points[i];
      var change = firstValue != null ? p.value - firstValue : null;
      var changePct = firstValue ? (p.value / firstValue - 1) * 100 : null;
      setLegend(p.value, change, changePct, fmtDate(p.iso));
      draw();
    }

    canvas.addEventListener("mousemove", function (e) {
      if (!points.length) return;
      showHover(indexFromClientX(e.clientX));
    });
    canvas.addEventListener("mouseleave", function () {
      showHover(-1);
    });

    new ResizeObserver(function () { draw(); }).observe(host);
    window.addEventListener("ui:theme", function () { draw(); });

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
          points = (d.points || []).map(function (p) {
            return { iso: p[0], value: p[1] };
          });
          firstValue = points.length ? points[0].value : null;
          hoverIndex = -1;
          applyAvailability(d.available_ranges);
          markPressed(d.range);
          idle = {
            price: d.last, change: d.change, changePct: d.change_pct,
            dateLabel: legendLabel(d)
          };
          setLegend(idle.price, idle.change, idle.changePct, idle.dateLabel);
          setStatus("");
          draw();
        })
        .catch(function (err) {
          if (err.name === "AbortError") return;
          setStatus(err.message);
          if (currentRange) markPressed(currentRange);
        });
    }

    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        if (b.disabled) return;
        load(b.dataset.range);
      });
    });

    var active = root.querySelector('[data-range][aria-pressed="true"]');
    load(active ? active.dataset.range : "1y");
  }

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
