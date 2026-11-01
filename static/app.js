// CloudShop GUI - navigates the four agents and renders their output.
let S = null;          // server session state
let META = null;
let view = "brief";

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => "S$" + Number(n).toFixed(2);

// Copilot-style prompt suggestions.
// Served by GET /api/suggestions and grounded in how real shoppers phrase requests; each is
// tagged with the gulf-of-envisioning gap it crosses. Falls back to this set if the API is down.
let SUGGEST = null;
const GAP_META = {
  capability: { icon: "🧭", label: "what to specify" },
  instruction: { icon: "🎯", label: "make it concrete" },
  intentionality: { icon: "✨", label: "what 'good' looks like" },
};
const FALLBACK_STARTERS = [
  { text: "A linen dress for a summer wedding, size M, under $150", gap: "intentionality" },
  { text: "Minimalist white sneakers for travel, EU 42, under $140", gap: "capability" },
  { text: "Something cozy and warm for winter under $100", gap: "instruction" },
  { text: "A tailored blazer for job interviews, women's size S, under $200", gap: "intentionality" },
];
const SWATCH = { black: "#2b2b2b", white: "#f4f1ec", grey: "#9a9a9a", navy: "#1f2f55", blue: "#4f7cc3", beige: "#d9c7a7",
  cream: "#efe6d2", brown: "#8a5a3b", green: "#8fae8b", olive: "#6b6f3a", red: "#c0392b", pink: "#e8a5b8",
  burgundy: "#7a2233", yellow: "#e8c547" };
const EMOJI = { dress: "👗", "t-shirt": "👕", shirt: "👔", blouse: "👚", knitwear: "🧶", hoodie: "🧥", jacket: "🧥",
  blazer: "🧥", coat: "🧥", jeans: "👖", trousers: "👖", skirt: "👗", shorts: "🩳", shoes: "👞", sneakers: "👟",
  heels: "👠", flats: "🥿", sandals: "👡", boots: "🥾", loafers: "👞", bag: "👜" };
const TINT = { black: "#e5e7eb", white: "#f3f4f6", grey: "#e5e7eb", navy: "#dbe4f2", blue: "#dbeafe", beige: "#f1ead9",
  cream: "#faf6ec", brown: "#e9ddce", green: "#dceddc", olive: "#e6e9d4", red: "#fadbdb", pink: "#fbe0ea",
  burgundy: "#f0d9df", yellow: "#f8efcf" };
const BRIEF_FORM = [
  ["items_wanted", "Items wanted", "list"], ["section", "Section", "section"], ["occasion", "Occasion", "text"],
  ["style", "Style", "list"], ["colors_liked", "Colors you like", "list"], ["colors_avoided", "Colors to avoid", "list"],
  ["materials_avoided", "Materials to avoid", "list"], ["size_top", "Top / dress size", "text"],
  ["size_bottom", "Waist size", "text"], ["size_shoe", "Shoe size (EU/UK/US)", "text"],
  ["budget_total", "Total budget (S$)", "number"],
  ["budget_per_item", "Max per item (S$)", "number"], ["notes", "Notes", "text"],
];
const CHECK_COLS = ["Category", "Section", "Budget", "Size in stock", "Colors", "Materials", "Returns", "Price drop", "Rationale grounded"];
const ICON = { pass: "✓", warn: "!", fail: "✗" };

// ------------------------------------------------------------------ API
async function api(method, path, body, busyText) {
  if (busyText) { $("busy-text").textContent = busyText; $("busy").hidden = false; }
  try {
    const res = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data;
  } finally { $("busy").hidden = true; }
}
async function act(method, path, body, busyText) {
  try { S = await api(method, path, body, busyText); render(); return true; }
  catch (e) { toast(e.message); return false; }
}
const sp = (p) => `/api/session/${S.session_id}${p}`;

function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 4500);
}

function renderSuggestions() {
  const picks = SUGGEST?.starters?.length ? SUGGEST.starters : FALLBACK_STARTERS;
  $("suggestions").innerHTML = picks.slice(0, 4).map((s) => {
    const g = GAP_META[s.gap] || { icon: "💬", label: "" };
    return `<button class="suggest" data-suggest="${esc(s.text)}" title="Helps you cross the ${esc(s.gap || "")} gap, ${esc(g.label)}">` +
      `<span class="sg-ic">${g.icon}</span><span>${esc(s.text)}</span></button>`;
  }).join("");
}

// ------------------------------------------------------------------ navigation
function go(v) {
  view = v;
  document.querySelectorAll(".view").forEach((el) => (el.hidden = el.id !== "view-" + v));
  document.querySelectorAll(".step").forEach((el) => el.classList.toggle("active", el.dataset.view === v));
  render();
}

function render() {
  if (!S) return;
  renderDots(); renderCartMini(); renderChat();
  if (view === "brief") renderBrief();
  if (view === "discover") renderDiscover();
  if (view === "verify") renderVerify();
  if (view === "checkout") renderCheckout();
}

// The one conversation thread. Everything the shopper types goes through the concierge.
async function sendChat(text) {
  text = (text || "").trim();
  if (!text) return toast("Tell CloudShop what you're after.");
  const ok = await act("POST", sp("/chat"), { message: text }, "CloudShop is thinking…");
  if (ok) {
    $("intake-text").value = ""; $("chat-text").value = "";
    setChatOpen(true);                         // pop the corner chat open on the first reply
    if (S.view_hint) go(S.view_hint);          // follow the concierge to the relevant page
  }
}

let chatOpen = false;
function setChatOpen(open) {
  chatOpen = open;
  const panel = $("chat-panel"), launch = $("chat-launch");
  if (panel) panel.hidden = !open;
  if (launch) launch.hidden = open;
  const badge = $("chat-badge"); if (badge) badge.hidden = open || !(S.messages || []).length;
  if (open) { const l = $("chat-log"); if (l) l.scrollTop = l.scrollHeight; $("chat-text")?.focus(); }
}

function renderChat() {
  const log = $("chat-log");
  const msgs = S.messages || [];
  $("chat-widget").hidden = msgs.length === 0;
  if (!log) return;
  log.innerHTML = msgs.map((m) => `<div class="chat-msg ${m.role}">${esc(m.text)}</div>`).join("");
  log.scrollTop = log.scrollHeight;
  const badge = $("chat-badge"); if (badge) badge.hidden = chatOpen || msgs.length === 0;
}

function renderDots() {
  const set = (id, cls) => ($("dot-" + id).className = "dot " + (cls || ""));
  set("brief", S.brief ? (S.clarifications.length ? "attn" : "done") : "");
  set("discover", S.recommendations.length ? "done" : "");
  const flagged = S.recommendations.some((r) => r.verdict !== "pass");
  set("verify", S.recommendations.length ? (flagged ? "attn" : "done") : "");
  const sc = S.secure;
  set("checkout", S.orders.length ? "done" : (sc && !["captured", "rejected"].includes(sc.stage)) ? "attn" : "");
}

function renderCartMini() {
  const n = S.cart.reduce((a, c) => a + c.qty, 0);
  const sub = S.cart.reduce((a, c) => a + c.qty * c.product.price, 0);
  $("cart-mini").innerHTML = n ? `🛒 ${n} item(s) · ${money(sub)}` : "🛒 Cart is empty";
}

// ------------------------------------------------------------------ 1. brief
function renderBrief() {
  $("brief-area").hidden = !S.brief;
  if (!S.brief) return;
  $("brief-summary").textContent = S.summary;
  $("clarifications").innerHTML = S.clarifications.map((c, i) => {
    const g = GAP_META[c.gap];
    const badge = g ? `<span class="clar-gap" title="Crosses the ${esc(c.gap)} gap, ${esc(g.label)}">${g.icon} ${esc(g.label)}</span>` : "";
    return `
    <div class="clar"><div class="clar-q">${esc(c.question)}${badge}</div><div class="row">
      ${c.options.map((o) => `<button class="chip" data-clar="${i}" data-val="${esc(o)}">${esc(o)}</button>`).join("")}
      ${c.allow_free_text ? `<input placeholder="or type…" data-clar-text="${i}">` : ""}
    </div></div>`;
  }).join("");

  const b = S.brief;
  $("brief-form").innerHTML = BRIEF_FORM.map(([key, label, type]) => {
    const src = b.provenance[key] || "none";
    const val = Array.isArray(b[key]) ? b[key].join(", ") : (b[key] ?? "");
    const tag = { stated: "you said", inferred: "AI inferred – check", edited: "you edited", remembered: "remembered", none: "" }[src];
    const input = type === "section"
      ? `<select name="${key}"><option value="">any</option>${["women", "men", "unisex"].map((o) => `<option ${o === val ? "selected" : ""}>${o}</option>`).join("")}</select>`
      : `<input name="${key}" type="${type === "number" ? "number" : "text"}" value="${esc(val)}" placeholder="${type === "list" ? "comma separated" : "not set"}">`;
    return `<div class="bfield ${src}"><label><i class="src ${src}"></i>${label}<span class="tag">${tag}</span></label>${input}</div>`;
  }).join("");
}

function readBriefForm() {
  const f = new FormData($("brief-form"));
  const out = { ...S.brief };
  for (const [key, , type] of BRIEF_FORM) {
    const raw = (f.get(key) ?? "").toString().trim();
    if (type === "list") out[key] = raw ? raw.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean) : [];
    else if (type === "number") out[key] = raw === "" ? null : Number(raw);
    else out[key] = raw === "" ? (key === "notes" ? "" : null) : raw;
  }
  return out;
}
const briefChanged = () => JSON.stringify(readBriefForm()) !== JSON.stringify(S.brief);

// ------------------------------------------------------------------ 2. discover
function renderDiscover() {
  const showFailed = $("show-failed").checked;
  const recs = S.recommendations.filter((r) => showFailed || r.verdict !== "fail");
  const hidden = S.recommendations.length - recs.length;
  if (!S.recommendations.length) {
    $("product-grid").innerHTML = `<p class="muted">No picks yet. ${S.brief ? "Click “Ask stylist again”." : "Build a brief first."}</p>`;
    return;
  }
  $("product-grid").innerHTML = recs.map((r) => card(r)).join("") +
    (hidden ? `<p class="muted">${hidden} pick(s) hidden because they failed a hard check. Tick “Show items that failed checks” to see why.</p>` : "");
}

function card(r) {
  const p = r.product;
  const cols = p.colors || [];
  const t1 = TINT[cols[0]] || "#eceae6", t2 = TINT[cols[1]] || t1;
  const emoji = p.emoji || EMOJI[p.category] || "👕";
  const sizes = Object.entries(p.stock).map(([s, q]) =>
    `<option value="${esc(s)}" ${s === r.size ? "selected" : ""} ${q ? "" : "disabled"}>${esc(s)}${q ? "" : " (out)"}</option>`).join("");
  const label = { pass: "Verified", warn: "Check notes", fail: "Failed checks" }[r.verdict];
  const dots = cols.slice(0, 5).map((c) => `<i style="background:${SWATCH[c] || "#bbb"}" title="${esc(c)}"></i>`).join("");
  const r5 = Math.round(p.rating || 0), stars = "★".repeat(r5) + "☆".repeat(5 - r5);
  const off = p.original_price ? Math.round((1 - p.price / p.original_price) * 100) : 0;
  const total = Object.values(p.stock).reduce((a, b) => a + b, 0);
  const nColours = (p.colorways || cols).length;
  const chips = [
    p.free_shipping ? `<span class="pchip ship">Free shipping</span>` : "",
    p.returnable ? `<span class="pchip">Free returns</span>` : `<span class="pchip">Final sale</span>`,
    (total > 0 && total <= 5) ? `<span class="pchip low">Only ${total} left</span>` : "",
    nColours > 1 ? `<span class="pchip">${nColours} colours</span>` : "",
  ].filter(Boolean).join("");
  return `<article class="card ${r.verdict}">
    <div class="tile" style="background:linear-gradient(145deg, ${t1}, ${t2})">
      ${off ? `<span class="ribbon">-${off}%</span>` : ""}
      <span class="garment">${emoji}</span>
      <span class="badge ${r.verdict}">${label}</span>
      ${dots ? `<div class="swatches">${dots}</div>` : ""}
    </div>
    <div class="card-body">
      <div class="cbrand">${esc(p.brand)}${p.store ? ` <span class="soldby">at ${esc(p.store)}</span>` : ""}</div>
      <div class="card-title">${esc(p.name)}</div>
      ${p.rating ? `<div class="rating"><span class="stars">${stars}</span> ${p.rating} <span class="muted">(${p.review_count})</span></div>` : ""}
      <div class="price${p.original_price ? " onsale" : ""}"><span class="now">${money(p.price)}</span>${p.original_price ? `<span class="was">${money(p.original_price)}</span>` : ""}</div>
      ${chips ? `<div class="pchips">${chips}</div>` : ""}
      <div class="why">${esc(r.rationale)}</div>
      <details ${r.verdict !== "pass" ? "open" : ""}><summary>Why this pick</summary>${checklist(r)}</details>
    </div>
    <div class="card-foot"><select data-size="${esc(p.id)}">${sizes}</select>
      <button class="primary" data-add="${esc(p.id)}">Add to cart</button></div>
  </article>`;
}

const checklist = (r) => `<ul class="checks">${r.checks.map((c) =>
  `<li><span class="st ${c.status}">${c.status}</span><span><b>${esc(c.name)}</b> – ${esc(c.evidence)}</span></li>`).join("")}</ul>`;

// ------------------------------------------------------------------ 3. verify
function renderVerify() {
  if (!S.recommendations.length) { $("verify-table").innerHTML = `<p class="muted">Nothing to verify yet.</p>`; return; }
  const rows = S.recommendations.map((r) => {
    const byName = Object.fromEntries(r.checks.map((c) => [c.name, c]));
    const cells = CHECK_COLS.map((n) => {
      const c = byName[n];
      return c ? `<td class="cell" title="${esc(c.evidence)}"><span class="st ${c.status}">${ICON[c.status]}</span></td>` : `<td class="cell muted">–</td>`;
    }).join("");
    const claims = r.claims.length ? `<ul class="claims">${r.claims.map((c) =>
      `<li class="${c.supported ? "" : "no"}">${c.supported ? "✓" : "✗"} ${esc(c.claim)} <span class="muted">(${esc(c.evidence)})</span></li>`).join("")}</ul>` : "";
    return `<tr><td><b>${esc(r.product.name)}</b> <span class="muted">${money(r.product.price)} · size ${esc(r.size || "?")}</span>
      <div class="why">${esc(r.rationale)}</div>${claims}</td>
      <td class="cell"><span class="st ${r.verdict}">${r.verdict}</span></td>${cells}</tr>`;
  }).join("");
  $("verify-table").innerHTML = `<p class="muted">Hover a cell to see its evidence. ✗ = hard constraint broken, ! = worth a look.</p>
    <table><thead><tr><th>Recommendation & claims</th><th>Verdict</th>${CHECK_COLS.map((c) => `<th>${c}</th>`).join("")}</tr></thead>
    <tbody>${rows}</tbody></table>`;
}

// ------------------------------------------------------------------ 4. checkout
// Each gate maps to a reliance principle, shown as a caption so the guardrail is legible.
const GATE_STEPS = [
  { key: "cart_review", label: "Cart" },
  { key: "payment_method", label: "Card" },
  { key: "combined_review", label: "Review" },
  { key: "otp", label: "2FA" },
  { key: "authorized", label: "Pay" },
];

function renderCheckout() {
  $("cart-list").innerHTML = S.cart.length ? S.cart.map((c, i) => `
    <div class="cart-line"><span><b>${esc(c.product.name)}</b><br><span class="muted">${c.product.brand ? esc(c.product.brand) + " · " : ""}${c.color ? esc(c.color) + " · " : ""}size ${esc(c.size)} × ${c.qty} · ${money(c.product.price * c.qty)}</span></span>
    <button class="ghost" data-remove="${i}">Remove</button></div>`).join("") : `<p class="muted">Your cart is empty.</p>`;
  if (!$("pay-method").options.length) {
    $("pay-method").innerHTML = META.payment_methods.map((m) => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join("");
  }
  // Keep the dropdown in step with a card the shopper switched to from chat.
  if (S.secure?.plan?.payment_method) $("pay-method").value = S.secure.plan.payment_method;
  if ($("cap").value === "" && S.brief?.budget_total) $("cap").value = S.brief.budget_total;

  let html = S.secure ? renderSecure(S.secure) : "";
  html += S.orders.slice().reverse().map((o) =>
    `<div class="receipt">✅ Order <b>${esc(o.order_id)}</b> placed · ${money(o.total)} · settled ${esc(o.capture_id || o.payment_intent || "")}</div>`).join("");
  $("plan-area").innerHTML = html || `<p class="muted">Start a secure checkout to step through the guardrails before any money moves.</p>`;
}

function stepper(s) {
  const here = GATE_STEPS.findIndex((g) => g.key === s.stage);
  const done = s.stage === "captured" ? GATE_STEPS.length : here;
  return `<div class="sec-steps">${GATE_STEPS.map((g, i) =>
    `<span class="sec-step ${i < done ? "done" : ""} ${i === here ? "now" : ""}">${i + 1}. ${g.label}</span>`).join("<span class='sec-arr'>→</span>")}</div>`;
}

function totalsTable(p) {
  return `<table class="totals">${p.lines.map((l) => `<tr><td>${esc(l.name)} (${l.color ? esc(l.color) + ", " : ""}${esc(l.size)}) × ${l.qty}</td><td style="text-align:right">${money(l.line_total)}</td></tr>`).join("")}
    <tr><td class="muted">Subtotal</td><td style="text-align:right">${money(p.subtotal)}</td></tr>
    <tr><td class="muted">Shipping</td><td style="text-align:right">${money(p.shipping)}</td></tr>
    <tr><td class="muted">Tax</td><td style="text-align:right">${money(p.tax)}</td></tr>
    <tr class="grand"><td>Total</td><td style="text-align:right">${money(p.total)}</td></tr></table>`;
}

function renderSecure(s) {
  const p = s.plan;
  const q = s.question ? `<p class="gate-q">🔒 ${esc(s.question)}</p>` : "";
  let body = "";
  if (s.stage === "cart_review") {
    body = totalsTable(p) + (p.warnings.length ? `<div class="warnings"><b>Please check:</b><ul>${p.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : "")
      + `<div class="actions"><button class="danger" id="sc-reject">Cancel</button><button class="primary" id="sc-cart">Confirm cart ✓</button></div>`;
  } else if (s.stage === "payment_method") {
    body = `<p>Confirm the card to charge. Pick it on the left, then confirm.</p>
      <div class="actions"><button class="danger" id="sc-reject">Cancel</button><button class="primary" id="sc-card">Confirm card ✓</button></div>`;
  } else if (s.stage === "combined_review") {
    body = totalsTable(p) + `<p class="muted">Card: ${esc(META.payment_methods.find((m) => m.id === p.payment_method)?.label || p.payment_method)}</p>`
      + (s.requires_cap_override ? `<label class="field"><input type="checkbox" id="sc-cap" style="width:auto"> I know this is over my spending cap, go ahead</label>` : "")
      + `<div class="actions"><button class="danger" id="sc-reject">Cancel</button><button class="primary" id="sc-review">Confirm & authorize</button></div>`;
  } else if (s.stage === "otp") {
    const t = s.twofa || {};
    body = `<div class="draft"><b>The agent prepared this order:</b><ul>${(s.order_draft?.items || []).map((i) => `<li>${esc(i)}</li>`).join("")}</ul>
        <span class="muted">${esc(s.order_draft?.note || "")}</span></div>
      <p>🔐 A one-time code was sent to <b>${esc(t.sent_to || "your phone")}</b> (2FA via ${esc(t.method || "SMS")}). ${t.attempts_left != null ? `${t.attempts_left} attempt(s) left.` : ""}</p>
      ${t.demo_otp ? `<p class="muted">Sandbox: your code is <b>${esc(t.demo_otp)}</b> (a real bank would send it only to the phone).</p>` : ""}
      <div class="actions"><input id="otp-code" placeholder="6-digit code" inputmode="numeric" style="width:140px">
        <button class="danger" id="sc-reject">Cancel</button><button class="primary" id="sc-otp">Verify & continue</button></div>`;
  } else if (s.stage === "authorized") {
    body = totalsTable(p) + `<p>✅ 2FA passed and the payment is authorized (held, not yet charged). Give final approval to capture.</p>
      <div class="actions"><button class="danger" id="sc-reject">Cancel</button><button class="primary" id="sc-pay">Pay ${money(p.total)} now</button></div>`;
  } else if (s.stage === "captured") {
    body = `<p class="paid-note">✅ Payment captured, order placed.</p>`;
  } else if (s.stage === "rejected") {
    body = `<div class="error-box">Checkout stopped: <b>${esc(s.last_error || "cancelled")}</b>. No money was captured.</div>
      <div class="actions"><button class="primary" id="sc-restart">Start over</button></div>`;
  }
  const trail = s.history?.length ? `<details class="audit"><summary>Audit trail (${s.history.length})</summary><ol>${s.history.map((h) => `<li><code>${esc(h.stage)}</code> ${esc(h.action)}, ${esc(h.note)}</li>`).join("")}</ol></details>` : "";
  return `<div class="plan secure">${stepper(s)}${q}${body}${trail}</div>`;
}

// ------------------------------------------------------------------ events
document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("button");
  if (!t) return;
  if (t.classList.contains("step")) return go(t.dataset.view);
  if (t.dataset.suggest != null) { $("intake-text").value = t.dataset.suggest; $("intake-go").click(); return; }
  if (t.dataset.clar != null) {
    const c = S.clarifications[t.dataset.clar];
    return act("POST", sp("/clarify"), { field: c.field, value: t.dataset.val });
  }
  if (t.dataset.add) {
    const size = document.querySelector(`[data-size="${t.dataset.add}"]`).value;
    const color = document.querySelector(`[data-color="${t.dataset.add}"]`)?.value || null;
    if (await act("POST", sp("/cart"), { product_id: t.dataset.add, size, color, qty: 1 })) toast("Added to cart");
    return;
  }
  if (t.dataset.remove != null) return act("DELETE", sp(`/cart/${t.dataset.remove}`));

  switch (t.id) {
    case "intake-go": return sendChat($("intake-text").value);
    case "chat-send": return sendChat($("chat-text").value);
    case "chat-launch": return setChatOpen(true);
    case "chat-min": return setChatOpen(false);
    case "brief-save": return act("PUT", sp("/brief"), readBriefForm());
    case "to-discover":
      if (briefChanged() && !(await act("PUT", sp("/brief"), readBriefForm()))) return;
      if (await act("POST", sp("/recommend"), {}, "Finding and checking your best matches…")) go("discover");
      return;
    case "refine-go":
      return act("POST", sp("/recommend"), { feedback: $("feedback").value.trim() || "show me different options" }, "Finding different options…");
    case "plan-go": {
      const cap = $("cap").value;
      return act("POST", sp("/secure/begin"), { spending_cap: cap === "" ? null : Number(cap), payment_method: $("pay-method").value }, "Starting secure checkout…");
    }
    case "sc-cart": return act("POST", sp("/secure/confirm-cart"));
    case "sc-card": return act("POST", sp("/secure/confirm-card"), { payment_method: $("pay-method").value });
    case "sc-review": return act("POST", sp("/secure/confirm-review"), { cap_override: !!$("sc-cap")?.checked });
    case "sc-otp": {
      const code = $("otp-code").value.trim();
      if (!code) return toast("Enter the code from your phone.");
      return act("POST", sp("/secure/otp"), { code }, "Checking your code…");
    }
    case "sc-pay": return act("POST", sp("/secure/finalize"), null, "Capturing payment…");
    case "sc-reject": return act("POST", sp("/secure/reject"));
    case "sc-restart": {
      const cap = $("cap").value;
      return act("POST", sp("/secure/begin"), { spending_cap: cap === "" ? null : Number(cap), payment_method: $("pay-method").value });
    }
    case "plan-edit": return go("discover");
    case "reset": return start();
  }
});

// Enter submits the free-text answer to a clarification
document.addEventListener("keydown", (ev) => {
  const i = ev.target.dataset?.clarText;
  if (ev.key === "Enter" && i != null && ev.target.value.trim()) {
    act("POST", sp("/clarify"), { field: S.clarifications[i].field, value: ev.target.value.trim() });
  }
  if (ev.key === "Enter" && ev.target.id === "intake-text" && !ev.shiftKey) { ev.preventDefault(); sendChat($("intake-text").value); }
  if (ev.key === "Enter" && ev.target.id === "chat-text") { ev.preventDefault(); sendChat($("chat-text").value); }
});
$("show-failed").addEventListener("change", render);

async function start() {
  META = META || (await api("GET", "/api/meta"));
  $("llm-pill").textContent = META.llm;
  if ($("catalog-source")) $("catalog-source").textContent = META.catalog_source;
  // Fresh, gap-balanced prompt suggestions each visit (omit seed = dynamic; seed = reproducible).
  try { SUGGEST = await api("GET", "/api/suggestions?n=4"); } catch { SUGGEST = null; }
  renderSuggestions();
  $("cap").value = "";
  S = await api("POST", "/api/session");
  go("brief");
}
start().catch((e) => toast("Could not reach the server: " + e.message));
