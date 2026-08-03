const REFRESH_MS = 10000;

let state = {
  range: "today",
  selectedClient: null, // null == all clients
};

function fmtBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtDuration(seconds) {
  seconds = Math.round(seconds || 0);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function renderRangePicker() {
  const el = document.getElementById("range-picker");
  const ranges = [["today", "Today"], ["24h", "24h"], ["7d", "7 days"], ["30d", "30 days"]];
  el.innerHTML = "";
  for (const [key, label] of ranges) {
    const btn = document.createElement("button");
    btn.textContent = label;
    if (key === state.range) btn.classList.add("active");
    btn.onclick = () => { state.range = key; refresh(); };
    el.appendChild(btn);
  }
}

function renderClientCards(clients, summaries) {
  const grid = document.getElementById("client-grid");
  grid.innerHTML = "";

  const allCard = document.createElement("div");
  allCard.className = "client-card" + (state.selectedClient === null ? " selected" : "");
  allCard.innerHTML = `<div class="name-row"><span class="name">All PCs</span></div>
    <div class="metric"><span>Combined view</span></div>`;
  allCard.onclick = () => { state.selectedClient = null; refresh(); };
  grid.appendChild(allCard);

  const summaryById = Object.fromEntries(summaries.map(s => [s.client_id, s]));

  for (const c of clients) {
    const s = summaryById[c.client_id] || {};
    const card = document.createElement("div");
    card.className = "client-card" + (state.selectedClient === c.client_id ? " selected" : "");
    card.innerHTML = `
      <div class="name-row">
        <span class="name">${c.display_name}</span>
        <span class="dot ${c.online ? "online" : "offline"}" title="${c.online ? "online" : "offline"}"></span>
      </div>
      <div class="metric"><span>Downloaded</span><b>${fmtBytes(s.bytes_received)}</b></div>
      <div class="metric"><span>Uploaded</span><b>${fmtBytes(s.bytes_sent)}</b></div>
      <div class="metric"><span>Active time</span><b>${fmtDuration(s.active_seconds)}</b></div>
      <div class="metric"><span>Sites visited</span><b>${s.distinct_domains || 0}</b></div>
    `;
    card.onclick = () => { state.selectedClient = c.client_id; refresh(); };
    grid.appendChild(card);
  }
}

function renderTopDomains(domains) {
  const el = document.getElementById("top-domains");
  if (!domains.length) {
    el.innerHTML = `<div class="empty-state">No traffic recorded yet for this range.</div>`;
    return;
  }
  const max = Math.max(...domains.map(d => d.total_bytes));
  el.innerHTML = domains.map(d => `
    <div class="domain-row">
      <span class="name">${d.domain}</span>
      <span class="size">${fmtBytes(d.total_bytes)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(d.total_bytes / max * 100).toFixed(1)}%"></div></div>
    </div>
  `).join("");
}

function renderTimeseries(records) {
  const el = document.getElementById("timeseries");
  if (!records.length) {
    el.innerHTML = `<div class="empty-state">No traffic recorded yet for this range.</div>`;
    return;
  }

  // Bucket into up to 40 buckets across the observed time span.
  const times = records.map(r => new Date(r.started_at).getTime());
  const tMin = Math.min(...times), tMax = Math.max(...times);
  const span = Math.max(tMax - tMin, 60000);
  const bucketCount = 40;
  const bucketMs = span / bucketCount;
  const buckets = new Array(bucketCount).fill(0);

  for (const r of records) {
    const t = new Date(r.started_at).getTime();
    let idx = Math.floor((t - tMin) / bucketMs);
    if (idx >= bucketCount) idx = bucketCount - 1;
    if (idx < 0) idx = 0;
    buckets[idx] += (r.bytes_sent + r.bytes_received);
  }

  const w = 560, h = 160, pad = 24;
  const maxVal = Math.max(...buckets, 1);
  const barW = (w - pad * 2) / bucketCount;

  let bars = "";
  buckets.forEach((v, i) => {
    const barH = (v / maxVal) * (h - pad * 2);
    const x = pad + i * barW;
    const y = h - pad - barH;
    bars += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(barW - 2).toFixed(1)}" height="${barH.toFixed(1)}" fill="#4f8cff" rx="1"></rect>`;
  });

  el.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" width="100%" height="180">
      <text x="${pad}" y="14">${fmtBytes(maxVal)} / bucket</text>
      ${bars}
      <line x1="${pad}" y1="${h - pad}" x2="${w - pad}" y2="${h - pad}" stroke="#253048" />
    </svg>
  `;
}

async function refresh() {
  renderRangePicker();
  const cid = state.selectedClient;

  const [clients, summaries, domains, series] = await Promise.all([
    getJSON("/api/clients"),
    getJSON(`/api/stats/summary?range=${state.range}`),
    getJSON(`/api/stats/top-domains?range=${state.range}${cid ? `&client_id=${cid}` : ""}`),
    getJSON(`/api/stats/timeseries?range=${state.range}${cid ? `&client_id=${cid}` : ""}`),
  ]);

  renderClientCards(clients, summaries);
  renderTopDomains(domains);
  renderTimeseries(series);

  document.getElementById("last-refresh").textContent =
    "Updated " + new Date().toLocaleTimeString();

  const selName = cid
    ? (clients.find(c => c.client_id === cid) || {}).display_name
    : "All PCs";
  document.getElementById("detail-title").textContent = selName;
}

refresh();
setInterval(refresh, REFRESH_MS);
