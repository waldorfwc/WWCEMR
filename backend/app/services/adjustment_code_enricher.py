"""Generate plain-English explanation + fix guidance for a CARC/RARC
code via the Anthropic API.

Used by the seed script (bulk enrich at setup) and by the
`/adjustment-codes/{type}/{code}/regenerate` endpoint (single re-run).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import anthropic

from app.config import settings


ENRICHMENT_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = (
    "You are a senior medical-billing specialist at an OB/GYN practice in "
    "Maryland. You explain X12 remittance codes (CARC and RARC) to "
    "billers who know claim terminology but not X12/legal jargon. Your "
    "explanations are concrete and actionable — no fluff, no restating "
    "the official verbiage back, no legal disclaimers."
)


@dataclass
class Enrichment:
    plain_english: str
    how_to_fix: str


_GROUP_CODE_HINTS = {
    "CO": (
        "Contractual Obligation — the provider accepts this reduction per their "
        "payer contract. The patient CANNOT be balance-billed. Fix = appeal or "
        "write-off."
    ),
    "PR": (
        "Patient Responsibility — the patient owes this amount (copay, "
        "coinsurance, deductible). Fix = bill the patient."
    ),
    "OA": (
        "Other Adjustment — usually coordination-of-benefits / secondary-payer "
        "processing. Fix = rebill to the next payer in order."
    ),
    "PI": (
        "Payer-Initiated Reduction — payer audit/recoupment. Rare. "
        "Usually appeal-only based on the payer letter."
    ),
}


def synthesize_combo(
    group_code: str,
    carc: str,
    carc_verbiage: str,
    rarc_items: list[tuple[str, str]],  # (code, verbiage)
) -> Enrichment:
    """Produce a combined plain-English + fix-plan for an EOB codeset
    (group code + one CARC + zero or more RARCs). Used on-demand by the
    Denials page; results are cached by (group, CARC, RARCs) downstream.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    group_hint = _GROUP_CODE_HINTS.get(group_code.upper(), "")
    rarc_block = "\n".join(
        f'  - RARC {c}: "{v}"' for c, v in rarc_items
    ) or "  (no RARC on this adjustment)"

    user_prompt = (
        "A single denial/adjustment on an OB/GYN ERA has the following codeset. "
        "Synthesize the COMBINED meaning — the group code drives disposition, "
        "the CARC is the reason, the RARC(s) are qualifiers.\n\n"
        f"Group code: {group_code}\n"
        f'  ({group_hint})\n'
        f'CARC {carc}: "{carc_verbiage}"\n'
        f"RARCs:\n{rarc_block}\n\n"
        "Respond in EXACTLY this format, no preamble, no Markdown headers:\n\n"
        "PLAIN_ENGLISH:\n"
        "<2-3 sentences. Lead with what the group code means for disposition "
        "(can balance-bill the patient? or write-off/appeal only?). "
        "Then weave the CARC + RARC context into the explanation.>\n\n"
        "HOW_TO_FIX:\n"
        "- <concrete action 1, reflecting group-code disposition>\n"
        "- <concrete action 2>\n"
        "- <concrete action 3>\n"
        "(add 2-5 bullets total)\n"
    )

    resp = client.messages.create(
        model=ENRICHMENT_MODEL,
        max_tokens=700,
        system=[
            {"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )
    return _parse(resp.content[0].text)


def enrich_code(code_type: str, code: str, official_verbiage: str) -> Enrichment:
    """Call Claude to produce plain-English + fix guidance for one code.

    Raises RuntimeError if the response can't be parsed into both fields.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    user_prompt = (
        f"Code type: {code_type}\n"
        f"Code: {code}\n"
        f'Official verbiage: "{official_verbiage}"\n\n'
        "Respond in EXACTLY this format, no preamble, no Markdown headers:\n\n"
        "PLAIN_ENGLISH:\n"
        "<1-2 sentences, plain language, what this code actually means in "
        "practice for an OB/GYN claim>\n\n"
        "HOW_TO_FIX:\n"
        "- <concrete action 1>\n"
        "- <concrete action 2>\n"
        "- <concrete action 3>\n"
        "(add 2-5 bullets total, whatever fits)\n"
    )

    resp = client.messages.create(
        model=ENRICHMENT_MODEL,
        max_tokens=600,
        system=[
            # Mark the system prompt as cacheable — we reuse it across
            # every code during bulk seeding, so the cache hit rate
            # across the batch is ~100%.
            {"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = resp.content[0].text
    return _parse(text)


_SPLIT = re.compile(r"^\s*HOW_TO_FIX\s*:\s*$", re.IGNORECASE | re.MULTILINE)
_STRIP_LABEL = re.compile(r"^\s*PLAIN_ENGLISH\s*:\s*", re.IGNORECASE)


def _parse(text: str) -> Enrichment:
    parts = _SPLIT.split(text, maxsplit=1)
    if len(parts) != 2:
        raise RuntimeError(f"could not parse enrichment response: {text!r}")
    plain_raw, fix_raw = parts
    plain_english = _STRIP_LABEL.sub("", plain_raw).strip()
    how_to_fix = fix_raw.strip()
    if not plain_english or not how_to_fix:
        raise RuntimeError(f"empty enrichment field: {text!r}")
    return Enrichment(plain_english=plain_english, how_to_fix=how_to_fix)
