"""Тексты бота на трёх языках: ru / uz / en."""
LANGS = {"ru": "🇷🇺 Русский", "uz": "🇺🇿 O'zbekcha", "en": "🇬🇧 English"}

T = {
    # кнопки меню
    "b_sale": {"ru": "💰 Продажа", "uz": "💰 Sotuv", "en": "💰 Sale"},
    "b_made": {"ru": "🥟 Произведено", "uz": "🥟 Tayyorlandi", "en": "🥟 Produced"},
    "b_mine": {"ru": "📋 Мои записи", "uz": "📋 Mening yozuvlarim", "en": "📋 My records"},
    "b_undo": {"ru": "↩️ Отменить последнюю", "uz": "↩️ Oxirgisini bekor qilish", "en": "↩️ Undo last"},
    "b_today": {"ru": "📊 Сегодня", "uz": "📊 Bugun", "en": "📊 Today"},
    "b_week": {"ru": "📅 Неделя", "uz": "📅 Hafta", "en": "📅 Week"},
    "b_month": {"ru": "🗓 Месяц", "uz": "🗓 Oy", "en": "🗓 Month"},
    "b_stock": {"ru": "📦 Остаток", "uz": "📦 Qoldiq", "en": "📦 Stock"},
    "b_workers": {"ru": "👥 Работники", "uz": "👥 Xodimlar", "en": "👥 Workers"},
    "b_products": {"ru": "🧁 Товары", "uz": "🧁 Mahsulotlar", "en": "🧁 Products"},
    "b_invite": {"ru": "➕ Позвать работника", "uz": "➕ Xodim taklif qilish", "en": "➕ Invite worker"},
    "b_export": {"ru": "📤 Экспорт", "uz": "📤 Eksport", "en": "📤 Export"},
    "b_lang": {"ru": "🌐 Язык", "uz": "🌐 Til", "en": "🌐 Language"},
    # общее
    "pick_lang": {"ru": "🌐 Выберите язык\nTilni tanlang\nChoose language", "uz": "🌐 Выберите язык\nTilni tanlang\nChoose language", "en": "🌐 Выберите язык\nTilni tanlang\nChoose language"},
    "hi_admin": {"ru": "Привет, {name}! Вы — администратор.", "uz": "Salom, {name}! Siz — administratorsiz.", "en": "Hi, {name}! You are an administrator."},
    "hi_worker": {"ru": "Привет, {name}! Вы — работник. Нажимайте кнопки внизу.", "uz": "Salom, {name}! Siz — xodimsiz. Pastdagi tugmalarni bosing.", "en": "Hi, {name}! You are a worker. Use the buttons below."},
    "closed": {"ru": "Это закрытый бот учёта. Попросите администратора прислать ссылку-приглашение.", "uz": "Bu yopiq hisob boti. Administratordan taklif havolasini so'rang.", "en": "This is a private accounting bot. Ask the administrator for an invite link."},
    "joined": {"ru": "✅ Вы добавлены как работник. Нажимайте кнопки внизу.", "uz": "✅ Siz xodim sifatida qo'shildingiz. Pastdagi tugmalarni bosing.", "en": "✅ You were added as a worker. Use the buttons below."},
    "invite_bad": {"ru": "Эта ссылка уже использована или неверна. Попросите администратора новую.", "uz": "Bu havola ishlatilgan yoki noto'g'ri. Administratordan yangisini so'rang.", "en": "This link was already used or is invalid. Ask the administrator for a new one."},
    "new_worker": {"ru": "👤 Новый работник: {name} ({id})", "uz": "👤 Yangi xodim: {name} ({id})", "en": "👤 New worker: {name} ({id})"},
    "admin_no_work": {"ru": "Администратор не ведёт продажи — это делают работники.", "uz": "Administrator sotuv yozmaydi — buni xodimlar qiladi.", "en": "Administrators don't record sales — workers do."},
    # корзина
    "cart_sale": {"ru": "💰 Что продано? Нажимайте ➕ у каждого товара, потом «Записать».", "uz": "💰 Nima sotildi? Har bir mahsulotda ➕ bosing, so'ng «Yozish».", "en": "💰 What was sold? Tap ➕ on each item, then «Save»."},
    "cart_made": {"ru": "🥟 Что произведено? Нажимайте ➕ у каждого товара, потом «Записать».", "uz": "🥟 Nima tayyorlandi? Har bir mahsulotda ➕ bosing, so'ng «Yozish».", "en": "🥟 What was produced? Tap ➕ on each item, then «Save»."},
    "save_btn": {"ru": "✅ Записать ({n} шт)", "uz": "✅ Yozish ({n} dona)", "en": "✅ Save ({n} pcs)"},
    "pick_first": {"ru": "Выберите товары ➕", "uz": "Mahsulotlarni tanlang ➕", "en": "Pick items ➕"},
    "cancel_btn": {"ru": "✖️ Отмена", "uz": "✖️ Bekor", "en": "✖️ Cancel"},
    "cancelled": {"ru": "Отменено.", "uz": "Bekor qilindi.", "en": "Cancelled."},
    "stale": {"ru": "Список устарел. Нажмите «Продажа» или «Произведено» заново.", "uz": "Ro'yxat eskirgan. «Sotuv» yoki «Tayyorlandi» ni qayta bosing.", "en": "This list is outdated. Tap «Sale» or «Produced» again."},
    "sale_saved": {"ru": "✅ Продажа записана:", "uz": "✅ Sotuv yozildi:", "en": "✅ Sale saved:"},
    "made_saved": {"ru": "✅ Произведённое записано:", "uz": "✅ Tayyorlangani yozildi:", "en": "✅ Production saved:"},
    "sum": {"ru": "Сумма", "uz": "Summa", "en": "Total"},
    "no_products": {"ru": "Список товаров пуст. Попросите администратора добавить товары.", "uz": "Mahsulotlar ro'yxati bo'sh. Administratordan qo'shishni so'rang.", "en": "The product list is empty. Ask the administrator to add products."},
    "no_products_admin": {"ru": "Список товаров пуст. Нажмите «🧁 Товары» и добавьте.", "uz": "Mahsulotlar ro'yxati bo'sh. «🧁 Mahsulotlar» ni bosib qo'shing.", "en": "The product list is empty. Tap «🧁 Products» to add some."},
    # отмена
    "undone": {"ru": "↩️ Отменено: {p} × {q}", "uz": "↩️ Bekor qilindi: {p} × {q}", "en": "↩️ Undone: {p} × {q}"},
    "nothing_undo": {"ru": "Нечего отменять (можно отменить запись младше часа).", "uz": "Bekor qilinadigan narsa yo'q (faqat 1 soatdan yangi yozuvni).", "en": "Nothing to undo (only records newer than 1 hour)."},
    # отчёты
    "t_mine": {"ru": "Ваши записи сегодня", "uz": "Bugungi yozuvlaringiz", "en": "Your records today"},
    "t_today": {"ru": "Сегодня", "uz": "Bugun", "en": "Today"},
    "t_week": {"ru": "Последние 7 дней", "uz": "Oxirgi 7 kun", "en": "Last 7 days"},
    "t_month": {"ru": "Последние 30 дней", "uz": "Oxirgi 30 kun", "en": "Last 30 days"},
    "t_daily": {"ru": "Итог дня", "uz": "Kun yakuni", "en": "Daily summary"},
    "sold_h": {"ru": "💰 Продано:", "uz": "💰 Sotildi:", "en": "💰 Sold:"},
    "made_h": {"ru": "🥟 Произведено:", "uz": "🥟 Tayyorlandi:", "en": "🥟 Produced:"},
    "no_sales": {"ru": "Продаж пока нет.", "uz": "Hozircha sotuv yo'q.", "en": "No sales yet."},
    "revenue": {"ru": "Выручка", "uz": "Tushum", "en": "Revenue"},
    "by_worker": {"ru": "👥 По работникам:", "uz": "👥 Xodimlar bo'yicha:", "en": "👥 By worker:"},
    "top_h": {"ru": "🏆 Топ товаров:", "uz": "🏆 Eng ko'p sotilgan:", "en": "🏆 Top items:"},
    "best_day": {"ru": "📈 Лучший день: {d} — {q} шт", "uz": "📈 Eng yaxshi kun: {d} — {q} dona", "en": "📈 Best day: {d} — {q} pcs"},
    "peak": {"ru": "⏰ Пик продаж: {h}:00 — {q} шт", "uz": "⏰ Eng band soat: {h}:00 — {q} dona", "en": "⏰ Peak hour: {h}:00 — {q} pcs"},
    "avg": {"ru": "📊 В среднем в день: {n} шт", "uz": "📊 Kuniga o'rtacha: {n} dona", "en": "📊 Average per day: {n} pcs"},
    "pcs": {"ru": "шт", "uz": "dona", "en": "pcs"},
    "stock_h": {"ru": "📦 Остаток за сегодня (произведено − продано):", "uz": "📦 Bugungi qoldiq (tayyorlangan − sotilgan):", "en": "📦 Stock today (produced − sold):"},
    "no_data": {"ru": "Пока нет данных.", "uz": "Hozircha ma'lumot yo'q.", "en": "No data yet."},
    # работники
    "workers_h": {"ru": "Работники (нажмите, чтобы убрать):", "uz": "Xodimlar (olib tashlash uchun bosing):", "en": "Workers (tap to remove):"},
    "no_workers": {"ru": "Работников пока нет. Нажмите «➕ Позвать работника».", "uz": "Xodimlar hali yo'q. «➕ Xodim taklif qilish» ni bosing.", "en": "No workers yet. Tap «➕ Invite worker»."},
    "worker_removed": {"ru": "Работник убран.", "uz": "Xodim olib tashlandi.", "en": "Worker removed."},
    "invite_text": {"ru": "Отправьте работнику эту ссылку (одноразовая):\n{link}\n\nИли добавьте по id: /addworker 123456789 Имя", "uz": "Xodimga shu havolani yuboring (bir martalik):\n{link}\n\nYoki id bo'yicha: /addworker 123456789 Ism", "en": "Send this one-time link to the worker:\n{link}\n\nOr add by id: /addworker 123456789 Name"},
    "addworker_fmt": {"ru": "Формат: /addworker 123456789 Имя", "uz": "Format: /addworker 123456789 Ism", "en": "Format: /addworker 123456789 Name"},
    "worker_added": {"ru": "✅ Работник добавлен.", "uz": "✅ Xodim qo'shildi.", "en": "✅ Worker added."},
    # товары
    "products_h": {"ru": "Товары (нажмите ❌, чтобы убрать).\nЧтобы задать или изменить цену — «➕ Добавить товар» и напишите название и цену (название то же — цена обновится):", "uz": "Mahsulotlar (olib tashlash uchun ❌ bosing).\nNarxni belgilash yoki o'zgartirish uchun «➕ Mahsulot qo'shish» ni bosib, nom va narxni yozing (nom bir xil bo'lsa, narx yangilanadi):", "en": "Products (tap ❌ to remove).\nTo set or change a price tap «➕ Add product» and write the name and price (same name — price is updated):"},
    "add_product": {"ru": "➕ Добавить товар", "uz": "➕ Mahsulot qo'shish", "en": "➕ Add product"},
    "price_unset": {"ru": "цена не задана", "uz": "narx belgilanmagan", "en": "no price"},
    "prod_removed": {"ru": "Товар убран.", "uz": "Mahsulot olib tashlandi.", "en": "Product removed."},
    "addp_prompt": {"ru": "Напишите название и цену одной строкой, например:\nЧебурек с мясом 12000", "uz": "Nom va narxni bir qatorda yozing, masalan:\nGo'shtli cheburek 12000", "en": "Write the name and price in one line, e.g.:\nMeat chebureki 12000"},
    "addp_fmt": {"ru": "Формат: название и цена, например: Чебурек с мясом 12000", "uz": "Format: nom va narx, masalan: Go'shtli cheburek 12000", "en": "Format: name and price, e.g.: Meat chebureki 12000"},
    "product_added": {"ru": "✅ Сохранено: {n} — {p}", "uz": "✅ Saqlandi: {n} — {p}", "en": "✅ Saved: {n} — {p}"},
    # экспорт / прочее
    "export_cap": {"ru": "Записи за 30 дней", "uz": "30 kunlik yozuvlar", "en": "Records for 30 days"},
    "csv_head": {"ru": ["время", "работник", "тип", "товар", "кол-во", "сумма"], "uz": ["vaqt", "xodim", "turi", "mahsulot", "soni", "summa"], "en": ["time", "worker", "type", "product", "qty", "amount"]},
    "csv_sale": {"ru": "продажа", "uz": "sotuv", "en": "sale"},
    "csv_made": {"ru": "произведено", "uz": "tayyorlandi", "en": "produced"},
    "fallback": {"ru": "Нажмите кнопку внизу 👇", "uz": "Pastdagi tugmani bosing 👇", "en": "Tap a button below 👇"},
}

# все переводы одной кнопки — для фильтров
def variants(key):
    return {T[key][l] for l in LANGS}
