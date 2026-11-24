"""Isolated, tool-free Hermes model subprocess used by Studio generation jobs."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import StoreError

# Checkout of the Hermes agent used for generation. Set HERMES_AGENT_ROOT; without it generation is unavailable.
HERMES_ROOT = Path(os.environ.get("HERMES_AGENT_ROOT", "hermes-agent-not-configured"))
PROVIDER = "openai-codex"
MODEL = "gpt-5.6-terra"
MAX_PROMPT_CHARS = 50_000


def extract_model_result(response: dict, tools_count: int) -> dict:
    if tools_count != 0 or not isinstance(response, dict) or response.get('error'):
        raise ValueError('Model yanıtı veya araç izolasyonu geçersiz.')
    text = response.get('final_response')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Model nihai yanıt vermedi; kaynak metni yanıt sayılmadı.')
    messages = response.get('messages') or []
    if any(m.get('tool_calls') for m in messages if isinstance(m, dict)):
        raise ValueError('Üretim işçisi araç çağırdı.')
    response_id = response.get('response_id') or response.get('request_id')
    if not response_id:
        response_id = next((m.get('id') for m in reversed(messages) if isinstance(m, dict) and m.get('role') == 'assistant' and m.get('id')), None)
    return {'text': text, 'response_id': response_id, 'tools_count': 0, 'output_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}

_CHILD = r'''
import hashlib, json, os, sys
sys.path.insert(0, os.environ["HERMES_AGENT_ROOT"])
from run_agent import AIAgent
payload = json.loads(sys.stdin.read())
agent = AIAgent(provider="openai-codex", model="gpt-5.6-terra", enabled_toolsets=[],
    skip_memory=True, skip_context_files=True, skip_background_review=True,
    quiet_mode=True, max_iterations=1, run_budget_seconds=90,
    reasoning_config={"effort": "low"}, fallback_model=None)
tools = getattr(agent, "tools", None)
if tools is None:
    tools = getattr(agent, "available_tools", [])
if len(tools or []) != 0:
    raise RuntimeError("Tool isolation assertion failed")
response = agent.run_conversation(payload["prompt"])
if getattr(agent, '_fallback_chain', []):
    raise RuntimeError('Unexpected fallback provider')
sys.path.insert(0, payload['studio_root'])
from studio.model_worker import extract_model_result
print(json.dumps(extract_model_result(response, len(tools or [])), ensure_ascii=False))
'''


def _redact(text: str) -> str:
    """Keep diagnostics useful without passing provider output or environment-like values to clients."""
    lines = []
    for line in text.splitlines()[-12:]:
        lower = line.lower()
        if any(token in lower for token in ("api_key", "authorization", "bearer ", "token=", "password")):
            lines.append("[gizli hata satırı çıkarıldı]")
        else:
            lines.append(line[:500])
    return "\n".join(lines)


def run_real_model(prompt: str, log_path: Path, timeout_seconds: int = 105) -> dict[str, Any]:
    """Run the canonical Hermes agent in a subprocess and return only its real response."""
    if not HERMES_ROOT.joinpath("run_agent.py").is_file():
        raise StoreError("Hermes üretim işçisi bulunamadı; HERMES_AGENT_ROOT ortam değişkenini Hermes agent klasörüne ayarlayın.")
    if not isinstance(prompt, str) or len(prompt) > MAX_PROMPT_CHARS:
        raise StoreError("Üretim istemi sınırı aşıyor.")
    environment = dict(os.environ)
    environment.update({"HERMES_AGENT_ROOT": str(HERMES_ROOT), "HERMES_SAFE_MODE": "1", "HERMES_IGNORE_USER_CONFIG": "1", "HERMES_IGNORE_RULES": "1"})
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _CHILD], input=json.dumps({"prompt": prompt, "studio_root": str(Path(__file__).resolve().parent.parent)}, ensure_ascii=False),
            text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout_seconds, env=environment, cwd=str(HERMES_ROOT), shell=False, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise StoreError("Model zaman aşımı: taslak korundu. Durumu inceleyip açık düğmeyle yeniden deneyin; otomatik çağrı yapılmadı.") from exc
    if completed.returncode != 0:
        raise StoreError(f"Model işçisi çalışamadı (çıkış {completed.returncode}). Taslak korundu. Bağlantı ve işçi sağlığını kontrol edip açık düğmeyle yeniden deneyin.")
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise StoreError("Hermes üretim işçisi doğrulanabilir yanıt vermedi.") from exc
    if result.get("tools_count") != 0 or not isinstance(result.get("text"), str):
        raise StoreError("Hermes üretim yanıtı izolasyon denetimini geçemedi.")
    record = {"at": datetime.now(timezone.utc).isoformat(), "provider": PROVIDER, "model": MODEL,
              "response_id": result.get("response_id"), "tools_count": 0, "output_sha256": result["output_sha256"]}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return result
