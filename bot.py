# -*- coding: utf-8 -*-
from __future__ import annotations

import html
import json
import logging
import threading
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Flask, jsonify, request, send_from_directory
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from config import load_settings
from database import add_order, get_all_products, init_db, save_cart

LOGGER = logging.getLogger(__name__)
SETTINGS = load_settings(require_telegram_api=False)
BASE_DIR = Path(__file__).resolve().parent
TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

REQUEST = HTTPXRequest(connection_pool_size=20, connect_timeout=20.0, read_timeout=30.0)


def format_price(value: int) -> str:
    return f"{int(value):,}".replace(",", " ") + " ₽"


def _build_catalog_keyboard() -> InlineKeyboardMarkup:
    mini_app_url = _build_mini_app_url()
    keyboard = [
        [
            InlineKeyboardButton(
                text="Открыть каталог",
                web_app=WebAppInfo(url=mini_app_url),
            )
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def _build_mini_app_url() -> str:
    base = SETTINGS.mini_app_url
    api_base = SETTINGS.public_api_base.rstrip("/")
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query))
    query.setdefault("api", api_base)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _normalize_photo_url(raw_url: str | None, base_url: str) -> str | None:
    if not raw_url:
        return None

    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        return raw_url

    candidate = Path(raw_url)
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate

    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return None

    temp_resolved = TEMP_DIR.resolve()
    if temp_resolved not in resolved.parents and resolved != temp_resolved:
        return None

    return f"{base_url.rstrip('/')}/media/{resolved.name}"


def create_api_app() -> Flask:
    api_app = Flask(__name__)

    @api_app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    @api_app.get("/health")
    def health() -> tuple[dict[str, Any], int]:
        return {"ok": True}, 200

    @api_app.get("/products")
    def products() -> tuple[Any, int]:
        try:
            products_data = get_all_products()
            base_url = request.host_url.rstrip("/")

            prepared: list[dict[str, Any]] = []
            for product in products_data:
                normalized = dict(product)
                normalized["photo_url"] = _normalize_photo_url(product.get("photo_url"), base_url)
                prepared.append(normalized)

            return jsonify({"ok": True, "products": prepared}), 200
        except Exception as exc:
            LOGGER.exception("Ошибка API /products")
            return jsonify({"ok": False, "error": str(exc)}), 500

    @api_app.get("/media/<path:filename>")
    def media(filename: str):
        return send_from_directory(TEMP_DIR, filename)

    return api_app


def run_api_server() -> None:
    api_app = create_api_app()
    LOGGER.info("HTTP API запущено на %s:%s", SETTINGS.api_host, SETTINGS.api_port)
    api_app.run(
        host=SETTINGS.api_host,
        port=SETTINGS.api_port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


def normalize_order_items(raw_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    products_map = {int(product["id"]): product for product in get_all_products()}
    normalized: list[dict[str, Any]] = []

    for item in raw_items:
        try:
            product_id = int(item.get("id"))
            quantity = int(item.get("quantity", 1))
        except (TypeError, ValueError):
            continue

        if quantity <= 0:
            continue
        if quantity > 99:
            quantity = 99

        product = products_map.get(product_id)
        if not product:
            continue

        price = int(product.get("price") or 0)
        normalized.append(
            {
                "id": product_id,
                "name": product.get("name", "Без названия"),
                "price": price,
                "quantity": quantity,
                "line_total": price * quantity,
            }
        )

    total = sum(item["line_total"] for item in normalized)
    return normalized, total


def build_order_receipt(order_id: int, items: list[dict[str, Any]], total: int, delivery: int, final: int) -> str:
    item_lines = "\n".join(
        f"• {html.escape(item['name'])} × {item['quantity']} = <b>{format_price(item['line_total'])}</b>"
        for item in items
    )

    return (
        f"<b>Заказ #{order_id} принят</b>\n\n"
        f"{item_lines}\n\n"
        f"Товары: <b>{format_price(total)}</b>\n"
        f"Доставка: <b>{format_price(delivery)}</b>\n"
        f"Итого: <b>{format_price(final)}</b>"
    )


async def notify_admin(
    context: ContextTypes.DEFAULT_TYPE,
    order_id: int,
    user: Any,
    payload: dict[str, Any],
    items: list[dict[str, Any]],
    final: int,
) -> None:
    targets: list[str | int] = []
    if SETTINGS.admin_chat_id:
        targets.append(int(SETTINGS.admin_chat_id))
    if SETTINGS.admin_username:
        targets.append(SETTINGS.admin_username)

    if not targets:
        LOGGER.warning("Не задан ADMIN_CHAT_ID / ADMIN_USERNAME, уведомление админу пропущено")
        return

    items_text = "\n".join(
        f"• {item['name']} × {item['quantity']} = {format_price(item['line_total'])}"
        for item in items
    )

    username = f"@{user.username}" if user and user.username else "нет"
    admin_message = (
        f"Новый заказ #{order_id}\n\n"
        f"Покупатель: {payload['customer_name']}\n"
        f"Телефон: {payload['customer_phone']}\n"
        f"Адрес: {payload['customer_city']}, {payload['customer_address']}\n"
        f"Telegram: {username}\n"
        f"User ID: {user.id if user else '-'}\n\n"
        f"Состав:\n{items_text}\n\n"
        f"Итого: {format_price(final)}"
    )

    for target in dict.fromkeys(targets):
        try:
            await context.bot.send_message(chat_id=target, text=admin_message)
        except TelegramError:
            LOGGER.exception("Не удалось отправить уведомление админу: %s", target)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_name = update.effective_user.first_name if update.effective_user else "друг"
    text = (
        f"Привет, {html.escape(user_name)}.\n"
        "Это магазин Табачное Царство — открой каталог кнопкой ниже."
    )
    await update.effective_message.reply_text(text=text, reply_markup=_build_catalog_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Доступные команды:\n"
        "/start — открыть каталог\n"
        "/help — помощь\n\n"
        "Заказ оформляется внутри Mini App."
    )
    await update.effective_message.reply_text(text)


async def web_app_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user

    if message is None or message.web_app_data is None:
        return

    try:
        data = json.loads(message.web_app_data.data)
    except json.JSONDecodeError:
        LOGGER.warning("Получен некорректный web_app_data: %s", message.web_app_data.data)
        await message.reply_text("Не удалось обработать данные заказа. Попробуйте снова.")
        return

    action = data.get("action")
    if action == "save_cart":
        cart_items = data.get("items", [])
        if user and isinstance(cart_items, list):
            save_cart(user.id, cart_items, username=user.username, first_name=user.first_name)
            await message.reply_text("Корзина сохранена.")
            return
        await message.reply_text("Не удалось сохранить корзину.")
        return

    if action != "order":
        await message.reply_text("Неизвестное действие из Mini App.")
        return

    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        await message.reply_text("Ошибка формата корзины.")
        return

    items, total = normalize_order_items(raw_items)
    if not items:
        await message.reply_text("Корзина пуста или товары не найдены в базе.")
        return

    customer_name = str(data.get("name", "")).strip()
    customer_phone = str(data.get("phone", "")).strip()
    customer_city = str(data.get("city", "")).strip()
    customer_address = str(data.get("address", "")).strip()

    if not all([customer_name, customer_phone, customer_city, customer_address]):
        await message.reply_text("Заполните все поля: город, адрес, имя и телефон.")
        return

    delivery_price = int(SETTINGS.delivery_price)
    final_price = total + delivery_price

    order_payload = {
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "customer_city": customer_city,
        "customer_address": customer_address,
    }

    order_id = add_order(
        user_id=user.id if user else 0,
        user_name=user.full_name if user else "",
        items=items,
        total_price=total,
        delivery_price=delivery_price,
        final_price=final_price,
        customer_name=customer_name,
        customer_phone=customer_phone,
        customer_city=customer_city,
        customer_address=customer_address,
    )

    if user is not None:
        save_cart(user_id=user.id, cart_items=[], username=user.username, first_name=user.first_name)

    receipt = build_order_receipt(order_id, items, total, delivery_price, final_price)
    receipt += (
        f"\n\nДоставка: {html.escape(customer_city)}, {html.escape(customer_address)}"
        f"\nПолучатель: {html.escape(customer_name)} ({html.escape(customer_phone)})"
    )

    await message.reply_text(receipt, parse_mode="HTML")
    await notify_admin(context, order_id, user, order_payload, items, final_price)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Ошибка в обработчике", exc_info=context.error)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not SETTINGS.bot_token:
        raise RuntimeError("BOT_TOKEN пуст. Проверьте config.local.json")

    init_db()

    api_thread = threading.Thread(target=run_api_server, daemon=True, name="api-server")
    api_thread.start()

    bot_app = Application.builder().token(SETTINGS.bot_token).request(REQUEST).build()
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("help", help_command))
    bot_app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data_handler))
    bot_app.add_error_handler(error_handler)

    LOGGER.info("Бот запущен. Mini App URL: %s", SETTINGS.mini_app_url)
    LOGGER.info("API для Mini App: http://%s:%s/products", SETTINGS.api_host, SETTINGS.api_port)
    LOGGER.info("Публичный API base для Mini App: %s", SETTINGS.public_api_base)

    bot_app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
