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

// Results: the backtest runs when this request arrives, so it can take a while.


async function loadResults(box, loadingMarkup) {
  const started = Date.now();
  const elapsed = box.querySelector(".elapsed");
  const tick = setInterval(() => {
    elapsed.textContent = `${Math.round((Date.now() - started) / 1000)} s`;
  }, 1000);
  try {
    const response = await fetch(box.dataset.src);
    // 422 (bad symbol) and 429 (too many new runs) carry a readable message rendered by the server.
    if (!response.ok && response.status !== 422 && response.status !== 429) throw new Error(`HTTP ${response.status}`);
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
    notice.append(element("p", null, "The backtest didn't finish. The price provider may be slow, or the server is busy."));
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
const formatChange = window.d3?.format("+,.2f");
const formatSigned = (value) => formatChange(value).replace("-", "\u2212");

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
    drawErrors(charts.querySelector('[data-chart="errors"]'), data, key, names[key], t);
  };
  picker.addEventListener("change", draw);
  document.addEventListener("backcast:theme", draw);
  let width = 0;
  new ResizeObserver(([entry]) => {
    const next = Math.round(entry.contentRect.width);
    if (next !== width) {
      width = next;
      draw();
    }
  }).observe(charts);
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
// On a band scale (bars) there is no rule; the hovered bar stays solid and the others fade.
function hover(plotNode, chart, rows, t, describe, xKey = "date") {
  const x = chart.scale("x");
  const y = chart.scale("y");
  const positions = rows.map((row) => x.apply(row[xKey]) + (x.bandwidth ?? 0) / 2);
  const svg = d3.select(chart).style("touch-action", "pan-y");
  // Rounded bars render as paths and come from two marks, so match them to rows by position.
  const bars = svg.selectAll('g[aria-label="bar"] > *');
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
      rule.attr("x1", cx).attr("x2", cx).attr("display", x.bandwidth ? "none" : null);
      bars.attr("opacity", function () {
        const box = this.getBBox();
        return Math.abs(box.x + box.width / 2 - cx) < 1 ? 1 : 0.4;
      });
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
      bars.attr("opacity", null);
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
      { date: last.date, mean: last.close, lower: last.close, upper: last.close },
      ...data.future.dates.map((date, i) => ({
        date: parseDate(date),
        mean: forecast.mean[i],
        lower: forecast.lower[i],
        upper: forecast.upper[i],
      })),
    ];
  };
  const model = path(key);
  const naive = path("naive");
  const future = model.slice(1).map((point, i) => ({ ...point, naive: naive[i + 1].mean }));
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
      Plot.lineY(naive, { x: "date", y: "mean", stroke: t.muted, strokeWidth: 2 }),
      Plot.lineY(model, { x: "date", y: "mean", stroke: t.accent, strokeWidth: 2 }),
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
            { kind: "line", color: t.accent, value: row.mean, text: formatPrice(row.mean), label: name },
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
      row.mean != null ? formatPrice(row.mean) : "",
      row.lower != null ? formatPrice(row.lower) : "",
      row.upper != null ? formatPrice(row.upper) : "",
      row.naive != null ? formatPrice(row.naive) : "",
    ]),
  );
}

function drawErrors(figure, data, key, name, t) {
  const plotNode = figure.querySelector(".chart-plot");
  const windows = data.windows[key];
  const naive = data.windows.naive;
  const rows = windows.target.map((target, i) => ({
    window: i + 1,
    origin: parseDate(windows.origin[i]),
    date: parseDate(target),
    actual: windows.actual[i],
    predicted: windows.predicted[i],
    naivePredicted: naive.predicted[i],
    model: Math.abs(windows.actual[i] - windows.predicted[i]),
    naive: Math.abs(naive.actual[i] - naive.predicted[i]),
  }));
  for (const row of rows) row.gain = row.naive - row.model;
  const tone = (row) => (row.gain > 0 ? t.better : row.gain < 0 ? t.worse : t.muted);

  const options = baseOptions(plotNode, t, 300);
  // Columns never wider than 24px: the band's leftover is padding.
  const slot = (options.width - options.marginLeft - options.marginRight) / rows.length;
  const tickEvery = Math.ceil(rows.length / Math.max(2, Math.floor(options.width / 110)));
  const chart = Plot.plot({
    ...options,
    x: { type: "band", label: null, padding: Math.max(0.15, 1 - 24 / slot) },
    y: { label: null, nice: true },
    marks: [
      Plot.gridY({ stroke: t.rule, strokeOpacity: 1 }),
      Plot.axisY({ tickSize: 0, tickPadding: 8, label: null }),
      Plot.axisX({
        tickSize: 0,
        tickPadding: 10,
        label: null,
        ticks: rows.filter((row) => (row.window - 1) % tickEvery === 0).map((row) => row.window),
        tickFormat: (window) => d3.utcFormat("%-d %b")(rows[window - 1].date),
      }),
      // Rounded at the data end only, square at the zero line.
      Plot.barY(rows.filter((row) => row.gain >= 0), { x: "window", y: "gain", fill: tone, ry1: 4 }),
      Plot.barY(rows.filter((row) => row.gain < 0), { x: "window", y: "gain", fill: tone, ry2: 4 }),
      Plot.ruleY([0], { stroke: t.ruleStrong }),
    ],
  });
  chart.setAttribute("aria-label", `How much closer ${name} was than naive in each of ${rows.length} windows`);
  plotNode.replaceChildren(chart);

  hover(
    plotNode,
    chart,
    rows,
    t,
    (row) => ({
      title: `Window ${row.window}: ${formatDate(row.origin)} to ${formatDate(row.date)}`,
      items: [
        { kind: "line", color: tone(row), value: null, text: formatSigned(row.gain), label: row.gain >= 0 ? "Closer than naive" : "Further than naive" },
        { kind: "line", color: t.accent, value: null, text: formatPrice(row.model), label: `${name} error` },
        { kind: "line", color: t.muted, value: null, text: formatPrice(row.naive), label: "Naive error" },
      ],
    }),
    "window",
  );

  legend(figure, [
    ["rect", t.better, `${name} closer than naive`],
    ["rect", t.worse, `${name} further than naive`],
  ]);
  dataTable(
    figure,
    ["Window", "From", "To", "Actual", `${name} forecast`, "Naive forecast", `${name} error`, "Naive error", "Closer by"],
    rows.map((row) => [
      String(row.window),
      formatDate(row.origin),
      formatDate(row.date),
      formatPrice(row.actual),
      formatPrice(row.predicted),
      formatPrice(row.naivePredicted),
      formatPrice(row.model),
      formatPrice(row.naive),
      formatSigned(row.gain),
    ]),
  );
}

// Start-up runs last, once every constant above is defined.

const results = document.getElementById("results");
if (results?.dataset.src) loadResults(results, results.innerHTML);
else if (document.getElementById("chart-data")) initCharts();
