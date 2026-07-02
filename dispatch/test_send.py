"""Manual test-send CLI — verify WhatsApp / email is configured correctly.

Sends ONE real message through the configured provider so you can confirm your
AiSensy / Resend setup works before building anything on top. Reads config from
the environment (.env is loaded automatically).

    # email (uses Resend if RESEND_API_KEY set, else SMTP)
    python -m dispatch.test_send email you@example.com "Hello from Exly" "This is a test."

    # whatsapp (uses your approved AiSensy template; body = template param 1)
    python -m dispatch.test_send whatsapp +919876543210 "Hi, this is a test from Exly."

Prints the provider result dict — ``status: 'sent'`` means it worked.
"""
from __future__ import annotations

import sys
import uuid
from typing import List, Optional

# Load .env so the provider keys are available (best-effort).
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Importing the adapters registers them for their channel types.
import dispatch.email.adapter    # noqa: F401  registers 'email'
import dispatch.whatsapp.adapter  # noqa: F401  registers 'whatsapp'
from dispatch import registry


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[0] not in ("email", "whatsapp"):
        print("usage: python -m dispatch.test_send <email|whatsapp> <to> <body> [subject]",
              file=sys.stderr)
        return 2

    channel, to, body = args[0], args[1], args[2]
    subject = args[3] if len(args) > 3 else "Test from Exly Outbound"

    adapter = registry.get_adapter(channel)
    result = adapter.send(
        to=to, subject=subject, body=body,
        idempotency_key="test-" + uuid.uuid4().hex[:8],
    )
    print(result)
    return 0 if result.get("status") == "sent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
