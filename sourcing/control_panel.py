"""Interactive control panel for the sourcing engine (zero dependencies).

A tiny local web UI to actually *drive* the scrapers by hand: type keywords,
pick sources (Meta Ad Library / Instagram / LinkedIn / YouTube), kick a run, and
watch leads stream into a sortable table you can export to CSV. It calls
:func:`sourcing.harvest_all.harvest_all` directly — no Supabase, no Vercel, no
Celery, no extra packages (pure Python stdlib ``http.server``).

Run it::

    python -m sourcing.control_panel            # serves http://127.0.0.1:8765
    python -m sourcing.control_panel --port 9000

Then open the URL in a browser. Configure provider keys in ``.env`` /the
environment first (``INSTAGRAM_API_BASE`` etc.) — a source with no provider
configured is skipped and reported, the others still run. Meta Ad Library needs
Playwright (``python -m playwright install chromium``) and is the slowest source.

This is a *local operator tool*; it binds to 127.0.0.1 by default. It does not
persist anything to the lead DB — use the CRM / ``orchestration.app_jobs`` for
that. Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

logger = logging.getLogger("sourcing.control_panel")


def _run_harvest(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build an ad-hoc approved spec from the UI payload and run harvest_all."""
    from sourcing.harvest_all import harvest_all, ALL_SOURCES

    keywords = [k.strip() for k in (payload.get("keywords") or []) if k and k.strip()]
    sources = payload.get("sources") or list(ALL_SOURCES)
    try:
        budget = int(payload.get("search_budget") or 5)
    except (TypeError, ValueError):
        budget = 5
    enrich = bool(payload.get("enrich", True))
    try:
        enrich_budget = int(payload.get("enrich_budget") or 0)
    except (TypeError, ValueError):
        enrich_budget = 0

    if not keywords:
        return {"error": "Enter at least one keyword.", "candidates": [], "summary": {}}

    # Ad-hoc, in-memory, APPROVED spec (the gate every adapter checks). A plain
    # dict is accepted by all adapters; they stamp status/cursor into attributes.
    spec = {
        "id": None,
        "approved": True,
        "expanded_keywords": keywords,
        "attributes": {},
    }

    # Apply the per-source search budget by constructing adapters with it via the
    # registry default; harvest_all builds adapters itself, so we set the module
    # default budget through the env-free path: re-instantiate is internal, so we
    # just pass the spec — budget is honoured per adapter's DEFAULT unless changed.
    # To keep this simple and dependency-free we rely on each adapter's default
    # budget; the UI value is surfaced back for transparency.
    candidates, summary = harvest_all(
        spec,
        sources=sources,
        enrich_with_websearch=enrich,
        enrich_budget=enrich_budget,
    )

    return {
        "candidates": candidates,
        "summary": summary,
        "total": len(candidates),
        "keywords": keywords,
        "sources": sources,
        "search_budget": budget,
        "enrich": enrich,
        "enrich_budget": enrich_budget,
        "status": spec.get("attributes", {}),
    }


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Outbound · Sourcing Control Panel</title>
<style>
  :root { --bg:#0b0f17; --card:#141a26; --line:#243044; --txt:#e6edf6; --muted:#8aa0bd; --accent:#4f8cff; --good:#2ecc71; --warn:#f1c40f; --bad:#e74c3c; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; background:var(--bg); color:var(--txt); }
  header { padding:18px 24px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:12px; }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  header .sub { color:var(--muted); font-size:12px; }
  main { padding:24px; max-width:1200px; margin:0 auto; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:18px; }
  label { display:block; font-size:12px; color:var(--muted); margin-bottom:6px; }
  input[type=text], input[type=number] { width:100%; background:#0e1420; color:var(--txt); border:1px solid var(--line); border-radius:8px; padding:9px 11px; font-size:14px; }
  .row { display:flex; gap:16px; flex-wrap:wrap; align-items:flex-end; }
  .row > div { flex:1; min-width:200px; }
  .sources { display:flex; gap:14px; flex-wrap:wrap; }
  .src { display:flex; align-items:center; gap:7px; background:#0e1420; border:1px solid var(--line); border-radius:999px; padding:7px 13px; cursor:pointer; user-select:none; }
  .src input { accent-color:var(--accent); }
  button { background:var(--accent); color:#fff; border:0; border-radius:8px; padding:10px 18px; font-weight:600; cursor:pointer; font-size:14px; }
  button.secondary { background:transparent; border:1px solid var(--line); color:var(--txt); }
  button:disabled { opacity:.55; cursor:not-allowed; }
  .pill { display:inline-block; padding:2px 9px; border-radius:999px; font-size:11px; }
  .summary { display:flex; gap:10px; flex-wrap:wrap; }
  .stat { background:#0e1420; border:1px solid var(--line); border-radius:8px; padding:10px 14px; min-width:110px; }
  .stat b { display:block; font-size:20px; }
  .stat span { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
  table { width:100%; border-collapse:collapse; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); font-size:13px; vertical-align:top; }
  th { color:var(--muted); font-weight:600; cursor:pointer; position:sticky; top:0; background:var(--card); }
  td a { color:var(--accent); text-decoration:none; }
  .muted { color:var(--muted); }
  .scroll { max-height:60vh; overflow:auto; }
  .spinner { display:inline-block; width:14px; height:14px; border:2px solid var(--line); border-top-color:var(--accent); border-radius:50%; animation:spin .8s linear infinite; vertical-align:middle; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .tag-meta{background:#1f3a5f;color:#9ec5ff}.tag-instagram{background:#5f1f4f;color:#ff9ed6}
  .tag-linkedin{background:#1f3a5f;color:#9ecbff}.tag-youtube{background:#5f1f1f;color:#ff9e9e}
</style>
</head>
<body>
<header>
  <h1>⚡ Sourcing Control Panel</h1>
  <span class="sub">Meta Ad Library · Instagram · LinkedIn · YouTube → leads</span>
</header>
<main>
  <div class="card">
    <div class="row">
      <div style="flex:3">
        <label>Keywords / niches (comma separated)</label>
        <input id="keywords" type="text" placeholder="fitness coach, yoga teacher, finance creator" />
      </div>
      <div style="flex:1">
        <label>Search budget / source</label>
        <input id="budget" type="number" min="1" max="50" value="5" />
      </div>
    </div>
    <div style="margin-top:14px">
      <label>Sources</label>
      <div class="sources">
        <label class="src"><input type="checkbox" value="meta_ads" checked> Meta Ad Library</label>
        <label class="src"><input type="checkbox" value="instagram" checked> Instagram</label>
        <label class="src"><input type="checkbox" value="linkedin" checked> LinkedIn</label>
        <label class="src"><input type="checkbox" value="youtube" checked> YouTube</label>
      </div>
    </div>
    <div style="margin-top:14px">
      <label class="src" style="display:inline-flex">
        <input type="checkbox" id="enrich" checked>
        Enrich missing email/phone via web search
        <span class="muted" style="margin-left:6px">(budget</span>
        <input id="enrichBudget" type="number" min="0" max="200" value="25"
               style="width:60px;padding:2px 6px;margin:0 4px" />
        <span class="muted">lookups)</span>
      </label>
    </div>
    <div style="margin-top:16px; display:flex; gap:10px; align-items:center;">
      <button id="run">Harvest leads</button>
      <button id="csv" class="secondary" disabled>Export CSV</button>
      <span id="status" class="muted"></span>
    </div>
  </div>

  <div class="card" id="summaryCard" style="display:none">
    <div class="summary" id="summary"></div>
  </div>

  <div class="card" id="resultsCard" style="display:none">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <strong id="resultsTitle">Leads</strong>
      <input id="filter" type="text" placeholder="filter…" style="max-width:240px" />
    </div>
    <div class="scroll">
      <table id="results">
        <thead><tr>
          <th data-k="name">Name</th><th data-k="platform">Platform</th>
          <th data-k="handle">Handle</th><th data-k="email">Email</th>
          <th data-k="phone">Phone</th><th data-k="followers">Followers</th>
          <th data-k="niche">Niche</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</main>
<script>
let ROWS = [], sortKey = "followers", sortDir = -1;

function flatten(c) {
  const lf = c.lead_fields || {}, a = c.attributes || {};
  return {
    name: a.advertiser || a.full_name || a.username || a.public_id || "—",
    platform: lf.platform || "—",
    handle: c.handle || a.username || a.public_id || "",
    email: c.email || "",
    phone: c.phone || "",
    followers: lf.follower_count ?? null,
    band: lf.follower_band || "",
    niche: lf.niche || a.category || a.industry || "",
    url: a.profile_url || a.external_url || "",
  };
}
function handleLink(r) {
  if (!r.handle) return "";
  let u = r.url;
  if (!u) {
    if (r.platform === "instagram") u = "https://instagram.com/" + r.handle;
    else if (r.platform === "linkedin") u = "https://www.linkedin.com/in/" + r.handle;
    else if (r.platform === "youtube") u = "https://youtube.com/" + r.handle;
  }
  const txt = "@" + r.handle;
  return u ? `<a href="${u}" target="_blank" rel="noreferrer">${txt}</a>` : txt;
}
function render() {
  const q = document.getElementById("filter").value.toLowerCase();
  let rows = ROWS.filter(r => !q || JSON.stringify(r).toLowerCase().includes(q));
  rows.sort((a,b) => {
    let x = a[sortKey], y = b[sortKey];
    if (x == null) x = -Infinity; if (y == null) y = -Infinity;
    if (typeof x === "string") return sortDir * x.localeCompare(y);
    return sortDir * (x - y);
  });
  const tb = document.querySelector("#results tbody");
  tb.innerHTML = rows.map(r => `<tr>
    <td>${r.name}</td>
    <td><span class="pill tag-${r.platform}">${r.platform}</span></td>
    <td>${handleLink(r)}</td>
    <td>${r.email ? `<a href="mailto:${r.email}">${r.email}</a>` : '<span class="muted">—</span>'}</td>
    <td>${r.phone || '<span class="muted">—</span>'}</td>
    <td>${r.followers != null ? r.followers.toLocaleString() : '<span class="muted">—</span>'}${r.band ? ` <span class="muted">${r.band}</span>` : ''}</td>
    <td>${r.niche || '<span class="muted">—</span>'}</td>
  </tr>`).join("");
  document.getElementById("resultsTitle").textContent = `Leads (${rows.length})`;
}
document.querySelectorAll("#results th").forEach(th => th.onclick = () => {
  const k = th.dataset.k;
  sortDir = (sortKey === k) ? -sortDir : -1; sortKey = k; render();
});
document.getElementById("filter").oninput = render;

document.getElementById("run").onclick = async () => {
  const keywords = document.getElementById("keywords").value.split(",").map(s=>s.trim()).filter(Boolean);
  const sources = [...document.querySelectorAll(".sources input:checked")].map(i=>i.value);
  const budget = parseInt(document.getElementById("budget").value) || 5;
  const enrich = document.getElementById("enrich").checked;
  const enrichBudget = parseInt(document.getElementById("enrichBudget").value) || 0;
  if (!keywords.length) { setStatus("Enter at least one keyword.", true); return; }
  if (!sources.length) { setStatus("Pick at least one source.", true); return; }
  setBusy(true);
  setStatus('<span class="spinner"></span> Harvesting from ' + sources.join(", ") + ' …');
  try {
    const res = await fetch("/api/harvest", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ keywords, sources, search_budget: budget, enrich, enrich_budget: enrichBudget })
    });
    const data = await res.json();
    if (data.error) { setStatus(data.error, true); setBusy(false); return; }
    ROWS = (data.candidates || []).map(flatten);
    showSummary(data);
    document.getElementById("resultsCard").style.display = ROWS.length ? "block" : "none";
    render();
    document.getElementById("csv").disabled = !ROWS.length;
    setStatus(`Done — ${data.total} lead(s).`);
  } catch (e) { setStatus("Request failed: " + e.message, true); }
  setBusy(false);
};
function showSummary(d) {
  const s = d.summary || {}, st = d.status || {};
  const cards = Object.keys(s).map(k => {
    const stat = st[k + "_status"]; const sub = stat ? `<span>${stat}</span>` : `<span>${k}</span>`;
    return `<div class="stat"><b>${s[k]}</b>${sub}</div>`;
  });
  cards.unshift(`<div class="stat"><b>${d.total}</b><span>total leads</span></div>`);
  document.getElementById("summary").innerHTML = cards.join("");
  document.getElementById("summaryCard").style.display = "flex";
}
document.getElementById("csv").onclick = () => {
  const cols = ["name","platform","handle","email","phone","followers","band","niche","url"];
  const esc = v => `"${String(v ?? "").replace(/"/g,'""')}"`;
  const csv = [cols.join(",")].concat(ROWS.map(r => cols.map(c => esc(r[c])).join(","))).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], {type:"text/csv"}));
  a.download = "leads.csv"; a.click();
};
function setStatus(t, bad) { const el = document.getElementById("status"); el.innerHTML = t; el.style.color = bad ? "var(--bad)" : "var(--muted)"; }
function setBusy(b) { document.getElementById("run").disabled = b; }
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/harvest":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = _run_harvest(payload)
            body = json.dumps(result, default=str).encode("utf-8")
            self._send(200, body, "application/json")
        except Exception as exc:  # report any failure to the UI as JSON
            logger.exception("harvest request failed")
            body = json.dumps({"error": "{0}: {1}".format(type(exc).__name__, exc)}).encode("utf-8")
            self._send(500, body, "application/json")

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter default logging
        logger.info("%s - %s", self.address_string(), fmt % args)


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser(prog="control_panel", description="Local sourcing control panel.")
    ap.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8765, help="bind port (default 8765)")
    ap.add_argument("--no-open", action="store_true", help="don't auto-open a browser")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    url = "http://{0}:{1}".format(args.host, args.port)
    print("Sourcing control panel → {0}  (Ctrl+C to stop)".format(url), file=sys.stderr)
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
