"""Вызов модели Gemini + запись ответов в data/llm_cache/.

Режим replay (по умолчанию): берём записанный ответ, ключ не нужен.
Режим live: вызываем настоящую модель и перезаписываем ответ.
Ответ ищется по хешу промпта: изменился промпт — нужен новый live-прогон.
"""
import hashlib
import json
import os
import time
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "llm_cache"
DEFAULT_MODEL = "gemini-3.5-flash-lite"


def call_gemini(prompt):
    from google import genai  # импорт здесь: в режиме replay библиотека не нужна
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip(),
                          http_options=types.HttpOptions(timeout=90_000))  # не ждать вечно (мс)
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
            )
            return response.text or ""
        except Exception as e:
            # 429 — лимит бесплатного тарифа, 503 — модель перегружена: ждём и пробуем ещё раз
            if ("429" in str(e) or "503" in str(e)) and attempt < 3:
                time.sleep(30)
            else:
                raise


def make_llm(live=False):
    """Возвращает функцию «промпт → ответ модели»."""
    def llm(prompt):
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        path = CACHE_DIR / f"{key}.json"
        if not live:
            if not path.exists():
                raise RuntimeError(f"Нет записанного ответа модели ({path.name}). "
                                   "Запустите с --live и GEMINI_API_KEY в .env.")
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        response = call_gemini(prompt)
        record = {"model": os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
                  "prompt": prompt, "response": response}
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return response
    return llm
