from __future__ import annotations

import json
from typing import Any


def workload_graph_html(snapshot: dict[str, Any]) -> str:
    data = {
        "label": snapshot.get("label", ""),
        "dailyLoad": snapshot.get("daily_load", 0),
        "dueCounts": {str(k): int(v) for k, v in snapshot.get("due_counts", {}).items()},
    }
    payload = json.dumps(data, ensure_ascii=False)
    html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body { margin: 0; padding: 18px; font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #20242a; background: #f7f8fa; }
    .toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
    button { border: 1px solid #c8ced8; background: white; border-radius: 4px; padding: 5px 10px; cursor: pointer; }
    button.active { background: #2f6f9f; color: white; border-color: #2f6f9f; }
    .summary { margin-left: auto; color: #4a5562; }
    svg { width: 100%; height: 420px; background: white; border: 1px solid #d8dee8; }
    .bar { fill: #4f8f63; }
    .axis { stroke: #8a94a3; stroke-width: 1; }
    .tick { fill: #5d6875; font-size: 11px; }
    .empty { fill: #6b7280; font-size: 16px; }
  </style>
</head>
<body>
  <div class="toolbar">
    <button data-range="31" class="active">1 month</button>
    <button data-range="90">3 months</button>
    <button data-range="365">1 year</button>
    <button data-range="all">All</button>
    <span class="summary" id="summary"></span>
  </div>
  <svg viewBox="0 0 900 420" role="img" aria-label="Future workload graph">
    <g id="plot"></g>
  </svg>
  <script>
    const snapshot = __PAYLOAD__;
    let activeRange = 31;
    const plot = document.getElementById("plot");
    const summary = document.getElementById("summary");
    function entriesForRange(range) {
      const entries = Object.entries(snapshot.dueCounts).map(([day, count]) => [Number(day), Number(count)]);
      let minDay = Math.min(0, ...entries.map(([day]) => day));
      let maxDay = Math.max(1, ...entries.map(([day]) => day));
      if (range !== "all") {
        maxDay = Number(range);
      }
      return { entries: entries.filter(([day]) => day <= maxDay), minDay, maxDay };
    }
    function render() {
      const { entries, minDay, maxDay } = entriesForRange(activeRange);
      plot.innerHTML = "";
      const width = 900, height = 420, left = 48, right = 16, top = 24, bottom = 42;
      const days = [];
      for (let day = minDay; day <= maxDay; day++) days.push(day);
      const counts = new Map(entries);
      const total = days.reduce((sum, day) => sum + (counts.get(day) || 0), 0);
      summary.textContent = `Total: ${total} | Daily load: ${snapshot.dailyLoad}/day`;
      if (!total) {
        plot.innerHTML = `<text class="empty" x="450" y="210" text-anchor="middle">No review workload in this range</text>`;
        return;
      }
      const maxCount = Math.max(1, ...days.map(day => counts.get(day) || 0));
      const plotWidth = width - left - right;
      const plotHeight = height - top - bottom;
      const barWidth = Math.max(1, plotWidth / Math.max(1, days.length));
      plot.innerHTML += `<line class="axis" x1="${left}" y1="${height-bottom}" x2="${width-right}" y2="${height-bottom}"></line>`;
      plot.innerHTML += `<line class="axis" x1="${left}" y1="${top}" x2="${left}" y2="${height-bottom}"></line>`;
      for (const [index, day] of days.entries()) {
        const count = counts.get(day) || 0;
        const barHeight = count / maxCount * plotHeight;
        const x = left + index * barWidth;
        const y = height - bottom - barHeight;
        plot.innerHTML += `<rect class="bar" x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${Math.max(1, barWidth - 1).toFixed(2)}" height="${barHeight.toFixed(2)}"><title>Day ${day}: ${count} cards</title></rect>`;
      }
      for (const frac of [0, .25, .5, .75, 1]) {
        const value = Math.round(maxCount * frac);
        const y = height - bottom - plotHeight * frac;
        plot.innerHTML += `<text class="tick" x="${left-8}" y="${y+4}" text-anchor="end">${value}</text>`;
      }
      const ticks = [minDay, 0, Math.round(maxDay/2), maxDay].filter((v, i, a) => a.indexOf(v) === i);
      for (const day of ticks) {
        const x = left + (day - minDay) / Math.max(1, maxDay - minDay) * plotWidth;
        plot.innerHTML += `<text class="tick" x="${x}" y="${height-16}" text-anchor="middle">${day}</text>`;
      }
    }
    for (const button of document.querySelectorAll("button[data-range]")) {
      button.addEventListener("click", () => {
        for (const other of document.querySelectorAll("button")) other.classList.remove("active");
        button.classList.add("active");
        activeRange = button.dataset.range === "all" ? "all" : Number(button.dataset.range);
        render();
      });
    }
    render();
  </script>
</body>
</html>"""
    return html.replace("__PAYLOAD__", payload)
