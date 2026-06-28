"""The AI Targeting brain: persona/keyword intent -> an approved target_spec.

Two seams, mirroring personalization/generate.py:

  1. :class:`Brain` — the abstract model. Two implementations:
       * :class:`AnthropicBrain` — the real Sonnet-class model. ``anthropic`` is
         imported lazily *inside* the call, ``ANTHROPIC_API_KEY`` is read at call
         time, model id is the Sonnet-class ``claude-sonnet-4-6``. Importing this
         module never needs the SDK or a key.
       * :class:`FakeBrain` — deterministic, offline. Expands keywords by a fixed
         rule and emits a fixed deep breakdown, so DB tests and dry runs work
         with no network and no key.

  2. The flows:
       * Mode B :func:`run_mode_b` — expand -> VALIDATION GATE -> write an
         approved ``mode='keyword'`` spec.
       * Mode A :func:`run_mode_a` — clarifying questions -> structured filters
         + keywords -> write an UNapproved ``mode='deep'`` spec.
       * :func:`approve` — flip ``approved=TRUE`` (the human sign-off).

The validation gate (:func:`expand_and_validate_keywords`) is pure and the
single guarantee that an over-broad or duplicate keyword never reaches sourcing.

Python 3.9 compatible: typing.Optional/List/Dict only, no PEP-604 unions, no
match statements. psycopg + anthropic are both lazy/optional.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("targeting.brain")

# Sonnet-class model id for the targeting brain (PRD §13: the "thinking" tier).
SONNET_MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Validation gate tuning (the contract that protects sourcing from junk specs).
# ---------------------------------------------------------------------------

#: Hard cap on expanded keywords per spec. Past this we have a fan-out problem,
#: not a target. Keeps each spec's search budget bounded.
MAX_EXPANDED_KEYWORDS = 25

#: Minimum meaningful length for a keyword (single-char terms are noise).
MIN_KEYWORD_LEN = 3

#: Over-broad single tokens that match almost everything — never a useful query
#: on their own. Dropped by the gate (multi-word phrases containing them stay).
OVERBROAD_TERMS = frozenset(
    {
        "india", "online", "best", "top", "free", "course", "coach", "creator",
        "training", "class", "program", "expert", "guru", "tips", "learn",
        "new", "the", "and", "for", "with", "near", "me",
    }
)


# ---------------------------------------------------------------------------
# TargetSpec — the in-memory view of a target_specs row
# ---------------------------------------------------------------------------

class TargetSpec:
    """In-memory mirror of a ``target_specs`` row (the sourcing contract).

    Adapters read ``.seed_keywords``/``.expanded_keywords``/``.filters`` and the
    ``.approved`` gate off this. ``.id`` is set once the row is written.
    """

    __slots__ = (
        "id", "mode", "persona_text", "seed_keywords", "expanded_keywords",
        "filters", "attributes", "approved", "created_by_model",
    )

    def __init__(
        self,
        id: Optional[int] = None,
        mode: str = "keyword",
        persona_text: Optional[str] = None,
        seed_keywords: Optional[List[str]] = None,
        expanded_keywords: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        attributes: Optional[Dict[str, Any]] = None,
        approved: bool = False,
        created_by_model: Optional[str] = None,
    ) -> None:
        self.id = id
        self.mode = mode
        self.persona_text = persona_text
        self.seed_keywords = seed_keywords or []
        self.expanded_keywords = expanded_keywords or []
        self.filters = filters or {}
        self.attributes = attributes or {}
        self.approved = approved
        self.created_by_model = created_by_model

    def keywords(self) -> List[str]:
        """The query set an adapter should source by: expanded if present, else
        seed. (Mode A specs may carry only seed/keyword hints in filters.)"""
        return list(self.expanded_keywords or self.seed_keywords)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "TargetSpec(id={0}, mode={1!r}, approved={2}, kw={3})".format(
            self.id, self.mode, self.approved, len(self.keywords())
        )


# ---------------------------------------------------------------------------
# Brain interface + implementations
# ---------------------------------------------------------------------------

class Brain:
    """Abstract targeting brain.

    ``expand_keywords(seeds) -> list[str]`` proposes a raw expansion (the gate
    validates it afterward). ``deep_breakdown(persona) -> dict`` returns the
    structured audience breakdown for Mode A.
    """

    model: str = SONNET_MODEL

    def expand_keywords(self, seeds: List[str]) -> List[str]:
        raise NotImplementedError

    def deep_breakdown(self, persona_text: str) -> Dict[str, Any]:
        raise NotImplementedError

    def clarifying_questions(self, persona_text: str) -> List[str]:
        """Questions a human answers before a deep breakdown is finalized.

        Default: a small generic set. The real model proposes persona-specific
        ones; the Fake returns a fixed list so tests are deterministic.
        """
        return [
            "Which sub-niches matter most, and which should we exclude?",
            "What follower range is in-scope (nano, micro, mid, macro)?",
            "Any geos beyond India, or India-only?",
            "Which platforms — Instagram, YouTube, both?",
        ]


class AnthropicBrain(Brain):
    """Real Sonnet-class brain. Lazy ``import anthropic``; key read at call time.

    Neither importing this module nor constructing the object needs the SDK or
    ``ANTHROPIC_API_KEY`` — both are only touched when a method actually calls
    the model. Each method asks for STRICT JSON and parses defensively, so a
    malformed model reply degrades to an empty result the gate then rejects,
    never a crash.
    """

    def __init__(
        self,
        model: str = SONNET_MODEL,
        max_tokens: int = 1024,
        api_key: Optional[str] = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._api_key = api_key  # else read from env at call time

    # -- low-level call ----------------------------------------------------
    def _complete(self, system: str, prompt: str) -> str:
        import anthropic  # lazy: only needed for a real call

        api_key = self._api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set; cannot call the Anthropic API. "
                "Set it in the environment or pass api_key=..."
            )
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_text(resp)

    # -- Mode B ------------------------------------------------------------
    def expand_keywords(self, seeds: List[str]) -> List[str]:
        system = (
            "You expand seed search keywords for sourcing Indian course/coaching "
            "creators and affiliates on ad libraries and YouTube. Return ONLY a "
            "JSON array of specific, multi-word search queries. Prefer concrete "
            "sub-niches over broad single words. No commentary."
        )
        prompt = "Seed keywords: {0}\nReturn the expanded JSON array.".format(
            json.dumps(list(seeds))
        )
        data = _loads_array(self._complete(system, prompt))
        return [str(x) for x in data if isinstance(x, (str,))]

    # -- Mode A ------------------------------------------------------------
    def deep_breakdown(self, persona_text: str) -> Dict[str, Any]:
        system = (
            "You turn a target-audience persona into a structured breakdown for "
            "outbound sourcing of Indian creators/affiliates. Return ONLY a JSON "
            "object with keys: segments (array of {name, sub_niches[], signals[]}), "
            "follower_bands (array of strings), geo (array of strings), platforms "
            "(array of strings), keywords (array of strings). No commentary."
        )
        prompt = "Persona:\n{0}\n\nReturn the JSON object.".format(persona_text)
        data = _loads_object(self._complete(system, prompt))
        return data


class FakeBrain(Brain):
    """Deterministic, offline brain for tests and dry runs.

    * ``expand_keywords`` derives a fixed expansion per seed and deliberately
      injects a couple of *bad* terms (an over-broad single word and a dup) so
      tests can prove the validation gate drops them.
    * ``deep_breakdown`` returns a fixed, well-formed filters object.
    """

    def __init__(self, inject_bad: bool = True) -> None:
        # When True, expand_keywords emits noise the gate is expected to remove.
        self.inject_bad = inject_bad

    def expand_keywords(self, seeds: List[str]) -> List[str]:
        out: List[str] = []
        for s in seeds:
            s = (s or "").strip()
            if not s:
                continue
            out.append(s)
            out.append("{0} india".format(s))
            out.append("{0} course india".format(s))
        if self.inject_bad and out:
            # Noise the gate MUST drop: an over-broad single token, an exact
            # duplicate of the first expansion, and an empty/too-short term.
            out.append("india")            # over-broad single token
            out.append(out[0])             # exact duplicate
            out.append("x")                # too short
        return out

    def deep_breakdown(self, persona_text: str) -> Dict[str, Any]:
        return {
            "segments": [
                {
                    "name": "money mindset coaches",
                    "sub_niches": ["wealth coaching", "abundance mindset"],
                    "signals": ["paid course", "1:1 program", "webinar funnel"],
                },
                {
                    "name": "trauma healing coaches",
                    "sub_niches": ["inner child healing", "somatic healing"],
                    "signals": ["certification", "cohort"],
                },
            ],
            "follower_bands": ["1k-10k", "10k-100k"],
            "geo": ["IN"],
            "platforms": ["instagram", "youtube"],
            "keywords": ["money mindset coach", "trauma healing coach"],
        }


# ---------------------------------------------------------------------------
# The validation gate (pure) — the single guarantee against junk specs
# ---------------------------------------------------------------------------

class ValidationResult:
    """Outcome of the keyword validation gate.

    ``kept`` is the validated query set to persist; ``dropped`` maps each
    rejected term to a short reason (for logging/inspection).
    """

    __slots__ = ("kept", "dropped")

    def __init__(self, kept: List[str], dropped: Dict[str, str]) -> None:
        self.kept = kept
        self.dropped = dropped

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "ValidationResult(kept={0}, dropped={1})".format(
            len(self.kept), len(self.dropped)
        )


def _is_overbroad(term: str) -> bool:
    """A query is over-broad iff it is a SINGLE token that is a known broad word.

    Multi-word phrases ("money mindset coach") are specific enough even if they
    contain a broad word; a lone "coach"/"india"/"online" is not.
    """
    tokens = term.split()
    return len(tokens) == 1 and tokens[0] in OVERBROAD_TERMS


def expand_and_validate_keywords(
    seeds: List[str],
    expanded: List[str],
    existing: Optional[List[str]] = None,
    max_count: int = MAX_EXPANDED_KEYWORDS,
) -> ValidationResult:
    """Validate a raw expansion into a clean query set BEFORE it is persisted.

    Rules, in order (first failing rule wins for the drop reason):
      1. normalize: lowercase + collapse whitespace; empties dropped.
      2. too-short: shorter than ``MIN_KEYWORD_LEN`` -> dropped.
      3. over-broad: a single broad token (see :data:`OVERBROAD_TERMS`) -> dropped.
      4. duplicate: a term already kept (after normalize) -> dropped.
      5. already-known: a term present in ``existing`` (specs already in the DB)
         -> dropped, so we never re-source the same query set.
      6. cap: once ``max_count`` are kept, the rest are dropped as 'over_cap'.

    Seeds are folded in first (they are intent, presumed good) and still pass
    through the same rules. Returns a :class:`ValidationResult`.
    """
    existing_norm = {_norm_kw(e) for e in (existing or []) if _norm_kw(e)}
    kept: List[str] = []
    kept_set: "set[str]" = set()
    dropped: Dict[str, str] = {}

    for raw in list(seeds or []) + list(expanded or []):
        term = _norm_kw(raw)
        if not term:
            if raw not in dropped:
                dropped[str(raw)] = "empty"
            continue
        if len(term) < MIN_KEYWORD_LEN:
            dropped[term] = "too_short"
            continue
        if _is_overbroad(term):
            dropped[term] = "over_broad"
            continue
        if term in kept_set:
            dropped[term] = "duplicate"
            continue
        if term in existing_norm:
            dropped[term] = "already_in_existing_spec"
            continue
        if len(kept) >= max_count:
            dropped[term] = "over_cap"
            continue
        kept.append(term)
        kept_set.add(term)

    return ValidationResult(kept=kept, dropped=dropped)


def _norm_kw(value: Any) -> str:
    """Lowercase + collapse internal whitespace; '' for unusable input."""
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _existing_keywords(cur) -> List[str]:
    """All keywords already committed across target_specs (seed + expanded).

    Used by the gate to dedupe a new expansion against what we have already
    sourced, so two runs of similar seeds don't re-source the same queries.
    """
    cur.execute("SELECT seed_keywords, expanded_keywords FROM target_specs")
    out: List[str] = []
    for seed, expanded in cur.fetchall():
        for arr in (seed or [], expanded or []):
            for kw in arr:
                if kw:
                    out.append(kw)
    return out


def _insert_spec(cur, spec: TargetSpec) -> int:
    cur.execute(
        """
        INSERT INTO target_specs
            (mode, persona_text, seed_keywords, expanded_keywords, filters,
             attributes, approved, created_by_model)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            spec.mode,
            spec.persona_text,
            list(spec.seed_keywords),
            list(spec.expanded_keywords),
            json.dumps(spec.filters or {}),
            json.dumps(spec.attributes or {}),
            spec.approved,
            spec.created_by_model,
        ),
    )
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Mode B (auto) — expand -> gate -> write APPROVED keyword spec
# ---------------------------------------------------------------------------

def run_mode_b(
    conn,
    seeds: List[str],
    brain: Optional[Brain] = None,
    max_count: int = MAX_EXPANDED_KEYWORDS,
) -> TargetSpec:
    """Mode B: seed keywords -> validated expansion -> approved keyword spec.

    Flow:
      1. ``brain.expand_keywords(seeds)`` proposes a raw expansion.
      2. The VALIDATION GATE runs BEFORE any write: caps the count, drops
         over-broad/dup/too-short terms, and dedupes against keywords already in
         ``target_specs``.
      3. A single ``mode='keyword'`` row is written with the validated
         ``expanded_keywords`` and ``approved=TRUE`` (auto mode needs no human).

    Returns the written :class:`TargetSpec` (``.id`` set). Raises ``ValueError``
    if the gate left nothing to source (an empty spec is never written).
    """
    if brain is None:
        brain = AnthropicBrain()

    raw_expanded = brain.expand_keywords(list(seeds))

    with conn.cursor() as cur:
        existing = _existing_keywords(cur)
        result = expand_and_validate_keywords(
            seeds=seeds, expanded=raw_expanded, existing=existing, max_count=max_count
        )
        if not result.kept:
            raise ValueError(
                "validation gate dropped every keyword; nothing to source "
                "(dropped: {0})".format(result.dropped)
            )
        spec = TargetSpec(
            mode="keyword",
            seed_keywords=[_norm_kw(s) for s in seeds if _norm_kw(s)],
            expanded_keywords=result.kept,
            filters={"geo": ["IN"]},
            attributes={"dropped_in_validation": result.dropped},
            approved=True,  # Mode B is auto-approved
            created_by_model=getattr(brain, "model", SONNET_MODEL),
        )
        spec.id = _insert_spec(cur, spec)
    conn.commit()
    logger.info(
        "Mode B wrote approved keyword spec id=%s (%d kept, %d dropped)",
        spec.id, len(result.kept), len(result.dropped),
    )
    return spec


# ---------------------------------------------------------------------------
# Mode A (deep) — persona -> structured filters -> write UNAPPROVED deep spec
# ---------------------------------------------------------------------------

def run_mode_a(
    conn,
    persona_text: str,
    brain: Optional[Brain] = None,
) -> TargetSpec:
    """Mode A: persona -> clarifying questions -> structured filters -> deep spec.

    Flow:
      1. ``brain.clarifying_questions(persona)`` (stashed in attributes for the
         human reviewing the spec).
      2. ``brain.deep_breakdown(persona)`` -> a structured audience breakdown.
         We coerce it into the ``filters`` shape:
         ``{segments:[{name,sub_niches,signals}], follower_bands, geo, platforms}``.
      3. Any keywords the breakdown proposes seed the spec's ``seed_keywords``.
      4. A single ``mode='deep'`` row is written with ``approved=FALSE`` — deep
         targeting requires :func:`approve` (human sign-off) before sourcing.

    Returns the written :class:`TargetSpec` (``.id`` set, ``.approved`` False).
    """
    if brain is None:
        brain = AnthropicBrain()

    questions = brain.clarifying_questions(persona_text)
    breakdown = brain.deep_breakdown(persona_text) or {}

    filters = _coerce_filters(breakdown)
    keywords = [
        _norm_kw(k) for k in (breakdown.get("keywords") or []) if _norm_kw(k)
    ]

    with conn.cursor() as cur:
        spec = TargetSpec(
            mode="deep",
            persona_text=persona_text,
            seed_keywords=keywords,
            expanded_keywords=[],
            filters=filters,
            attributes={"clarifying_questions": questions},
            approved=False,  # deep mode needs human sign-off
            created_by_model=getattr(brain, "model", SONNET_MODEL),
        )
        spec.id = _insert_spec(cur, spec)
    conn.commit()
    logger.info(
        "Mode A wrote UNAPPROVED deep spec id=%s (%d segments)",
        spec.id, len(filters.get("segments", [])),
    )
    return spec


def _coerce_filters(breakdown: Dict[str, Any]) -> Dict[str, Any]:
    """Force a model breakdown into the agreed ``filters`` JSONB shape.

    Always returns all four keys with the right types, so downstream readers can
    rely on the shape even if the model omitted a field:
    ``{segments:[{name,sub_niches,signals}], follower_bands, geo, platforms}``.
    """
    segments_out: List[Dict[str, Any]] = []
    for seg in breakdown.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        segments_out.append(
            {
                "name": str(seg.get("name") or "").strip(),
                "sub_niches": [str(x) for x in (seg.get("sub_niches") or [])],
                "signals": [str(x) for x in (seg.get("signals") or [])],
            }
        )
    return {
        "segments": segments_out,
        "follower_bands": [str(x) for x in (breakdown.get("follower_bands") or [])],
        "geo": [str(x) for x in (breakdown.get("geo") or ["IN"])] or ["IN"],
        "platforms": [str(x) for x in (breakdown.get("platforms") or [])],
    }


# ---------------------------------------------------------------------------
# approve — the human sign-off
# ---------------------------------------------------------------------------

def approve(conn, spec_id: int) -> bool:
    """Flip ``target_specs.approved = TRUE`` for ``spec_id``.

    Returns True if a row was updated (the spec existed), False otherwise. This
    is the one mechanism that lets a deep (Mode A) spec become sourceable.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE target_specs SET approved = TRUE WHERE id = %s RETURNING id",
            (spec_id,),
        )
        row = cur.fetchone()
    conn.commit()
    updated = row is not None
    logger.info("approve(spec_id=%s) -> %s", spec_id, updated)
    return updated


def load_spec(conn, spec_id: int) -> Optional[TargetSpec]:
    """Read a ``target_specs`` row back into a :class:`TargetSpec` (or None)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, mode, persona_text, seed_keywords, expanded_keywords, "
            "filters, attributes, approved, created_by_model "
            "FROM target_specs WHERE id = %s",
            (spec_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    filters = row[5] if isinstance(row[5], dict) else json.loads(row[5] or "{}")
    attributes = row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}")
    return TargetSpec(
        id=row[0],
        mode=row[1],
        persona_text=row[2],
        seed_keywords=list(row[3] or []),
        expanded_keywords=list(row[4] or []),
        filters=filters,
        attributes=attributes,
        approved=bool(row[7]),
        created_by_model=row[8],
    )


# ---------------------------------------------------------------------------
# Model-response parsing helpers (defensive)
# ---------------------------------------------------------------------------

def _extract_text(resp: Any) -> str:
    parts = []
    for block in getattr(resp, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _strip_code_fence(text: str) -> str:
    """Drop a leading ```json / ``` fence if the model wrapped its JSON."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _loads_array(text: str) -> List[Any]:
    try:
        data = json.loads(_strip_code_fence(text))
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _loads_object(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(_strip_code_fence(text))
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}
