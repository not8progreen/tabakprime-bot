# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from config import load_settings

LOGGER = logging.getLogger(__name__)
SETTINGS = load_settings(require_telegram_api=False)

DB_PATH = Path(SETTINGS.db_path)
if not DB_PATH.is_absolute():
    DB_PATH = Path(__file__).resolve().parent / DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _get_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    rows = cursor.fetchall()
    return {str(row["name"]) for row in rows}


def _ensure_columns(conn: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
    existing = _get_table_columns(conn, table_name)
    cursor = conn.cursor()

    for column_name, definition in columns.items():
        if column_name in existing:
            continue
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {definition}")
        LOGGER.info("Добавлена колонка %s.%s", table_name, column_name)


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price INTEGER NOT NULL DEFAULT 0,
                description TEXT DEFAULT '',
                photo_url TEXT,
                category TEXT DEFAULT 'без категории',
                source_channel TEXT,
                source_message_id INTEGER UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                items TEXT NOT NULL,
                total_price INTEGER NOT NULL,
                delivery_price INTEGER NOT NULL DEFAULT 500,
                final_price INTEGER NOT NULL,
                customer_name TEXT NOT NULL,
                customer_phone TEXT NOT NULL,
                customer_city TEXT NOT NULL,
                customer_address TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                tracking_number TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                cart TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        _ensure_columns(
            conn,
            "products",
            {
                "description": "description TEXT DEFAULT ''",
                "price": "price INTEGER NOT NULL DEFAULT 0",
                "photo_url": "photo_url TEXT",
                "category": "category TEXT DEFAULT 'без категории'",
                "source_channel": "source_channel TEXT",
                "source_message_id": "source_message_id INTEGER",
                "created_at": "created_at TEXT",
                "updated_at": "updated_at TEXT",
            },
        )
        _ensure_columns(
            conn,
            "orders",
            {
                "delivery_price": "delivery_price INTEGER NOT NULL DEFAULT 500",
                "status": "status TEXT NOT NULL DEFAULT 'pending'",
                "tracking_number": "tracking_number TEXT",
                "created_at": "created_at TEXT",
                "updated_at": "updated_at TEXT",
            },
        )
        _ensure_columns(
            conn,
            "users",
            {
                "cart": "cart TEXT NOT NULL DEFAULT '[]'",
                "created_at": "created_at TEXT",
                "updated_at": "updated_at TEXT",
            },
        )

        cursor.execute("UPDATE products SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)")
        cursor.execute("UPDATE products SET updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)")
        cursor.execute("UPDATE orders SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)")
        cursor.execute("UPDATE orders SET updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)")
        cursor.execute("UPDATE users SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)")
        cursor.execute("UPDATE users SET updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_channel ON products(source_channel)")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_source_message ON products(source_message_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")

        conn.commit()

    LOGGER.info("База данных инициализирована: %s", DB_PATH)


def add_product(
    name: str,
    price: int,
    description: str = "",
    photo_url: str | None = None,
    category: str = "без категории",
    source_channel: str | None = None,
    source_message_id: int | None = None,
) -> int:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO products (name, price, description, photo_url, category, source_channel, source_message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, int(price), description, photo_url, category, source_channel, source_message_id),
        )
        conn.commit()
        return int(cursor.lastrowid)


def upsert_product_from_channel(
    source_channel: str,
    source_message_id: int,
    name: str,
    price: int,
    description: str,
    photo_url: str | None,
    category: str,
) -> int:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO products (name, price, description, photo_url, category, source_channel, source_message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_message_id) DO UPDATE SET
                name=excluded.name,
                price=excluded.price,
                description=excluded.description,
                photo_url=excluded.photo_url,
                category=excluded.category,
                source_channel=excluded.source_channel,
                updated_at=CURRENT_TIMESTAMP
            """,
            (name, int(price), description, photo_url, category, source_channel, int(source_message_id)),
        )
        cursor.execute("SELECT id FROM products WHERE source_message_id = ?", (int(source_message_id),))
        row = cursor.fetchone()
        conn.commit()

    if row is None:
        raise RuntimeError("Не удалось сохранить товар")

    return int(row["id"])


def update_product(
    product_id: int,
    name: str | None = None,
    price: int | None = None,
    description: str | None = None,
    photo_url: str | None = None,
    category: str | None = None,
) -> bool:
    fields: list[str] = []
    values: list[Any] = []

    if name is not None:
        fields.append("name = ?")
        values.append(name)
    if price is not None:
        fields.append("price = ?")
        values.append(int(price))
    if description is not None:
        fields.append("description = ?")
        values.append(description)
    if photo_url is not None:
        fields.append("photo_url = ?")
        values.append(photo_url)
    if category is not None:
        fields.append("category = ?")
        values.append(category)

    if not fields:
        return False

    fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(int(product_id))

    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE products SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        conn.commit()
        return cursor.rowcount > 0


def get_product_by_message_id(source_message_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM products WHERE source_message_id = ?",
            (int(source_message_id),),
        )
        return _row_to_dict(cursor.fetchone())


def delete_product(
    product_id: int | None = None,
    source_message_id: int | None = None,
) -> dict[str, Any] | None:
    if product_id is None and source_message_id is None:
        raise ValueError("Нужно передать product_id или source_message_id")

    with _connect() as conn:
        cursor = conn.cursor()

        if product_id is not None:
            cursor.execute("SELECT * FROM products WHERE id = ?", (int(product_id),))
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute("DELETE FROM products WHERE id = ?", (int(product_id),))
        else:
            cursor.execute(
                "SELECT * FROM products WHERE source_message_id = ?",
                (int(source_message_id),),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                "DELETE FROM products WHERE source_message_id = ?",
                (int(source_message_id),),
            )

        conn.commit()
        return _row_to_dict(row)


def remove_products_not_in_channel(
    source_channel: str,
    existing_message_ids: set[int],
) -> list[dict[str, Any]]:
    with _connect() as conn:
        cursor = conn.cursor()

        if existing_message_ids:
            placeholders = ",".join("?" for _ in existing_message_ids)
            query = (
                f"SELECT * FROM products WHERE source_channel = ? "
                f"AND source_message_id IS NOT NULL AND source_message_id NOT IN ({placeholders})"
            )
            params: list[Any] = [source_channel, *sorted(existing_message_ids)]
            cursor.execute(query, params)
            rows = cursor.fetchall()

            del_query = (
                f"DELETE FROM products WHERE source_channel = ? "
                f"AND source_message_id IS NOT NULL AND source_message_id NOT IN ({placeholders})"
            )
            cursor.execute(del_query, params)
        else:
            cursor.execute(
                "SELECT * FROM products WHERE source_channel = ? AND source_message_id IS NOT NULL",
                (source_channel,),
            )
            rows = cursor.fetchall()
            cursor.execute(
                "DELETE FROM products WHERE source_channel = ? AND source_message_id IS NOT NULL",
                (source_channel,),
            )

        conn.commit()
        return [dict(row) for row in rows]


def get_all_products() -> list[dict[str, Any]]:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, name, price, description, photo_url, category, created_at, updated_at
            FROM products
            ORDER BY datetime(created_at) DESC, id DESC
            """
        )
        return [dict(row) for row in cursor.fetchall()]


def add_order(
    user_id: int,
    user_name: str,
    items: list[dict[str, Any]] | str,
    total_price: int,
    delivery_price: int,
    final_price: int,
    customer_name: str,
    customer_phone: str,
    customer_city: str,
    customer_address: str,
) -> int:
    items_json = items if isinstance(items, str) else json.dumps(items, ensure_ascii=False)

    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO orders (
                user_id,
                user_name,
                items,
                total_price,
                delivery_price,
                final_price,
                customer_name,
                customer_phone,
                customer_city,
                customer_address,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                int(user_id),
                user_name,
                items_json,
                int(total_price),
                int(delivery_price),
                int(final_price),
                customer_name,
                customer_phone,
                customer_city,
                customer_address,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_orders(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with _connect() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                "SELECT * FROM orders WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, int(limit)),
            )
        else:
            cursor.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ?",
                (int(limit),),
            )
        return [dict(row) for row in cursor.fetchall()]


def update_order_status(order_id: int, status: str, tracking_number: str | None = None) -> bool:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE orders
            SET status = ?,
                tracking_number = COALESCE(?, tracking_number),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, tracking_number, int(order_id)),
        )
        conn.commit()
        return cursor.rowcount > 0


def save_cart(
    user_id: int,
    cart_items: list[dict[str, Any]],
    username: str | None = None,
    first_name: str | None = None,
) -> None:
    cart_json = json.dumps(cart_items, ensure_ascii=False)
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (user_id, username, first_name, cart)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = COALESCE(excluded.username, users.username),
                first_name = COALESCE(excluded.first_name, users.first_name),
                cart = excluded.cart,
                updated_at = CURRENT_TIMESTAMP
            """,
            (int(user_id), username, first_name, cart_json),
        )
        conn.commit()


def get_cart(user_id: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT cart FROM users WHERE user_id = ?", (int(user_id),))
        row = cursor.fetchone()

    if row is None:
        return []

    try:
        parsed = json.loads(row["cart"])
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        LOGGER.warning("Некорректный JSON корзины у user_id=%s", user_id)

    return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    init_db()
    print(f"База данных готова: {DB_PATH}")
