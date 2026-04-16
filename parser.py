# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

from config import Settings, load_settings
from database import (
    delete_product,
    get_product_by_message_id,
    init_db,
    remove_products_not_in_channel,
    upsert_product_from_channel,
)

try:
    import socks
except ImportError:  # pragma: no cover
    socks = None

LOGGER = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

PRICE_PATTERN = re.compile(
    r"(?P<amount>\d[\d\s]{0,14})\s*(?:₽|руб(?:\.|лей|ля|ль)?|р\b)",
    flags=re.IGNORECASE,
)
CATEGORY_PATTERN = re.compile(r"(?:категория|category)\s*[:\-]\s*([^\n]+)", flags=re.IGNORECASE)
HASHTAG_PATTERN = re.compile(r"#([\wа-яА-ЯёЁ\-]+)")


def _extract_price(text: str) -> int:
    match = PRICE_PATTERN.search(text)
    if not match:
        return 0
    amount = re.sub(r"\s+", "", match.group("amount"))
    try:
        return int(amount)
    except ValueError:
        return 0


def _extract_category(text: str) -> str:
    category_match = CATEGORY_PATTERN.search(text)
    if category_match:
        return category_match.group(1).strip().lower()

    hashtag_match = HASHTAG_PATTERN.search(text)
    if hashtag_match:
        return hashtag_match.group(1).strip().lower()

    return "табак"


def _parse_product_text(message_id: int, raw_text: str) -> tuple[str, int, str, str]:
    text = (raw_text or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if lines:
        name = lines[0][:200]
        description = "\n".join(lines[1:]).strip()
    else:
        name = f"Товар #{message_id}"
        description = ""

    if not description:
        description = text

    price = _extract_price(text)
    category = _extract_category(text)
    return name, price, description, category


def _delete_file(path_value: str | None) -> None:
    if not path_value:
        return

    path = Path(path_value)
    if not path.is_absolute():
        path = BASE_DIR / path

    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        return

    temp_dir = TEMP_DIR.resolve()
    if temp_dir not in resolved.parents and resolved != temp_dir:
        LOGGER.warning("Пропущено удаление файла вне temp/: %s", resolved)
        return

    try:
        resolved.unlink(missing_ok=True)
    except Exception:
        LOGGER.exception("Не удалось удалить файл: %s", resolved)


def _build_proxy_tuple(proxy_cfg: dict[str, Any]) -> tuple[Any, ...] | None:
    if socks is None:
        return None

    proxy_type_name = str(proxy_cfg.get("type", "socks5")).lower()
    proxy_type = {
        "socks5": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "http": socks.HTTP,
    }.get(proxy_type_name)

    if proxy_type is None:
        return None

    host = proxy_cfg.get("host")
    port = proxy_cfg.get("port")
    if not host or not port:
        return None

    username = proxy_cfg.get("username")
    password = proxy_cfg.get("password")
    rdns = bool(proxy_cfg.get("rdns", True))

    return (proxy_type, str(host), int(port), rdns, username, password)


def build_proxy_candidates(settings: Settings) -> list[tuple[Any, ...] | None]:
    candidates: list[tuple[Any, ...] | None] = []

    for proxy_cfg in settings.proxies:
        proxy_tuple = _build_proxy_tuple(proxy_cfg)
        if proxy_tuple is not None:
            candidates.append(proxy_tuple)

    candidates.append(None)

    unique: list[tuple[Any, ...] | None] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = repr(candidate)
        if key not in seen:
            unique.append(candidate)
            seen.add(key)

    return unique


async def sync_message(client: TelegramClient, channel_key: str, message) -> None:
    if message is None or message.id is None:
        return

    text = (message.message or "").strip()
    if not text and not message.photo:
        return

    existing = get_product_by_message_id(int(message.id))
    existing_photo = existing.get("photo_url") if existing else None
    photo_path = existing_photo

    if message.photo:
        filename = f"{channel_key.strip('@').replace('/', '_')}_{message.id}.jpg"
        target = TEMP_DIR / filename
        downloaded = await client.download_media(message.photo, file=str(target))
        if downloaded:
            downloaded_path = Path(downloaded)
            if downloaded_path.is_absolute():
                try:
                    photo_path = str(downloaded_path.relative_to(BASE_DIR).as_posix())
                except ValueError:
                    photo_path = str(downloaded_path.as_posix())
            else:
                photo_path = str(downloaded_path.as_posix())

    name, price, description, category = _parse_product_text(int(message.id), text)

    upsert_product_from_channel(
        source_channel=channel_key,
        source_message_id=int(message.id),
        name=name,
        price=price,
        description=description,
        photo_url=photo_path,
        category=category,
    )

    if existing_photo and existing_photo != photo_path and message.photo:
        _delete_file(existing_photo)

    LOGGER.info("Синхронизирован пост #%s: %s (%s)", message.id, name, price)


async def full_history_sync(client: TelegramClient, channel_key: str) -> None:
    LOGGER.info("Запущена полная синхронизация канала %s", channel_key)
    existing_message_ids: set[int] = set()

    async for message in client.iter_messages(channel_key, reverse=True):
        if message.id is None:
            continue

        if not (message.message or message.photo):
            continue

        existing_message_ids.add(int(message.id))

        try:
            await sync_message(client, channel_key, message)
        except Exception:
            LOGGER.exception("Ошибка синхронизации поста #%s", message.id)

    removed = remove_products_not_in_channel(channel_key, existing_message_ids)
    for product in removed:
        _delete_file(product.get("photo_url"))

    LOGGER.info(
        "Полная синхронизация завершена. Обработано постов: %s, удалено устаревших: %s",
        len(existing_message_ids),
        len(removed),
    )


def register_handlers(client: TelegramClient, channel_key: str) -> None:
    @client.on(events.NewMessage(chats=channel_key))
    async def on_new_message(event):
        try:
            await sync_message(client, channel_key, event.message)
        except Exception:
            LOGGER.exception("Ошибка обработки нового поста")

    @client.on(events.MessageEdited(chats=channel_key))
    async def on_message_edited(event):
        try:
            await sync_message(client, channel_key, event.message)
        except Exception:
            LOGGER.exception("Ошибка обработки измененного поста")

    @client.on(events.MessageDeleted(chats=channel_key))
    async def on_message_deleted(event):
        for deleted_id in event.deleted_ids:
            try:
                removed = delete_product(source_message_id=int(deleted_id))
                if removed:
                    _delete_file(removed.get("photo_url"))
                    LOGGER.info("Удален товар из удаленного поста #%s", deleted_id)
            except Exception:
                LOGGER.exception("Ошибка удаления товара для поста #%s", deleted_id)


async def connect_client(settings: Settings) -> TelegramClient:
    if not settings.api_id or not settings.api_hash:
        raise RuntimeError("Не заполнены api_id/api_hash в config.local.json")

    last_error: Exception | None = None

    for proxy in build_proxy_candidates(settings):
        proxy_label = "без прокси" if proxy is None else f"через {proxy[1]}:{proxy[2]}"
        LOGGER.info("Попытка подключения к Telegram %s", proxy_label)

        client = TelegramClient(
            settings.session_name,
            int(settings.api_id),
            settings.api_hash,
            proxy=proxy,
            auto_reconnect=True,
            connection_retries=10,
            request_retries=5,
            retry_delay=3,
        )

        try:
            await client.start(phone=settings.phone)
            LOGGER.info("Подключение успешно %s", proxy_label)
            return client
        except Exception as exc:
            last_error = exc
            LOGGER.warning("Не удалось подключиться %s: %s", proxy_label, exc)
            try:
                await client.disconnect()
            except Exception:
                pass

    raise RuntimeError(f"Не удалось подключиться к Telegram ни через один вариант: {last_error}")


async def run_parser() -> None:
    init_db()

    while True:
        settings = load_settings(require_telegram_api=True)
        client: TelegramClient | None = None

        try:
            client = await connect_client(settings)
            await client.get_entity(settings.parser_channel)

            await full_history_sync(client, settings.parser_channel)
            register_handlers(client, settings.parser_channel)

            LOGGER.info("Парсер в режиме отслеживания канала %s", settings.parser_channel)
            await client.run_until_disconnected()

        except FloodWaitError as exc:
            delay = int(exc.seconds) + 5
            LOGGER.warning("FloodWait: пауза %s секунд", delay)
            await asyncio.sleep(delay)

        except Exception:
            LOGGER.exception("Критическая ошибка парсера, перезапуск через %s сек", settings.parser_reconnect_delay)
            await asyncio.sleep(max(5, int(settings.parser_reconnect_delay)))

        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    LOGGER.exception("Ошибка при отключении клиента Telegram")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(run_parser())


if __name__ == "__main__":
    main()
