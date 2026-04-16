(() => {
    const DELIVERY_PRICE = 500;
    const FALLBACK_API_BASE = "http://127.0.0.1:8080";

    const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
    if (tg) {
        tg.ready();
        tg.expand();
    }

    const state = {
        apiBase: "",
        products: [],
        cart: new Map(),
        category: "all",
        search: "",
        cartOpen: false,
    };

    const els = {
        categories: document.getElementById("categories"),
        products: document.getElementById("products"),
        status: document.getElementById("status"),
        searchInput: document.getElementById("searchInput"),
        cartButton: document.getElementById("cartButton"),
        cartCount: document.getElementById("cartCount"),
        cartPanel: document.getElementById("cartPanel"),
        closeCartButton: document.getElementById("closeCartButton"),
        cartItems: document.getElementById("cartItems"),
        summaryItems: document.getElementById("summaryItems"),
        summaryDelivery: document.getElementById("summaryDelivery"),
        summaryTotal: document.getElementById("summaryTotal"),
        checkoutForm: document.getElementById("checkoutForm"),
    };

    function formatPrice(value) {
        const num = Number(value || 0);
        return `${num.toLocaleString("ru-RU")} ₽`;
    }

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function resolveApiBase() {
        const url = new URL(window.location.href);
        const fromQuery = url.searchParams.get("api");
        const fromStorage = localStorage.getItem("tabakprime_api_base");
        const resolved = (fromQuery || fromStorage || FALLBACK_API_BASE).replace(/\/$/, "");

        if (fromQuery) {
            localStorage.setItem("tabakprime_api_base", resolved);
        }

        return resolved;
    }

    function setStatus(message, isError = false) {
        if (!message) {
            els.status.hidden = true;
            els.status.textContent = "";
            els.status.style.background = "";
            els.status.style.borderColor = "";
            els.status.style.color = "";
            return;
        }

        els.status.hidden = false;
        els.status.textContent = message;

        if (isError) {
            els.status.style.background = "#ffecea";
            els.status.style.borderColor = "#ffbeb7";
            els.status.style.color = "#822f26";
        } else {
            els.status.style.background = "#effbf8";
            els.status.style.borderColor = "#bfebe2";
            els.status.style.color = "#0e544d";
        }
    }

    function showPopup(title, message) {
        if (tg) {
            tg.showPopup({
                title,
                message,
                buttons: [{ type: "ok" }],
            });
        } else {
            window.alert(`${title}\n\n${message}`);
        }
    }

    function getCartCount() {
        let count = 0;
        for (const item of state.cart.values()) {
            count += item.quantity;
        }
        return count;
    }

    function getItemsTotal() {
        let total = 0;
        for (const item of state.cart.values()) {
            total += item.price * item.quantity;
        }
        return total;
    }

    function getGrandTotal() {
        return getItemsTotal() + DELIVERY_PRICE;
    }

    function getFilteredProducts() {
        return state.products.filter((product) => {
            if (state.category !== "all" && product.category !== state.category) {
                return false;
            }

            if (state.search) {
                const q = state.search.toLowerCase();
                const name = String(product.name || "").toLowerCase();
                if (!name.includes(q)) {
                    return false;
                }
            }

            return true;
        });
    }

    function renderCategories() {
        const categories = [
            "all",
            ...Array.from(
                new Set(
                    state.products
                        .map((p) => (p.category || "без категории").trim() || "без категории")
                        .filter(Boolean)
                )
            ),
        ];

        els.categories.innerHTML = categories
            .map((cat) => {
                const active = state.category === cat ? "active" : "";
                const label = cat === "all" ? "Все" : escapeHtml(cat);
                return `<button class="category-chip ${active}" data-category="${escapeHtml(cat)}" type="button">${label}</button>`;
            })
            .join("");

        els.categories.querySelectorAll(".category-chip").forEach((btn) => {
            btn.addEventListener("click", () => {
                state.category = btn.dataset.category || "all";
                renderCategories();
                renderProducts();
            });
        });
    }

    function renderProducts() {
        const filtered = getFilteredProducts();

        if (!filtered.length) {
            els.products.innerHTML = '<div class="empty-state">По этому фильтру товары не найдены.</div>';
            return;
        }

        const html = filtered
            .map((product, idx) => {
                const description = product.description ? escapeHtml(product.description.slice(0, 120)) : "Описание скоро добавим.";
                const category = escapeHtml(product.category || "без категории");
                const name = escapeHtml(product.name || "Без названия");
                const price = formatPrice(product.price || 0);
                const image = product.photo_url
                    ? `<img class="product-media" src="${escapeHtml(product.photo_url)}" alt="${name}" loading="lazy">`
                    : `<div class="product-placeholder">TABAK</div>`;

                return `
                    <article class="product-card" style="animation-delay:${idx * 0.02}s">
                        ${image}
                        <div class="product-body">
                            <span class="product-category">${category}</span>
                            <h3 class="product-title">${name}</h3>
                            <p class="product-description">${description}</p>
                            <div class="product-footer">
                                <strong class="product-price">${price}</strong>
                                <button class="add-button" data-id="${product.id}" type="button">В корзину</button>
                            </div>
                        </div>
                    </article>
                `;
            })
            .join("");

        els.products.innerHTML = html;

        els.products.querySelectorAll(".add-button").forEach((btn) => {
            btn.addEventListener("click", () => {
                addToCart(Number(btn.dataset.id));
            });
        });
    }

    function addToCart(productId) {
        const product = state.products.find((item) => Number(item.id) === Number(productId));
        if (!product) {
            showPopup("Ошибка", "Товар не найден.");
            return;
        }

        const current = state.cart.get(productId);
        if (current) {
            current.quantity += 1;
        } else {
            state.cart.set(productId, {
                id: Number(product.id),
                name: String(product.name || "Без названия"),
                price: Number(product.price || 0),
                quantity: 1,
            });
        }

        updateCartUI();
        showPopup("Добавлено", `${product.name} добавлен в корзину.`);
    }

    function updateCartItem(productId, diff) {
        const item = state.cart.get(productId);
        if (!item) {
            return;
        }

        item.quantity += diff;
        if (item.quantity <= 0) {
            state.cart.delete(productId);
        }

        updateCartUI();
    }

    function removeCartItem(productId) {
        state.cart.delete(productId);
        updateCartUI();
    }

    function renderCartItems() {
        if (!state.cart.size) {
            els.cartItems.innerHTML = '<div class="empty-state">Корзина пока пустая.</div>';
            return;
        }

        const html = Array.from(state.cart.values())
            .map((item) => {
                return `
                    <div class="cart-row">
                        <div>
                            <p class="cart-row-title">${escapeHtml(item.name)}</p>
                            <p class="cart-row-meta">${formatPrice(item.price)} × ${item.quantity} = ${formatPrice(item.price * item.quantity)}</p>
                        </div>
                        <div class="qty-box">
                            <button class="qty-button" type="button" data-action="minus" data-id="${item.id}">−</button>
                            <strong>${item.quantity}</strong>
                            <button class="qty-button" type="button" data-action="plus" data-id="${item.id}">+</button>
                            <button class="remove-button" type="button" data-action="remove" data-id="${item.id}">×</button>
                        </div>
                    </div>
                `;
            })
            .join("");

        els.cartItems.innerHTML = html;

        els.cartItems.querySelectorAll("button[data-action]").forEach((button) => {
            button.addEventListener("click", () => {
                const id = Number(button.dataset.id);
                const action = button.dataset.action;
                if (action === "plus") {
                    updateCartItem(id, 1);
                } else if (action === "minus") {
                    updateCartItem(id, -1);
                } else if (action === "remove") {
                    removeCartItem(id);
                }
            });
        });
    }

    function updateMainButton() {
        if (!tg || !tg.MainButton) {
            return;
        }

        const count = getCartCount();
        if (count <= 0) {
            tg.MainButton.hide();
            return;
        }

        if (state.cartOpen) {
            tg.MainButton.setText(`Оформить за ${formatPrice(getGrandTotal())}`);
        } else {
            tg.MainButton.setText(`Корзина: ${count} шт.`);
        }

        tg.MainButton.show();
    }

    function updateCartUI() {
        const count = getCartCount();
        els.cartCount.textContent = String(count);

        const itemsTotal = getItemsTotal();
        const grandTotal = itemsTotal + DELIVERY_PRICE;

        els.summaryItems.textContent = formatPrice(itemsTotal);
        els.summaryDelivery.textContent = formatPrice(DELIVERY_PRICE);
        els.summaryTotal.textContent = formatPrice(grandTotal);

        renderCartItems();
        updateMainButton();
    }

    function openCart() {
        state.cartOpen = true;
        els.cartPanel.classList.add("open");
        els.cartPanel.setAttribute("aria-hidden", "false");

        if (tg && tg.BackButton) {
            tg.BackButton.show();
        }

        updateMainButton();
    }

    function closeCart() {
        state.cartOpen = false;
        els.cartPanel.classList.remove("open");
        els.cartPanel.setAttribute("aria-hidden", "true");

        if (tg && tg.BackButton) {
            tg.BackButton.hide();
        }

        updateMainButton();
    }

    function collectOrderPayload() {
        const city = document.getElementById("cityInput").value.trim();
        const address = document.getElementById("addressInput").value.trim();
        const name = document.getElementById("nameInput").value.trim();
        const phone = document.getElementById("phoneInput").value.trim();

        if (!city || !address || !name || !phone) {
            showPopup("Незаполненные поля", "Введите город, адрес, имя и телефон.");
            return null;
        }

        if (!/^\+?[0-9\s()\-]{7,20}$/.test(phone)) {
            showPopup("Проверьте телефон", "Введите телефон в корректном формате.");
            return null;
        }

        const items = Array.from(state.cart.values()).map((item) => ({
            id: item.id,
            quantity: item.quantity,
        }));

        const total = getItemsTotal();

        return {
            action: "order",
            items,
            total,
            delivery: DELIVERY_PRICE,
            city,
            address,
            name,
            phone,
        };
    }

    function submitOrder() {
        if (!state.cart.size) {
            showPopup("Корзина пуста", "Добавьте товары перед оформлением.");
            return;
        }

        const payload = collectOrderPayload();
        if (!payload) {
            return;
        }

        if (!tg) {
            showPopup("Тестовый режим", `Данные заказа:\n${JSON.stringify(payload, null, 2)}`);
            return;
        }

        tg.sendData(JSON.stringify(payload));
        showPopup("Заказ отправлен", `Итого: ${formatPrice(getGrandTotal())}. Менеджер скоро свяжется с вами.`);

        state.cart.clear();
        updateCartUI();
        closeCart();
        els.checkoutForm.reset();
    }

    async function loadProducts() {
        state.apiBase = resolveApiBase();
        setStatus(`Загрузка каталога из ${state.apiBase}/products...`);

        try {
            const response = await fetch(`${state.apiBase}/products`, {
                method: "GET",
                cache: "no-store",
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const payload = await response.json();
            if (!payload || payload.ok !== true || !Array.isArray(payload.products)) {
                throw new Error("Некорректный формат ответа API");
            }

            state.products = payload.products.map((product) => ({
                id: Number(product.id),
                name: String(product.name || "Без названия"),
                description: String(product.description || ""),
                price: Number(product.price || 0),
                category: String(product.category || "без категории"),
                photo_url: product.photo_url || "",
            }));

            if (!state.products.length) {
                setStatus("Каталог пока пуст. Сначала запустите parser.py для наполнения базы.");
            } else {
                setStatus("");
            }

            renderCategories();
            renderProducts();
        } catch (error) {
            console.error(error);
            setStatus(
                `Не удалось загрузить товары. Проверьте, что bot.py запущен и API доступно по ${state.apiBase}/products`,
                true
            );
            state.products = [];
            renderCategories();
            renderProducts();
        }
    }

    function bindEvents() {
        els.searchInput.addEventListener("input", (event) => {
            state.search = String(event.target.value || "").trim();
            renderProducts();
        });

        els.cartButton.addEventListener("click", () => {
            if (!state.cart.size) {
                showPopup("Корзина пуста", "Добавьте товары из каталога.");
                return;
            }
            openCart();
        });

        els.closeCartButton.addEventListener("click", closeCart);

        els.cartPanel.addEventListener("click", (event) => {
            if (event.target === els.cartPanel) {
                closeCart();
            }
        });

        els.checkoutForm.addEventListener("submit", (event) => {
            event.preventDefault();
            submitOrder();
        });

        if (tg && tg.MainButton) {
            tg.MainButton.onClick(() => {
                if (state.cartOpen) {
                    submitOrder();
                } else if (state.cart.size) {
                    openCart();
                } else {
                    showPopup("Корзина пуста", "Добавьте товары из каталога.");
                }
            });
        }

        if (tg && tg.BackButton) {
            tg.BackButton.onClick(() => {
                if (state.cartOpen) {
                    closeCart();
                }
            });
        }
    }

    bindEvents();
    updateCartUI();
    loadProducts();
})();
