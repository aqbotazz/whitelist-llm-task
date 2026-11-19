"""Пайплайн для одного документа: промпт → модель → валидация (с повтором) → проверки по тексту."""
import json

from pydantic import ValidationError

from extractor.checks import SCALAR_FIELDS, verify
from extractor.schema import ExtractionResult

MAX_RETRIES = 2  # сколько раз переспрашиваем модель после невалидного ответа

PROMPT = """Ты извлекаешь данные из текста документа (счёт, коммерческое предложение, акт). Верни ТОЛЬКО JSON.

Главное правило: НЕ ВЫДУМЫВАЙ. Если значения нет в тексте — value: null.
Не выводи БИН из названия, не подставляй сегодняшнюю дату, не считай НДС и итог сам, не складывай строки.

Для каждого поля верни {"value": ..., "quote": "..."}, где quote — точная цитата из текста
(скопированная символ в символ, с теми же опечатками), из которой взято значение. Если value null — quote null.

Поля:
- doc_type: "invoice" (счёт, счёт на оплату, счёт-фактура, шот-фактура, төлемге шот), "commercial_offer"
  (коммерческое предложение), "act" (акт выполненных работ / оказанных услуг), "unknown" (всё остальное:
  письма, приглашения). Для unknown все остальные поля null, line_items пустой.
- supplier_name: поставщик / исполнитель (тот, кто продаёт), с правовой формой как в тексте (ТОО, ИП, ЖШС).
  Не менеджер и не покупатель. Если название на двух языках — русский вариант.
- supplier_bin: 12-значный БИН/ИИН/БСН поставщика (не покупателя и не заказчика). Если в нём ошибки
  распознавания (O вместо 0, l вместо 1) — value исправленные 12 цифр, quote как в тексте.
- doc_number: номер документа как в тексте, без «№».
- doc_date: дата составления документа, формат YYYY-MM-DD. Не срок действия и не срок оплаты.
- currency: "KZT" (тенге, тг, ₸, теңге), "USD" или "EUR".
- total_amount: итоговая сумма к оплате (с НДС, если он есть), числом. Если сумма только прописью —
  переведи в число. Если итоговой суммы в тексте нет — null.
- vat_amount: сумма НДС (ҚҚС) числом. «НДС не облагается» / «Без НДС» → 0.
  Указана только ставка без суммы или сумма нечитаема → null.
- line_items: позиции; у позиции ПЛОСКИЕ поля (без value/quote внутри), например
  {"name": "Бумага А4", "qty": 50, "price": 2100, "quote": "Бумага А4   50   2 100   105 000"}:
  name как в тексте, qty — количество, price — цена за единицу; не указано → null (не 1);
  quote — строка позиции из текста.
- warnings: список строк на русском о противоречиях: две разные даты документа, две разные итоговые
  суммы, тип документа не совпадает с номером, нечитаемые значения.

Формат ответа:
{"doc_type": {"value": "invoice", "quote": "Счёт на оплату"},
 "supplier_name": {"value": ..., "quote": ...}, "supplier_bin": {...}, "doc_number": {...},
 "doc_date": {...}, "currency": {...}, "total_amount": {...}, "vat_amount": {...},
 "line_items": [...], "warnings": [...]}

Текст документа:
<<<
{text}
>>>"""


def strip_code_fence(raw):
    """Модели иногда оборачивают JSON в ```json ... ``` — убираем обёртку."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        raw = raw.rsplit("```", 1)[0]
    return raw


def split_answer(data):
    """Делит ответ модели на значения (для схемы) и цитаты (для проверки по тексту)."""
    if not isinstance(data, dict):
        raise ValueError("ответ должен быть JSON-объектом")
    values, quotes = {}, {}
    for field in ["doc_type"] + SCALAR_FIELDS:
        item = data.get(field)
        if isinstance(item, dict):
            values[field], quotes[field] = item.get("value"), item.get("quote")
        else:
            values[field], quotes[field] = item, None  # без цитаты поле потом обнулится
    values["line_items"], item_quotes = [], []
    for item in data.get("line_items") or []:
        if not isinstance(item, dict):
            raise ValueError("каждая позиция line_items должна быть объектом")
        values["line_items"].append({k: item.get(k) for k in ("name", "qty", "price")})
        item_quotes.append(item.get("quote"))
    values["warnings"] = [f"модель: {w}" for w in data.get("warnings") or []]
    return values, quotes, item_quotes


def degrade(values, item_quotes, error):
    """Модель так и не дала валидный ответ: невалидные поля → null, документ не теряем."""
    warnings = list(values.get("warnings", []))
    bad_items = set()
    for e in error.errors():
        field = e["loc"][0]
        warnings.append(f"{field}: не прошло валидацию после {MAX_RETRIES} повторов ({e['msg']}) → null")
        if field == "line_items" and len(e["loc"]) > 1:
            bad_items.add(e["loc"][1])  # выкидываем только сломанную позицию
        elif field == "line_items":
            values["line_items"] = []
        elif field == "doc_type":
            values["doc_type"] = "unknown"
        else:
            values[field] = None
    keep = [i for i in range(len(values["line_items"])) if i not in bad_items]
    values["line_items"] = [values["line_items"][i] for i in keep]
    values["warnings"] = warnings
    return ExtractionResult(**values), [item_quotes[i] for i in keep]


def extract(text, llm):
    """Главная функция. llm — любая функция «промпт → строка» (настоящая модель или фейковая в тестах)."""
    base_prompt = PROMPT.replace("{text}", text)
    prompt = base_prompt
    values, quotes, item_quotes, error = None, {}, [], None
    for attempt in range(1 + MAX_RETRIES):
        raw = llm(prompt)
        try:
            values, quotes, item_quotes = split_answer(json.loads(strip_code_fence(raw)))
            result = ExtractionResult(**values)
            if attempt > 0:
                result.warnings.append(f"ответ модели прошёл валидацию с попытки {attempt + 1}")
            break
        except (json.JSONDecodeError, ValueError, ValidationError) as e:
            error = e
            # Повтор: показываем модели её ошибку и просим исправить
            prompt = (base_prompt + "\n\nТвой предыдущий ответ не прошёл проверку:\n"
                      f"{e}\nВерни исправленный JSON целиком.")
    else:
        # Все попытки неудачны
        if isinstance(error, ValidationError) and values is not None:
            result, item_quotes = degrade(values, item_quotes, error)
        else:
            return ExtractionResult(doc_type="unknown", warnings=[
                f"модель не вернула корректный JSON за {1 + MAX_RETRIES} попытки: {error}"])
    return verify(result, quotes, item_quotes, text)
