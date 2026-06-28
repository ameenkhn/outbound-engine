"""Value-prop library — the segment x angle content the generator draws from.

This is the **M4 value-prop library** (PRD §11 L3, §6 ICP):

  * **Creators** — course/coaching/digital-product creators in India who could
    host on Exly. Angles: cost saving vs competitors, all-in-one, payouts,
    ease of launch.
  * **Affiliates** — marketers who promote Exly creators' offers for commission.
    Angles: affiliate fee/earnings, ready catalog, tracking, payouts.

Each entry pairs three things the generator must weave in:

  * ``usp``         — the Exly unique selling point for this segment+angle.
  * ``differentiator`` — how Exly is different from the alternative the lead
    is using today (competitive differentiation).
  * ``wiifm``       — "what's in it for them": the lead-facing benefit line.

It is **pure data** plus two tiny helpers (:func:`get_value_prop`,
:func:`pick_angle`). No model, no I/O, no pricing numbers baked in — the
guardrail (decision P4) forbids invented pricing, so the copy stays
benefit-led, never quoting a figure we'd have to defend.

Angle keys are shared with ``messages.angle`` (0001): one of
``cost_saving | affiliate_fee | competitive_switch | ease``. Not every angle
is meaningful for every segment, but the library carries content for **all**
``segment x angle`` pairs so generation never falls through a hole — see the
tests in ``tests/test_value_props.py``.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# Canonical segment keys (mirror segment_t in 0001).
SEGMENTS: List[str] = ["creator", "affiliate"]

# Canonical angle keys (mirror the documented messages.angle values in 0001).
ANGLES: List[str] = ["cost_saving", "affiliate_fee", "competitive_switch", "ease"]


# ---------------------------------------------------------------------------
# The library: VALUE_PROPS[segment][angle] -> {usp, differentiator, wiifm}
# ---------------------------------------------------------------------------
VALUE_PROPS: Dict[str, Dict[str, Dict[str, str]]] = {
    "creator": {
        "cost_saving": {
            "usp": (
                "Exly is an all-in-one creator platform, so you replace a stack "
                "of separate tools (course host, payments, email, booking) with "
                "one subscription."
            ),
            "differentiator": (
                "Versus stitching together a course host, a payment gateway and "
                "a scheduling tool — each with its own fee — Exly bundles them, "
                "so your cost-per-tool drops as you scale."
            ),
            "wiifm": (
                "Keep more of every enrolment instead of leaking margin to a "
                "patchwork of subscriptions."
            ),
        },
        "ease": {
            "usp": (
                "Exly gets a creator from idea to a live, sellable offer fast — "
                "landing page, checkout and delivery come ready-made."
            ),
            "differentiator": (
                "No developer, no plugins, no week of setup the way a "
                "self-hosted site or a generic website builder demands."
            ),
            "wiifm": (
                "Launch your next batch this week, not next month — you focus on "
                "teaching, Exly handles the plumbing."
            ),
        },
        "competitive_switch": {
            "usp": (
                "Exly is built for India-first creators — UPI, local payment "
                "methods and INR payouts are native, not bolted on."
            ),
            "differentiator": (
                "Unlike global platforms that treat India as an afterthought "
                "(clunky payments, FX friction, support in another time zone), "
                "Exly is built around how Indian creators actually sell."
            ),
            "wiifm": (
                "Switch to a platform that fits your audience's buying habits — "
                "fewer dropped checkouts, smoother payouts."
            ),
        },
        "affiliate_fee": {
            # For creators, the "affiliate" angle reframes as: recruit affiliates
            # to sell *your* offer through Exly's built-in affiliate tooling.
            "usp": (
                "Exly has built-in affiliate tooling, so you can recruit "
                "affiliates to promote your course and track their sales without "
                "a third-party app."
            ),
            "differentiator": (
                "Versus bolting on a separate affiliate plugin and reconciling "
                "payouts by hand, Exly tracks referrals and commissions inside "
                "the same dashboard you already use."
            ),
            "wiifm": (
                "Turn your happy students and partners into a sales force — more "
                "reach without more ad spend."
            ),
        },
    },
    "affiliate": {
        "affiliate_fee": {
            "usp": (
                "Exly gives affiliates a transparent commission on every sale "
                "they drive, paid out reliably to Indian bank accounts."
            ),
            "differentiator": (
                "Unlike networks where attribution is murky and payouts are "
                "slow or held offshore, Exly's tracking is clear and payouts "
                "land in INR on schedule."
            ),
            "wiifm": (
                "Earn predictable commission you can actually count on — clear "
                "attribution, dependable payouts."
            ),
        },
        "ease": {
            "usp": (
                "Exly hands affiliates a ready catalog of creator offers plus "
                "tracking links, so you can start promoting in minutes."
            ),
            "differentiator": (
                "No need to chase individual creators for terms or cobble "
                "together your own tracking — the catalog and links are there "
                "the day you join."
            ),
            "wiifm": (
                "Skip the setup grind and get straight to promoting offers your "
                "audience will actually buy."
            ),
        },
        "competitive_switch": {
            "usp": (
                "Exly gives affiliates first-party tracking and a real-time "
                "dashboard for clicks, conversions and pending commission."
            ),
            "differentiator": (
                "Unlike platforms where you fly blind between monthly reports, "
                "Exly shows your performance live so you can double down on "
                "what's converting."
            ),
            "wiifm": (
                "See what's working as it happens and optimise your promotion "
                "instead of guessing."
            ),
        },
        "cost_saving": {
            # For affiliates, "cost saving" reframes as: zero cost to join, keep
            # more of what you earn (no platform fee eating the commission).
            "usp": (
                "It costs nothing for an affiliate to join Exly and start "
                "promoting — there's no platform fee skimming your commission."
            ),
            "differentiator": (
                "Versus networks that charge a seat fee or clip a slice of every "
                "payout, joining Exly's affiliate programme is free, so your "
                "commission is yours."
            ),
            "wiifm": (
                "Start earning with zero upfront cost and keep the full "
                "commission you've earned."
            ),
        },
    },
}

# The default angle to lead with per segment, when the caller / Loop B hasn't
# picked one. Creators respond best to the cost story; affiliates to earnings.
DEFAULT_ANGLE: Dict[str, str] = {
    "creator": "cost_saving",
    "affiliate": "affiliate_fee",
}


def pick_angle(segment: str) -> str:
    """Return the default angle to lead with for ``segment``.

    This is the safe default the generator uses when no angle is supplied
    (e.g. before Loop B has a winner). Raises :class:`ValueError` for an
    unknown segment so a typo fails loudly rather than silently picking wrong.
    """
    try:
        return DEFAULT_ANGLE[segment]
    except KeyError:
        raise ValueError(
            "unknown segment %r; expected one of %s" % (segment, SEGMENTS)
        )


def get_value_prop(segment: str, angle: Optional[str] = None) -> Dict[str, str]:
    """Return the ``{usp, differentiator, wiifm}`` entry for ``segment``/``angle``.

    ``angle`` defaults to :func:`pick_angle` for the segment. Raises
    :class:`ValueError` for an unknown segment or angle.
    """
    if segment not in VALUE_PROPS:
        raise ValueError(
            "unknown segment %r; expected one of %s" % (segment, SEGMENTS)
        )
    if angle is None:
        angle = pick_angle(segment)
    seg = VALUE_PROPS[segment]
    if angle not in seg:
        raise ValueError(
            "unknown angle %r for segment %r; expected one of %s"
            % (angle, segment, sorted(seg))
        )
    return seg[angle]
