# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.local.json"


@dataclass
class Settings:
    bot_token: str = "8706282437:AAF9CaTjhlq8___8nUtGYUa2AQ1Ra3JHjXk"
    bot_username: str = "@TobaccoKingdom_bot"
    mini_app_url: str = "https://adorable-bienenstitch-88359b.netlify.app"
    parser_channel: str = "@tobacco_kingdom"
    owner_phone: str = "+79168287088"
    admin_username: str = "@ArnoldRich"
    admin_chat_id: int | None = None

    # Telethon данные (если пустые - будут запрошены при первом запуске parser.py)
    api_id: int | None = None
    api_hash: str = ""
    phone: str = "+79168287088"
    session_name: str = "session"

    # Локальные сервисы
    db_path: str = "shop.db"
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    public_api_base: str = "http://127.0.0.1:8080"
    delivery_price: int = 500

    # Прокси для Telegram (будут пробоваться по очереди)
    proxies: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"type": "socks5", "host": "127.0.0.1", "port": 1080, "rdns": True},
            {"type": "socks5", "host": "185.23.118.14", "port": 1080, "rdns": True},
            {"type": "socks5", "host": "31.43.63.70", "port": 1080, "rdns": True},
        ]
    )

    parser_reconnect_delay: int = 15


def _merge_dicts(defaults: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
    merged = defaults.copy()
    for key, value in loaded.items():
        if key not in defaults:
            continue
        merged[key] = value
    return merged


def _apply_env_overrides(settings: Settings) -> Settings:
    env_map: dict[str, tuple[str, Any]] = {
        "BOT_TOKEN": ("bot_token", str),
        "BOT_USERNAME": ("bot_username", str),
        "MINI_APP_URL": ("mini_app_url", str),
        "PARSER_CHANNEL": ("parser_channel", str),
        "OWNER_PHONE": ("owner_phone", str),
        "ADMIN_USERNAME": ("admin_username", str),
        "API_HASH": ("api_hash", str),
        "PHONE": ("phone", str),
        "SESSION_NAME": ("session_name", str),
        "DB_PATH": ("db_path", str),
        "API_HOST": ("api_host", str),
        "PUBLIC_API_BASE": ("public_api_base", str),
    }

    int_env_map: dict[str, str] = {
        "API_ID": "api_id",
        "ADMIN_CHAT_ID": "admin_chat_id",
        "API_PORT": "api_port",
        "DELIVERY_PRICE": "delivery_price",
        "PARSER_RECONNECT_DELAY": "parser_reconnect_delay",
    }

    for env_name, (field_name, caster) in env_map.items():
        value = os.getenv(env_name)
        if value:
            setattr(settings, field_name, caster(value))

    for env_name, field_name in int_env_map.items():
        value = os.getenv(env_name)
        if value:
            try:
                setattr(settings, field_name, int(value))
            except ValueError:
                LOGGER.warning("Переменная %s имеет некорректное значение: %s", env_name, value)

    return settings


def save_settings(settings: Settings) -> None:
    CONFIG_FILE.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _prompt_telethon_credentials(settings: Settings) -> Settings:
    print("\n[CONFIG] Требуются данные API_ID и API_HASH из https://my.telegram.org")

    if not settings.api_id:
        while True:
            value = input("Введите API_ID: ").strip()
            if value.isdigit():
                settings.api_id = int(value)
                break
            print("API_ID должен быть числом. Попробуйте снова.")

    if not settings.api_hash:
        while True:
            value = input("Введите API_HASH: ").strip()
            if value:
                settings.api_hash = value
                break
            print("API_HASH не может быть пустым.")

    save_settings(settings)
    print(f"[CONFIG] Сохранено в {CONFIG_FILE}")
    return settings


def load_settings(require_telegram_api: bool = False) -> Settings:
    defaults = asdict(Settings())

    loaded: dict[str, Any] = {}
    if CONFIG_FILE.exists():
        try:
            loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                LOGGER.warning("config.local.json поврежден: ожидается JSON-объект")
                loaded = {}
        except Exception:
            LOGGER.exception("Не удалось прочитать config.local.json")
            loaded = {}

    merged = _merge_dicts(defaults, loaded)
    settings = Settings(**merged)
    settings = _apply_env_overrides(settings)

    if not CONFIG_FILE.exists():
        save_settings(settings)
        LOGGER.info("Создан файл конфигурации: %s", CONFIG_FILE)

    if require_telegram_api and (not settings.api_id or not settings.api_hash):
        try:
            settings = _prompt_telethon_credentials(settings)
        except EOFError as exc:
            raise RuntimeError(
                "Нужно заполнить api_id и api_hash в config.local.json или через переменные окружения"
            ) from exc

    return settings
