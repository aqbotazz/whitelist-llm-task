"""Метрики eval: галлюцинации, пропуски и неверные значения считаются раздельно."""
from eval import classify, compare_items


def test_classify_separates_hallucination_miss_and_wrong():
    assert classify(None, None) == "верно"
    assert classify("170340021554", None) == "галлюцинация"  # заполнено, а в эталоне null
    assert classify(None, "170340021554") == "пропуск"       # null, а в эталоне есть
    assert classify(160500, 186180) == "неверно"            # оба есть, но разные
    assert classify(186180.0, 186180) == "верно"
    assert classify('ТОО «ОфисЛенд»', 'ТОО "ОфисЛенд"') == "верно"  # кавычки не важны


def test_line_items_qty_one_instead_of_null_is_hallucination():
    expected = [{"name": "Разработка сайта", "qty": None, "price": 450000}]
    got = [{"name": "Разработка сайта", "qty": 1, "price": 450000},
           {"name": "Итого", "qty": None, "price": 510000}]  # лишняя позиция
    results = compare_items(got, expected)
    assert results["line_items.qty"] == ["галлюцинация", "галлюцинация"]
    assert results["line_items.price"] == ["верно", "галлюцинация"]


def test_missing_line_item_is_miss():
    expected = [{"name": "Цемент", "qty": 20, "price": 2350}, {"name": "Песок", "qty": 5, "price": 3200}]
    got = [{"name": "Цемент М400", "qty": 20, "price": None}]
    results = compare_items(got, expected)
    assert results["line_items.price"] == ["пропуск", "пропуск"]
    assert results["line_items.name"] == ["верно", "пропуск"]
