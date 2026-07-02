import "server-only";

/**
 * Minimal Google Calendar integration for demo booking (L7).
 *
 * Auth model: a single-owner OAuth **refresh token** (this is an internal ops
 * tool, one calendar). Set these in Vercel env:
 *   GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
 *   GOOGLE_CALENDAR_ID   (optional, default "primary")
 * If any are missing, calendar sync is silently skipped and booking still works
 * internally — the feature is strictly additive.
 */

function cfg() {
  const client_id = process.env.GOOGLE_CLIENT_ID;
  const client_secret = process.env.GOOGLE_CLIENT_SECRET;
  const refresh_token = process.env.GOOGLE_REFRESH_TOKEN;
  const calendarId = process.env.GOOGLE_CALENDAR_ID || "primary";
  if (!client_id || !client_secret || !refresh_token) return null;
  return { client_id, client_secret, refresh_token, calendarId };
}

export function googleConfigured(): boolean {
  return cfg() !== null;
}

async function accessToken(c: NonNullable<ReturnType<typeof cfg>>): Promise<string> {
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: c.client_id,
      client_secret: c.client_secret,
      refresh_token: c.refresh_token,
      grant_type: "refresh_token",
    }),
  });
  if (!res.ok) throw new Error(`Google token ${res.status}: ${(await res.text()).slice(0, 160)}`);
  const j = await res.json();
  if (!j.access_token) throw new Error("Google token: no access_token");
  return j.access_token as string;
}

/**
 * Create a calendar event (with a Google Meet link) for a booked demo.
 * Returns the event's link, or null if Google isn't configured. Best-effort:
 * throws only on a real API error so the caller can surface it.
 */
export async function createCalendarEvent(input: {
  title: string;
  description?: string;
  startISO: string;       // event start; if absent the caller should default
  durationMin?: number;
  attendeeEmail?: string | null;
}): Promise<{ htmlLink: string | null; hangoutLink: string | null } | null> {
  const c = cfg();
  if (!c) return null;

  const token = await accessToken(c);
  const start = new Date(input.startISO);
  const end = new Date(start.getTime() + (input.durationMin || 30) * 60000);
  const tz = "Asia/Kolkata";

  const body: Record<string, unknown> = {
    summary: input.title,
    description: input.description || "",
    start: { dateTime: start.toISOString(), timeZone: tz },
    end: { dateTime: end.toISOString(), timeZone: tz },
    conferenceData: {
      createRequest: { requestId: `exly-${Date.now()}`, conferenceSolutionKey: { type: "hangoutsMeet" } },
    },
  };
  if (input.attendeeEmail) body.attendees = [{ email: input.attendeeEmail }];

  const res = await fetch(
    `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(c.calendarId)}/events?conferenceDataVersion=1&sendUpdates=all`,
    {
      method: "POST",
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw new Error(`Google Calendar ${res.status}: ${(await res.text()).slice(0, 160)}`);
  const j = await res.json();
  return { htmlLink: j.htmlLink ?? null, hangoutLink: j.hangoutLink ?? null };
}
