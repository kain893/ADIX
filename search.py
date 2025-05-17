#!/usr/bin/env python3
import telebot
from telebot import types
from database import SessionLocal, Ad, User, AdChat, Sale, AdComplaint
from config import MAIN_CATEGORIES, CITY_STRUCTURE
from utils import main_menu_keyboard

def register_search_handlers(bot: telebot.TeleBot, user_steps: dict):
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

    @bot.message_handler(func=lambda m: m.text == "🔍Поиск объявлений")
    def start_search_flow(message: telebot.types.Message):
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
        ask_for_region(chat_id)

    # ====================== Шаг 1: Выбор региона ======================
    def ask_for_region(chat_id):
        region_names = list(CITY_STRUCTURE.keys())
        user_steps[chat_id]["region_list"] = region_names

        kb = types.InlineKeyboardMarkup()
        for i, reg_name in enumerate(region_names):
            cb_data = f"srch_region_{i}"
            kb.add(types.InlineKeyboardButton(reg_name, callback_data=cb_data))
        kb.add(types.InlineKeyboardButton("Добавить свой город", callback_data="srch_city_custom"))
        kb.add(types.InlineKeyboardButton("Пропустить город", callback_data="srch_city_skip"))
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="srch_cancel"))

        txt = "1) Выберите регион или «Добавить свой город», либо «Пропустить»:"
        bot.send_message(chat_id, txt, reply_markup=kb)

    @bot.callback_query_handler(func=lambda call:
        call.data.startswith("srch_region_") or
        call.data in ("srch_city_custom", "srch_city_skip", "srch_cancel"))
    def handle_region_choice(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps or user_steps[chat_id]["mode"] != "search_flow":
            bot.answer_callback_query(call.id, "Нет активного поиска", show_alert=True)
            return

        st = user_steps[chat_id]

        if call.data == "srch_cancel":
            # Отмена
            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id, "Поиск отменён.")
            bot.send_message(chat_id, "Поиск отменён.", reply_markup=main_menu_keyboard())
            user_steps.pop(chat_id, None)
            return

        if call.data == "srch_city_skip":
            # Пропустить город
            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id, "Без города")
            st["city"] = None
            st["use_region_wide"] = False
            st["is_custom_city"] = False
            ask_for_category(chat_id)
            return

        if call.data == "srch_city_custom":
            # Пользователь вводит свой город вручную
            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id)
            msg = bot.send_message(chat_id, "Введите свой город (поиск будет по частичному совпадению):")
            bot.register_next_step_handler(msg, process_custom_city)
            return

        # srch_region_{i}
        if call.data.startswith("srch_region_"):
            idx_str = call.data.replace("srch_region_", "")
            try:
                idx = int(idx_str)
            except:
                bot.answer_callback_query(call.id, "Ошибка индекса региона", show_alert=True)
                return
            regions = st["region_list"]
            if idx < 0 or idx >= len(regions):
                bot.answer_callback_query(call.id, "Недопустимый индекс", show_alert=True)
                return

            chosen_region = regions[idx]
            st["picked_region"] = chosen_region
            st["is_custom_city"] = False

            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id, f"Регион: {chosen_region}")
            show_city_list(chat_id)

    def process_custom_city(message: telebot.types.Message):
        chat_id = message.chat.id
        if chat_id not in user_steps or user_steps[chat_id]["mode"] != "search_flow":
            return

        city_str = message.text.strip()
        st = user_steps[chat_id]
        st["city"] = city_str
        st["use_region_wide"] = False
        st["is_custom_city"] = True

        ask_for_category(chat_id)

    # ====================== Шаг 2: Выбор города/округа ======================
    def show_city_list(chat_id):
        st = user_steps[chat_id]
        region_name = st["picked_region"]
        city_list = CITY_STRUCTURE.get(region_name, [])
        st["city_list"] = city_list

        kb = types.InlineKeyboardMarkup()
        # «По всему региону»
        kb.add(types.InlineKeyboardButton(f"По всему региону «{region_name}»", callback_data="srch_wide_region"))

        for i, c_name in enumerate(city_list):
            cb = f"srch_city_{i}"
            kb.add(types.InlineKeyboardButton(c_name, callback_data=cb))
        kb.add(types.InlineKeyboardButton("Назад к регионам", callback_data="srch_back_regions"))

        txt = f"Регион: {region_name}\nВыберите конкретный округ или «По всему региону»:"
        bot.send_message(chat_id, txt, reply_markup=kb)

    @bot.callback_query_handler(func=lambda call:
        call.data.startswith("srch_city_") or
        call.data in ("srch_wide_region", "srch_back_regions"))
    def handle_city_selection(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps or user_steps[chat_id]["mode"] != "search_flow":
            bot.answer_callback_query(call.id, "Нет активного поиска", show_alert=True)
            return

        st = user_steps[chat_id]

        if call.data == "srch_back_regions":
            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id)
            ask_for_region(chat_id)
            return

        if call.data == "srch_wide_region":
            region_name = st["picked_region"]
            st["city"] = region_name
            st["use_region_wide"] = True
            st["is_custom_city"] = False
            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id, f"По всему региону: {region_name}")
            ask_for_category(chat_id)
            return

        if call.data.startswith("srch_city_"):
            idx_str = call.data.replace("srch_city_", "")
            try:
                idx = int(idx_str)
            except:
                bot.answer_callback_query(call.id, "Ошибка индекса города", show_alert=True)
                return
            c_list = st["city_list"]
            if idx < 0 or idx >= len(c_list):
                bot.answer_callback_query(call.id, "Недопустимый индекс города", show_alert=True)
                return

            chosen_city = c_list[idx]
            region = st["picked_region"]
            st["city"] = f"{region} | {chosen_city}"
            st["use_region_wide"] = False
            st["is_custom_city"] = False

            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id, f"Город: {region} | {chosen_city}")
            ask_for_category(chat_id)

    # ====================== Шаг 3: Выбор категории ======================
    def ask_for_category(chat_id):
        kb = types.InlineKeyboardMarkup(row_width=2)
        for cat_name in MAIN_CATEGORIES.keys():
            cb = f"srch_cat_{cat_name}"
            kb.add(types.InlineKeyboardButton(cat_name, callback_data=cb))
        kb.add(types.InlineKeyboardButton("Все категории", callback_data="srch_cat_all"))
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="srch_cancel"))

        bot.send_message(chat_id, "3) Выберите категорию или «Все категории»:", reply_markup=kb)

    @bot.callback_query_handler(func=lambda call:
        call.data.startswith("srch_cat_") or
        call.data in ("srch_cat_all", "srch_cancel"))
    def handle_category_choice(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        st = user_steps.get(chat_id)
        if not st or st.get("mode") != "search_flow":
            return bot.answer_callback_query(call.id, "Нет активного поиска", show_alert=True)

        if call.data == "srch_cancel":
            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id, "Поиск отменён.")
            bot.send_message(chat_id, "Поиск отменён.", reply_markup=main_menu_keyboard())
            user_steps.pop(chat_id, None)
            return

        if call.data == "srch_cat_all":
            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id, "Все категории")
            st["category"] = None
            st["subcat_list"] = []
            st["subcategory"] = None
            do_search(chat_id)
            return

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

            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id, f"Категория: {cat_name}")

            if not flat:
                # если в категории нет подкатегорий
                st["subcategory"] = None
                do_search(chat_id)
            else:
                ask_for_subcategory(chat_id, cat_name)

    def ask_for_subcategory(chat_id, cat_name: str):
        """
        Шаг 4: предста­вляем пользователю список подкатегорий,
        каждая из которых уже — отдельная кнопка.
        """
        st = user_steps[chat_id]
        sub_list = st.get("subcat_list", [])

        kb = types.InlineKeyboardMarkup(row_width=2)
        for i, name in enumerate(sub_list):
            kb.add(types.InlineKeyboardButton(name, callback_data=f"srch_subcat_{i}"))
        kb.add(types.InlineKeyboardButton("Пропустить", callback_data="srch_subcat_skip"))
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="srch_cancel"))

        bot.send_message(
            chat_id,
            f"Категория «{cat_name}»: выберите подкатегорию:",
            reply_markup=kb
        )


    @bot.callback_query_handler(func=lambda call:
        call.data.startswith("srch_subcat_") or call.data == "srch_subcat_skip")
    def handle_subcat_choice(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        st = user_steps.get(chat_id)
        if not st or st.get("mode") != "search_flow":
            return bot.answer_callback_query(call.id, "Нет активного поиска", show_alert=True)

        if call.data == "srch_subcat_skip":
            st["subcategory"] = None
            bot.answer_callback_query(call.id, "Подкатегория пропущена.")
        else:
            idx = int(call.data.replace("srch_subcat_", ""))
            sub_list = st.get("subcat_list", [])
            if 0 <= idx < len(sub_list):
                chosen = sub_list[idx]
                st["subcategory"] = chosen
                bot.answer_callback_query(call.id, f"Подкатегория: {chosen}")
            else:
                return bot.answer_callback_query(call.id, "Некорректный индекс", show_alert=True)

        bot.delete_message(chat_id, call.message.message_id)
        do_search(chat_id)

    # ====================== Шаг 4: Поиск ======================
    def do_search(chat_id):
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
            bot.send_message(
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
        sent = bot.send_message(chat_id, text, reply_markup=kb)

        st["last_list_msg_id"] = sent.message_id

    def build_results_kb(chat_id, ads_slice):
        st = user_steps[chat_id]
        kb = types.InlineKeyboardMarkup()
        for ad_obj in ads_slice:
            # Текст кнопки
            label = ad_obj.inline_button_text or (ad_obj.text[:15] + "...")
            cb_data = f"srch_openad_{ad_obj.id}"
            kb.add(types.InlineKeyboardButton(label, callback_data=cb_data))
        if st["shown_count"] < len(st["search_results"]):
            kb.add(types.InlineKeyboardButton("Показать ещё", callback_data="srch_show_more"))
        return kb

    @bot.callback_query_handler(func=lambda call: call.data == "srch_show_more")
    def handle_show_more(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        st = user_steps.get(chat_id)
        if not st or st["mode"] != "search_flow":
            return bot.answer_callback_query(call.id, "Нет активного поиска", show_alert=True)

        ads = st["search_results"]
        shown = st["shown_count"]
        slice_ = ads[shown: shown + 10]
        if not slice_:
            return bot.answer_callback_query(call.id, "Больше объявлений нет.", show_alert=True)

        st["shown_count"] += len(slice_)
        kb = build_results_kb(chat_id, slice_)

        try:
            bot.edit_message_reply_markup(chat_id,
                                          st["last_list_msg_id"],
                                          reply_markup=kb)
        except:
            sent = bot.send_message(chat_id, "Дополнительные объявления:", reply_markup=kb)
            st["last_list_msg_id"] = sent.message_id  # вдруг старое нельзя было редактировать

        bot.answer_callback_query(call.id)

    # ================== Показ одного объявления ==================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("srch_openad_"))
    def handle_open_ad(call: telebot.types.CallbackQuery):
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
                return bot.answer_callback_query(
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
            types.InlineKeyboardButton(buy_lbl, callback_data=f"buy_ad_{ad_obj.id}"),
            types.InlineKeyboardButton("Подробнее", callback_data=f"details_ad_{ad_obj.id}")
        )
        kb.add(types.InlineKeyboardButton("Написать продавцу", callback_data=f"write_seller_ad_{ad_obj.id}"))

        if sale_done:
            kb.add(types.InlineKeyboardButton("Оставить отзыв", callback_data=f"feedback_ad_{ad_obj.id}"))
        else:
            kb.add(types.InlineKeyboardButton("Пожаловаться", callback_data=f"complain_ad_{ad_obj.id}"))

        kb.add(types.InlineKeyboardButton("Отзывы о продавце", callback_data=f"viewfeedback_seller_{user_obj.id}"))

        # --- выводим фото или текст ---------------------------
        photos = [p for p in (ad_obj.photos or "").split(",") if p]
        if photos:
            media = [types.InputMediaPhoto(media=photos[0], caption=caption)]
            media.extend(types.InputMediaPhoto(media=p) for p in photos[1:])
            bot.send_media_group(chat_id, media)
            bot.send_message(chat_id, "Выберите действие:", reply_markup=kb)
        else:
            bot.send_message(chat_id, caption, reply_markup=kb)

        bot.answer_callback_query(call.id)

        # --- обновляем «список объявлений» под сообщением ------
        if st and "last_list_msg_id" in st:
            try:
                bot.delete_message(chat_id, st["last_list_msg_id"])
            except:
                pass

            slice_ = st["search_results"][:st["shown_count"]]
            new_kb = build_results_kb(chat_id, slice_)
            txt = f"Найдено объявлений: {len(st['search_results'])}.\nВыберите:"
            new_msg = bot.send_message(chat_id, txt, reply_markup=new_kb)
            st["last_list_msg_id"] = new_msg.message_id

    # =============== Пожаловаться ================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("complain_ad_"))
    def complain_about_ad(call: telebot.types.CallbackQuery):
        """
        Пользователь жалуется на объявление (не купил или сделка не завершена).
        Сохраняем в AdComplaint, уведомляем админов.
        """
        ad_id_str = call.data.replace("complain_ad_", "")
        try:
            ad_id = int(ad_id_str)
        except:
            bot.answer_callback_query(call.id, "Некорректный ID объявления", show_alert=True)
            return

        user_id = call.from_user.id
        bot.answer_callback_query(call.id)
        # Просим текст жалобы
        msg = bot.send_message(user_id, "Опишите причину/суть жалобы:")
        user_steps[user_id] = {"complaint_ad_id": ad_id}
        bot.register_next_step_handler(msg, process_complaint_text)

    def process_complaint_text(message: telebot.types.Message):
        user_id = message.chat.id
        if user_id not in user_steps or "complaint_ad_id" not in user_steps[user_id]:
            bot.send_message(user_id, "Ошибка: нет информации об объявлении. Жалоба отменена.")
            return

        ad_id = user_steps[user_id]["complaint_ad_id"]
        text_of_complaint = message.text.strip()

        from database import AdComplaint
        with SessionLocal() as sess:
            ad_obj = sess.query(Ad).filter_by(id=ad_id).first()
            if not ad_obj:
                bot.send_message(user_id, "Объявление не найдено, жалоба отменена.")
                user_steps.pop(user_id, None)
                return

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
                types.InlineKeyboardButton("Написать продавцу", callback_data=f"complaint_msg_seller_{c_id}"),
                types.InlineKeyboardButton("Удалить объявление", callback_data=f"complaint_del_ad_{c_id}"),
                types.InlineKeyboardButton("Заблокировать пользователя", callback_data=f"complaint_ban_{c_id}")
            )

            bot.send_message(
                ADMIN_COMPLAINT_CHAT_ID,
                f"Поступила жалоба #{c_id} на объявление #{ad_id}.\n"
                f"От пользователя #{user_id}.\n"
                f"Текст жалобы:\n{text_of_complaint}\n\n"
                "Действия:",
                reply_markup=kb_admin
            )

        bot.send_message(user_id, "Ваша жалоба отправлена администраторам.")
        user_steps.pop(user_id, None)

    # =============== «Написать продавцу» в объявлении ================
    @bot.callback_query_handler(func=lambda call: call.data.startswith("write_seller_ad_"))
    def handle_write_seller(call: telebot.types.CallbackQuery):
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
                return bot.answer_callback_query(
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
            types.InlineKeyboardButton("💬 Открыть чат", callback_data=f"open_chat_{chat_id_db}"),
            types.InlineKeyboardButton("✏️ Ответить", callback_data=f"chat_write_{chat_id_db}")
        )

        # покупателю
        bot.send_message(
            buyer_id,
            f"Открыт чат #{chat_id_db} с продавцом (объявление #{ad_id}).",
            reply_markup=kb_open
        )

        # продавцу
        mention = f"@{buyer_name}" if buyer_name else f"#{buyer_id}"
        bot.send_message(
            seller_id,
            f"Покупатель {mention} написал вам по объявлению #{ad_id}.",
            reply_markup=kb_open
        )

        bot.answer_callback_query(call.id, "Чат создан.")