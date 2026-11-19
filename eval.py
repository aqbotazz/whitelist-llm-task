"""Мини-eval: прогоняет все документы из data/docs и сравнивает с эталонами из data/expected.

Запуск: python eval.py          (записанные ответы модели, ключ не нужен)
        python eval.py --live   (вызвать модель заново, нужен GEMINI_API_KEY)
"""
import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from extractor.checks import SCALAR_FIELDS, normalize
from extractor.llm import make_llm
from extractor.pipeline import extract

DATA = Path(__file__).resolve().parent / "data"
FIELDS = ["doc_type"] + SCALAR_FIELDS
OUTCOMES = ["верно", "галлюцинация", "пропуск", "неверно"]


def same(got, expected):
    """Равны ли значения: числа — с точностью до копейки, строки — без учёта регистра, кавычек и точки в конце."""
    if isinstance(got, (int, float)) and isinstance(expected, (int, float)):
        return abs(got - expected) < 0.01
    if isinstance(got, str) and isinstance(expected, str):
        return normalize(got).rstrip(" .") == normalize(expected).rstrip(" .")
    return got == expected


def classify(got, expected):
    """Главное правило eval: галлюцинации и пропуски считаются раздельно."""
    if got is None and expected is None:
        return "верно"
    if expected is None:
        return "галлюцинация"  # заполнено, а в эталоне null
    if got is None:
        return "пропуск"       # null, а в эталоне есть значение
    return "верно" if same(got, expected) else "неверно"


def compare_items(got_items, expected_items):
    """Позиции сравниваем по порядку: лишняя позиция — галлюцинация, недостающая — пропуск.
    Для пар сравниваем qty и price отдельно (ловушка «qty = 1, когда количества нет»)."""
    results = {"line_items.qty": [], "line_items.price": [], "line_items.name": []}
    for got, expected in zip(got_items, expected_items):
        for key in ("qty", "price"):
            results[f"line_items.{key}"].append(classify(got[key], expected[key]))
        # Название сравниваем мягко: одно содержится в другом («Оказание услуг» ⊂ «Қызмет көрсету / Оказание услуг»)
        g, e = normalize(got["name"]).rstrip(" ."), normalize(expected["name"]).rstrip(" .")
        results["line_items.name"].append("верно" if g in e or e in g else "неверно")
    extra = len(got_items) - len(expected_items)
    for key in results:
        results[key] += ["галлюцинация"] * max(extra, 0) + ["пропуск"] * max(-extra, 0)
    return results


def evaluate(got, expected):
    """Сравнивает один документ. Возвращает {поле: [исходы]}."""
    results = {field: [classify(got[field], expected[field])] for field in FIELDS}
    results.update(compare_items(got["line_items"], expected["line_items"]))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="вызвать модель заново")
    args = parser.parse_args()
    load_dotenv()
    llm = make_llm(live=args.live)

    totals = {}           # поле → {исход → число}
    errors = []           # строки с описанием ошибок для печати
    warn_expected = warn_given = 0
    for doc_path in sorted((DATA / "docs").glob("*.txt")):
        name = doc_path.stem
        expected = json.loads((DATA / "expected" / f"{name}.json").read_text(encoding="utf-8"))
        try:
            got = extract(doc_path.read_text(encoding="utf-8"), llm).model_dump()
        except Exception as e:  # документ не обработан: все ожидаемые значения — пропуски
            errors.append(f"{name}: ОШИБКА ОБРАБОТКИ — {e}")
            got = {field: None for field in FIELDS} | {"doc_type": None, "line_items": [], "warnings": []}

        for field, outcomes in evaluate(got, expected).items():
            counts = totals.setdefault(field, dict.fromkeys(OUTCOMES, 0))
            for outcome in outcomes:
                counts[outcome] += 1
                if outcome != "верно":
                    errors.append(f"{name}: {field} — {outcome}")
        if field_details := [f"{f}: {got[f]!r} вместо {expected[f]!r}" for f in FIELDS
                             if classify(got[f], expected[f]) != "верно"]:
            errors.append(f"{name}:   " + "; ".join(field_details))

        # Предупреждения: если в эталоне они ожидаются — система должна выдать хоть одно
        if expected["warnings"]:
            warn_expected += 1
            warn_given += bool(got["warnings"])
            if not got["warnings"]:
                errors.append(f"{name}: нет ожидаемого предупреждения ({expected['warnings']})")

    print(f"\n{'поле':<18}{'точность':>9}{'верно':>7}{'галлюц.':>9}{'пропуск':>9}{'неверно':>9}")
    print("-" * 61)
    for field, c in totals.items():
        total = sum(c.values())
        print(f"{field:<18}{c['верно'] / total:>9.0%}{c['верно']:>7}{c['галлюцинация']:>9}"
              f"{c['пропуск']:>9}{c['неверно']:>9}")
    print("-" * 61)
    sums = {o: sum(c[o] for c in totals.values()) for o in OUTCOMES}
    print(f"{'ИТОГО':<18}{sums['верно'] / sum(sums.values()):>9.0%}{sums['верно']:>7}"
          f"{sums['галлюцинация']:>9}{sums['пропуск']:>9}{sums['неверно']:>9}")
    print(f"\nГаллюцинаций (заполнено, а в эталоне null): {sums['галлюцинация']}")
    print(f"Пропусков (null, а в эталоне есть значение): {sums['пропуск']}")
    print(f"Неверных значений (оба не null, но разные):  {sums['неверно']}")
    print(f"Предупреждения выданы в {warn_given} из {warn_expected} документов, где они ожидались")
    print(f"Режим: {'live (модель вызвана заново)' if args.live else 'replay (записанные ответы модели)'}")
    if errors:
        print("\nРасхождения:")
        print("\n".join("  " + line for line in errors))


if __name__ == "__main__":
    main()
