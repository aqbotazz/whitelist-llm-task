"""Пайплайн целиком с фейковой моделью: выдумки, битый JSON, повторы, противоречия."""
import json
from pathlib import Path

from extractor.pipeline import extract

DOCS = Path(__file__).resolve().parent.parent / "data" / "docs"


def doc(name):
    return (DOCS / f"{name}.txt").read_text(encoding="utf-8")


def fake_llm(*answers):
    """Фейковая модель: отдаёт ответы по очереди (последний повторяет) и запоминает промпты."""
    def llm(prompt):
        llm.prompts.append(prompt)
        answer = answers[min(len(llm.prompts), len(answers)) - 1]
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
    llm.prompts = []
    return llm


def answer(doc_type="invoice", items=(), **fields):
    """Ответ модели в формате промпта: поле = (значение, цитата)."""
    data = {"doc_type": {"value": doc_type, "quote": fields.pop("type_quote", "Счёт на оплату")}}
    data.update({k: {"value": v, "quote": q} for k, (v, q) in fields.items()})
    data["line_items"] = [{"name": n, "qty": qty, "price": p, "quote": q} for n, qty, p, q in items]
    return data


OFFER = "02_offer_digital_solutions"


def test_hallucinated_bin_not_in_text_is_nulled():
    # В КП нет БИН. Модель «выдумала» его и приложила цитату, которой в тексте нет.
    llm = fake_llm(answer("commercial_offer", type_quote="Коммерческое предложение",
                          supplier_bin=("123456789012", "БИН 123456789012")))
    result = extract(doc(OFFER), llm)
    assert result.supplier_bin is None
    assert any("supplier_bin" in w and "не найдена" in w for w in result.warnings)


def test_value_not_following_from_real_quote_is_nulled():
    # Цитаты настоящие, но значения из них не следуют: сложила строки КП, подставила сегодняшнюю дату, qty=1
    llm = fake_llm(answer("commercial_offer", type_quote="Коммерческое предложение",
                          total_amount=(510000, "Разработка сайта — 450 000 тг"),
                          doc_date=("2026-11-19", "Составлено 05.11.2026."),
                          items=[("Разработка сайта", 1, 450000, "Разработка сайта — 450 000 тг")]))
    result = extract(doc(OFFER), llm)
    assert result.total_amount is None
    assert result.doc_date is None
    assert result.line_items[0].qty is None and result.line_items[0].price == 450000


def test_vat_computed_from_rate_is_nulled():
    # Документ 10: указана только ставка «НДС 16%». Посчитать НДС самой — выдумка.
    llm = fake_llm(answer(vat_amount=(320, "Цены указаны с учётом НДС 16%.")))
    result = extract(doc("10_invoice_vat_rate_only"), llm)
    assert result.vat_amount is None


def test_buyer_bin_is_rejected():
    llm = fake_llm(answer(supplier_bin=("181140023456", "БИН покупателя: 181140023456")))
    result = extract(doc("05_invoice_two_bins"), llm)
    assert result.supplier_bin is None
    assert any("покупателя" in w for w in result.warnings)


def test_invalid_answer_is_retried_with_error_text():
    # 1-й ответ — не JSON, 2-й — сумма строкой (схема не пропустит), 3-й — правильный
    good = answer(total_amount=(186180, "Всего к оплате: 186 180 тенге"))
    bad = answer(total_amount=("186 180 тг", "Всего к оплате: 186 180 тенге"))
    llm = fake_llm("Извините, вот данные: ...", bad, good)
    result = extract(doc("01_invoice_almaty_trade"), llm)
    assert len(llm.prompts) == 3
    assert "не прошёл проверку" in llm.prompts[1]   # модели показали ошибку
    assert "total_amount" in llm.prompts[2]         # текст ошибки pydantic с именем поля
    assert result.total_amount == 186180
    assert any("с попытки 3" in w for w in result.warnings)


def test_retries_exhausted_only_bad_field_is_nulled():
    # Модель упорно отдаёт БИН из 11 цифр: после 2 повторов обнуляется только он
    bad = answer(supplier_bin=("17034002155", "БИН 170340021554"),
                 doc_number=("1245", "Счёт на оплату № 1245"))
    llm = fake_llm(bad)
    result = extract(doc("01_invoice_almaty_trade"), llm)
    assert len(llm.prompts) == 3  # 1 попытка + 2 повтора
    assert result.supplier_bin is None
    assert result.doc_number == "1245"  # остальные данные сохранены
    assert any("supplier_bin" in w and "не прошло валидацию" in w for w in result.warnings)


def test_no_json_at_all_gives_unknown_with_warning():
    result = extract(doc("01_invoice_almaty_trade"), fake_llm("я не знаю"))
    assert result.doc_type == "unknown" and result.total_amount is None
    assert any("не вернула корректный JSON" in w for w in result.warnings)


def test_two_payable_amounts_picks_one_matching_lines():
    # Документ 7: «к оплате» 348 000 и 384 000. Модель выбрала не ту — код берёт сходящуюся со строками
    llm = fake_llm(answer(
        total_amount=(384000, "Итого к оплате: 384 000 тенге"), vat_amount=(48000, "НДС 16%: 48 000"),
        items=[('Монитор Dell 24"', 2, 100000, 'Монитор Dell 24"              2     100 000    200 000'),
               ("Клавиатура Logitech", 10, 10000, "Клавиатура Logitech          10      10 000    100 000")]))
    result = extract(doc("07_invoice_contradictions"), llm)
    assert result.total_amount == 348000
    assert any("несколько разных сумм" in w for w in result.warnings)


def test_invalid_checksum_bin_is_kept_with_low_confidence():
    llm = fake_llm(answer(supplier_bin=("170340021554", "БИН 170340021554")))
    result = extract(doc("01_invoice_almaty_trade"), llm)
    assert result.supplier_bin == "170340021554"  # он есть в документе — не обнуляем
    assert result.field_confidence["supplier_bin"] == 0.5
    assert any("контрольную сумму" in w for w in result.warnings)


def test_arithmetic_mismatch_gives_warning():
    # Модель взяла «Итого» без НДС: 160 500 + НДС 25 680 ≠ 160 500
    llm = fake_llm(answer(
        total_amount=(160500, "Итого: 160 500"), vat_amount=(25680, "НДС 16%: 25 680"),
        items=[("Бумага А4 80г/м2", 50, 2100, "Бумага А4 80г/м2            50      2 100     105 000"),
               ("Картридж HP 85A", 3, 18500, "Картридж HP 85A              3     18 500      55 500")]))
    result = extract(doc("01_invoice_almaty_trade"), llm)
    assert any("арифметика" in w for w in result.warnings)
    assert result.field_confidence["total_amount"] == 0.6


def test_ocr_bin_is_restored_with_warning():
    llm = fake_llm(answer(supplier_bin=("210640900180", "БСН/БИН: 2l064090Ol80")))
    result = extract(doc("03_invoice_kokzhiek_ocr"), llm)
    assert result.supplier_bin == "210640900180"
    assert result.field_confidence["supplier_bin"] == 0.7
    assert any("OCR" in w for w in result.warnings)
