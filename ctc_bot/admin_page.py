"""The admin page: merging spellings that are probably one person.

Its own host and its own page, deliberately. Two reasons, and only the second
one is worth much:

* Ordinary members should not trip over a tool that rewrites who owns which
  results. The club's page is for looking at your own times.
* More usefully, **the main page contains no trace of this at all** - not a
  hidden section, not a dormant fetch, nothing in view-source. Hiding a control
  with CSS leaves the API it calls sitting there for anyone who opens devtools;
  moving the whole thing to a host the server gates separately does not.

That gating lives in :mod:`ctc_bot.server`: with ``CTC_ADMIN_HOST`` set, the
merge endpoints answer on that host and 404 everywhere else. Without it - the
local install - everything is available at ``/admin``, because there is nobody
to hide from on 127.0.0.1.

The subdomain is **not a secret**. Traefik requests a certificate per router,
so the name appears in public Certificate Transparency logs the moment it first
serves. It keeps members from wandering in; it does not keep anyone out. The
login password is what does that.
"""

from __future__ import annotations


def render() -> str:
    return _TEMPLATE


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Cork Tri Club — tidying names</title>
<style>
  /* Dark only, matching the dashboard. */
  :root {
    color-scheme: dark;
    --plane:#0d0d0d; --surface:#1a1a19;
    --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --border:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#e0a458; --good:#5fbf7f; --bad:#e66767;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--plane); color:var(--ink);
         font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  header { position:sticky; top:0; z-index:5; background:var(--plane);
           border-bottom:1px solid var(--border); padding:12px 20px;
           display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  header h1 { margin:0; font-size:17px; letter-spacing:-0.02em; }
  header .count { color:var(--muted); font-size:13px; }
  main { max-width:940px; margin:0 auto; padding:18px 20px 60px; }
  .hint { color:var(--ink-2); font-size:13.5px; }
  .note { border-left:3px solid var(--s2); padding:8px 12px; margin:14px 0;
          background:var(--surface); border-radius:0 8px 8px 0; font-size:13.5px; }
  .pill { font-size:11px; padding:2px 7px; border-radius:999px;
          border:1px solid var(--border); color:var(--ink-2); white-space:nowrap; }
  .pill.weak { border-color:var(--s2); color:var(--s2); }
  .pill.warn { border-color:var(--bad); color:var(--bad); }
  button { font:inherit; padding:6px 12px; border-radius:7px; cursor:pointer;
           background:var(--surface); color:var(--ink); border:1px solid var(--border); }
  button.primary { background:var(--s1); border-color:var(--s1); color:#fff; }
  button.ghost { background:none; border:0; color:var(--bad); }
  button:disabled { opacity:.5; cursor:default; }
  .filters { display:flex; gap:6px; margin:14px 0 4px; flex-wrap:wrap; }
  .filters button[aria-selected="true"] { border-color:var(--s1); color:var(--s1); }

  .card { border:1px solid var(--border); border-radius:9px; padding:11px 13px;
          margin:9px 0; background:var(--surface); }
  .card.weak { border-style:dashed; }
  .card .top { display:flex; gap:9px; align-items:center; flex-wrap:wrap; }
  .card .who { font-weight:600; }
  .card .acts { display:flex; gap:6px; margin-left:auto; }
  .card .variants { margin-top:8px; color:var(--ink-2); font-size:13px; line-height:1.9; }
  .card code { background:var(--plane); padding:2px 7px; border-radius:5px;
               font:inherit; color:var(--ink); }
  .card .owner { color:var(--muted); font-size:12px; }
  .card .why { margin-top:6px; color:var(--muted); font-size:12px; }
  .msg { min-height:20px; font-size:13.5px; margin:10px 0; }
  .good { color:var(--good); } .bad { color:var(--bad); }
  @media (max-width:520px) {
    .card .acts { margin-left:0; width:100%; }
    .card .acts button { flex:1; }
  }
</style>
</head>
<body>
<header>
  <h1>Tidying names</h1>
  <span class="count" id="count"></span>
</header>

<main>
  <p class="hint">
    Results are grouped only by the name written on the entry sheet, so one
    person who was typed three different ways looks like three people, each with
    a short broken history. These are the groups that look like they are really
    one — <b>nothing is joined until you say so</b>.
  </p>
  <div class="note">
    Joining is just a batch of ordinary claims, so it comes apart the same way:
    open that athlete on the main site and use <b>Not mine</b> on any result that
    should not be there. There is no bulk undo, so take the dashed ones slowly.
  </div>

  <div class="filters" id="filters"></div>
  <div class="msg" id="msg"></div>
  <div id="list"></div>
</main>

<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));

let ALL = [];
let filter = "all";

async function load() {
  try {
    const res = await fetch("/api/merge-suggestions");
    const out = await res.json();
    ALL = out.suggestions || [];
  } catch (e) {
    $("msg").innerHTML = `<span class="bad">No server behind this page.</span>`;
    return;
  }
  render();
}

function render() {
  const strong = ALL.filter(r => r.confidence === "strong").length;
  const weak = ALL.length - strong;
  $("count").textContent = ALL.length
    ? `${ALL.length} to look at · ${strong} strong, ${weak} on a first name alone`
    : "nothing left to tidy";

  $("filters").innerHTML = [
    ["all", `Everything (${ALL.length})`],
    ["strong", `Strong evidence (${strong})`],
    ["weak", `First name only (${weak})`],
  ].map(([k, label]) =>
    `<button data-f="${k}" aria-selected="${filter === k}">${label}</button>`).join("");
  $("filters").querySelectorAll("button").forEach(b => {
    b.onclick = () => { filter = b.dataset.f; render(); };
  });

  const rows = ALL.filter(r => filter === "all" || r.confidence === filter);
  if (!rows.length) {
    $("list").innerHTML = `<p class="hint">Nothing here. ${ALL.length ? "Try another filter." : "Every suggestion has been dealt with."}</p>`;
    return;
  }

  $("list").innerHTML = rows.map(r => {
    const variants = r.variants.map(v =>
      `<code>${esc(v.name)}</code> <span class="owner">${v.races} result${v.races === 1 ? "" : "s"}` +
      `${v.owner ? ` · already ${esc(v.owner)}` : ""}</span>`
    ).join(" &nbsp;+&nbsp; ");
    return `<div class="card ${r.confidence}">
      <div class="top">
        <span class="who">${esc(r.name)}</span>
        <span class="pill">${r.races} results</span>
        ${r.confidence === "weak" ? '<span class="pill weak">first name only</span>' : ""}
        ${r.joinsClaimed ? '<span class="pill warn">joins two confirmed people</span>' : ""}
        ${r.mergesInto ? `<span class="pill">into ${esc(r.mergesInto)}</span>` : ""}
        <span class="acts">
          <button class="primary js-merge" data-k="${esc(r.key)}">Same person</button>
          <button class="ghost js-dismiss" data-k="${esc(r.key)}">Not the same</button>
        </span>
      </div>
      <div class="variants">${variants}</div>
      <div class="why">${esc(r.reasons.join(" · "))}${
        r.raceTypes.length > 1 ? " · both series" : ""}</div>
    </div>`;
  }).join("");

  $("list").querySelectorAll("button.js-merge").forEach(b => {
    b.onclick = () => act("/api/merge", b);
  });
  $("list").querySelectorAll("button.js-dismiss").forEach(b => {
    b.onclick = () => act("/api/dismiss-merge", b);
  });
}

/* Applied one at a time and re-fetched, rather than reloading the page.
   Confirming a merge can change what is left to suggest - two groups can share
   a spelling - so the list has to come from the server again, and the visitor
   should not lose their place in it while that happens. */
async function act(url, button) {
  const card = button.closest(".card");
  card.querySelectorAll("button").forEach(b => b.disabled = true);
  $("msg").textContent = "Saving…";
  try {
    const res = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: button.dataset.k }),
    });
    const out = await res.json();
    $("msg").innerHTML = out.ok
      ? `<span class="good">${esc(out.message)}</span>`
      : `<span class="bad">${esc(out.message || "Could not save.")}</span>`;
    if (out.ok) { await load(); return; }
  } catch (e) {
    $("msg").innerHTML = `<span class="bad">No server behind this page.</span>`;
  }
  card.querySelectorAll("button").forEach(b => b.disabled = false);
}

load();
</script>
</body>
</html>
"""
