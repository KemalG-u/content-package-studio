"""Shared explicit-human-approval fixtures for offline Studio contract tests."""
from __future__ import annotations

from typing import Any


BASELINE_MAIN = "Bu özgün not, kaynağın sınırlarını Türkçe ve açık biçimde ayırır."
BASELINE_REPLY = "Okur, kaynağı kendi koşulunda doğrulamalıdır."


def copy_text(title: str, main: str, reply: str) -> str:
    """Match the deterministic claim-map binding for title, main, and reply."""
    return " ".join(part for part in (title, main, reply) if part.strip())


def original_claim_map(title: str, main: str, reply: str) -> dict[str, Any]:
    """A real original-contribution claim map bound to the supplied copy."""
    return {
        "claims": [{
            "id": "original-copy",
            "text": copy_text(title, main, reply),
            "kind": "original",
            "evidence_refs": ["original-note"],
        }],
        "evidence": [{"id": "original-note", "kind": "original"}],
    }


def original_editorial_payload(
    angle: dict[str, Any],
    *,
    title: str = "",
    main: str = BASELINE_MAIN,
    reply: str = BASELINE_REPLY,
) -> dict[str, Any]:
    """First-save payload: complete brief, offered angle, copy, and claim map."""
    return {
        "title": title,
        "main": main,
        "reply": reply,
        "reader_brief": {
            "reader_problem": "Açıkla",
            "benefit": "Okur, kaynağı kendi bağlamında sınayabilir.",
            "depth": "intermediate",
            "content_type": angle["content_type"],
            "provenance": "original_contribution",
            "original_contribution": "Kaynağın sınırlarını Türkçe ve uygulanabilir bir notla ayırır.",
            "selected_angle": angle,
        },
        "claim_map": original_claim_map(title, main, reply),
    }


def approval_payload(draft: dict[str, Any]) -> dict[str, Any]:
    """Use the currently rendered fingerprint; never synthesize an approval."""
    return {
        "revision": draft["revision"],
        "fingerprint": draft["approval_fingerprint"],
        "confirmed": True,
    }
