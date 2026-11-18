/* ==========================================================================
   Price chart island — vanilla canvas.

   TradingView Lightweight Charts kept right-aligning short series in a wide
   pane (half-empty chart) across multiple stretch/logical-range fixes. We
   only need one close series, a crosshair, and range tabs — so we draw it
   ourselves. No CDN, no dependency.

   X is index-linear (every series fills the pane, weekends and overnight gaps
   don't leave holes) except for 1D, which is laid out by clock time across the
   6.5-hour session so a half-finished day honestly stops part-way across.
   Axis labels sit on calendar boundaries — month starts, year starts, session
   days, whole hours — at the point where that boundary actually falls, never
   spread evenly over the data.
   ========================================================================== */
(function () {
  "use strict";

  var PAD = { top: 16, right: 14, bottom: 30 };
  var SESSION_OPEN = 9 * 60 + 30;      // 9:30 AM, minutes since midnight
  var SESSION_LEN = 390;               // to 4:00 PM
  var MIN_LABEL_GAP = 10;
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function withAlpha(color, a) {
    var m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color);
    if (!m) return color;
    var h = m[1];
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }

  // Timestamps are "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM", already in exchange
  // time, so they are sliced rather than run through Date's timezone rules.
  function parseIso(iso) {
    return {
      y: +iso.slice(0, 4), m: +iso.slice(5, 7), d: +iso.slice(8, 10),
      hh: iso.length > 10 ? +iso.slice(11, 13) : 0,
      mm: iso.length > 10 ? +iso.slice(14, 16) : 0
    };
  }

  function dayNum(p) { return Math.floor(Date.UTC(p.y, p.m - 1, p.d) / 86400000); }
  function weekKey(p) { return Math.floor((dayNum(p) + 3) / 7); }   // weeks start Monday

  function fmtPrice(v) {
    return v == null ? "—" : "$" + v.toLocaleString(undefined, {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  function fmtSignedMoney(v) {
    if (v == null) return "—";
    return (v >= 0 ? "+" : "-") + "$" + Math.abs(v).toLocaleString(undefined, {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  function fmtSignedPct(v) {
    if (v == null) return "";
    return "(" + (v >= 0 ? "+" : "-") + Math.abs(v).toFixed(2) + "%)";
  }

  function fmtDate(iso) {
    var p = parseIso(iso);
    return MONTHS[p.m - 1] + " " + p.d + ", " + p.y;
  }

  function fmtShortDate(iso) {
    var p = parseIso(iso);
    return MONTHS[p.m - 1] + " " + p.d;
  }

  function fmtClock(p) {
    var h12 = p.hh % 12 === 0 ? 12 : p.hh % 12;
    return h12 + ":" + (p.mm < 10 ? "0" : "") + p.mm + (p.hh < 12 ? " AM" : " PM");
  }

  function hourLabel(h) {
    return (h % 12 === 0 ? 12 : h % 12) + (h < 12 ? " AM" : " PM");
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

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
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
    var xf = [];               // x position of each point, 0..1 across the plot
    var info = { range: null, intraday: false, baseline: null, prevClose: null, change: null };
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
        b.title = ok ? "" : (b.dataset.range === "1d"
          ? "Intraday prices aren't available"
          : "Not enough price history for this range");
      });
    }

    function legendLabel(d) {
      if (!d.first_date || !d.last_date) return "";
      var a = d.first_date.slice(0, 10), b = d.last_date.slice(0, 10);
      var span = (d.range === "1d" || a === b) ? fmtDate(b) : fmtDate(a) + " → " + fmtDate(b);
      var tag = (d.range || "").toUpperCase();
      if (d.partial) tag += " · all available";
      return span + "  ·  " + tag + "  ·  " + (d.quote_label || "At close");
    }

    function pointLabel(p) {
      if (!info.intraday) return fmtDate(p.iso);
      var t = parseIso(p.iso);
      return info.range === "1d" ? fmtClock(t) : fmtShortDate(p.iso) + ", " + fmtClock(t);
    }

    // Where each point sits across the plot.
    function layoutX() {
      var n = points.length;
      xf = points.map(function (p, i) {
        if (info.intraday && info.range === "1d") {
          var t = parseIso(p.iso);
          return Math.max(0, Math.min(1, (t.hh * 60 + t.mm - SESSION_OPEN) / SESSION_LEN));
        }
        return n > 1 ? i / (n - 1) : 0.5;
      });
    }

    function xAt(i) { return plot.x0 + xf[i] * plot.w; }

    function yAt(v) {
      var t = (v - plot.min) / (plot.max - plot.min || 1);
      return plot.y0 + plot.h * (1 - t);
    }

    // Axis labels at calendar boundaries: [{f: position 0..1, label}].
    function buildTicks() {
      var n = points.length;
      if (!n) return [];

      if (info.intraday && info.range === "1d") {
        return [10, 12, 14, 16].map(function (h) {
          return { f: (h * 60 - SESSION_OPEN) / SESSION_LEN, label: hourLabel(h) };
        });
      }

      var first = parseIso(points[0].iso), last = parseIso(points[n - 1].iso);
      var spanDays = dayNum(last) - dayNum(first);
      var ticks = [];

      if (spanDays <= 8) {                  // one label per trading day, centred under its bars
        var start = 0;
        for (var i = 1; i <= n; i++) {
          if (i === n || points[i].iso.slice(0, 10) !== points[start].iso.slice(0, 10)) {
            ticks.push({ f: (xf[start] + xf[i - 1]) / 2, label: fmtShortDate(points[start].iso) });
            start = i;
          }
        }
        return ticks;
      }

      var unit = spanDays <= 45 ? "week" : spanDays <= 800 ? "month" : "year";
      var step = 1;
      if (unit === "year") {
        var pxPerYear = plot.w / (spanDays / 365.25);
        var steps = [1, 2, 5, 10, 20];
        step = 20;
        for (var s = 0; s < steps.length; s++) {
          if (steps[s] * pxPerYear >= 64) { step = steps[s]; break; }
        }
      }

      var prev = first;
      for (var j = 1; j < n; j++) {
        var cur = parseIso(points[j].iso), hit = false, label = "";
        if (unit === "week") {
          hit = weekKey(cur) !== weekKey(prev);
          label = MONTHS[cur.m - 1] + " " + cur.d;
        } else if (unit === "month") {
          hit = cur.m !== prev.m;
          label = cur.m === 1 ? String(cur.y) : MONTHS[cur.m - 1];     // January carries the year
        } else {
          hit = cur.y !== prev.y && cur.y % step === 0;
          label = String(cur.y);
        }
        if (hit) ticks.push({ f: xf[j], label: label });
        prev = cur;
      }
      return ticks;
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
      var up = css("--up") || accent;
      var down = css("--down") || accent;
      var text1 = css("--text-1") || "#fff";
      var text2 = css("--text-2") || "#aaa";
      var text3 = css("--text-3") || "#777";
      var border = css("--border-subtle") || "rgba(255,255,255,.08)";
      var borderStrong = css("--border") || border;
      var font = css("--font-sans") || "sans-serif";
      var surface = css("--surface-3") || "#222";

      ctx.clearRect(0, 0, cssW, cssH);
      if (!points.length) return;

      var n = points.length;
      var lineColor = info.change == null || info.change >= 0 ? up : down;

      var vals = points.map(function (p) { return p.value; });
      if (info.prevClose != null) vals.push(info.prevClose);
      var lo = Math.min.apply(null, vals);
      var hi = Math.max.apply(null, vals);
      var pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.02 || 1;
      plot.min = lo - pad;
      plot.max = hi + pad;

      var yTicks = niceScale(plot.min, plot.max, 5);
      var yStep = yTicks.length > 1 ? yTicks[1] - yTicks[0] : 1;
      var decimals = yStep >= 1 ? 0 : yStep >= 0.1 ? 1 : 2;
      var yLabels = yTicks.map(function (v) {
        return v.toLocaleString(undefined, {
          minimumFractionDigits: decimals, maximumFractionDigits: decimals
        });
      });

      ctx.font = "11px " + font;
      var widest = 0;
      yLabels.forEach(function (l) { widest = Math.max(widest, ctx.measureText(l).width); });
      plot.x0 = Math.ceil(widest) + 16;
      plot.y0 = PAD.top;
      plot.w = Math.max(1, cssW - plot.x0 - PAD.right);
      plot.h = Math.max(1, cssH - PAD.top - PAD.bottom);
      var bottom = plot.y0 + plot.h;

      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      yTicks.forEach(function (v, i) {
        var y = yAt(v);
        ctx.strokeStyle = border;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(plot.x0, y);
        ctx.lineTo(plot.x0 + plot.w, y);
        ctx.stroke();
        ctx.fillStyle = text2;
        ctx.fillText(yLabels[i], plot.x0 - 8, y);
      });

      // Day separators on the 5-day view.
      if (info.intraday && info.range === "5d") {
        ctx.strokeStyle = border;
        for (var k = 1; k < n; k++) {
          if (points[k].iso.slice(0, 10) !== points[k - 1].iso.slice(0, 10)) {
            var sx = plot.x0 + ((xf[k] + xf[k - 1]) / 2) * plot.w;
            ctx.beginPath();
            ctx.moveTo(sx, plot.y0);
            ctx.lineTo(sx, bottom);
            ctx.stroke();
          }
        }
      }

      // X axis labels, thinned so they never collide and clamped so the
      // outermost ones are never cut off by the canvas edge.
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      var lastRight = -Infinity;
      buildTicks().forEach(function (t) {
        var x = plot.x0 + t.f * plot.w;
        var w = ctx.measureText(t.label).width;
        var cx = Math.max(w / 2 + 2, Math.min(cssW - w / 2 - 2, x));
        if (cx - w / 2 < lastRight + MIN_LABEL_GAP) return;
        lastRight = cx + w / 2;
        ctx.strokeStyle = text3;
        ctx.beginPath();
        ctx.moveTo(x, bottom);
        ctx.lineTo(x, bottom + 4);
        ctx.stroke();
        ctx.fillStyle = text3;
        ctx.fillText(t.label, cx, bottom + 9);
      });

      // Previous close (1D): the line the day's move is measured from.
      if (info.prevClose != null) {
        var py = yAt(info.prevClose);
        ctx.strokeStyle = text3;
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 4]);
        ctx.beginPath();
        ctx.moveTo(plot.x0, py);
        ctx.lineTo(plot.x0 + plot.w, py);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = text3;
        ctx.textAlign = "right";
        ctx.textBaseline = "bottom";
        ctx.fillText("Previous close " + info.prevClose.toLocaleString(undefined, {
          minimumFractionDigits: 2, maximumFractionDigits: 2
        }), plot.x0 + plot.w - 4, py - 4);
      }

      // Area under the line, fading out downward.
      var grad = ctx.createLinearGradient(0, plot.y0, 0, bottom);
      grad.addColorStop(0, withAlpha(lineColor, 0.26));
      grad.addColorStop(1, withAlpha(lineColor, 0));
      ctx.beginPath();
      points.forEach(function (p, i) {
        var x = xAt(i), y = yAt(p.value);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.lineTo(xAt(n - 1), bottom);
      ctx.lineTo(xAt(0), bottom);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      ctx.beginPath();
      points.forEach(function (p, i) {
        var x = xAt(i), y = yAt(p.value);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = lineColor;
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.stroke();

      if (hoverIndex < 0) {
        ctx.fillStyle = lineColor;
        ctx.beginPath();
        ctx.arc(xAt(n - 1), yAt(points[n - 1].value), 3.5, 0, Math.PI * 2);
        ctx.fill();
        return;
      }

      var hp = points[hoverIndex];
      var hx = xAt(hoverIndex);
      var hy = yAt(hp.value);
      ctx.strokeStyle = text3;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(hx, plot.y0);
      ctx.lineTo(hx, bottom);
      ctx.moveTo(plot.x0, hy);
      ctx.lineTo(plot.x0 + plot.w, hy);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = surface;
      ctx.strokeStyle = lineColor;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(hx, hy, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      // Readout bubble, parked just above the time axis so it never covers the line's peaks.
      ctx.font = "12px " + font;
      var txt = fmtPrice(hp.value) + "   " + pointLabel(hp);
      var bw = ctx.measureText(txt).width + 22, bh = 26;
      var bx = Math.max(plot.x0, Math.min(plot.x0 + plot.w - bw, hx - bw / 2));
      var by = bottom - bh - 8;
      roundRect(ctx, bx, by, bw, bh, 6);
      ctx.fillStyle = surface;
      ctx.fill();
      ctx.strokeStyle = borderStrong;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = text1;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(txt, bx + 11, by + bh / 2 + 0.5);
    }

    function indexFromClientX(clientX) {
      var rect = canvas.getBoundingClientRect();
      var f = (clientX - rect.left - plot.x0) / plot.w;
      var n = points.length;
      if (f < -0.02 || f > xf[n - 1] + 0.02) return -1;     // past the data (e.g. rest of an unfinished day)
      var best = 0, bestD = Infinity;
      for (var i = 0; i < n; i++) {
        var dist = Math.abs(xf[i] - f);
        if (dist < bestD) { bestD = dist; best = i; }
      }
      return best;
    }

    function showHover(i) {
      hoverIndex = i;
      if (i < 0 || i >= points.length) {
        hoverIndex = -1;
        setLegend(idle.price, idle.change, idle.changePct, idle.dateLabel);
        draw();
        return;
      }
      var p = points[i];
      var base = info.baseline;
      setLegend(p.value,
                base != null ? p.value - base : null,
                base ? (p.value / base - 1) * 100 : null,
                pointLabel(p));
      draw();
    }

    canvas.addEventListener("pointermove", function (e) {
      if (!points.length) return;
      showHover(indexFromClientX(e.clientX));
    });
    canvas.addEventListener("pointerleave", function () { showHover(-1); });
    canvas.addEventListener("pointercancel", function () { showHover(-1); });
    canvas.addEventListener("pointerup", function (e) {
      if (e.pointerType === "touch") showHover(-1);
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
          if (!r.ok) {
            throw new Error(r.status === 404 ? "No price history"
              : r.status === 503 ? "Intraday prices are unavailable right now"
              : "Couldn't load prices");
          }
          return r.json();
        })
        .then(function (d) {
          points = (d.points || []).map(function (p) {
            return { iso: p[0], value: p[1] };
          });
          info = {
            range: d.range,
            intraday: !!d.intraday,
            baseline: d.baseline != null ? d.baseline : (points.length ? points[0].value : null),
            prevClose: d.prev_close != null ? d.prev_close : null,
            change: d.change
          };
          layoutX();
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
