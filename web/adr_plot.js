(function () {
  "use strict";

  const DEFAULT_SUMMARY = "/adr_plot_lab/fixtures/yuusei85_fake_phase_clouds.json";

  const COLORS = {
    phase1Safe: "#6baed6",
    phase1Unsafe: "#cc4c4c",
    phase2: "#9ecae1",
    phase3: "#74c476",
    phase4: "#fd8d3c",
    phase3Extra: "#756bb1",
    fixed: "#737373",
    frontier: "#111111",
    selectedFallback: "#238b45",
    selected: {
      Recommended: "#d4a017",
      Aggressive: "#e6550d",
      Calm: "#00897b",
      "Max Spread": "#1f78b4",
      "Efficiency Potential": "#2ca25f",
      "Memory Potential": "#756bb1",
      Original: "#d62728"
    }
  };

  const SELECTED_PRIORITY = [
    "Recommended",
    "Aggressive",
    "Calm",
    "Efficiency Potential",
    "Memory Potential",
    "Max Spread",
    "Original"
  ];

  const PHASE_LAYERS = [
    {
      label: "Phase 1 safe",
      keys: ["phase1_safe", "phase_1_safe"],
      color: COLORS.phase1Safe,
      symbol: "circle",
      size: 5,
      opacity: 0.16
    },
    {
      label: "Phase 1 unsafe",
      keys: ["phase1_unsafe", "phase_1_unsafe"],
      color: COLORS.phase1Unsafe,
      symbol: "x",
      size: 5,
      opacity: 0.12
    },
    {
      label: "Phase 2 refine",
      keys: ["phase2", "phase_2", "phase2_refine"],
      color: COLORS.phase2,
      symbol: "circle",
      size: 5,
      opacity: 0.22
    },
    {
      label: "Phase 3 refine",
      keys: ["phase3", "phase_3", "phase3_refine"],
      color: COLORS.phase3,
      symbol: "circle",
      size: 5,
      opacity: 0.24
    },
    {
      label: "Phase 4 hillclimb",
      keys: ["phase4", "phase_4", "phase4_hillclimb"],
      color: COLORS.phase4,
      symbol: "circle",
      size: 6,
      opacity: 0.55
    },
    {
      label: "Phase 3 frontier render-only",
      keys: ["phase3_render_extra", "phase_3_frontier_render_only"],
      color: COLORS.phase3Extra,
      symbol: "diamond",
      size: 6,
      opacity: 0.35
    }
  ];

  function finiteNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function formatNumber(value, digits) {
    const number = finiteNumber(value);
    return number === null ? "n/a" : number.toLocaleString(undefined, {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits
    });
  }

  function fixed(value, digits) {
    const number = finiteNumber(value);
    return number === null ? "n/a" : number.toFixed(digits);
  }

  function formatPercent(value, digits) {
    const number = finiteNumber(value);
    return number === null ? "n/a" : `${(number * 100).toFixed(digits)}%`;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function pointKey(point) {
    return [point.flat, point.s_multi, point.d_multi]
      .map((value) => {
        const number = finiteNumber(value);
        return number === null ? "NaN" : number.toFixed(6);
      })
      .join(",");
  }

  function pointFromCurveItem(item) {
    return item && item.point ? item.point : item;
  }

  function pointsFromCurve(curve) {
    return Array.isArray(curve) ? curve.map(pointFromCurveItem).filter(Boolean) : [];
  }

  function layerSource(summary) {
    if (summary && summary.plot_layers && typeof summary.plot_layers === "object") {
      return summary.plot_layers;
    }
    return summary || {};
  }

  function layerPoints(summary, layer) {
    const source = layerSource(summary);
    for (const key of layer.keys) {
      if (Array.isArray(source[key])) {
        return source[key].map(pointFromCurveItem).filter(Boolean);
      }
    }
    return [];
  }

  function xValues(points) {
    return points.map((point) => point.memorized_cards);
  }

  function yValues(points) {
    return points.map((point) => point.memorized_per_minute);
  }

  function drForPoint(point) {
    if (point.dr_samples) {
      return `dr=${fixed(point.dr_mean, 4)} band=${fixed(point.dr_spread * 100, 2)}%`;
    }
    return "dr=n/a band=n/a";
  }

  function hoverForPoint(point, label) {
    return [
      label ? `${label}<br>` : "",
      `cards=${formatNumber(point.memorized_cards, 1)}`,
      `eff=${formatNumber(point.memorized_per_minute, 2)}`,
      `flat=${fixed(point.flat, 3)} s=${fixed(point.s_multi, 3)} d=${fixed(point.d_multi, 3)}`,
      drForPoint(point)
    ].join("<br>");
  }

  function markerTrace(name, points, options) {
    const marker = {
      color: options.color,
      size: options.size,
      symbol: options.symbol,
      opacity: options.opacity
    };
    if (options.line) {
      marker.line = options.line;
    }
    const trace = {
      type: "scatter",
      mode: "markers",
      name,
      x: xValues(points),
      y: yValues(points),
      hovertext: points.map((point) => hoverForPoint(point, name)),
      hoverinfo: options.hoverinfo || "skip",
      marker,
      showlegend: false
    };
    if (options.customdata) {
      trace.customdata = options.customdata;
    }
    return trace;
  }

  function lineTrace(name, points, options) {
    const trace = {
      type: "scatter",
      mode: options.mode || "lines",
      name,
      x: xValues(points),
      y: yValues(points),
      hovertext: points.map((point) => hoverForPoint(point, name)),
      hoverinfo: "text",
      line: {
        color: options.color,
        width: options.width,
        dash: options.dash || "solid"
      },
      showlegend: false
    };
    if (options.marker) {
      trace.marker = options.marker;
    }
    return trace;
  }

  function selectedGroups(summary) {
    const selected = summary && summary.selected ? summary.selected : {};
    const selectedByKey = new Map();

    Object.keys(selected).forEach((label) => {
      const point = selected[label];
      if (!point) {
        return;
      }
      const key = pointKey(point);
      const current = selectedByKey.get(key) || { point, labels: [] };
      if (!current.labels.includes(label)) {
        current.labels.push(label);
      }
      selectedByKey.set(key, current);
    });

    const grouped = summary && summary.labels_by_point ? summary.labels_by_point : {};
    Object.keys(grouped).forEach((key) => {
      const labels = Array.isArray(grouped[key]) ? grouped[key] : [];
      const matchingPoint = labels.map((label) => selected[label]).find(Boolean);
      if (!matchingPoint) {
        return;
      }
      selectedByKey.set(key, {
        point: matchingPoint,
        labels: labels.slice()
      });
    });

    return Array.from(selectedByKey.values()).sort((a, b) => primaryLabelRank(a.labels) - primaryLabelRank(b.labels));
  }

  function primaryLabelRank(labels) {
    const ranks = labels.map((label) => {
      const index = SELECTED_PRIORITY.indexOf(label);
      return index === -1 ? SELECTED_PRIORITY.length : index;
    });
    return Math.min.apply(null, ranks);
  }

  function primaryLabel(labels) {
    const sorted = labels.slice().sort((a, b) => {
      const ai = SELECTED_PRIORITY.indexOf(a);
      const bi = SELECTED_PRIORITY.indexOf(b);
      return (ai === -1 ? SELECTED_PRIORITY.length : ai) - (bi === -1 ? SELECTED_PRIORITY.length : bi);
    });
    return sorted[0] || "Selected";
  }

  function selectedTraces(summary) {
    return selectedGroups(summary).map((group) => {
      const label = group.labels.join(" / ");
      const primary = primaryLabel(group.labels);
      return markerTrace(label, [group.point], {
        color: COLORS.selected[primary] || COLORS.selectedFallback,
        size: group.labels.includes("Recommended") ? 11 : 10,
        symbol: "circle",
        opacity: 0.98,
        hoverinfo: "text",
        customdata: [{
          labels: group.labels,
          key: pointKey(group.point),
          selected: true
        }],
        line: {
          color: "#ffffff",
          width: 1.2
        }
      });
    });
  }

  function presetName(summary) {
    return (summary && summary.preset && summary.preset.name) || "preset";
  }

  function targetDr(summary) {
    if (summary && summary.target_dr !== undefined && summary.target_dr !== null) {
      return Number(summary.target_dr).toFixed(3);
    }
    return "n/a";
  }

  function buildFigure(summary) {
    const fixedCurveItems = Array.isArray(summary.fixed_curve_points) ? summary.fixed_curve_points : [];
    const fixedCurvePoints = pointsFromCurve(fixedCurveItems);
    const finalFrontier = Array.isArray(summary.final_frontier) ? summary.final_frontier : [];
    const traces = [];
    const phaseCounts = {};

    for (const layer of PHASE_LAYERS) {
      const points = layerPoints(summary, layer);
      phaseCounts[layer.label] = points.length;
      if (points.length) {
        traces.push(markerTrace(layer.label, points, layer));
      }
    }

    if (finalFrontier.length) {
      traces.push(lineTrace("Final verified ADR frontier", finalFrontier, {
        color: COLORS.frontier,
        width: 2.6,
        marker: {
          color: COLORS.frontier,
          size: 4
        },
        mode: "lines+markers"
      }));
    }

    if (fixedCurvePoints.length) {
      traces.push(lineTrace("Fixed DR curve", fixedCurvePoints, {
        color: COLORS.fixed,
        width: 1.6
      }));
    }

    traces.push(...selectedTraces(summary));

    return {
      data: traces,
      layout: {
        title: {
          text: `FSRS-ADR Pareto Search: ${presetName(summary)} target DR ${targetDr(summary)}`,
          x: 0.5,
          xanchor: "center",
          font: {
            size: 18,
            color: "#111111"
          }
        },
        paper_bgcolor: "#ffffff",
        plot_bgcolor: "#ffffff",
        margin: {
          l: 72,
          r: 18,
          t: 48,
          b: 78
        },
        xaxis: {
          title: {
            text: "Average memorized cards",
            standoff: 16
          },
          zeroline: false,
          showgrid: true,
          gridcolor: "rgba(0,0,0,0.10)",
          tickformat: ",.0f",
          fixedrange: true,
          automargin: true
        },
        yaxis: {
          title: {
            text: "Average memorized cards per daily minute",
            standoff: 16
          },
          zeroline: false,
          showgrid: true,
          gridcolor: "rgba(0,0,0,0.10)",
          fixedrange: true,
          automargin: true
        },
        hovermode: false,
        showlegend: false,
        annotations: buildFixedDrAnnotations(fixedCurveItems, summary),
        dragmode: false
      },
      config: {
        staticPlot: true,
        responsive: true,
        displayModeBar: false,
        displaylogo: false
      },
      meta: {
        fixedCurvePoints: fixedCurvePoints.length,
        finalFrontierPoints: finalFrontier.length,
        selectedPoints: selectedGroups(summary).length,
        phaseCounts,
        missingPrimaryPhases: PHASE_LAYERS.slice(0, 5).every((layer) => phaseCounts[layer.label] === 0)
      }
    };
  }

  function buildFixedDrAnnotations(curve, summary) {
    if (!Array.isArray(curve) || curve.length === 0) {
      return [];
    }
    const target = finiteNumber(summary.target_dr);
    const targetPct = target === null ? null : target * 100;
    const labelsByKey = new Map();

    function keepClosest(key, distance, annotation) {
      const existing = labelsByKey.get(key);
      if (!existing || distance < existing.distance) {
        labelsByKey.set(key, { distance, annotation });
      }
    }

    curve.forEach((item) => {
      const dr = finiteNumber(item.dr);
      const point = pointFromCurveItem(item);
      if (dr === null || !point) {
        return;
      }
      const pct = dr * 100;
      const targetDistance = targetPct === null ? Infinity : Math.abs(pct - targetPct);
      const decadePct = Math.round(pct / 10) * 10;
      const decadeDistance = Math.abs(pct - decadePct);
      const isTarget = targetDistance <= 0.11;
      const isTenth = decadeDistance <= 0.4;
      if (!isTarget && !isTenth) {
        return;
      }

      const labelPct = isTarget ? Math.round(targetPct) : decadePct;
      const annotation = {
        x: point.memorized_cards,
        y: point.memorized_per_minute,
        text: isTarget ? `Target ${labelPct}%` : `${labelPct}%`,
        showarrow: true,
        arrowhead: 0,
        ax: 9,
        ay: isTarget ? 13 : -12,
        font: {
          size: 11,
          color: "#5b5b5b"
        },
        arrowcolor: "#777777",
        bgcolor: "rgba(255,255,255,0.38)",
        borderpad: 1
      };

      if (isTarget) {
        keepClosest(`target:${labelPct}`, targetDistance, annotation);
      } else {
        keepClosest(`fixed:${decadePct}`, decadeDistance, annotation);
      }
    });

    return Array.from(labelsByKey.values())
      .map((entry) => entry.annotation)
      .sort((left, right) => left.x - right.x);
  }

  function layerSymbolClass(layer) {
    if (layer.symbol === "x") {
      return "cross";
    }
    if (layer.symbol === "diamond") {
      return "diamond";
    }
    return "";
  }

  function buildResultBox(summary) {
    const metrics = summary.selected_fixed_curve_metrics || {};
    const layerRows = PHASE_LAYERS.map((layer) => {
      const count = layerPoints(summary, layer).length;
      const faded = count === 0 ? " opacity: 0.35;" : "";
      return `
        <div class="legend-row" style="${faded}">
          <span class="legend-symbol ${layerSymbolClass(layer)}" style="color:${layer.color}"></span>
          <span>${escapeHtml(layer.label)}</span>
        </div>
      `;
    }).join("");

    const lineRows = `
      <div class="legend-row">
        <span class="legend-symbol line thick" style="color:${COLORS.frontier}"></span>
        <span>Final verified ADR frontier</span>
      </div>
      <div class="legend-row">
        <span class="legend-symbol line" style="color:${COLORS.fixed}"></span>
        <span>Fixed DR curve</span>
      </div>
    `;

    const selectedRows = selectedGroups(summary).map((group) => {
      const primary = primaryLabel(group.labels);
      const color = COLORS.selected[primary] || COLORS.selectedFallback;
      const metric = group.labels.map((label) => metrics[label]).find(Boolean);
      const point = group.point;
      const metricText = metric
        ? `eff=${metric.efficiency_label} mem=${metric.memory_label} spread=${metric.spread_label}`
        : "eff=n/a mem=n/a spread=n/a";
      const text = [
        `flat=${fixed(point.flat, 3)} s=${fixed(point.s_multi, 3)} d=${fixed(point.d_multi, 3)}`,
        drForPoint(point),
        metricText
      ].join("\n");
      return `
        <div class="selected-item" data-point-key="${escapeHtml(pointKey(point))}">
          <span class="legend-symbol" style="color:${color}"></span>
          <div>
            <div class="selected-name">${escapeHtml(group.labels.join(" / "))}</div>
            <div class="selected-lines">${escapeHtml(text)}</div>
          </div>
        </div>
      `;
    }).join("");

    const hasPrimaryPhases = PHASE_LAYERS.slice(0, 5).some((layer) => layerPoints(summary, layer).length > 0);
    const dataNote = hasPrimaryPhases ? "" : `
      <div class="data-note">
        Phase 1-4 clouds are not in this summary JSON. The renderer will draw them when a future summary includes plot_layers.
      </div>
    `;

    return `${layerRows}${lineRows}<div class="selected-report">${selectedRows}</div>${dataNote}`;
  }

  async function render(container, summary) {
    if (!window.Plotly) {
      throw new Error("Plotly is not loaded.");
    }
    const figure = buildFigure(summary);
    window.AdrParetoPlot.lastFigure = figure;
    container.__adrLastFigure = figure;
    container.__adrSelectPoint = setActiveSelectedPoint;
    await window.Plotly.react(container, figure.data, figure.layout, figure.config);
    const box = document.getElementById("result-box");
    if (box) {
      box.innerHTML = buildResultBox(summary);
    }
    wireSelectedPointInteractions(container, figure);
    return figure;
  }

  function setActiveSelectedPoint(payload) {
    const box = document.getElementById("result-box");
    if (!box || !payload) {
      return;
    }
    box.querySelectorAll(".selected-item.active").forEach((node) => {
      node.classList.remove("active");
    });
    const active = box.querySelector(`[data-point-key="${cssEscape(payload.key)}"]`);
    if (active) {
      active.classList.add("active");
    }
    setStatus(`Selected ${payload.labels.join(" / ")}.`);
  }

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(value);
    }
    return String(value).replace(/["\\]/g, "\\$&");
  }

  function clearActiveSelectedPoint() {
    const box = document.getElementById("result-box");
    if (!box) {
      return;
    }
    box.querySelectorAll(".selected-item.active").forEach((node) => {
      node.classList.remove("active");
    });
  }

  function wireSelectedPointInteractions(container, figure) {
    if (!container || !figure) {
      return;
    }
    const payloads = figure.data
      .filter((trace) => trace.customdata && trace.customdata[0] && trace.customdata[0].selected)
      .map((trace) => trace.customdata[0]);
    const nodes = container.querySelectorAll(".point.plotly-customdata");
    nodes.forEach((node, index) => {
      const payload = payloads[index];
      if (!payload) {
        return;
      }
      node.setAttribute("role", "button");
      node.setAttribute("tabindex", "0");
      node.setAttribute("aria-label", payload.labels.join(" / "));
      node.addEventListener("mouseenter", () => setActiveSelectedPoint(payload));
      node.addEventListener("mouseleave", clearActiveSelectedPoint);
      node.addEventListener("focus", () => setActiveSelectedPoint(payload));
      node.addEventListener("blur", clearActiveSelectedPoint);
      node.addEventListener("click", (event) => {
        event.stopPropagation();
        setActiveSelectedPoint(payload);
      });
      node.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          setActiveSelectedPoint(payload);
        }
      });
    });
  }

  async function loadSummary(path) {
    if (window.location.protocol === "file:") {
      throw new Error(
        "JSON loading is blocked from file:// pages. Open this preview through the local server at http://127.0.0.1:8788/adr_plot_lab/web/index.html, or use Open JSON to choose a local file."
      );
    }
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Could not load ${path}: HTTP ${response.status}`);
    }
    return response.json();
  }

  function setStatus(message) {
    const status = document.getElementById("status");
    if (status) {
      status.textContent = message || "";
    }
  }

  function setTitle(summary, source) {
    const title = document.getElementById("summary-title");
    if (title) {
      title.textContent = `${presetName(summary)} | target DR ${targetDr(summary)} | ${source}`;
    }
  }

  function statusText(figure) {
    const phaseTotal = Object.values(figure.meta.phaseCounts).reduce((sum, count) => sum + count, 0);
    const missing = figure.meta.missingPrimaryPhases
      ? " Phase 1-4 point clouds were not present in this JSON."
      : "";
    return `Rendered ${phaseTotal} phase/background points, ${figure.meta.fixedCurvePoints} fixed-curve points, ${figure.meta.finalFrontierPoints} frontier points, ${figure.meta.selectedPoints} selected points.${missing}`;
  }

  async function loadAndRender(path) {
    const plot = document.getElementById("plot");
    const input = document.getElementById("summary-path");
    if (!plot) {
      return;
    }
    if (input) {
      input.value = path;
    }
    setStatus("Loading...");
    const summary = await loadSummary(path);
    const figure = await render(plot, summary);
    setTitle(summary, path);
    setStatus(statusText(figure));
  }

  async function renderInitialSummary(source) {
    const plot = document.getElementById("plot");
    if (!plot || !window.ADR_INITIAL_SUMMARY) {
      return false;
    }
    const input = document.getElementById("summary-path");
    const label = source || window.ADR_INITIAL_SOURCE || "embedded summary";
    if (input) {
      input.value = label;
    }
    try {
      setStatus("Loading...");
      const figure = await render(plot, window.ADR_INITIAL_SUMMARY);
      setTitle(window.ADR_INITIAL_SUMMARY, label);
      setStatus(statusText(figure));
      return true;
    } catch (error) {
      setStatus(error.message);
      return true;
    }
  }

  function wirePreviewPage() {
    const plot = document.getElementById("plot");
    if (!plot) {
      return;
    }

    const params = new URLSearchParams(window.location.search);
    const initialPath = params.get("summary");
    const form = document.getElementById("summary-form");
    const fileInput = document.getElementById("summary-file");

    if (form) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const input = document.getElementById("summary-path");
        const path = input && input.value ? input.value.trim() : DEFAULT_SUMMARY;
        if (path) {
          loadAndRender(path).catch((error) => setStatus(error.message));
        }
      });
    }

    if (fileInput) {
      fileInput.addEventListener("change", async () => {
        const file = fileInput.files && fileInput.files[0];
        if (!file) {
          return;
        }
        try {
          setStatus("Loading...");
          const summary = JSON.parse(await file.text());
          const figure = await render(plot, summary);
          setTitle(summary, file.name);
          setStatus(statusText(figure));
        } catch (error) {
          setStatus(error.message);
        }
      });
    }

    if (initialPath) {
      loadAndRender(initialPath).catch((error) => setStatus(error.message));
      return;
    }
    renderInitialSummary(window.ADR_INITIAL_SOURCE).then((rendered) => {
      if (!rendered) {
        loadAndRender(DEFAULT_SUMMARY).catch((error) => setStatus(error.message));
      }
    });
  }

  window.AdrParetoPlot = {
    buildFigure,
    buildResultBox,
    render,
    selectPoint: setActiveSelectedPoint,
    lastFigure: null
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wirePreviewPage);
  } else {
    wirePreviewPage();
  }
}());
