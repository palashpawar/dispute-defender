/* Dispute Defender front end: AG Grid queue + case drawer. No build step. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v, c = "USD") => new Intl.NumberFormat("en-US", { style: "currency", currency: c }).format(v || 0);

const REASONS = {
  MERCHANDISE_OR_SERVICE_NOT_RECEIVED: "Item not received",
  MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED: "Not as described",
  UNAUTHORISED: "Unauthorized",
  CREDIT_NOT_PROCESSED: "Credit not processed",
  DUPLICATE_TRANSACTION: "Duplicate charge",
  INCORRECT_AMOUNT: "Incorrect amount",
  PAYMENT_BY_OTHER_MEANS: "Paid by other means",
  CANCELED_RECURRING_BILLING: "Canceled subscription",
  PROBLEM_WITH_REMITTANCE: "Remittance problem",
  OTHER: "Other",
};
const STATUS = {
  WAITING_FOR_SELLER_RESPONSE: ["Needs response", "danger"],
  WAITING_FOR_BUYER_RESPONSE: ["Awaiting buyer", "info"],
  UNDER_REVIEW: ["PayPal reviewing", "info"],
  OPEN: ["Open", "danger"],
  RESOLVED: ["Resolved", ""],
  OTHER: ["Other", ""],
};
const ACTION_LABEL = { FIGHT: "Submit evidence to PayPal", OFFER: "Send offer via PayPal", ACCEPT: "Accept claim & refund via PayPal" };
const TOOL_LABEL = {
  get_dispute: "Read PayPal dispute", lookup_order: "Matched merchant order", get_shipment_tracking: "Checked carrier tracking",
  get_support_messages: "Read support inbox", get_refunds: "Checked refund ledger", get_customer_history: "Pulled customer history",
  get_fulfillment_records: "Checked fulfillment records", get_store_policy: "Read store policy",
};

let meta = {}, grid, rows = [], selected = null, detail = null, draft = null, poll = null;

function timeLeft(iso) {
  if (!iso) return { text: "-", hot: false, ms: Infinity };
  const ms = new Date(iso) - Date.now();
  if (ms <= 0) return { text: "Overdue", hot: true, ms };
  const h = Math.floor(ms / 36e5), d = Math.floor(h / 24);
  return { text: d ? `${d}d ${h % 24}h` : `${h}h ${Math.floor((ms % 36e5) / 6e4)}m`, hot: ms < 48 * 36e5, ms };
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(typeof body === "string" ? body : JSON.stringify(body.detail ?? body));
  return body;
}

function toast(msg, ms = 3200) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}

/* ---------- grid ---------- */
const verdictCell = (p) => {
  const r = p.data;
  if (r.case_status === "analyzing") return `<span class="pill brand"><span class="spinner"></span>Analyzing</span>`;
  if (r.case_status === "error") return `<span class="pill danger">Error</span>`;
  if (!r.recommendation) return r.status === "RESOLVED" ? "" : `<span class="muted">-</span>`;
  const done = r.case_status === "responded" ? " ✓" : "";
  return `<span class="pill ${r.recommendation}" title="${esc(r.headline)}">${r.recommendation}${done}</span>`;
};
const winCell = (p) => {
  const v = p.value;
  if (v == null) return "";
  const cls = v >= 0.65 ? "" : v >= 0.4 ? "mid" : "low";
  return `<div class="winbar"><div class="track"><div class="fill ${cls}" style="width:${Math.round(v * 100)}%"></div></div><span>${Math.round(v * 100)}%</span></div>`;
};
const statusCell = (p) => {
  const r = p.data;
  if (r.status === "RESOLVED") {
    const won = (r.outcome || "").includes("SELLER");
    return `<span class="pill ${won ? "FIGHT" : ""}">${won ? "Won" : "Refunded"}</span>`;
  }
  const [label, cls] = STATUS[r.status] || [r.status, ""];
  return `<span class="pill ${cls}">${label}</span>`;
};

function initGrid() {
  grid = agGrid.createGrid($("#grid"), {
    rowData: [],
    getRowId: (p) => p.data.dispute_id,
    rowSelection: "single",
    animateRows: true,
    rowHeight: 46,
    autoSizeStrategy: { type: "fitGridWidth" },
    onGridSizeChanged: (e) => e.api.sizeColumnsToFit(),
    defaultColDef: { sortable: true, resizable: true, filter: true, suppressHeaderMenuButton: true },
    columnDefs: COLUMNS.map((c) => ({ minWidth: c.width, ...c })),
    onRowClicked: (e) => openCase(e.data.dispute_id),
    overlayNoRowsTemplate: '<span class="muted">No disputes, nice.</span>',
  });
}

const COLUMNS = [
  { headerName: "Dispute", field: "dispute_id", width: 120, cellClass: "mono" },
  { headerName: "Buyer", field: "buyer", flex: 1, minWidth: 130 },
  { headerName: "Reason", field: "reason", minWidth: 165, flex: 1, valueFormatter: (p) => REASONS[p.value] || p.value },
  { headerName: "Amount", field: "amount", width: 108, type: "numericColumn", valueFormatter: (p) => money(p.value, p.data.currency) },
  { headerName: "Stage", field: "stage", width: 116, cellRenderer: (p) => `<span class="pill ${p.value === "INQUIRY" ? "" : "danger"}">${esc(p.value?.toLowerCase().replace("_", "-"))}</span>` },
  { headerName: "Respond by", field: "due", width: 118, sort: "asc",
    comparator: (a, b) => timeLeft(a).ms - timeLeft(b).ms,
    cellRenderer: (p) => { if (p.data.status === "RESOLVED" || p.data.status === "UNDER_REVIEW") return `<span class="muted">-</span>`; const t = timeLeft(p.value); return `<span class="due ${t.hot ? "hot" : ""}">${t.text}</span>`; } },
  { headerName: "Status", field: "status", width: 140, cellRenderer: statusCell },
  { headerName: "AI verdict", field: "recommendation", width: 120, cellRenderer: verdictCell },
  { headerName: "Win odds", field: "win_probability", width: 130, cellRenderer: winCell },
];

async function refresh() {
  rows = await api("/api/disputes");
  grid.setGridOption("rowData", rows);
  grid.refreshCells({ force: true });
  if (selected) grid.getRowNode(selected)?.setSelected(true);
  const open = rows.filter((r) => r.status !== "RESOLVED");
  $("#k-open").textContent = open.length;
  $("#k-risk").textContent = money(open.reduce((s, r) => s + r.amount, 0));
  $("#k-due").textContent = open.filter((r) => r.status === "WAITING_FOR_SELLER_RESPONSE" && timeLeft(r.due).hot).length;
  const fight = open.filter((r) => r.recommendation === "FIGHT");
  $("#k-fight").textContent = fight.length ? `${money(fight.reduce((s, r) => s + r.amount, 0))}` : "-";
  const won = rows.filter((r) => (r.outcome || "").includes("SELLER"));
  $("#k-won").textContent = `${won.length} / ${rows.length - open.length}`;
  const busy = rows.some((r) => r.case_status === "analyzing");
  if (busy && !poll) poll = setInterval(refresh, 2000);
  if (!busy && poll) { clearInterval(poll); poll = null; }
  loadConsole();
}

/* ---------- drawer ---------- */
async function openCase(id) {
  selected = id;
  $("#drawer").hidden = false;
  detail = await api(`/api/disputes/${id}`);
  draft = detail.case.plan ? structuredClone(detail.case.plan) : null;
  renderDrawer();
  if (detail.case.status === "analyzing") streamAnalysis(id, false);
}

function closeDrawer() {
  selected = null; detail = null; $("#drawer").hidden = true; grid.deselectAll();
}

function renderDrawer() {
  const { dispute: d, order, shipment, case: c } = detail;
  const due = timeLeft(d.seller_response_due_date);
  const [slabel, scls] = STATUS[d.status] || [d.status, ""];
  const tx = d.disputed_transactions[0];
  const lastScan = shipment?.events?.at(-1);
  $("#drawer-body").innerHTML = `
    <div class="d-head">
      <div>
        <div class="muted mono">${esc(d.dispute_id)}</div>
        <h3>${esc(REASONS[d.reason] || d.reason)}</h3>
      </div>
      <button class="close" aria-label="Close" id="close">×</button>
    </div>
    <div class="amount">${money(d.dispute_amount.value, d.dispute_amount.currency_code)}</div>
    <div class="meta-row">
      <span class="pill ${scls}">${slabel}</span>
      <span class="pill ${d.dispute_life_cycle_stage === "INQUIRY" ? "" : "danger"}">${esc(d.dispute_life_cycle_stage)}</span>
      ${d.status === "WAITING_FOR_SELLER_RESPONSE" ? `<span class="pill ${due.hot ? "danger" : ""}">Respond in ${due.text}</span>` : ""}
      ${d.dispute_outcome ? `<span class="pill ${d.dispute_outcome.outcome_code.includes("SELLER") ? "FIGHT" : ""}">${esc(d.dispute_outcome.outcome_code.replaceAll("_", " ").toLowerCase())}</span>` : ""}
    </div>

    <div class="section"><h4>Buyer says</h4>
      ${d.messages.filter((m) => m.posted_by === "BUYER").map((m) => `<div class="quote">“${esc(m.content)}”</div>`).join("") || '<span class="muted">No message.</span>'}
    </div>

    <div class="section"><h4>Linked order</h4>
      ${order ? `<div class="card"><dl class="kv">
        <dt>Order</dt><dd><b>${esc(order.order_id)}</b> · ${esc(order.created.slice(0, 10))}</dd>
        <dt>Customer</dt><dd>${esc(order.customer.name)} · <span class="muted">${esc(order.customer.email)}</span></dd>
        <dt>Items</dt><dd>${order.items.map((i) => `${i.qty}× ${esc(i.name)}`).join("<br>")}</dd>
        <dt>Ship to</dt><dd>${esc(order.ship_to)}</dd>
        ${shipment ? `<dt>Tracking</dt><dd>${esc(shipment.carrier)} <span class="mono">${esc(shipment.tracking_number)}</span><br><span class="muted">${esc(lastScan?.status)}</span></dd>` : ""}
        <dt>PayPal txn</dt><dd class="mono">${esc(tx.seller_transaction_id)}</dd>
      </dl></div>` : `<div class="card muted">No matching order found for transaction ${esc(tx.seller_transaction_id)}.</div>`}
    </div>

    <div class="section" id="ai-section"></div>`;
  $("#close").onclick = closeDrawer;
  renderAI();
}

function renderAI() {
  const el = $("#ai-section");
  const c = detail.case, d = detail.dispute;
  const engine = meta.ai_engine === "claude" ? "Claude" : "rules engine";
  if (c.status === "idle" || (c.status === "error" && !c.plan)) {
    el.innerHTML = `<h4>AI case analysis</h4>
      <div class="investigate">
        ${c.status === "error" ? `<div class="pill danger">Last run failed: ${esc(c.error)}</div>` : ""}
        <p>${meta.ai_engine === "claude" ? "Claude will read the PayPal dispute, cross-check your orders, tracking, inbox and refunds, and draft a response." : "No API key is configured, so the rules engine will draft a response instead of Claude."}</p>
        <button class="btn primary big" id="btn-investigate">Investigate with ${engine}</button>
      </div>`;
    $("#btn-investigate").onclick = () => streamAnalysis(d.dispute_id, true);
    return;
  }
  if (c.status === "analyzing") {
    el.innerHTML = `<h4>AI investigation <span class="spinner"></span></h4><ul class="trace" id="trace"></ul>`;
    c.trace.forEach(addTrace);
    return;
  }
  renderPlan(el);
}

function addTrace(e) {
  const ul = $("#trace");
  if (!ul) return;
  if (e.type === "tool") {
    const li = document.createElement("li");
    li.dataset.tool = e.name;
    li.innerHTML = `<span class="ic">⚙</span><div><div><b>${esc(TOOL_LABEL[e.name] || e.name)}</b> <span class="tool-name muted">${esc(e.name)}(${esc(Object.values(e.input || {}).filter(Boolean).join(", "))})</span></div><div class="sum">…</div></div>`;
    ul.appendChild(li);
  } else if (e.type === "tool_result") {
    const li = [...ul.querySelectorAll(`li[data-tool="${e.name}"]`)].find((x) => $(".sum", x).textContent === "…");
    if (li) $(".sum", li).textContent = e.summary;
  } else if (e.type === "thought" || e.type === "status") {
    const li = document.createElement("li");
    li.className = "thought";
    li.innerHTML = `<span class="ic">${e.type === "status" ? "▸" : "✎"}</span><div class="txt">${esc(e.text.length > 420 ? e.text.slice(0, 420) + "…" : e.text)}</div>`;
    ul.appendChild(li);
  } else if (e.type === "error") {
    const li = document.createElement("li");
    li.className = "err";
    li.innerHTML = `<span class="ic">!</span><div class="txt">${esc(e.text)}</div>`;
    ul.appendChild(li);
  }
  ul.parentElement.parentElement.scrollTop = 1e9;
}

async function streamAnalysis(id, start) {
  detail.case.status = "analyzing";
  detail.case.trace = start ? [] : detail.case.trace;
  renderAI();
  if (start) refreshSoon();
  const resp = await fetch(`/api/disputes/${id}/analyze${start && detail.case.plan ? "?force=true" : ""}`, { method: "POST" });
  const reader = resp.body.getReader(), dec = new TextDecoder();
  let buf = "", seen = start ? 0 : detail.case.trace.length;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n"); buf = parts.pop();
    for (const p of parts) {
      if (!p.startsWith("data: ")) continue;
      const e = JSON.parse(p.slice(6));
      if (selected !== id) continue;
      if (e.type === "end") {
        detail.case = e.case;
        draft = e.case.plan ? structuredClone(e.case.plan) : null;
        renderAI();
        refresh();
      } else if (seen-- <= 0) {
        addTrace(e);
      }
    }
  }
}
const refreshSoon = () => setTimeout(refresh, 300);

function gauge(p) {
  const r = 26, C = 2 * Math.PI * r, v = Math.round(p * 100);
  const col = p >= 0.65 ? "var(--fight)" : p >= 0.4 ? "var(--offer)" : "var(--accept)";
  return `<svg class="gauge" viewBox="0 0 64 64" role="img" aria-label="Win probability ${v}%">
    <circle cx="32" cy="32" r="${r}" fill="none" stroke="var(--border)" stroke-width="6"/>
    <circle cx="32" cy="32" r="${r}" fill="none" stroke="${col}" stroke-width="6" stroke-linecap="round"
      stroke-dasharray="${(C * p).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 32 32)"/>
    <text x="32" y="37" text-anchor="middle">${v}%</text></svg>`;
}

function renderPlan(el) {
  const c = detail.case, d = detail.dispute, p = draft;
  const responded = c.status === "responded" || d.status !== "WAITING_FOR_SELLER_RESPONSE";
  const rec = p.recommendation;
  const engineLabel = c.engine === "claude" ? `Claude · ${esc(meta.model)}` : "Rules engine (no API key)";
  el.innerHTML = `
    <h4>AI case analysis</h4>
    <div class="verdict ${rec}">
      ${gauge(p.win_probability)}
      <div>
        <span class="pill big ${rec}">${rec}</span>
        <div class="hl" style="margin-top:6px">${esc(p.headline)}</div>
        <div class="eng">${engineLabel}${p.edited_by_merchant ? " · edited by you" : ""}</div>
      </div>
    </div>
    <p style="margin:10px 2px 0">${esc(p.reasoning)}</p>

    ${p.key_facts?.length ? `<div class="section"><h4>Key facts</h4><ul class="facts">
      ${p.key_facts.map((f) => `<li class="${f.supports}"><span class="mark"></span><div>${esc(f.fact)}<div class="src">${esc(f.source)}</div></div></li>`).join("")}
    </ul></div>` : ""}

    ${p.risk_flags?.length ? `<div class="section"><h4>Risks</h4><ul class="flags">${p.risk_flags.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>` : ""}

    <div class="section"><h4>Response</h4>
      <div class="seg" id="seg">${["FIGHT", "OFFER", "ACCEPT"].map((r) => `<button data-r="${r}" class="${r === rec ? "on" : ""}" ${responded ? "disabled" : ""}>${r}</button>`).join("")}</div>
    </div>

    ${rec === "FIGHT" ? `
      <div class="section"><h4>Evidence for PayPal (${(p.evidence || []).length})</h4>
        ${(p.evidence || []).map((e, i) => `<label class="ev"><input type="checkbox" data-ev="${i}" ${e._off ? "" : "checked"} ${responded ? "disabled" : ""}>
          <div><div class="t">${esc(e.evidence_type)}</div><div class="n">${esc(e.notes)}</div>
          ${e.tracking_number ? `<div class="x">${esc(e.carrier_name)} ${esc(e.tracking_number)}</div>` : ""}
          ${e.refund_id ? `<div class="x">refund ${esc(e.refund_id)}</div>` : ""}</div></label>`).join("") || '<div class="muted">No evidence items.</div>'}
        <div class="muted" style="font-size:12px;margin-top:6px">+ evidence packet PDF attached automatically.</div>
      </div>
      <div class="section"><h4>Statement to PayPal</h4>
        <textarea id="statement" rows="7" ${responded ? "readonly" : ""}>${esc(p.seller_statement)}</textarea>
        <div class="count" id="statement-count"></div>
      </div>` : ""}

    ${rec === "OFFER" ? `
      <div class="section"><h4>Offer</h4>
        <div class="row2">
          <select class="field" id="offer-type" ${responded ? "disabled" : ""}>${["REFUND", "REFUND_WITH_RETURN", "REFUND_WITH_REPLACEMENT"].map((t) => `<option ${p.offer?.offer_type === t ? "selected" : ""}>${t}</option>`).join("")}</select>
          <input class="field" id="offer-amount" inputmode="decimal" value="${esc(p.offer?.amount ?? d.dispute_amount.value)}" ${responded ? "readonly" : ""} aria-label="Offer amount">
        </div>
      </div>` : ""}

    <div class="section"><h4>${rec === "FIGHT" ? "Message to buyer" : rec === "OFFER" ? "Note with offer" : "Note to buyer"}</h4>
      <textarea id="buyer-msg" rows="3" ${responded ? "readonly" : ""}>${esc(p.buyer_message)}</textarea>
    </div>

    <div class="cta">
      ${responded ? respondedBlock() : `
        <button class="btn primary big block" id="btn-respond">${ACTION_LABEL[rec]}</button>`}
      <div class="row2">
        <a class="btn" href="/api/disputes/${d.dispute_id}/packet.pdf" target="_blank" rel="noopener">Evidence packet (PDF)</a>
        <button class="btn" id="btn-rerun">Re-run analysis</button>
      </div>
    </div>
    ${c.trace?.length ? `<details class="section"><summary class="muted" style="cursor:pointer">How ${c.engine === "claude" ? "Claude" : "the rules engine"} got here (${c.trace.filter((t) => t.type === "tool").length} lookups)</summary><ul class="trace" id="trace"></ul></details>` : ""}`;

  if ($("#trace")) c.trace.forEach(addTrace);
  $("#btn-rerun").onclick = () => streamAnalysis(d.dispute_id, true);
  if (responded) { wireRuling(); return; }

  $("#seg").onclick = (e) => {
    const r = e.target.dataset.r;
    if (!r) return;
    collectDraft(); draft.recommendation = r;
    if (r === "OFFER" && !draft.offer) draft.offer = { offer_type: "REFUND", amount: d.dispute_amount.value };
    renderPlan(el);
  };
  el.querySelectorAll("[data-ev]").forEach((cb) => (cb.onchange = () => (draft.evidence[cb.dataset.ev]._off = !cb.checked)));
  const st = $("#statement");
  if (st) {
    const upd = () => { const n = st.value.length; const ct = $("#statement-count"); ct.textContent = `${n} / 2000`; ct.classList.toggle("over", n > 2000); };
    st.oninput = upd; upd();
  }
  $("#btn-respond").onclick = submitResponse;
}

function collectDraft() {
  if ($("#statement")) draft.seller_statement = $("#statement").value;
  if ($("#buyer-msg")) draft.buyer_message = $("#buyer-msg").value;
  if ($("#offer-type")) draft.offer = { offer_type: $("#offer-type").value, amount: $("#offer-amount").value };
}

function previewPayload(p) {
  const id = detail.dispute.dispute_id;
  if (p.recommendation === "FIGHT") {
    const ev = p.evidence.map(({ evidence_type, notes, tracking_number, carrier_name, refund_id }) => ({
      evidence_type, notes, ...(tracking_number ? { evidence_info: { tracking_info: [{ carrier_name, tracking_number }] } } : {}),
      ...(refund_id ? { evidence_info: { refund_ids: [refund_id] } } : {}) }));
    ev.push({ evidence_type: "MERCHANT_RESPONSE", notes: p.seller_statement.slice(0, 120) + "…" });
    return `POST /v1/customer/disputes/${id}/provide-evidence\nmultipart: input + evidence-file (${id}-evidence.pdf)\n\n${JSON.stringify({ evidences: ev }, null, 2)}`;
  }
  if (p.recommendation === "OFFER")
    return `POST /v1/customer/disputes/${id}/make-offer\n\n${JSON.stringify({ note: p.buyer_message, offer_type: p.offer.offer_type, offer_amount: { currency_code: "USD", value: p.offer.amount } }, null, 2)}`;
  return `POST /v1/customer/disputes/${id}/accept-claim\n\n${JSON.stringify({ note: p.buyer_message, accept_claim_type: p.accept_claim_type || "REFUND" }, null, 2)}`;
}

async function submitResponse() {
  collectDraft();
  const p = { ...draft, evidence: (draft.evidence || []).filter((e) => !e._off).map(({ _off, ...e }) => e) };
  if (p.seller_statement?.length > 2000) return toast("Statement is over PayPal's 2000-character limit.");
  const dlg = $("#confirm");
  $("#confirm-title").textContent = ACTION_LABEL[p.recommendation];
  $("#confirm-text").textContent = `This sends your response to PayPal${meta.paypal_mode === "mock" ? " (mock sandbox)" : " sandbox"}. You can't edit it afterwards.`;
  $("#confirm-payload").textContent = previewPayload(p);
  dlg.showModal();
  const ok = await new Promise((res) => dlg.addEventListener("close", () => res(dlg.returnValue === "ok"), { once: true }));
  if (!ok) return;
  const btn = $("#btn-respond");
  btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> Sending to PayPal…`;
  try {
    await api(`/api/disputes/${detail.dispute.dispute_id}/plan`, { method: "PUT", body: JSON.stringify(p) });
    const res = await api(`/api/disputes/${detail.dispute.dispute_id}/respond`, { method: "POST" });
    detail.lastResult = res;
    toast(`PayPal accepted: ${res.results.map((r) => r.call).join(" + ")}`);
    await openCase(detail.dispute.dispute_id);
    refresh();
  } catch (err) {
    toast(`PayPal error: ${err.message}`, 6000);
    btn.disabled = false; btn.textContent = ACTION_LABEL[p.recommendation];
  }
}

function respondedBlock() {
  const d = detail.dispute;
  const last = detail.case.actions?.at(-1);
  const review = d.status === "UNDER_REVIEW";
  return `<div class="result">
      <b>${last ? `Sent to PayPal: ${esc(last.calls.join(" + "))}` : "Response already on file"}</b>
      <div class="muted" style="font-size:12.5px">Dispute status is now <b>${esc((STATUS[d.status] || [d.status])[0])}</b>.</div>
      ${review ? `<div style="margin-top:10px" class="muted">Sandbox only: ask PayPal to rule on it now.</div>
        <div class="row2" style="margin-top:6px"><button class="btn" data-rule="SELLER_FAVOR">Simulate ruling: seller wins</button><button class="btn" data-rule="BUYER_FAVOR">Buyer wins</button></div>` : ""}
    </div>`;
}

function wireRuling() {
  document.querySelectorAll("[data-rule]").forEach((b) => (b.onclick = async () => {
    try {
      await api(`/api/disputes/${detail.dispute.dispute_id}/simulate-ruling`, { method: "POST", body: JSON.stringify({ outcome: b.dataset.rule }) });
      toast("PayPal ruled on the dispute");
      await openCase(detail.dispute.dispute_id); refresh();
    } catch (e) { toast(e.message, 6000); }
  }));
}

/* ---------- console ---------- */
async function loadConsole() {
  const calls = await api("/api/paypal-log");
  $("#console-count").textContent = `(${calls.length} calls)`;
  $("#console-body").innerHTML = calls.map((c) => `<div class="call"><span class="muted">${esc(c.time.slice(11, 19))}</span><span class="m ${c.method}">${c.method}</span>
    ${c.body ? `<details><summary>${esc(c.path)}${c.status ? ` · ${c.status}` : ""}</summary><pre>${esc(JSON.stringify(c.body, null, 2))}</pre></details>` : `<span>${esc(c.path)}${c.status ? ` · ${c.status}` : ""}</span>`}</div>`).join("");
}

/* ---------- boot ---------- */
async function boot() {
  meta = await api("/api/meta");
  $("#seller").textContent = `${meta.seller.name} · merchant ${meta.seller.merchant_id}`;
  const pp = $("#pp-badge");
  pp.textContent = meta.paypal_mode === "mock" ? "PayPal: mock sandbox" : "PayPal: sandbox (live API)";
  pp.className = `badge ${meta.paypal_mode === "mock" ? "warn" : "live"}`;
  const ai = $("#ai-badge");
  ai.textContent = meta.ai_engine === "claude" ? `AI: ${meta.model}` : "AI: rules engine (no key)";
  ai.className = `badge ${meta.ai_engine === "claude" ? "ai" : "warn"}`;
  $("#btn-simulate").hidden = meta.paypal_mode !== "mock";
  $("#btn-simulate").onclick = async () => {
    try {
      const r = await api("/api/simulate/dispute", { method: "POST" });
      toast(`Webhook received: ${r.webhook_event.event_type} · ${r.dispute_id}. Analysis started.`);
      await refresh();
      grid.flashCells({ rowNodes: [grid.getRowNode(r.dispute_id)].filter(Boolean) });
    } catch (e) { toast(e.message.includes("No more") ? "No more queued demo disputes." : e.message); }
  };
  $("#btn-track").hidden = meta.paypal_mode === "mock";
  $("#btn-track").onclick = async () => {
    const id = prompt("PayPal case ID (e.g. PP-R-ABC-12345678)");
    if (!id) return;
    try {
      const r = await api("/api/disputes/track", { method: "POST", body: JSON.stringify({ dispute_id: id }) });
      toast(`Tracking ${r.dispute_id}. Analysis started.`);
      await refresh();
    } catch (e) { toast(e.message); }
  };
  $("#btn-analyze-all").onclick = async () => {
    const r = await api("/api/analyze-all", { method: "POST" });
    toast(r.started.length ? `Analyzing ${r.started.length} dispute(s)…` : "Everything open is already analyzed.");
    refresh();
  };
  $("#quick").oninput = (e) => grid.setGridOption("quickFilterText", e.target.value);
  initGrid();
  await refresh();
}
boot().catch((e) => toast(`Failed to load: ${e.message}`, 8000));
