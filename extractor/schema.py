"""Схема итогового JSON. Pydantic проверяет каждое поле: тип, формат, допустимые значения."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")  # лишние поля = ошибка

    name: str = Field(min_length=1)
    qty: float | None = Field(default=None, ge=0)    # не указано -> null, а не 1
    price: float | None = Field(default=None, ge=0)  # цена за единицу


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_type: Literal["invoice", "commercial_offer", "act", "unknown"]
    supplier_name: str | None = None
    supplier_bin: str | None = Field(default=None, pattern=r"^\d{12}$")  # ровно 12 цифр
    doc_number: str | None = None
    doc_date: str | None = None  # строка YYYY-MM-DD, проверяется ниже
    currency: Literal["KZT", "USD", "EUR"] | None = None
    total_amount: float | None = Field(default=None, ge=0)
    vat_amount: float | None = Field(default=None, ge=0)
    line_items: list[LineItem] = []
    field_confidence: dict[str, float] = {}
    warnings: list[str] = []

    @field_validator("doc_date")
    @classmethod
    def check_date(cls, value):
        # Пропускаем только настоящую дату в формате YYYY-MM-DD (31.02 не пройдёт)
        if value is not None:
            if len(value) != 10:
                raise ValueError("дата должна быть в формате YYYY-MM-DD")
            date.fromisoformat(value)
        return value

    @field_validator("field_confidence")
    @classmethod
    def check_confidence(cls, value):
        for field, score in value.items():
            if not 0 <= score <= 1:
                raise ValueError(f"confidence для {field} должен быть от 0 до 1")
        return value
