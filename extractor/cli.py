"""Командная строка: python -m extractor.cli data/docs/01_invoice_almaty_trade.txt [--live]"""
import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from extractor.llm import make_llm
from extractor.pipeline import extract


def main():
    parser = argparse.ArgumentParser(description="Извлекает данные из текста документа в JSON")
    parser.add_argument("file", help="путь к .txt с текстом документа")
    parser.add_argument("--live", action="store_true", help="вызвать модель заново (нужен GEMINI_API_KEY)")
    args = parser.parse_args()

    load_dotenv()  # читает GEMINI_API_KEY и GEMINI_MODEL из .env
    text = Path(args.file).read_text(encoding="utf-8")
    try:
        result = extract(text, make_llm(live=args.live))
    except RuntimeError as e:  # нет записанного ответа модели — понятное сообщение вместо трассировки
        raise SystemExit(f"Ошибка: {e}")
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
