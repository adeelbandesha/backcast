"use strict";

const root = document.documentElement;
const darkQuery = matchMedia("(prefers-color-scheme: dark)");

// Theme: an explicit choice wins; otherwise follow the operating system.

const isDark = () => (root.dataset.theme ? root.dataset.theme === "dark" : darkQuery.matches);

function syncThemeToggles() {
  for (const button of document.querySelectorAll("[data-theme-toggle]")) {
    button.setAttribute("aria-pressed", String(isDark()));
  }
}

function themeChanged() {
  syncThemeToggles();
  document.dispatchEvent(new Event("backcast:theme"));
}

for (const button of document.querySelectorAll("[data-theme-toggle]")) {
  button.addEventListener("click", () => {
    root.dataset.theme = isDark() ? "light" : "dark";
    try {
      localStorage.setItem("backcast-theme", root.dataset.theme);
    } catch {}
    themeChanged();
  });
}
darkQuery.addEventListener("change", () => {
  if (!root.dataset.theme) themeChanged();
});
syncThemeToggles();

// Symbol combobox (ARIA 1.2 pattern: focus stays in the input, options are aria-activedescendant).

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function initCombobox(box) {
  const input = box.querySelector("input");
  const list = box.querySelector("[role=listbox]");
  let options = [];
  let active = -1;
  let timer;
  let controller;

  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  };

  const highlight = (index) => {
    active = index;
    list.querySelectorAll("[role=option]").forEach((option, i) => {
      option.setAttribute("aria-selected", String(i === index));
      if (i === index) {
        input.setAttribute("aria-activedescendant", option.id);
        option.scrollIntoView({ block: "nearest" });
      }
    });
  };

  const choose = (index) => {
    input.value = options[index].symbol;
    close();
    input.form.requestSubmit();
  };

  const show = (results) => {
    options = results;
    list.replaceChildren(
      ...results.map((result, i) => {
        const option = element("li");
        option.id = `${list.id}-${i}`;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        option.append(
          element("span", "option-symbol", result.symbol),
          element("span", "option-name", result.name),
          element("span", "option-exchange", result.exchange),
        );
        option.addEventListener("pointerdown", (event) => event.preventDefault()); // keep focus in the input
        option.addEventListener("click", () => choose(i));
        return option;
      }),
    );
    active = -1;
    if (!results.length) return close();
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    controller?.abort();
    const query = input.value.trim();
    if (!query) return close();
    timer = setTimeout(async () => {
      controller = new AbortController();
      try {
        const response = await fetch(`${box.dataset.searchUrl}?q=${encodeURIComponent(query)}`, { signal: controller.signal });
        show(response.ok ? (await response.json()).results : []);
      } catch (error) {
        if (error.name !== "AbortError") close();
      }
    }, 180);
  });

  input.addEventListener("keydown", (event) => {
    const open = !list.hidden;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!options.length) return;
      event.preventDefault();
      if (!open) {
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
      }
      const step = event.key === "ArrowDown" ? 1 : -1;
      highlight((active + step + options.length) % options.length);
    } else if (event.key === "Enter" && open && active >= 0) {
      event.preventDefault();
      choose(active);
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      close();
    }
  });

  input.addEventListener("blur", close);
}

document.querySelectorAll("[data-combobox]").forEach(initCombobox);

// Results: the run is queued or running in the worker, so poll until it is done.

async function loadResults(box, loadingMarkup) {
  const started = Date.now();
  const elapsed = box.querySelector(".elapsed");
  const progress = box.querySelector(".progress");
  const tick = setInterval(() => {
    elapsed.textContent = `${Math.round((Date.now() - started) / 1000)} s`;
  }, 1000);
  try {
    let response;
    // 202 means not finished yet; the body says whether the run is queued or running.
    while ((response = await fetch(box.dataset.src)).status === 202) {
      progress.textContent = await response.text();
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    box.innerHTML = await response.text();
  } catch {
    const retry = element("button", "button button-primary", "Try again");
    retry.type = "button";
    retry.addEventListener("click", () => {
      box.innerHTML = loadingMarkup;
      box.setAttribute("aria-busy", "true");
      loadResults(box, loadingMarkup);
    });
    const notice = element("div", "notice");
    notice.setAttribute("role", "alert");
    notice.append(element("p", null, "Lost touch with the server while waiting for the results. The run may still be going."));
    const actions = element("p");
    actions.append(retry);
    notice.append(actions);
    box.replaceChildren(notice);
    return;
  } finally {
    clearInterval(tick);
    box.removeAttribute("aria-busy");
  }
  const headline = box.querySelector(".headline, .notice");
  if (headline && (document.activeElement === document.body || !document.activeElement)) {
    headline.tabIndex = -1;
    headline.focus({ preventScroll: true });
  }
  if (document.getElementById("chart-data")) initCharts();
}

// Charts. SVG presentation attributes can't read CSS variables, so token values are resolved here
// and every chart is redrawn when the theme changes.

// d3 and Plot load only on backtest pages.
const parseDate = window.d3?.utcParse("%Y-%m-%d");
const formatDate = window.d3?.utcFormat("%-d %b %Y");
const formatPrice = window.d3?.format(",.2f");
const formatPoints = (value) => window.d3.format("+,.1f")(value).replace("-", "−");
const formatError = window.d3?.format(".2%");

function tokens() {
  const style = getComputedStyle(root);
  const read = (name) => style.getPropertyValue(`--${name}`).trim();
  return {
    ink: read("ink"),
    ink2: read("ink-2"),
    muted: read("muted"),
    rule: read("rule"),
    ruleStrong: read("rule-strong"),
    surface: read("surface"),
    accent: read("accent"),
    better: read("better-mark"),
    worse: read("worse-mark"),
    sans: read("sans"),
  };
}

function initCharts() {
  const data = JSON.parse(document.getElementById("chart-data").textContent);
  const names = Object.fromEntries(data.models.map((model) => [model.key, model.name]));
  const picker = document.querySelector(".model-picker");
  const charts = document.querySelector(".charts");
  const draw = () => {
    const key = picker.querySelector("input:checked")?.value ?? data.best;
    const t = tokens();
    drawPrice(charts.querySelector('[data-chart="price"]'), data, key, names[key], t);
    drawCumulative(charts.querySelector('[data-chart="cumulative"]'), data, key, names[key], t);
  };
  picker.addEventListener("change", draw);
  redrawOn(charts, draw);
}

function redrawOn(node, draw) {
  document.addEventListener("backcast:theme", draw);
  let width = 0;
  new ResizeObserver(([entry]) => {
    const next = Math.round(entry.contentRect.width);
    if (next !== width) {
      width = next;
      draw();
    }
  }).observe(node);
}

function baseOptions(plotNode, t, height) {
  return {
    width: plotNode.clientWidth,
    height,
    marginLeft: 52,
    marginRight: 8,
    marginTop: 12,
    style: { fontFamily: t.sans, fontSize: "12px", color: t.muted, background: "transparent" },
    color: { type: "identity" },
    x: { type: "utc", label: null },
  };
}

function axes(t, width) {
  return [
    Plot.gridY({ stroke: t.rule, strokeOpacity: 1 }),
    Plot.axisY({ tickSize: 0, tickPadding: 8, label: null }),
    Plot.axisX({ tickSize: 0, tickPadding: 10, label: null, ticks: Math.max(2, Math.floor(width / 110)) }),
  ];
}

function legend(figure, items) {
  figure.querySelector(".chart-legend").replaceChildren(
    ...items.map(([kind, color, label]) => {
      const item = element("span");
      const key = element("span", `key-${kind}`);
      key.style.color = color;
      item.append(key, label);
      return item;
    }),
  );
}

function dataTable(figure, headers, rows) {
  const table = element("table", "data-table");
  const head = table.createTHead().insertRow();
  headers.forEach((header, i) => {
    const cell = element("th", i ? "num" : null, header);
    cell.scope = "col";
    head.append(cell);
  });
  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    row.forEach((value, i) => tr.append(element("td", i ? "num" : null, value)));
  }
  figure.querySelector(".chart-data .table-scroll").replaceChildren(table);
}

// Crosshair and tooltip: the vertical rule snaps to the nearest x, and one tooltip lists every series there.
function hover(plotNode, chart, rows, t, describe, xKey = "date") {
  const x = chart.scale("x");
  const y = chart.scale("y");
  const positions = rows.map((row) => x.apply(row[xKey]));
  const svg = d3.select(chart).style("touch-action", "pan-y");
  const [bottom, top] = y.range;
  const layer = svg.append("g").attr("pointer-events", "none").attr("display", "none");
  const rule = layer.append("line").attr("y1", top).attr("y2", bottom).attr("stroke", t.ruleStrong);
  const dots = layer.append("g");
  const tip = element("div", "chart-tip");
  tip.hidden = true;
  plotNode.append(tip);

  svg
    .on("pointermove", (event) => {
      const [px] = d3.pointer(event);
      const index = d3.leastIndex(positions, (position) => Math.abs(position - px));
      const { title, items } = describe(rows[index]);
      const cx = positions[index];
      layer.attr("display", null);
      rule.attr("x1", cx).attr("x2", cx);
      dots
        .selectAll("circle")
        .data(items.filter((item) => item.value != null))
        .join("circle")
        .attr("cx", cx)
        .attr("cy", (item) => y.apply(item.value))
        .attr("r", 4)
        .attr("fill", (item) => item.color)
        .attr("stroke", t.surface)
        .attr("stroke-width", 2);
      tip.replaceChildren(element("div", "tip-title", title));
      for (const item of items) {
        const row = element("div", "tip-row");
        const key = element("span", `key-${item.kind}`);
        key.style.color = item.color;
        row.append(key, element("strong", null, item.text), element("span", null, item.label));
        tip.append(row);
      }
      tip.hidden = false;
      const room = plotNode.clientWidth - cx - 16;
      tip.style.left = `${room > tip.offsetWidth ? cx + 16 : cx - tip.offsetWidth - 16}px`;
    })
    .on("pointerleave", () => {
      layer.attr("display", "none");
      tip.hidden = true;
    });
}

function drawPrice(figure, data, key, name, t) {
  const plotNode = figure.querySelector(".chart-plot");
  const history = data.history.dates.map((date, i) => ({ date: parseDate(date), close: data.history.close[i] }));
  const last = history.at(-1);
  // Each forecast path starts at the latest close so it joins the price line.
  const path = (modelKey) => {
    const forecast = data.future.paths[modelKey];
    return [
      { date: last.date, median: last.close, lower: last.close, upper: last.close },
      ...data.future.dates.map((date, i) => ({
        date: parseDate(date),
        median: forecast.median[i],
        lower: forecast.lower[i],
        upper: forecast.upper[i],
      })),
    ];
  };
  const model = path(key);
  const naive = path("naive");
  const future = model.slice(1).map((point, i) => ({ ...point, naive: naive[i + 1].median }));
  const rows = [...history, ...future];

  const options = baseOptions(plotNode, t, 340);
  const chart = Plot.plot({
    ...options,
    y: { label: null, nice: true },
    marks: [
      ...axes(t, options.width),
      Plot.ruleX([last.date], { stroke: t.ruleStrong }),
      Plot.areaY(model, { x: "date", y1: "lower", y2: "upper", fill: t.accent, fillOpacity: 0.1 }),
      Plot.lineY(history, { x: "date", y: "close", stroke: t.ink, strokeWidth: 2 }),
      Plot.lineY(naive, { x: "date", y: "median", stroke: t.muted, strokeWidth: 2 }),
      Plot.lineY(model, { x: "date", y: "median", stroke: t.accent, strokeWidth: 2 }),
    ],
  });
  chart.setAttribute("aria-label", `Recent closes and the next ${data.horizon} trading days forecast by ${name} and naive`);
  plotNode.replaceChildren(chart);

  hover(plotNode, chart, rows, t, (row) => ({
    title: formatDate(row.date),
    items:
      row.close != null
        ? [{ kind: "line", color: t.ink, value: row.close, text: formatPrice(row.close), label: "Close" }]
        : [
            { kind: "line", color: t.accent, value: row.median, text: formatPrice(row.median), label: name },
            { kind: "band", color: t.accent, value: null, text: `${formatPrice(row.lower)}–${formatPrice(row.upper)}`, label: "Interval" },
            { kind: "line", color: t.muted, value: row.naive, text: formatPrice(row.naive), label: "Naive" },
          ],
  }));

  legend(figure, [
    ["line", t.ink, "Close"],
    ["line", t.accent, name],
    ["band", t.accent, "80% interval"],
    ["line", t.muted, "Naive"],
  ]);
  dataTable(
    figure,
    ["Date", "Close", name, "Low", "High", "Naive"],
    rows.map((row) => [
      formatDate(row.date),
      row.close != null ? formatPrice(row.close) : "",
      row.median != null ? formatPrice(row.median) : "",
      row.lower != null ? formatPrice(row.lower) : "",
      row.upper != null ? formatPrice(row.upper) : "",
      row.naive != null ? formatPrice(row.naive) : "",
    ]),
  );
}

// Welch and Goyal's cumulative error difference: rises while the model beats naive, falls while it loses.
function drawCumulative(figure, data, key, name, t) {
  const plotNode = figure.querySelector(".chart-plot");
  const series = data.origins[key];
  const naive = data.origins.naive;
  let total = 0;
  const rows = series.origin.map((origin, i) => {
    const gain = 100 * (naive.error[i] - series.error[i]); // log errors, in percentage points
    total += gain;
    return {
      date: parseDate(origin),
      target: parseDate(series.target[i]),
      actual: series.actual[i],
      predicted: series.predicted[i],
      naivePredicted: naive.predicted[i],
      model: series.error[i],
      naive: naive.error[i],
      gain,
      total,
    };
  });

  const options = baseOptions(plotNode, t, 300);
  const chart = Plot.plot({
    ...options,
    y: { label: null, nice: true, tickFormat: formatPoints },
    marks: [
      ...axes(t, options.width),
      Plot.ruleY([0], { stroke: t.ruleStrong }),
      Plot.lineY(rows, { x: "date", y: "total", stroke: t.accent, strokeWidth: 2 }),
    ],
  });
  chart.setAttribute("aria-label", `Running total of how much closer ${name} was than naive over ${rows.length} days`);
  plotNode.replaceChildren(chart);

  hover(plotNode, chart, rows, t, (row) => ({
    title: `Forecast on ${formatDate(row.date)} for ${formatDate(row.target)}`,
    items: [
      { kind: "line", color: t.accent, value: row.total, text: formatPoints(row.total), label: "Running total" },
      { kind: "line", color: row.gain >= 0 ? t.better : t.worse, value: null, text: formatPoints(row.gain), label: row.gain >= 0 ? "Closer than naive" : "Further than naive" },
      { kind: "line", color: t.accent, value: null, text: formatError(row.model), label: `${name} error` },
      { kind: "line", color: t.muted, value: null, text: formatError(row.naive), label: "Naive error" },
    ],
  }));

  dataTable(
    figure,
    ["Forecast on", "Scored on", "Actual", `${name} forecast`, "Naive forecast", `${name} error`, "Naive error", "Running total"],
    rows.map((row) => [
      formatDate(row.date),
      formatDate(row.target),
      formatPrice(row.actual),
      formatPrice(row.predicted),
      formatPrice(row.naivePredicted),
      formatError(row.model),
      formatError(row.naive),
      formatPoints(row.total),
    ]),
  );
}

// Findings: where the study's SPA p-values fell, real stocks against their placebos, one panel per horizon.

const BINS = 20;

function drawPValues(figure, data, t) {
  const formatShare = d3.format(".1%");
  const share = (values) => {
    const counts = new Array(BINS).fill(0);
    for (const p of values) counts[Math.min(BINS - 1, Math.floor(p * BINS))]++;
    return counts.map((count) => ({ count, share: values.length ? count / values.length : 0 }));
  };
  const panels = Object.entries(data.p_values).map(([horizon, kinds]) => {
    const real = share(kinds.real ?? []);
    const placebo = share(kinds.placebo ?? []);
    return {
      horizon,
      rows: real.map((cell, bin) => ({
        from: bin / BINS,
        to: (bin + 1) / BINS,
        middle: (bin + 0.5) / BINS,
        real: cell,
        placebo: placebo[bin],
      })),
    };
  });
  const top = d3.max(panels.flatMap((panel) => panel.rows.flatMap((row) => [row.real.share, row.placebo.share])));
  const range = (row) => `${row.from.toFixed(2)} to ${row.to.toFixed(2)}`;

  for (const { horizon, rows } of panels) {
    const plotNode = figure.querySelector(`[data-horizon="${horizon}"] .chart-plot`);
    const chart = Plot.plot({
      ...baseOptions(plotNode, t, 200),
      marginLeft: 44,
      x: { type: "linear", domain: [0, 1] },
      y: { domain: [0, top], nice: true },
      marks: [
        Plot.gridY({ stroke: t.rule, strokeOpacity: 1, ticks: 4 }),
        Plot.axisY({ tickSize: 0, tickPadding: 8, label: null, ticks: 4, tickFormat: d3.format(".0%") }),
        Plot.axisX({ ticks: [0, 0.25, 0.5, 0.75, 1], tickFormat: String, tickSize: 0, tickPadding: 10, label: null }),
        Plot.rectY(rows, { x1: "from", x2: "to", y: (row) => row.real.share, fill: t.accent, insetLeft: 1, insetRight: 1, ry2: 2 }),
        Plot.ruleY(rows.filter((row) => row.placebo.count), {
          x1: "from",
          x2: "to",
          y: (row) => row.placebo.share,
          stroke: t.ink,
          strokeWidth: 2,
          insetLeft: 1,
          insetRight: 1,
        }),
        Plot.ruleY([0], { stroke: t.ruleStrong }),
      ],
    });
    chart.setAttribute("aria-label", `SPA p-values at ${horizon} trading days, real stocks against their placebos`);
    plotNode.replaceChildren(chart);
    hover(plotNode, chart, rows, t, (row) => ({
      title: `p from ${range(row)}`,
      items: [
        { kind: "rect", color: t.accent, value: null, text: formatShare(row.real.share), label: `Stocks (${row.real.count})` },
        { kind: "line", color: t.ink, value: null, text: formatShare(row.placebo.share), label: `Placebos (${row.placebo.count})` },
      ],
    }), "middle");
  }

  legend(figure, [
    ["rect", t.accent, "Stocks"],
    ["line", t.ink, "Placebos"],
  ]);
  dataTable(
    figure,
    ["SPA p-value", ...panels.flatMap(({ horizon }) => [`${horizon}d stocks`, `${horizon}d placebos`])],
    panels[0].rows.map((_, bin) => [
      range(panels[0].rows[bin]),
      ...panels.flatMap(({ rows }) => [formatShare(rows[bin].real.share), formatShare(rows[bin].placebo.share)]),
    ]),
  );
}

function initFindings() {
  const data = JSON.parse(document.getElementById("findings-data").textContent);
  const figure = document.querySelector('[data-chart="p-values"]');
  redrawOn(figure, () => drawPValues(figure, data, tokens()));
}

// Start-up runs last, once every constant above is defined.

const results = document.getElementById("results");
if (results?.dataset.src) loadResults(results, results.innerHTML);
else if (document.getElementById("chart-data")) initCharts();
else if (document.getElementById("findings-data")) initFindings();
