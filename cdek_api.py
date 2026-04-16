# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)

CDEK_BASE_URL = "https://api.cdek.ru/v2"
CDEK_TIMEOUT = 15


def _auth_headers() -> dict[str, str]:
    token = os.getenv("CDEK_ACCESS_TOKEN", "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def calculate_delivery(city_code: int | str, weight: int = 1000, total_price: int = 1000) -> int:
    """Пробует рассчитать доставку СДЭК; при ошибке возвращает 500."""
    payload = {
        "tariff_code": 137,
        "from_location": {"code": 44},
        "to_location": {"code": int(city_code)},
        "packages": [{"weight": int(weight), "length": 10, "width": 10, "height": 10}],
        "services": [{"code": "INSURANCE", "parameter": int(total_price)}],
    }

    try:
        response = requests.post(
            f"{CDEK_BASE_URL}/calculator/tariff",
            json=payload,
            headers=_auth_headers(),
            timeout=CDEK_TIMEOUT,
        )
        if response.ok:
            data: dict[str, Any] = response.json()
            return int(data.get("total_sum") or data.get("delivery_sum") or data.get("price") or 500)

        LOGGER.warning("СДЭК вернул %s: %s", response.status_code, response.text[:300])
    except Exception:
        LOGGER.exception("Ошибка запроса расчета доставки СДЭК")

    return 500


def get_city_code(city_name: str) -> int | None:
    """Ищет код города СДЭК по названию."""
    city = city_name.strip()
    if not city:
        return None

    try:
        response = requests.get(
            f"{CDEK_BASE_URL}/location/cities",
            params={"q": city, "size": 1},
            headers=_auth_headers(),
            timeout=CDEK_TIMEOUT,
        )
        if response.ok:
            cities = response.json()
            if isinstance(cities, list) and cities:
                code = cities[0].get("code")
                if code is not None:
                    return int(code)

        LOGGER.warning("СДЭК cities вернул %s: %s", response.status_code, response.text[:300])
    except Exception:
        LOGGER.exception("Ошибка запроса к СДЭК по городу")

    return None
