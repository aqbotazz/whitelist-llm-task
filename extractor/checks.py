"""Проверки, которые делает код, а не модель: опора на текст, OCR, БИН, арифметика.

Главная идея: модель к каждому значению прикладывает цитату из документа.
Если цитаты нет в тексте или значение из неё не следует — поле обнуляется.
"""
import re
from datetime import date

from extractor.schema import ExtractionResult

SCALAR_FIELDS = ["supplier_name", "supplier_bin", "doc_number", "doc_date",
                 "currency", "total_amount", "vat_amount"]

# Насколько доверяем значению в зависимости от того, как оно подтвердилось
CONFIDENCE = {"exact": 1.0, "words": 0.9, "ocr": 0.7}

# ---------- Нормализация текста ----------

OCR_TO_DIGITS = str.maketrans({"O": "0", "О": "0", "o": "0", "о": "0",
                               "l": "1", "I": "1", "i": "1"})

# Кириллические буквы, которые выглядят как латинские (АКТ и AKT — одно и то же на глаз)
LOOKALIKES = str.maketrans("аеорсхктмнву", "aeopcxktmhby")


def fix_ocr(text):
    """Исправляет буквы, похожие на цифры, только в «числовых» словах: 2l064090Ol80, 2O.1l.2026, OOO."""
    def fix(match):
        word = match.group()
        looks_numeric = re.fullmatch(r"[\dOОoоlIi.,:;/№-]+", word)
        if looks_numeric and (re.search(r"\d", word) or re.fullmatch(r"[OО]{3}", word)):
            return word.translate(OCR_TO_DIGITS)
        return word
    return re.sub(r"\S+", fix, text)


def normalize(text):
    """Для сравнения: нижний регистр, без кавычек, одинаковые пробелы и похожие буквы."""
    text = text.lower().replace(" ", " ")
    text = re.sub(r"[«»\"“”„']", "", text)
    return " ".join(text.split()).translate(LOOKALIKES)


def find_quote(quote, text):
    """Есть ли цитата в тексте: 'exact' — как есть, 'ocr' — после исправления OCR, None — нет."""
    if not quote:
        return None
    if normalize(quote) in normalize(text):
        return "exact"
    if normalize(fix_ocr(quote)) in normalize(fix_ocr(text)):
        return "ocr"
    return None

# ---------- БИН ----------


def bin_checksum_ok(bin_value):
    """Контрольная сумма БИН/ИИН: веса 1..11, при остатке 10 — веса 3..11,1,2."""
    digits = [int(c) for c in bin_value]
    for weights in ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], [3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2]):
        control = sum(d * w for d, w in zip(digits, weights)) % 11
        if control != 10:
            return control == digits[11]
    return False  # оба раза получилось 10 — номер некорректен


def bin_status(value, quote):
    if value in re.findall(r"(?<!\d)\d{12}(?!\d)", quote):
        return "exact"
    if value in re.findall(r"(?<!\d)\d{12}(?!\d)", fix_ocr(quote)):
        return "ocr"
    return None

# ---------- Числа ----------


def number_forms(value):
    """Как число может быть записано в документе: 186180, 186 180, 186 180,00, 812.00 ..."""
    whole = int(value)
    cents = round((value - whole) * 100)
    heads = {str(whole), f"{whole:,}".replace(",", " ")}
    tails = {"", ".00", ",00"} if cents == 0 else {f".{cents:02d}", f",{cents:02d}"}
    return {head + tail for head in heads for tail in tails}


def number_in(value, text):
    """Встречается ли число в тексте целиком (не как часть другого числа)."""
    text = " ".join(text.replace(" ", " ").split())
    for form in number_forms(value):
        if re.search(r"(?<![\d.,])" + re.escape(form) + r"(?![\d]|[.,]\d)", text):
            return True
    return False


WORD_NUMBERS = {
    "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40,
    "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80,
    "девяносто": 90, "сто": 100, "двести": 200, "триста": 300, "четыреста": 400,
    "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
    # казахский
    "бір": 1, "екі": 2, "үш": 3, "төрт": 4, "бес": 5, "алты": 6, "жеті": 7,
    "сегіз": 8, "тоғыз": 9, "он": 10, "жиырма": 20, "отыз": 30, "қырық": 40,
    "елу": 50, "алпыс": 60, "жетпіс": 70, "сексен": 80, "тоқсан": 90,
}
MULTIPLIERS = {"тысяча": 1000, "тысячи": 1000, "тысяч": 1000, "мың": 1000,
               "миллион": 10**6, "миллиона": 10**6, "миллионов": 10**6}


def words_to_number(text):
    """Сумма прописью → число. «Двести тридцать четыре тысячи пятьсот» → 234500.

    По-казахски «жүз» умножает: «үш жүз қырық мың» = (3·100 + 40)·1000 = 340000.
    Берём первую подряд идущую группу слов-чисел; если её нет — None.
    """
    total = current = 0
    found = False
    for word in re.findall(r"[^\W\d_]+", text.lower()):
        if word in WORD_NUMBERS:
            current += WORD_NUMBERS[word]
        elif word == "жүз":
            current = (current or 1) * 100
        elif word in MULTIPLIERS:
            total += (current or 1) * MULTIPLIERS[word]
            current = 0
        elif found:
            break  # группа слов-чисел закончилась
        else:
            continue
        found = True
    return total + current if found else None


def amount_status(value, quote):
    if number_in(value, quote):
        return "exact"
    if number_in(value, fix_ocr(quote)):
        return "ocr"
    if words_to_number(quote) == value:
        return "words"
    return None


ZERO_VAT = ("не облага", "без ндс", "ндс не", "ққс-сыз", "ққс салынбайды", "освобожд")

# ---------- Даты ----------

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    "қаңтар": 1, "ақпан": 2, "наурыз": 3, "сәуір": 4, "мамыр": 5, "маусым": 6,
    "шілде": 7, "тамыз": 8, "қыркүйек": 9, "қазан": 10, "қараша": 11, "желтоқсан": 12,
}


def month_number(word):
    for stem, number in MONTHS.items():
        if word.startswith(stem):
            return number
    return None


def dates_in(text):
    """Все даты в тексте в виде YYYY-MM-DD: 12.11.2026, 17 ноября 2026, 09 қазан 2026, 2026 жылғы 2 қараша."""
    text = text.lower()
    found = []  # тройки (год, месяц, день)
    for d, m, y in re.findall(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)", text):
        found.append((y, int(m), d))
    for d, word, y in re.findall(r"(?<!\d)(\d{1,2})\s+([^\W\d_]+)\s+(\d{4})", text):
        found.append((y, month_number(word), d))
    for y, d, word in re.findall(r"(\d{4})\s+жылғы\s+(\d{1,2})\s+([^\W\d_]+)", text):
        found.append((y, month_number(word), d))
    result = set()
    for y, m, d in found:
        try:
            result.add(date(int(y), m, int(d)).isoformat())
        except (TypeError, ValueError):
            pass  # не месяц или несуществующая дата
    return result


def date_status(value, quote):
    if value in dates_in(quote):
        return "exact"
    if value in dates_in(fix_ocr(quote)):
        return "ocr"
    return None

# ---------- Проверка одного поля ----------

CURRENCY_MARKERS = {
    "KZT": ("тенге", "теңге", "тенr", "тг", "₸", "kzt"),
    "USD": ("usd", "$", "доллар"),
    "EUR": ("eur", "€", "евро"),
}


def value_status(field, value, quote):
    """Следует ли значение из цитаты. Возвращает 'exact' / 'ocr' / 'words' или None."""
    if field == "supplier_bin":
        return bin_status(value, quote)
    if field == "doc_date":
        return date_status(value, quote)
    if field == "vat_amount" and value == 0:
        return "exact" if any(m in quote.lower() for m in ZERO_VAT) else None
    if field in ("total_amount", "vat_amount"):
        return amount_status(value, quote)
    if field == "currency":
        return "exact" if any(m in quote.lower() for m in CURRENCY_MARKERS[value]) else None
    # supplier_name, doc_number: значение должно быть внутри цитаты
    return "exact" if normalize(value) in normalize(quote) else None

# ---------- Итоговая проверка результата ----------

PAYABLE_WORDS = ("к оплате", "төлеуге", "итого с ндс")


def payable_amounts(text):
    """Все суммы в строках «к оплате» — чтобы найти противоречие между несколькими итогами."""
    amounts = []
    for line in text.splitlines():
        if any(word in line.lower() for word in PAYABLE_WORDS):
            for number in re.findall(r"\d{1,3}(?: \d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?", fix_ocr(line)):
                amount = float(number.replace(" ", "").replace(",", "."))
                if amount > 0:  # «00 тиын» — не сумма
                    amounts.append(amount)
    return amounts


def verify(result, quotes, item_quotes, text):
    """Проверяет ответ модели по тексту документа и считает field_confidence."""
    data = result.model_dump()
    warnings = data["warnings"]
    confidence = {}

    def reject(field, reason):
        warnings.append(f"{field}: {reason} → null")
        data[field] = None

    # Тип документа обнулить нельзя — только понижаем доверие
    confidence["doc_type"] = 1.0
    if data["doc_type"] != "unknown" and not find_quote(quotes.get("doc_type"), text):
        confidence["doc_type"] = 0.5
        warnings.append("doc_type: цитата-доказательство не найдена в тексте")

    # 1. Опора на текст: цитата есть в документе и значение из неё следует
    for field in SCALAR_FIELDS:
        value = data[field]
        if value is None:
            continue
        quote = quotes.get(field)
        quote_found = find_quote(quote, text)
        if not quote_found:
            reject(field, f"цитата {quote!r} не найдена в тексте")
            continue
        status = value_status(field, value, quote)
        if status is None:
            reject(field, f"значение {value!r} не следует из цитаты {quote!r}")
            continue
        if quote_found == "ocr" and status == "exact":
            status = "ocr"
        if field == "supplier_bin" and re.search(r"покупател|заказчик|получател", quote.lower()):
            reject(field, "похоже на БИН покупателя, а не поставщика")
            continue
        confidence[field] = CONFIDENCE[status]
        if status == "ocr":
            warnings.append(f"{field}: значение восстановлено после исправления ошибок OCR в {quote!r}")
        if status == "words":
            warnings.append(f"{field}: сумма взята из записи прописью")

    # Номер намекает на другой тип: «Шот-фактура № AKT-09». После normalize и АКТ, и AKT → akt
    if data["doc_type"] != "act" and normalize(data["doc_number"] or "").startswith("akt"):
        warnings.append(f"doc_type: номер {data['doc_number']!r} похож на акт, "
                        f"а документ определён как {data['doc_type']}")

    # 2. Контрольная сумма БИН: значение есть в документе, поэтому не обнуляем, а предупреждаем
    if data["supplier_bin"] and not bin_checksum_ok(data["supplier_bin"]):
        confidence["supplier_bin"] = 0.5
        warnings.append(f"supplier_bin: {data['supplier_bin']} не проходит контрольную сумму БИН/ИИН")

    # 3. Позиции: строка есть в тексте, количество и цена в ней написаны
    items = []
    for item, quote in zip(data["line_items"], item_quotes):
        if not find_quote(quote, text) or normalize(item["name"]) not in normalize(quote):
            warnings.append(f"позиция {item['name']!r} не найдена в тексте → удалена")
            continue
        for key in ("qty", "price"):
            if item[key] is not None and not amount_status(item[key], quote):
                warnings.append(f"позиция {item['name']!r}: {key}={item[key]} нет в строке → null")
                item[key] = None
        items.append(item)
    data["line_items"] = items

    # 4. Несколько разных сумм «к оплате»: берём ту, что сходится со строками, иначе последнюю
    complete = items and all(i["qty"] is not None and i["price"] is not None for i in items)
    lines_sum = sum(i["qty"] * i["price"] for i in items) if complete else None
    amounts = payable_amounts(text)
    if data["doc_type"] != "unknown" and len(set(amounts)) > 1:
        expected = lines_sum + (data["vat_amount"] or 0) if lines_sum else None
        chosen = expected if expected in amounts else amounts[-1]
        warnings.append(f"в документе несколько разных сумм к оплате {sorted(set(amounts))}: "
                        f"выбрана {chosen:g}")
        data["total_amount"] = chosen
        confidence["total_amount"] = 0.7

    # 5. Арифметика: сумма строк + НДС должна равняться итогу
    if lines_sum is not None and data["total_amount"] is not None:
        if abs(lines_sum + (data["vat_amount"] or 0) - data["total_amount"]) > 0.01:
            warnings.append(f"арифметика: сумма строк {lines_sum:g} + НДС {data['vat_amount']} "
                            f"не равна итогу {data['total_amount']:g}")
            confidence["total_amount"] = min(confidence.get("total_amount", 1.0), 0.6)

    data["field_confidence"] = {f: c for f, c in confidence.items() if f == "doc_type" or data[f] is not None}
    return ExtractionResult(**data)
