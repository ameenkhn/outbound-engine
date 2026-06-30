"use server";

import { getServerClient } from "@/lib/supabase/server";
import { CSV_COLS, applyFilters, type LeadFilters } from "./filters";

export type { LeadFilters };

/** Export up to `limit` filtered leads as a CSV string (server-side, past RLS). */
export async function exportLeadsCsv(
  f: LeadFilters,
  limit = 5000,
): Promise<{ ok: true; csv: string; count: number } | { ok: false; error: string }> {
  try {
    const supa = getServerClient();
    let q = supa
      .from("leads")
      .select(CSV_COLS.join(","))
      .order("icp_score", { ascending: false, nullsFirst: false })
      .limit(limit);
    q = applyFilters(q, f);
    const { data, error } = await q;
    if (error) return { ok: false, error: error.message };

    const rows = (data ?? []) as unknown as Record<string, unknown>[];
    const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const csv = [CSV_COLS.join(",")]
      .concat(rows.map((r) => CSV_COLS.map((c) => esc(r[c])).join(",")))
      .join("\n");
    return { ok: true, csv, count: rows.length };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

// ── Import leads (Google Sheet link / CSV) ─────────────────────────────────

const COL = {
  email: ["email", "e-mail", "email address", "business_email", "mail"],
  phone: ["phone", "phone number", "mobile", "whatsapp", "business_phone_number", "contact number"],
  handle: ["handle", "username", "instagram", "ig", "insta", "social"],
  name: ["creator name", "name", "full name", "full_name", "advertiser", "company", "title"],
  category: ["sub-category", "subcategory", "sub category", "category", "niche", "industry"],
  platform: ["platform", "channel", "source platform"],
  profile: ["profile link", "profile", "profile_url", "profile url", "link", "url", "website"],
  course: ["course link", "course", "course_url", "course url", "offer"],
  audience: ["audience size", "audience", "followers", "follower count", "subscribers", "reach"],
  price: ["program / price", "program/price", "price", "pricing", "program price", "program"],
  source: ["source (verified on)", "source", "verified on", "verified", "found on"],
  notes: ["notes", "note", "remarks", "comment", "comments"],
};

function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let cur: string[] = [], field = "", inQ = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQ) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else inQ = false; }
      else field += c;
    } else if (c === '"') inQ = true;
    else if (c === ",") { cur.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      cur.push(field); if (cur.some((x) => x !== "")) rows.push(cur); cur = []; field = "";
    } else field += c;
  }
  if (field !== "" || cur.length) { cur.push(field); if (cur.some((x) => x !== "")) rows.push(cur); }
  if (rows.length < 2) return [];
  const headers = rows[0].map((h) => h.trim().toLowerCase());
  return rows.slice(1).map((r) =>
    Object.fromEntries(headers.map((h, i) => [h, (r[i] ?? "").trim()])));
}

const pick = (row: Record<string, string>, names: string[]) => {
  for (const n of names) if (row[n]) return row[n];
  return "";
};

function gsheetCsvUrls(url: string): string[] {
  // "Publish to web" link (…/spreadsheets/d/e/<id>/pub…) — use directly as CSV.
  if (url.includes("/spreadsheets/d/e/")) {
    const base = url.split("#")[0];
    return [base.includes("output=csv") ? base : base + (base.includes("?") ? "&" : "?") + "output=csv"];
  }
  const m = url.match(/\/spreadsheets\/d\/([a-zA-Z0-9-_]+)/);
  if (!m) return [];
  const id = m[1];
  const gid = (url.match(/[#&?]gid=([0-9]+)/) || [])[1] || "0";
  // Try the standard export endpoint first, then the gviz endpoint as a fallback.
  return [
    `https://docs.google.com/spreadsheets/d/${id}/export?format=csv&gid=${gid}`,
    `https://docs.google.com/spreadsheets/d/${id}/gviz/tq?tqx=out:csv&gid=${gid}`,
  ];
}

function buildLead(row: Record<string, string>): Record<string, unknown> | null {
  const email = pick(row, COL.email).toLowerCase();
  const phoneRaw = pick(row, COL.phone).replace(/[^\d]/g, "");
  const phone = phoneRaw ? (phoneRaw.length === 10 ? "+91" + phoneRaw : "+" + phoneRaw) : "";
  const handle = pick(row, COL.handle).toLowerCase().replace(/^@/, "").replace(/\s/g, "");
  const name = pick(row, COL.name);
  const category = pick(row, COL.category);
  const platform = pick(row, COL.platform).toLowerCase();
  const profile = pick(row, COL.profile);
  const course = pick(row, COL.course);
  const audience = pick(row, COL.audience);
  const price = pick(row, COL.price);
  const source = pick(row, COL.source);
  const notes = pick(row, COL.notes);

  let identity_key = "";
  if (email) identity_key = "email:" + email;
  else if (phone) identity_key = "phone:" + phone;
  else if (handle) identity_key = "handle:" + handle;
  else return null; // no reachable signal — skip

  const attributes: Record<string, unknown> = { imported: true };
  if (name) attributes.advertiser = name;
  if (email) attributes.email = email;
  if (phone) attributes.phone = phone;
  if (handle) attributes.handle = handle;
  if (category) attributes.sub_category = category;
  if (profile) attributes.profile_url = profile;
  if (course) attributes.course_link = course;
  if (audience) attributes.audience_size = audience;
  if (price) attributes.price = price;
  if (source) attributes.source_verified = source;
  if (notes) attributes.notes = notes;

  // try to pull a clean number out of a freeform audience string ("36K" → 36000)
  let followers: number | null = null;
  const am = audience.replace(/,/g, "").match(/([\d.]+)\s*([kKmM]?)/);
  if (am) {
    const n = parseFloat(am[1]);
    const mult = am[2].toLowerCase() === "m" ? 1e6 : am[2].toLowerCase() === "k" ? 1e3 : 1;
    if (!Number.isNaN(n)) followers = Math.round(n * mult);
  }

  // Note: no `status` here on purpose — on re-import we update existing leads
  // but must NOT reset their pipeline status. New leads get the DB default ('new').
  return {
    identity_key,
    segment: "creator",
    niche: category || null,
    platform: platform || (handle ? "instagram" : "web"),
    follower_count: followers,
    source: "import",
    attributes,
  };
}

/** Import leads from a Google Sheet link or pasted/uploaded CSV. Deduped by
 *  identity_key (email > phone > handle), so re-imports don't duplicate. */
export async function importLeads(input: {
  mode: "gsheet" | "csv";
  url?: string;
  csv?: string;
}): Promise<{ ok: true; inserted: number; skipped: number } | { ok: false; error: string }> {
  try {
    let text = input.csv || "";
    if (input.mode === "gsheet") {
      if (!input.url?.trim()) return { ok: false, error: "Paste a Google Sheet link." };
      const urls = gsheetCsvUrls(input.url.trim());
      if (urls.length === 0) return { ok: false, error: "That doesn't look like a Google Sheets link." };
      let got = "";
      for (const u of urls) {
        try {
          const res = await fetch(u, { redirect: "follow" });
          const t = await res.text();
          if (res.ok && t && !t.trimStart().startsWith("<")) { got = t; break; }
        } catch { /* try the next endpoint */ }
      }
      if (!got) {
        return { ok: false, error: "Can't read the sheet (Workspace/org sheets often block anonymous export). Either: File → Share → Publish to web → CSV and paste that link, OR File → Download → CSV and use the 'CSV file' tab." };
      }
      text = got;
    }
    if (!text.trim()) return { ok: false, error: "No data found to import." };

    const rows = parseCsv(text);
    if (rows.length === 0) return { ok: false, error: "Couldn't find any rows (need a header row + data rows)." };

    const leads = rows.map(buildLead).filter(Boolean) as Record<string, unknown>[];
    const skipped = rows.length - leads.length;
    if (leads.length === 0) {
      return { ok: false, error: "No rows had an email, phone, or handle column to identify a lead." };
    }

    const supa = getServerClient();
    // ignoreDuplicates:false => existing leads are UPDATED (enriched) with the
    // sheet's columns; new leads are inserted. `status` is omitted from the
    // payload so an existing lead's pipeline status is preserved on re-import.
    const { error } = await supa.from("leads").upsert(leads, {
      onConflict: "identity_key",
      ignoreDuplicates: false,
    });
    if (error) return { ok: false, error: error.message };

    // Attach email/phone/handle as proper contact channels (so they show on the
    // lead-360 page and are reachable by outreach). Need the lead ids first —
    // fetch them by identity_key (covers both newly-inserted and existing leads).
    const keys = leads.map((l) => String(l.identity_key));
    const { data: idRows } = await supa.from("leads").select("id,identity_key").in("identity_key", keys);
    const idByKey = new Map((idRows ?? []).map((r: any) => [r.identity_key as string, r.id as number]));

    const channels: { lead_id: number; type: string; handle: string }[] = [];
    for (const l of leads) {
      const id = idByKey.get(String(l.identity_key));
      if (!id) continue;
      const a = (l.attributes ?? {}) as Record<string, unknown>;
      if (a.email) channels.push({ lead_id: id, type: "email", handle: String(a.email) });
      if (a.phone) channels.push({ lead_id: id, type: "whatsapp", handle: String(a.phone) });
      if (a.handle) channels.push({ lead_id: id, type: "linkedin", handle: String(a.handle) });
    }
    if (channels.length) {
      // (type, handle) is globally unique — ignore dupes so re-imports are safe.
      await supa.from("channels").upsert(channels, { onConflict: "type,handle", ignoreDuplicates: true });
    }

    return { ok: true, inserted: leads.length, skipped };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
