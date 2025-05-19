#!/usr/bin/env python3
from aiogram import Bot, Dispatcher, types
from database import SessionLocal, Ad, User, AdChat, Sale, AdComplaint
from config import MAIN_CATEGORIES, CITY_STRUCTURE
from utils import main_menu_keyboard

def register_search_handlers(bot: Bot, dp: Dispatcher, user_steps: dict):
    """
    Поиск объявлений с логикой:
      1) Выбор региона
      2) Выбор конкретного города/округа
      3) Выбор категории (или все)
      4) Выбор подкатегории (или пропустить)
      5) Поиск, постраничный вывод (10 на страницу)
      6) Кнопки «купить», «детали», «написать продавцу», и теперь:
         - если сделка завершена => «Оставить отзыв»
         - иначе => «Пожаловаться»
    """

    @dp.message(func=lambda m: m.text == "🔍Поиск объявлений")
    async def start_search_flow(message: types.Message):
        chat_id = message.chat.id
        user_steps[chat_id] = {
            "mode": "search_flow",
            "picked_region": None,
            "region_list": [],
            "city_list": [],
            "city": None,
            "use_region_wide": False,
            "is_custom_city": False,
            "category": None,
            "subcat_list": [],
            "subcategory": None,
            "search_results": [],
            "shown_count": 0
        }
        await ask_for_region(chat_id)

    # ====================== Шаг 1: Выбор региона ======================
    async def ask_for_region(chat_id):
        region_names = list(CITY_STRUCTURE.keys())
        user_steps[chat_id]["region_list"] = region_names

        kb = types.InlineKeyboardMarkup()
        for i, reg_name in enumerate(region_names):
            cb_data = f"srch_region_{i}"
            kb.add(types.InlineKeyboardButton(text=reg_name, callback_data=cb_data))
        kb.add(types.InlineKeyboardButton(text="Добавить свой город", callback_data="srch_city_custom"))
        kb.add(types.InlineKeyboardButton(text="Пропустить город", callback_data="srch_city_skip"))
        kb.add(types.InlineKeyboardButton(text="Отмена", callback_data="srch_cancel"))

        txt = "1) Выберите регион или «Добавить свой город», либо «Пропустить»:"
        await bot.send_message(chat_id, txt, reply_markup=kb)

    @dp.callback_query(func=lambda call:
        call.data.startswith("srch_region_") or
        call.data in ("srch_city_custom", "srch_city_skip", "srch_cancel"))
    async def handle_region_choice(call: types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps or user_steps[chat_id]["mode"] != "search_flow":
            return await bot.answer_callback_query(call.id, "Нет активного поиска", show_alert=True)

        st = user_steps[chat_id]

        if call.data == "srch_cancel":
            # Отмена
            await bot.delete_message(chat_id, call.message.message_id)
            await bot.answer_callback_query(call.id, "Поиск отменён.")
            await bot.send_message(chat_id, "Поиск отменён.", reply_markup=main_menu_keyboard())
            user_steps.pop(chat_id, None)
            return None

        if call.data == "srch_city_skip":
            # Пропустить город
            await bot.delete_message(chat_id, call.message.message_id)
            await bot.answer_callback_query(call.id, "Без города")
            st["city"] = None
            st["use_region_wide"] = False
            st["is_custom_city"] = False
            return await ask_for_category(chat_id)

        if call.data == "srch_city_custom":
            # Пользователь вводит свой город вручную
            await bot.delete_message(chat_id, call.message.message_id)
            await bot.answer_callback_query(call.id)
            msg = await bot.send_message(chat_id, "Введите свой город (поиск будет по частичному совпадению):")
            return await bot.register_next_step_handler(msg, process_custom_city)

        # srch_region_{i}
        if call.data.startswith("srch_region_"):
            idx_str = call.data.replace("srch_region_", "")
            try:
                idx = int(idx_str)
            except:
                return await bot.answer_callback_query(call.id, "Ошибка индекса региона", show_alert=True)
            regions = st["region_list"]
            if idx < 0 or idx >= len(regions):
                return await bot.answer_callback_query(call.id, "Недопустимый индекс", show_alert=True)

            chosen_region = regions[idx]
            st["picked_region"] = chosen_region
            st["is_custom_city"] = False

            await bot.delete_message(chat_id, call.message.message_id)
            await bot.answer_callback_query(call.id, f"Регион: {chosen_region}")
            return await show_city_list(chat_id)
        else:
            return None

    async def process_custom_city(message: types.Message):
        chat_id = message.chat.id
        if chat_id not in user_steps or user_steps[chat_id]["mode"] != "search_flow":
            return

        city_str = message.text.strip()
        st = user_steps[chat_id]
        st["city"] = city_str
        st["use_region_wide"] = False
        st["is_custom_city"] = True

        await ask_for_category(chat_id)

    # ====================== Шаг 2: Выбор города/округа ======================
    async def show_city_list(chat_id):
        st = user_steps[chat_id]
        region_name = st["picked_region"]
        city_list = CITY_STRUCTURE.get(region_name, [])
        st["city_list"] = city_list

        kb = types.InlineKeyboardMarkup()
        # «По всему региону»
        kb.add(types.InlineKeyboardButton(text=f"По всему региону «{region_name}»", callback_data="srch_wide_region"))

        for i, c_name in enumerate(city_list):
            cb = f"srch_city_{i}"
            kb.add(types.InlineKeyboardButton(text=c_name, callback_data=cb))
        kb.add(types.InlineKeyboardButton(text="Назад к регионам", callback_data="srch_back_regions"))

        txt = f"Регион: {region_name}\nВыберите конкретный округ или «По всему региону»:"
        await bot.send_message(chat_id, txt, reply_markup=kb)

    @dp.callback_query(func=lambda call:
        call.data.startswith("srch_city_") or
        call.data in ("srch_wide_region", "srch_back_regions"))
    async def handle_city_selection(call: types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps or user_steps[chat_id]["mode"] != "search_flow":
            return await bot.answer_callback_query(call.id, "Нет активного поиска", show_alert=True)

        st = user_steps[chat_id]

        if call.data == "srch_back_regions":
            await bot.delete_message(chat_id, call.message.message_id)
            await bot.answer_callback_query(call.id)
            return await ask_for_region(chat_id)

        if call.data == "srch_wide_region":
            region_name = st["picked_region"]
            st["city"] = region_name
            st["use_region_wide"] = True
            st["is_custom_city"] = False
            await bot.delete_message(chat_id, call.message.message_id)
            await bot.answer_callback_query(call.id, f"По всему региону: {region_name}")
            return await ask_for_category(chat_id)

        if call.data.startswith("srch_city_"):
            idx_str = call.data.replace("srch_city_", "")
            try:
                idx = int(idx_str)
            except:
                return await bot.answer_callback_query(call.id, "Ошибка индекса города", show_alert=True)
            c_list = st["city_list"]
            if idx < 0 or idx >= len(c_list):
                return await bot.answer_callback_query(call.id, "Недопустимый индекс города", show_alert=True)

            chosen_city = c_list[idx]
            region = st["picked_region"]
            st["city"] = f"{region} | {chosen_city}"
            st["use_region_wide"] = False
            st["is_custom_city"] = False

            await bot.delete_message(chat_id, call.message.message_id)
            await bot.answer_callback_query(call.id, f"Город: {region} | {chosen_city}")
            return await ask_for_category(chat_id)
        else:
            return None

    # ====================== Шаг 3: Выбор категории ======================
    async def ask_for_category(chat_id):
        kb = types.InlineKeyboardMarkup(row_width=2)
        for cat_name in MAIN_CATEGORIES.keys():
            cb = f"srch_cat_{cat_name}"
            kb.add(types.InlineKeyboardButton(text=cat_name, callback_data=cb))
        kb.add(types.InlineKeyboardButton(text="Все категории", callback_data="srch_cat_all"))
        kb.add(types.InlineKeyboardButton(text="Отмена", callback_data="srch_cancel"))

        await bot.send_message(chat_id, "3) Выберите категорию или «Все категории»:", reply_markup=kb)

    @dp.callback_query(func=lambda call:
        call.data.startswith("srch_cat_") or
        call.data in ("srch_cat_all", "srch_cancel"))
    async def handle_category_choice(call: types.CallbackQuery):
        chat_id = call.message.chat.id
        st = user_steps.get(chat_id)
        if not st or st.get("mode") != "search_flow":
            return await bot.answer_callback_query(call.id, "Нет активного поиска", show_alert=True)

        if call.data == "srch_cancel":
            await bot.delete_message(chat_id, call.message.message_id)
            await bot.answer_callback_query(call.id, "Поиск отменён.")
            await bot.send_message(chat_id, "Поиск отменён.", reply_markup=main_menu_keyboard())
            user_steps.pop(chat_id, None)
            return None

        if call.data == "srch_cat_all":
            await bot.delete_message(chat_id, call.message.message_id)
            await bot.answer_callback_query(call.id, "Все категории")
            st["category"] = None
            st["subcat_list"] = []
            st["subcategory"] = None
            return await do_search(chat_id)

        # выбор конкретной категории
        if call.data.startswith("srch_cat_"):
            cat_name = call.data.replace("srch_cat_", "")
            st["category"] = cat_name

            # раскладываем все записи из MAIN_CATEGORIES[cat_name] по запятым
            raw_list = MAIN_CATEGORIES.get(cat_name, [])
            flat = []
            for entry in raw_list:
                parts = [p.strip() for p in entry.split(",") if p.strip()]
                flat.extend(parts)
            st["subcat_list"] = flat

            await bot.delete_message(chat_id, call.message.message_id)
            await bot.answer_callback_query(call.id, f"Категория: {cat_name}")

            if not flat:
                # если в категории нет подкатегорий
                st["subcategory"] = None
                return await do_search(chat_id)
            else:
                return await ask_for_subcategory(chat_id, cat_name)
        else:
            return None

    async def ask_for_subcategory(chat_id, cat_name: str):
        """
        Шаг 4: предста­вляем пользователю список подкатегорий,
        каждая из которых уже — отдельная кнопка.
        """
        st = user_steps[chat_id]
        sub_list = st.get("subcat_list", [])

        kb = types.InlineKeyboardMarkup(row_width=2)
        for i, name in enumerate(sub_list):
            kb.add(types.InlineKeyboardButton(text=name, callback_data=f"srch_subcat_{i}"))
        kb.add(types.InlineKeyboardButton(text="Пропустить", callback_data="srch_subcat_skip"))
        kb.add(types.InlineKeyboardButton(text="Отмена", callback_data="srch_cancel"))

        await bot.send_message(
            chat_id,
            f"Категория «{cat_name}»: выберите подкатегорию:",
            reply_markup=kb
        )


    @dp.callback_query(func=lambda call:
        call.data.startswith("srch_subcat_") or call.data == "srch_subcat_skip")
    async def handle_subcat_choice(call: types.CallbackQuery):
        chat_id = call.message.chat.id
        st = user_steps.get(chat_id)
        if not st or st.get("mode") != "search_flow":
            return await bot.answer_callback_query(call.id, "Нет активного поиска", show_alert=True)

        if call.data == "srch_subcat_skip":
            st["subcategory"] = None
            await bot.answer_callback_query(call.id, "Подкатегория пропущена.")
        else:
            idx = int(call.data.replace("srch_subcat_", ""))
            sub_list = st.get("subcat_list", [])
            if 0 <= idx < len(sub_list):
                chosen = sub_list[idx]
                st["subcategory"] = chosen
                await bot.answer_callback_query(call.id, f"Подкатегория: {chosen}")
            else:
                return await bot.answer_callback_query(call.id, "Некорректный индекс", show_alert=True)

        await bot.delete_message(chat_id, call.message.message_id)
        return await do_search(chat_id)

    # ====================== Шаг 4: Поиск ======================
    async def do_search(chat_id):
        st = user_steps[chat_id]
        city = st["city"]
        region_ok = st["use_region_wide"]
        is_custom = st["is_custom_city"]
        cat = st["category"]
        subcat = st["subcategory"]

        with SessionLocal() as sess:
            # Берём только одобренные и активные объявления
            q = sess.query(Ad).filter(
                Ad.status == "approved",
                Ad.is_active == True
            )

            # --- фильтрация по месту -------------------------------
            if city is not None:
                if is_custom:
                    q = q.filter(Ad.city.ilike(f"%{city}%"))
                elif region_ok:
                    q = q.filter(Ad.city.ilike(f"{city}%"))
                else:
                    q = q.filter(Ad.city == city)

            # --- фильтрация по категории ---------------------------
            if cat:
                q = q.filter(Ad.category == cat)
            if subcat:
                q = q.filter(Ad.subcategory == subcat)

            ads_found = q.order_by(Ad.created_at.desc()).all()

        st["search_results"] = ads_found
        st["shown_count"] = 0

        if not ads_found:
            await bot.send_message(
                chat_id,
                "Ничего не найдено по заданным критериям.",
                reply_markup=main_menu_keyboard()
            )
            user_steps.pop(chat_id, None)
            return

        # Показываем первые 10
        slice_ = ads_found[:10]
        st["shown_count"] = len(slice_)

        kb = build_results_kb(chat_id, slice_)
        text = f"Найдено объявлений: {len(ads_found)}.\nВыберите:"
        sent = await bot.send_message(chat_id, text, reply_markup=kb)

        st["last_list_msg_id"] = sent.message_id

    def build_results_kb(chat_id, ads_slice):
        st = user_steps[chat_id]
        kb = types.InlineKeyboardMarkup()
        for ad_obj in ads_slice:
            # Текст кнопки
            label = ad_obj.inline_button_text or (ad_obj.text[:15] + "...")
            cb_data = f"srch_openad_{ad_obj.id}"
            kb.add(types.InlineKeyboardButton(text=label, callback_data=cb_data))
        if st["shown_count"] < len(st["search_results"]):
            kb.add(types.InlineKeyboardButton(text="Показать ещё", callback_data="srch_show_more"))
        return kb

    @dp.callback_query(func=lambda call: call.data == "srch_show_more")
    async def handle_show_more(call: types.CallbackQuery):
        chat_id = call.message.chat.id
        st = user_steps.get(chat_id)
        if not st or st["mode"] != "search_flow":
            return await bot.answer_callback_query(call.id, "Нет активного поиска", show_alert=True)

        ads = st["search_results"]
        shown = st["shown_count"]
        slice_ = ads[shown: shown + 10]
        if not slice_:
            return await bot.answer_callback_query(call.id, "Больше объявлений нет.", show_alert=True)

        st["shown_count"] += len(slice_)
        kb = build_results_kb(chat_id, slice_)

        try:
            await bot.edit_message_reply_markup(chat_id,
                                                st["last_list_msg_id"],
                                                reply_markup=kb)
        except:
            sent = await bot.send_message(chat_id, "Дополнительные объявления:", reply_markup=kb)
            st["last_list_msg_id"] = sent.message_id  # вдруг старое нельзя было редактировать

        return await bot.answer_callback_query(call.id)

    # ================== Показ одного объявления ==================
    @dp.callback_query(func=lambda call: call.data.startswith("srch_openad_"))
    async def handle_open_ad(call: types.CallbackQuery):
        ad_id = int(call.data.replace("srch_openad_", ""))
        chat_id = call.message.chat.id
        st = user_steps.get(chat_id)

        # --- берём объявление и продавца -----------------------
        with SessionLocal() as sess:
            ad_obj = sess.query(Ad).filter(
                Ad.id == ad_id,
                Ad.status == "approved",
                Ad.is_active == True
            ).first()

            if not ad_obj:
                return await bot.answer_callback_query(
                    call.id,
                    "Объявление не найдено или деактивировано.",
                    show_alert=True
                )

            user_obj = sess.query(User).filter_by(id=ad_obj.user_id).first() or User(id=ad_obj.user_id)
            sale_done = sess.query(Sale).filter_by(
                ad_id=ad_id,
                buyer_id=call.from_user.id,
                status="completed"
            ).first()

        # --- формируем текст и клавиатуру ---------------------
        cat_info = ad_obj.category or "—"
        if ad_obj.subcategory:
            cat_info += f" / {ad_obj.subcategory}"
        city_info = ad_obj.city or "—"

        caption = (
            f"{ad_obj.text}\n\n"
            f"Цена: {ad_obj.price} руб.\n"
            f"Кол-во: {ad_obj.quantity}\n"
            f"Категория: {cat_info}\n"
            f"Город: {city_info}\n\n"
            f"Продавец: @{user_obj.username or user_obj.id}\n"
            "Нажмите «Купить», чтобы оформить сделку через бота."
        )

        kb = types.InlineKeyboardMarkup(row_width=2)
        buy_lbl = f"Купить «{ad_obj.inline_button_text}»" if ad_obj.inline_button_text else "Купить"
        kb.add(
            types.InlineKeyboardButton(text=buy_lbl, callback_data=f"buy_ad_{ad_obj.id}"),
            types.InlineKeyboardButton(text="Подробнее", callback_data=f"details_ad_{ad_obj.id}")
        )
        kb.add(types.InlineKeyboardButton(text="Написать продавцу", callback_data=f"write_seller_ad_{ad_obj.id}"))

        if sale_done:
            kb.add(types.InlineKeyboardButton(text="Оставить отзыв", callback_data=f"feedback_ad_{ad_obj.id}"))
        else:
            kb.add(types.InlineKeyboardButton(text="Пожаловаться", callback_data=f"complain_ad_{ad_obj.id}"))

        kb.add(types.InlineKeyboardButton(text="Отзывы о продавце", callback_data=f"viewfeedback_seller_{user_obj.id}"))

        # --- выводим фото или текст ---------------------------
        photos = [p for p in (ad_obj.photos or "").split(",") if p]
        if photos:
            media = [types.InputMediaPhoto(media=photos[0], caption=caption)]
            media.extend(types.InputMediaPhoto(media=p) for p in photos[1:])
            await bot.send_media_group(chat_id, media)
            await bot.send_message(chat_id, "Выберите действие:", reply_markup=kb)
        else:
            await bot.send_message(chat_id, caption, reply_markup=kb)

        await bot.answer_callback_query(call.id)

        # --- обновляем «список объявлений» под сообщением ------
        if st and "last_list_msg_id" in st:
            try:
                await bot.delete_message(chat_id, st["last_list_msg_id"])
            except:
                pass

            slice_ = st["search_results"][:st["shown_count"]]
            new_kb = build_results_kb(chat_id, slice_)
            txt = f"Найдено объявлений: {len(st['search_results'])}.\nВыберите:"
            new_msg = await bot.send_message(chat_id, txt, reply_markup=new_kb)
            st["last_list_msg_id"] = new_msg.message_id
        return None

    # =============== Пожаловаться ================
    @dp.callback_query(func=lambda call: call.data.startswith("complain_ad_"))
    async def complain_about_ad(call: types.CallbackQuery):
        """
        Пользователь жалуется на объявление (не купил или сделка не завершена).
        Сохраняем в AdComplaint, уведомляем админов.
        """
        ad_id_str = call.data.replace("complain_ad_", "")
        try:
            ad_id = int(ad_id_str)
        except:
            return await bot.answer_callback_query(call.id, "Некорректный ID объявления", show_alert=True)

        user_id = call.from_user.id
        await bot.answer_callback_query(call.id)
        # Просим текст жалобы
        msg = await bot.send_message(user_id, "Опишите причину/суть жалобы:")
        user_steps[user_id] = {"complaint_ad_id": ad_id}
        return await bot.register_next_step_handler(msg, process_complaint_text)

    async def process_complaint_text(message: types.Message):
        user_id = message.chat.id
        if user_id not in user_steps or "complaint_ad_id" not in user_steps[user_id]:
            return await bot.send_message(user_id, "Ошибка: нет информации об объявлении. Жалоба отменена.")

        ad_id = user_steps[user_id]["complaint_ad_id"]
        text_of_complaint = message.text.strip()

        from database import AdComplaint
        with SessionLocal() as sess:
            ad_obj = sess.query(Ad).filter_by(id=ad_id).first()
            if not ad_obj:
                await bot.send_message(user_id, "Объявление не найдено, жалоба отменена.")
                user_steps.pop(user_id, None)
                return None

            # Создаём жалобу
            complaint = AdComplaint(
                ad_id=ad_id,
                user_id=user_id,
                text=text_of_complaint,
                status="new"
            )
            sess.add(complaint)
            sess.commit()
            c_id = complaint.id

            # Уведомляем админов
            # Соберите нужный ID группы или список админов
            ADMIN_COMPLAINT_CHAT_ID = -1002288960086  # или ваш ID группы
            kb_admin = types.InlineKeyboardMarkup()
            kb_admin.add(
                types.InlineKeyboardButton(text="Написать продавцу", callback_data=f"complaint_msg_seller_{c_id}"),
                types.InlineKeyboardButton(text="Удалить объявление", callback_data=f"complaint_del_ad_{c_id}"),
                types.InlineKeyboardButton(text="Заблокировать пользователя", callback_data=f"complaint_ban_{c_id}")
            )

            await bot.send_message(
                ADMIN_COMPLAINT_CHAT_ID,
                f"Поступила жалоба #{c_id} на объявление #{ad_id}.\n"
                f"От пользователя #{user_id}.\n"
                f"Текст жалобы:\n{text_of_complaint}\n\n"
                "Действия:",
                reply_markup=kb_admin
            )

        await bot.send_message(user_id, "Ваша жалоба отправлена администраторам.")
        user_steps.pop(user_id, None)
        return None

    # =============== «Написать продавцу» в объявлении ================
    @dp.callback_query(func=lambda call: call.data.startswith("write_seller_ad_"))
    async def handle_write_seller(call: types.CallbackQuery):
        """
        Создаёт (или находит) AdChat и шлёт обеим сторонам кнопку «Открыть / Ответить».
        Исправлено:
          • избегаем DetachedInstanceError — сохраняем chat_id до выхода из сессии;
          • страхуем, что записи о пользователях есть в таблице users.
        """
        buyer_id = call.from_user.id
        buyer_name = call.from_user.username
        ad_id = int(call.data.replace("write_seller_ad_", ""))

        # ---------- работа с БД -------------
        with SessionLocal() as sess:

            # 1. объявление
            ad = sess.query(Ad).filter_by(id=ad_id, status="approved").first()
            if not ad or ad.user_id == buyer_id:
                return await bot.answer_callback_query(
                    call.id, "Объявление не найдено или это ваше объявление.",
                    show_alert=True
                )
            seller_id = ad.user_id

            # 2. гарантируем наличие пользователей
            def ensure_user(u_id, uname=None):
                row = sess.query(User).filter_by(id=u_id).first()
                if not row:
                    row = User(id=u_id, username=uname)
                    sess.add(row)
                elif uname and row.username != uname:
                    row.username = uname
                return row

            ensure_user(buyer_id, buyer_name)
            ensure_user(seller_id)

            # 3. находим / создаём чат
            chat = (sess.query(AdChat)
                    .filter_by(ad_id=ad_id,
                               buyer_id=buyer_id,
                               seller_id=seller_id)
                    .first())
            if not chat:
                chat = AdChat(ad_id=ad_id,
                              buyer_id=buyer_id,
                              seller_id=seller_id,
                              status="open")
                sess.add(chat)

            sess.commit()
            chat_id_db = chat.id  # <‑‑ сохраняем до выхода из with‑блока

        # ---------- UI -------------
        kb_open = types.InlineKeyboardMarkup(row_width=1)
        kb_open.add(
            types.InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"open_chat_{chat_id_db}"),
            types.InlineKeyboardButton(text="✏️ Ответить", callback_data=f"chat_write_{chat_id_db}")
        )

        # покупателю
        await bot.send_message(
            buyer_id,
            f"Открыт чат #{chat_id_db} с продавцом (объявление #{ad_id}).",
            reply_markup=kb_open
        )

        # продавцу
        mention = f"@{buyer_name}" if buyer_name else f"#{buyer_id}"
        await bot.send_message(
            seller_id,
            f"Покупатель {mention} написал вам по объявлению #{ad_id}.",
            reply_markup=kb_open
        )

        return await bot.answer_callback_query(call.id, "Чат создан.")
