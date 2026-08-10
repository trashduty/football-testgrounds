from __future__ import annotations

import json
import re
from typing import Any

import requests


OPENAI_RESPONSES_URL = (
    "https://api.openai.com/v1/responses"
)


class BTBReplyGenerationError(
    RuntimeError
):
    pass


def _json_safe(
    value: Any,
) -> str:

    return json.dumps(
        value,
        indent=2,
        ensure_ascii=False,
        default=str,
    )


def _extract_output_text(
    payload: dict,
) -> str:

    # Some API responses expose convenience output_text.
    output_text = (
        payload.get(
            "output_text"
        )
    )

    if output_text:
        return str(
            output_text
        )

    pieces = []

    for item in payload.get(
        "output",
        [],
    ):

        for content in item.get(
            "content",
            [],
        ):

            if (
                content.get(
                    "type"
                )
                == "output_text"
            ):

                text = (
                    content.get(
                        "text"
                    )
                )

                if text:
                    pieces.append(
                        str(text)
                    )

    return "\n".join(
        pieces
    ).strip()


def _extract_json_object(
    text: str,
) -> dict:

    text = text.strip()

    # Remove markdown fences if model added them.
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*```$",
        "",
        text,
    )

    try:

        return json.loads(
            text
        )

    except json.JSONDecodeError:

        start = text.find(
            "{"
        )

        end = text.rfind(
            "}"
        )

        if (
            start >= 0
            and end > start
        ):

            return json.loads(
                text[
                    start:
                    end + 1
                ]
            )

        raise


def generate_btb_reply(
    api_key: str,
    model: str,
    post: dict,
    btb_context: dict,
    timeout_seconds: int = 45,
    preferred_max_characters: int = 500,
    alternative_max_characters: int = 500,
) -> dict:

    if not api_key:

        raise ValueError(
            "OPENAI_API_KEY is missing."
        )

    instructions = f"""
You are the internal social-media response writer for BTB Analytics.

Your job is to decide whether BTB has a genuinely useful and differentiated
response to a public X post.

BTB is a football analytics business. The goal is NOT to advertise aggressively.
The goal is to join relevant football conversations with proprietary analysis
that makes people curious about BTB.

STRICT RULES:

1. Use ONLY BTB numbers and factual claims supplied in BTB_CONTEXT.
2. Never invent a projection, probability, edge, ranking, market number,
   player fact, injury fact, or statistic.
3. You may use the original X post as context, but do not treat unsupported
   claims in it as BTB facts.
4. If the supplied BTB context does not provide a worthwhile angle,
   set useful_reply=false.
5. Responses should sound like a knowledgeable football analyst, not an ad.
6. Do not write generic filler such as "Great point" or "Couldn't agree more."
7. Do not force a link or subscription promotion.
8. Prefer disagreement, nuance, or an interesting quantitative comparison.
9. Keep the preferred response under {preferred_max_characters} characters.
10. Keep the alternative under {alternative_max_characters} characters.
11. Usually use 1-3 sentences.
12. Do not call anything a lock.
13. Do not claim BTB has data that is not actually present.
14. If a market line or model number appears, clearly distinguish which is BTB
    and which is the market.
15. Avoid sounding robotic or overly formal.
16. A strong response can agree with the hype while explaining why the market
    price/projection may still be too high or too low.
17. Return JSON only.

Return exactly this structure:

{{
  "useful_reply": true,
  "preferred_reply": "...",
  "alternative_reply": "...",
  "angle": "...",
  "facts_used": ["..."],
  "reason_if_skipped": ""
}}
""".strip()

    user_input = f"""
ORIGINAL_X_POST:

Author: @{post.get("username", "")}
Text:
{post.get("text", "")}

Conversation type:
{post.get("conversation_type", "")}

Detected team:
{btb_context.get("team", "")}

BTB_CONTEXT:

{_json_safe(btb_context)}
""".strip()

    payload = {

        "model":
            model,

        "instructions":
            instructions,

        "input":
            user_input,

        "store":
            False,
    }

    response = requests.post(

        OPENAI_RESPONSES_URL,

        headers={
            "Authorization":
                f"Bearer {api_key}",

            "Content-Type":
                "application/json",
        },

        json=payload,

        timeout=timeout_seconds,
    )

    if response.status_code != 200:

        raise BTBReplyGenerationError(
            "\n".join(
                [
                    "OpenAI response generation failed.",
                    (
                        "HTTP status: "
                        f"{response.status_code}"
                    ),
                    (
                        "Response: "
                        f"{response.text[:2000]}"
                    ),
                ]
            )
        )

    payload = response.json()

    output_text = (
        _extract_output_text(
            payload
        )
    )

    if not output_text:

        raise BTBReplyGenerationError(
            "OpenAI returned no output text."
        )

    try:

        result = (
            _extract_json_object(
                output_text
            )
        )

    except Exception as exc:

        raise BTBReplyGenerationError(
            "Could not parse reply JSON. "
            f"Raw output: {output_text[:2000]}"
        ) from exc

    result.setdefault(
        "useful_reply",
        False,
    )

    result.setdefault(
        "preferred_reply",
        "",
    )

    result.setdefault(
        "alternative_reply",
        "",
    )

    result.setdefault(
        "angle",
        "",
    )

    result.setdefault(
        "facts_used",
        [],
    )

    result.setdefault(
        "reason_if_skipped",
        "",
    )

    return result
