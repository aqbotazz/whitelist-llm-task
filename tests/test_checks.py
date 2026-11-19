"""Детерминированные проверки: БИН, OCR, сумма прописью, даты, схема."""
import pytest
from pydantic import ValidationError

from extractor.checks import bin_checksum_ok, dates_in, fix_ocr, words_to_number
from extractor.schema import ExtractionResult


def test_bin_checksum():
    assert bin_checksum_ok("210640900180")      # документ 3 после исправления OCR
    assert bin_checksum_ok("190340001233")
    assert not bin_checksum_ok("170340021554")  # документ 1: контрольная цифра должна быть 1, стоит 4


def test_ocr_fix_only_in_numeric_words():
    assert fix_ocr("БСН/БИН: 2l064090Ol80") == "БСН/БИН: 210640900180"
    assert fix_ocr("от 2O.1l.2026") == "от 20.11.2026"
    assert fix_ocr("Итого: 47 OOO") == "Итого: 47 000"
    # обычные слова не трогаем, даже если в них есть О, l, i
    assert fix_ocr('ТОО "Logitech" и Olivia') == 'ТОО "Logitech" и Olivia'


def test_amount_in_words_russian_and_kazakh():
    assert words_to_number("Двести тридцать четыре тысячи пятьсот тенге 00 тиын") == 234500
    assert words_to_number("Жалпы сомасы: Үш жүз қырық мың теңге") == 340000
    assert words_to_number("сумма не указана") is None


def test_dates_in_all_formats():
    assert dates_in("Счёт № 1245 от 12.11.2026") == {"2026-11-12"}
    assert dates_in("от 17 ноября 2026 г.") == {"2026-11-17"}
    assert dates_in("AKT-09 / 09 қазан 2026 ж.") == {"2026-10-09"}
    assert dates_in("2026 жылғы 2 қараша") == {"2026-11-02"}
    assert dates_in("31.02.2026") == set()  # несуществующая дата


@pytest.mark.parametrize("bad", [
    {"doc_type": "bill"},                                   # тип не из списка
    {"doc_type": "invoice", "supplier_bin": "2l064090Ol80"},  # БИН не из 12 цифр
    {"doc_type": "invoice", "doc_date": "12.11.2026"},      # дата не ISO
    {"doc_type": "invoice", "currency": "RUB"},             # валюта не из списка
    {"doc_type": "invoice", "total_amount": -5},            # отрицательная сумма
    {"doc_type": "invoice", "total_amount": "186 180 тг"},  # сумма строкой
    {"doc_type": "invoice", "line_items": [{"name": "", "qty": 1, "price": 1}]},  # пустое название
])
def test_schema_rejects_invalid_data(bad):
    with pytest.raises(ValidationError):
        ExtractionResult(**bad)
