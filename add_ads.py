#!/usr/bin/env python3
import telebot
from telebot import types
from decimal import Decimal
from utils import calc_chat_price
from config import MAIN_CATEGORIES, MODERATION_GROUP_ID, CITY_STRUCTURE
from database import SessionLocal, User, Ad, ChatGroup
from utils import main_menu_keyboard, rus_status
from datetime import datetime

MARKIROVKA_GROUP_ID = -1002288960086  # чат для «маркировки»


def register_add_ads_handlers(bot: telebot.TeleBot, user_steps: dict):
    """
    Хендлеры для двух форматов объявлений:
      - Формат №1 (обычное объявление)
      - Формат №2 (биржа, «Разместить на бирже»).
    """

    @bot.message_handler(func=lambda m: m.text == "➕Разместить объявление")
    def add_ad_start(message: telebot.types.Message):
        """
        Главное меню для добавления объявления.
        В этом месте проверяем, не заблокирован ли пользователь.
        """
        chat_id = message.chat.id

        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            if not user:
                bot.send_message(chat_id, "Вы не зарегистрированы в системе.", reply_markup=main_menu_keyboard())
                return
            if user.is_banned:
                ban_info = f"Причина бана: {user.ban_reason or '—'}"
                ban_until_str = ""
                if user.ban_until:
                    ban_until_str = f"\nБан действует до: {user.ban_until} (UTC)."
                bot.send_message(
                    chat_id,
                    "Ваш аккаунт заблокирован, вы не можете размещать объявления.\n"
                    f"{ban_info}{ban_until_str}\n\n"
                    "Вы можете продолжать покупать чужие товары.\n"
                    "Если хотите обжаловать блокировку — напишите в поддержку.",
                    reply_markup=main_menu_keyboard()
                )
                return

        # Если пользователь не заблокирован — продолжаем
        user_steps[chat_id] = {}
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Создать объявление в боте", callback_data="create_ad_start"))
        kb.add(types.InlineKeyboardButton("Разместить на бирже", callback_data="adix_market_start"))
        kb.add(types.InlineKeyboardButton("Мои объявления", callback_data="my_ads_list"))
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation"))

        bot.send_message(chat_id, "Выберите действие:", reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data in [
        "create_ad_start", "my_ads_list", "cancel_ad_creation", "adix_market_start"
    ])
    def handle_main_menu_callback(call: telebot.types.CallbackQuery):
        """
        Обработка выбора в инлайн-меню "Создать объявление / Разместить на бирже / ..."
        """
        chat_id = call.message.chat.id
        user_id = call.from_user.id

        if call.data == "cancel_ad_creation":
            bot.delete_message(chat_id, call.message.message_id)
            bot.send_message(chat_id, "Создание объявления отменено.", reply_markup=main_menu_keyboard())
            user_steps.pop(chat_id, None)
            bot.answer_callback_query(call.id)
            return

        if call.data == "my_ads_list":
            bot.delete_message(chat_id, call.message.message_id)
            show_user_ads_list(chat_id, user_id)
            bot.answer_callback_query(call.id)
            return

        if call.data == "create_ad_start":
            # Здесь тоже на всякий случай можно перепроверить бан
            with SessionLocal() as session:
                user = session.query(User).filter_by(id=chat_id).first()
                if user and user.is_banned:
                    bot.delete_message(chat_id, call.message.message_id)
                    bot.answer_callback_query(call.id)
                    bot.send_message(
                        chat_id,
                        "Ваш аккаунт заблокирован. Объявления размещать нельзя.\n"
                        "Для обжалования бана обратитесь в поддержку.",
                        reply_markup=main_menu_keyboard()
                    )
                    user_steps.pop(chat_id, None)
                    return

            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id)

            user_steps[chat_id] = {
                "format": "format1",
                "inline_button_text": None,
                "text": None,
                "photos": [],
                "price": None,
                "quantity": 1,
                "city": None,
                "category": None,
                "subcat_list": [],
                "subcategory": None
            }
            ask_for_inline_button_name(chat_id)
            return

        if call.data == "adix_market_start":
            # И здесь проверяем
            with SessionLocal() as session:
                user = session.query(User).filter_by(id=chat_id).first()
                if user and user.is_banned:
                    bot.delete_message(chat_id, call.message.message_id)
                    bot.answer_callback_query(call.id)
                    bot.send_message(
                        chat_id,
                        "Ваш аккаунт заблокирован. Объявления размещать нельзя.\n"
                        "Для обжалования бана обратитесь в поддержку.",
                        reply_markup=main_menu_keyboard()
                    )
                    user_steps.pop(chat_id, None)
                    return

            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id)
            user_steps[chat_id] = {
                "format": "format2",
                "title": None,
                "description": None,
                "photos": [],
                "fio": None,
                "company_name": None,
                "inn": None,
                "username_link": None,

                "region": None,
                "chatgroup_id": None,
                "chatgroup_price": 0.0,
                "post_count": 1,
                "ad_id": None,
                "payment_confirmed": False
            }
            start_format2_flow(chat_id)
            return

    def show_user_ads_list(chat_id, user_id):
        """
        Показываем объявления пользователя.
        """
        with SessionLocal() as session:
            user = session.query(User).filter_by(id=user_id).first()
            if not user:
                bot.send_message(chat_id, "Вы не зарегистрированы.", reply_markup=main_menu_keyboard())
                return
            ads_list = session.query(Ad).filter_by(user_id=user.id).all()
            if not ads_list:
                bot.send_message(chat_id, "У вас нет объявлений.", reply_markup=main_menu_keyboard())
                return

            kb = types.InlineKeyboardMarkup()
            for ad_obj in ads_list:
                cb_data = f"my_ad_detail_{ad_obj.id}"
                btn_text = f"Объявление #{ad_obj.id} ({rus_status(ad_obj.status)})"
                kb.add(types.InlineKeyboardButton(btn_text, callback_data=cb_data))
            kb.add(types.InlineKeyboardButton("Закрыть список", callback_data="close_my_ads_list"))
            bot.send_message(chat_id, "Ваши объявления:", reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("my_ad_detail_") or call.data == "close_my_ads_list")
    def handle_my_ads_inline_callbacks(call: telebot.types.CallbackQuery):
        """
        Детали объявления пользователя (или закрыть).
        """
        chat_id = call.message.chat.id
        if call.data == "close_my_ads_list":
            bot.delete_message(chat_id, call.message.message_id)
            bot.answer_callback_query(call.id)
            return

        ad_id_str = call.data.replace("my_ad_detail_", "")
        try:
            ad_id = int(ad_id_str)
        except:
            bot.answer_callback_query(call.id, "Некорректный ID.", show_alert=True)
            return

        with SessionLocal() as session:
            ad_obj = session.query(Ad).filter_by(id=ad_id).first()
            if not ad_obj:
                bot.answer_callback_query(call.id, "Объявление не найдено.", show_alert=True)
                return

            detail = (
                f"ID: {ad_obj.id}\n"
                f"Статус: {rus_status(ad_obj.status)}\n"
                f"Кнопка: {ad_obj.inline_button_text or '—'}\n"
                f"Текст: {ad_obj.text}\n"
                f"Цена: {ad_obj.price}\n"
                f"Кол-во: {ad_obj.quantity}\n"
                f"Категория: {ad_obj.category}\n"
                f"Подкатегория: {ad_obj.subcategory}\n"
                f"Город: {ad_obj.city}\n"
            )
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("Закрыть", callback_data="close_ad_detail"))
            bot.edit_message_text(detail, chat_id, call.message.message_id, reply_markup=kb)
            bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data == "close_ad_detail")
    def close_ad_detail(call: telebot.types.CallbackQuery):
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    # ---------------------------------------------------------------------------
    #                         ФОРМАТ №1 (обычное объявление)
    # ---------------------------------------------------------------------------
    def ask_for_inline_button_name(chat_id):
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation"))
        text_ask = (
            "1. Введите название для кнопки (до 3 слов). Например: «Стул».\n"
            "*Согласно пользовательскому соглашению ADIX.*"
        )
        bot.send_message(chat_id, text_ask, parse_mode="Markdown", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(chat_id, process_inline_button_name)

    def process_inline_button_name(message: telebot.types.Message):
        """
        Шаг 1: Название кнопки.
        """
        chat_id = message.chat.id
        if chat_id not in user_steps:
            return
        name_text = message.text.strip()
        if len(name_text.split()) > 3:
            ask_for_inline_button_name(chat_id)
            return
        user_steps[chat_id]["inline_button_text"] = name_text
        ask_for_ad_text(chat_id)

    def ask_for_ad_text(chat_id):
        """
        Шаг 2: Текст объявления.
        """
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation"))
        txt = (
            "2. Введите текст объявления (описание). Укажите характеристики (новое/б/у), детали и т.д.\n"
            "*Согласно соглашению ADIX.*"
        )
        bot.send_message(chat_id, txt, parse_mode="Markdown", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(chat_id, process_ad_text)

    def process_ad_text(message: telebot.types.Message):
        """
        Обрабатываем текст объявления.
        """
        chat_id = message.chat.id
        if chat_id not in user_steps:
            return
        user_steps[chat_id]["text"] = message.text.strip()
        ask_for_photos(chat_id)

    def ask_for_photos(chat_id):
        """
        Шаг 3: Фото (до 10 шт).
        """
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("Готово", callback_data="photo_done"),
            types.InlineKeyboardButton("Пропустить", callback_data="photo_skip"),
            types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation")
        )
        txt = "3. Отправьте фото (по одному). Когда закончите, нажмите «Готово», или «Пропустить»."
        bot.send_message(chat_id, txt, reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(chat_id, process_photos)

    def process_photos(message: telebot.types.Message):
        chat_id = message.chat.id
        if chat_id not in user_steps:
            return
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
            user_steps[chat_id]["photos"].append(file_id)
            bot.register_next_step_handler_by_chat_id(chat_id, process_photos)
        else:
            bot.register_next_step_handler_by_chat_id(chat_id, process_photos)

    @bot.callback_query_handler(func=lambda call: call.data in ("photo_done", "photo_skip"))
    def handle_photos_done_skip(call: telebot.types.CallbackQuery):
        """
        Нажата кнопка "Готово" или "Пропустить" при загрузке фото.
        """
        chat_id = call.message.chat.id
        bot.delete_message(chat_id, call.message.message_id)
        bot.clear_step_handler_by_chat_id(chat_id)
        if chat_id not in user_steps:
            return

        if call.data == "photo_done":
            bot.answer_callback_query(call.id, "Фото сохранены.")
        else:
            bot.answer_callback_query(call.id, "Без фото.")
        ask_for_region(chat_id)

    # --------------------------------------------------------------------------
    #    УПРОЩЁННЫЙ ВЫБОР ГОРОДА (шаблон)
    # --------------------------------------------------------------------------
    def ask_for_region(chat_id):
        """
        Шаг 4: Города/Регионы.
        """
        region_names = list(CITY_STRUCTURE.keys())  # ["Москва", "Московская область", "РФ города"]
        user_steps[chat_id]["regions_list"] = region_names
        kb = types.InlineKeyboardMarkup()
        for i, r_name in enumerate(region_names):
            kb.add(types.InlineKeyboardButton(r_name, callback_data=f"pick_region_{i}"))
        kb.add(types.InlineKeyboardButton("Добавить свой город", callback_data="city_custom"))
        kb.add(types.InlineKeyboardButton("Пропустить", callback_data="city_skip"))
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation"))

        msg = (
            "4. Укажите город/регион.\n"
            "Если нет в списке – «Добавить свой».\n"
            "Или «Пропустить».\n"
            "*Согласно соглашению ADIX.*"
        )
        bot.send_message(chat_id, msg, parse_mode="Markdown", reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pick_region_"))
    def handle_pick_region(call: telebot.types.CallbackQuery):
        """
        Выбор региона (Москва / МО / РФ).
        """
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            bot.answer_callback_query(call.id, "Нет активного шага", show_alert=True)
            return
        data = user_steps[chat_id]
        region_list = data.get("regions_list", [])
        idx_str = call.data.replace("pick_region_", "")
        try:
            idx = int(idx_str)
            region_name = region_list[idx]
        except:
            bot.answer_callback_query(call.id, "Ошибка индекса региона", show_alert=True)
            return

        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id, f"Регион: {region_name}")

        city_list = CITY_STRUCTURE.get(region_name, [])
        data["picked_region"] = region_name
        data["city_list"] = city_list
        show_city_list(chat_id)

    def show_city_list(chat_id):
        """
        Показ списка городов в выбранном регионе (Формат №1).
        """
        data = user_steps[chat_id]
        city_list = data["city_list"]
        region_name = data["picked_region"]

        kb = types.InlineKeyboardMarkup()
        for j, c_name in enumerate(city_list):
            kb.add(types.InlineKeyboardButton(c_name, callback_data=f"pick_city_{j}"))
        kb.add(types.InlineKeyboardButton("Назад к регионам", callback_data="back_to_regions"))

        bot.send_message(chat_id, f"Вы выбрали регион: {region_name}\nТеперь выберите город:", reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("pick_city_"))
    def handle_pick_city(call: telebot.types.CallbackQuery):
        """
        Пользователь выбрал конкретный город (Формат №1).
        """
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            bot.answer_callback_query(call.id, "Нет активного шага", show_alert=True)
            return

        data = user_steps[chat_id]
        city_list = data.get("city_list", [])
        idx_str = call.data.replace("pick_city_", "")
        try:
            idx = int(idx_str)
            chosen_city = city_list[idx]
        except:
            bot.answer_callback_query(call.id, "Ошибка индекса города", show_alert=True)
            return

        region_name = data["picked_region"]
        full_city = f"{region_name} | {chosen_city}"
        data["city"] = full_city

        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id, f"Вы выбрали: {full_city}")
        ask_for_category(chat_id)

    @bot.callback_query_handler(func=lambda call: call.data == "back_to_regions")
    def handle_back_to_regions(call: telebot.types.CallbackQuery):
        """
        Вернуться к списку регионов (Формат №1).
        """
        chat_id = call.message.chat.id
        bot.delete_message(chat_id, call.message.message_id)
        ask_for_region(chat_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data == "city_custom")
    def handle_city_custom(call: telebot.types.CallbackQuery):
        """
        Ввод собственного названия города (Формат №1).
        """
        chat_id = call.message.chat.id
        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "Введите название своего города:")
        bot.register_next_step_handler_by_chat_id(chat_id, process_custom_city)

    def process_custom_city(message: telebot.types.Message):
        chat_id = message.chat.id
        if chat_id not in user_steps:
            return
        user_steps[chat_id]["city"] = message.text.strip()
        ask_for_category(chat_id)

    @bot.callback_query_handler(func=lambda call: call.data == "city_skip")
    def handle_city_skip(call: telebot.types.CallbackQuery):
        """
        Пропустить выбор города (Формат №1).
        """
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            return
        user_steps[chat_id]["city"] = None
        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id, "Город пропущен.")
        ask_for_category(chat_id)

    # ------------------------------------------------------------------------
    # Шаги 5-7 (Формат №1): выбор категории, цены, количества
    # ------------------------------------------------------------------------
    def ask_for_category(chat_id):
        """
        Шаг 5 (Формат №1): категория.
        """
        kb = types.InlineKeyboardMarkup(row_width=2)
        for cat in MAIN_CATEGORIES.keys():
            kb.add(types.InlineKeyboardButton(cat, callback_data=f"select_category_{cat}"))
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation"))

        bot.send_message(chat_id, "5. Выберите категорию:", reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("select_category_"))
    def handle_category_selection(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        category = call.data.replace("select_category_", "")
        if chat_id not in user_steps:
            return

        # Пример исключения некоторых категорий
        if category in ["🏠 Недвижимость", "🚗 Авто и Мото"]:
            bot.answer_callback_query(call.id)
            bot.send_message(
                chat_id,
                f"Размещение в {category} пока доступно только через администратора.",
                reply_markup=main_menu_keyboard()
            )
            user_steps.pop(chat_id, None)
            return

        user_steps[chat_id]["category"] = category
        user_steps[chat_id]["subcat_list"] = MAIN_CATEGORIES.get(category, [])
        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id)

        sub_list = user_steps[chat_id]["subcat_list"]
        if not sub_list:
            user_steps[chat_id]["subcategory"] = None
            ask_for_price(chat_id)
            return

        kb = types.InlineKeyboardMarkup()
        for i, s in enumerate(sub_list):
            kb.add(types.InlineKeyboardButton(s, callback_data=f"subcat_{i}"))
        kb.add(types.InlineKeyboardButton("Пропустить", callback_data="skip_subcategory"))
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation"))
        bot.send_message(chat_id, f"Подкатегория для {category}:", reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("subcat_") or call.data == "skip_subcategory")
    def handle_subcat(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            return

        if call.data == "skip_subcategory":
            user_steps[chat_id]["subcategory"] = None
        else:
            idx_str = call.data.replace("subcat_", "")
            try:
                idx = int(idx_str)
                sub_list = user_steps[chat_id]["subcat_list"]
                user_steps[chat_id]["subcategory"] = sub_list[idx]
            except:
                user_steps[chat_id]["subcategory"] = None

        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id)
        ask_for_price(chat_id)

    def ask_for_price(chat_id):
        """
        Шаг 6 (Формат №1): цена.
        """
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Пропустить", callback_data="price_skip"),
               types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation"))
        bot.send_message(chat_id, "6. Введите цену (число) или «Пропустить».", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(chat_id, process_ad_price)

    @bot.callback_query_handler(func=lambda call: call.data == "price_skip")
    def skip_price(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        bot.delete_message(chat_id, call.message.message_id)
        if chat_id in user_steps:
            user_steps[chat_id]["price"] = 0
        bot.answer_callback_query(call.id, "Цена пропущена.")
        ask_for_quantity(chat_id)

    def process_ad_price(message: telebot.types.Message):
        """
        Обработка введённой цены (Формат №1).
        """
        chat_id = message.chat.id
        if chat_id not in user_steps:
            return
        try:
            val = float(message.text.strip())
        except:
            ask_for_price(chat_id)
            return
        user_steps[chat_id]["price"] = val
        ask_for_quantity(chat_id)

    def ask_for_quantity(chat_id):
        """
        Шаг 7 (Формат №1): количество (если нужно).
        """
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Пропустить", callback_data="quantity_skip"),
               types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation"))
        bot.send_message(chat_id, "7. Введите количество (число) или «Пропустить».", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(chat_id, process_ad_quantity)

    @bot.callback_query_handler(func=lambda call: call.data == "quantity_skip")
    def skip_quantity(call: telebot.types.CallbackQuery):
        """
        Пользователь пропустил ввод количества (Формат №1).
        """
        chat_id = call.message.chat.id
        bot.delete_message(chat_id, call.message.message_id)
        if chat_id in user_steps:
            user_steps[chat_id]["quantity"] = 1
        bot.answer_callback_query(call.id, "Количество пропущено.")
        finalize_ad_save(chat_id)

    def process_ad_quantity(message: telebot.types.Message):
        """
        Обработчик введённого количества (Формат №1).
        """
        chat_id = message.chat.id
        if chat_id not in user_steps:
            return
        try:
            q = int(message.text.strip())
        except:
            ask_for_quantity(chat_id)
            return
        user_steps[chat_id]["quantity"] = q
        finalize_ad_save(chat_id)

    def finalize_ad_save(chat_id):
        """
        Сохраняем объявление Формата №1 и отправляем в MODERATION_GROUP_ID сразу весь альбом.
        """
        d = user_steps[chat_id]
        # Состояние, заполненное шагами
        inline_button_text = d["inline_button_text"]
        text = d["text"]
        photos = d["photos"]  # список file_id
        price = d["price"]
        qty = d["quantity"]
        city = d["city"]
        cat = d["category"]
        subcat = d["subcategory"]

        # 1) Сохраняем объявление и забираем все нужные поля до закрытия сессии
        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            if not user:
                bot.send_message(chat_id, "Пользователь не найден в БД.", reply_markup=main_menu_keyboard())
                user_steps.pop(chat_id, None)
                return

            # выгружаем username и прочее
            username = user.username or str(user.id)
            inn_info = user.inn or "—"
            fio_info = user.full_name or user.company_name or "—"

            new_ad = Ad(
                user_id=user.id,
                inline_button_text=inline_button_text,
                text=text,
                price=Decimal(str(price)),
                quantity=qty,
                category=cat,
                subcategory=subcat,
                city=city,
                status="pending",
                ad_type="standard",
                photos=",".join(photos) if photos else ""
            )
            session.add(new_ad)
            session.commit()
            ad_id = new_ad.id

        # 2) Формируем подпись после сессии
        caption = (
                f"<b>Новое объявление #{ad_id}</b>\n"
                f"Кнопка: {inline_button_text}\n"
                f"Текст: {text}\n"
                f"Цена: {price} руб.\n"
                f"Кол-во: {qty}\n"
                f"Категория: {cat}" + (f" / {subcat}" if subcat else "") + "\n"
                f"Город: {city or '—'}\n"
                f"ИНН: {inn_info}, ФИО/Компания: {fio_info}\n"
                f"Контакты: @{username}\n\n"
                f"Статус: {rus_status('pending')}"
        )
        kb_mod = types.InlineKeyboardMarkup()
        kb_mod.add(
            types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_ad_{ad_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_ad_{ad_id}")
        )
        kb_mod.add(types.InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_ad_{ad_id}"))

        # 3) Отправляем весь альбом в модерационную группу
        if photos:
            media = []
            for idx, file_id in enumerate(photos):
                if idx == 0:
                    media.append(types.InputMediaPhoto(media=file_id, caption=caption, parse_mode="HTML"))
                else:
                    media.append(types.InputMediaPhoto(media=file_id))
            bot.send_media_group(MODERATION_GROUP_ID, media)
            bot.send_message(MODERATION_GROUP_ID, "Выберите действие:", reply_markup=kb_mod)
        else:
            bot.send_message(MODERATION_GROUP_ID, caption, parse_mode="HTML", reply_markup=kb_mod)

        # 4) Уведомляем автора
        bot.send_message(
            chat_id,
            f"Ваше объявление #{ad_id} отправлено на модерацию!",
            reply_markup=main_menu_keyboard()
        )
        user_steps.pop(chat_id, None)

    # ========================================================================
    #            ФОРМАТ №2 — «Разместить на бирже»
    # ========================================================================

    @bot.callback_query_handler(func=lambda call: call.data == "adix_market_start")
    def handle_adix_market_start(call: telebot.types.CallbackQuery):
        """
        Старт потока «Биржа» из главного меню.
        """
        chat_id = call.message.chat.id
        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id)

        # инициализируем состояние
        user_steps[chat_id] = {
            "format": "format2",
            "title": None,
            "description": None,
            "photos": [],
            "fio": None,
            "inn": None,
            "region": None,
            "f2_chats": [],
            "f2_chat_page": 0,
            "chatgroup_id": None,
            "chatgroup_price": 0.0,
            "post_count": 1,
            "total_sum": 0.0
        }
        start_format2_flow(chat_id)

    def start_format2_flow(chat_id: int):
        """
        Шаг 1 — «Формат №2 (Биржа)». Запрашиваем название объявления.
        """
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation")
        )
        bot.send_message(
            chat_id,
            "Формат №2 (Биржа).\n\n"
            "1) Введите название (до 3‑х слов).\n"
            "Например: «Ремонт окон»",
            reply_markup=kb
        )
        bot.register_next_step_handler_by_chat_id(chat_id, process_format2_title)

    # -----------------------------------------------------------------
    #  ── ЭТА СТРОКА ДЕЛАЕТ ФУНКЦИЮ ДОСТУПНОЙ ИЗ profile.py ──
    globals()["_start_format2_flow_fn"] = start_format2_flow

    def process_format2_title(message: telebot.types.Message):
        chat_id = message.chat.id
        if chat_id not in user_steps: return
        title = message.text.strip()
        if len(title.split()) > 3:
            return start_format2_flow(chat_id)
        user_steps[chat_id]["title"] = title
        ask_format2_description(chat_id)

    def ask_format2_description(chat_id: int):
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation")
        )
        bot.send_message(
            chat_id,
            "2) Введите описание услуги или товара (обязательно, без пропуска):",
            reply_markup=kb
        )
        bot.register_next_step_handler_by_chat_id(chat_id, process_format2_description)

    def process_format2_description(message: telebot.types.Message):
        chat_id = message.chat.id
        if chat_id not in user_steps: return
        desc = message.text.strip()
        if not desc:
            return ask_format2_description(chat_id)
        user_steps[chat_id]["description"] = desc
        ask_format2_photos(chat_id)

    def ask_format2_photos(chat_id: int):
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("Готово", callback_data="format2_photos_done"),
            types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation")
        )
        bot.send_message(
            chat_id,
            "3) Загрузите до 10 фото (по одному). Когда закончите, нажмите «Готово».",
            reply_markup=kb
        )
        bot.register_next_step_handler_by_chat_id(chat_id, process_format2_photos)

    def process_format2_photos(message: telebot.types.Message):
        chat_id = message.chat.id
        if chat_id not in user_steps: return
        if message.content_type == "photo":
            photos = user_steps[chat_id]["photos"]
            if len(photos) < 10:
                photos.append(message.photo[-1].file_id)
            else:
                bot.send_message(chat_id, "Максимум 10 фото!")
        bot.register_next_step_handler_by_chat_id(chat_id, process_format2_photos)

    @bot.callback_query_handler(func=lambda call: call.data == "format2_photos_done")
    def done_format2_photos(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        bot.delete_message(chat_id, call.message.message_id)
        bot.clear_step_handler_by_chat_id(chat_id)
        bot.answer_callback_query(call.id)
        ask_format2_fio(chat_id)

    def ask_format2_fio(chat_id: int):
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation")
        )
        bot.send_message(
            chat_id,
            "4) Укажите ФИО (Иванов Иван Иванович):",
            reply_markup=kb
        )
        bot.register_next_step_handler_by_chat_id(chat_id, process_format2_fio)

    def process_format2_fio(message: telebot.types.Message):
        chat_id = message.chat.id
        if chat_id not in user_steps: return
        fio = message.text.strip()
        if not fio:
            return ask_format2_fio(chat_id)
        user_steps[chat_id]["fio"] = fio
        ask_format2_inn(chat_id)

    def ask_format2_inn(chat_id: int):
        kb = types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation")
        )
        bot.send_message(
            chat_id,
            "5) Укажите ИНН (12 цифр для ФЛ или 10 для ЮЛ):",
            reply_markup=kb
        )
        bot.register_next_step_handler_by_chat_id(chat_id, process_format2_inn)

    def process_format2_inn(message: telebot.types.Message):
        chat_id = message.chat.id
        if chat_id not in user_steps: return
        inn = message.text.strip()
        if not (inn.isdigit() and len(inn) in (10, 12)):
            return ask_format2_inn(chat_id)
        user_steps[chat_id]["inn"] = inn

        # сразу к выбору региона:
        kb = types.InlineKeyboardMarkup(row_width=2).add(
            types.InlineKeyboardButton("Москва", callback_data="f2_region_moscow"),
            types.InlineKeyboardButton("МО", callback_data="f2_region_mo"),
            types.InlineKeyboardButton("Другие", callback_data="f2_region_rf")
        )
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation"))
        bot.send_message(chat_id, "6) Выберите регион размещения:", reply_markup=kb)

    # ---------------------------------------------------------------------
    #   ВЫБОР ЧАТОВ ДЛЯ «ФОРМАТА № 2»:   регион → несколько чатов → кол‑во → оплата
    # ---------------------------------------------------------------------

    # ---------- 1. выбор региона ----------------------------------------
    @bot.callback_query_handler(func=lambda c: c.data in ("f2_region_moscow", "f2_region_mo", "f2_region_rf"))
    def handle_format2_region(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        code_map = {
            "f2_region_moscow": "moscow",
            "f2_region_mo": "mo",
            "f2_region_rf": "rf"
        }
        label_map = {
            "f2_region_moscow": "Москва",
            "f2_region_mo": "МО",
            "f2_region_rf": "Города РФ"
        }

        user_steps[chat_id]["region"] = code_map[call.data]
        user_steps[chat_id]["region_label"] = label_map[call.data]

        # удаляем предложение выбрать регион
        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id, f"Регион: {label_map[call.data]}")
        start_chatgroup_selection(chat_id)

    # ---------- 2. формируем список чатов по региону --------------------
    def start_chatgroup_selection(chat_id: int):
        """
        Отправляет (или редактирует) сообщение со списком чатов для выбранного региона.
        """
        region_key = user_steps[chat_id]["region"]
        with SessionLocal() as sess:
            chats = sess.query(ChatGroup) \
                .filter_by(is_active=True, region=region_key) \
                .order_by(ChatGroup.title) \
                .all()

        if not chats:
            kb = types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("🔙 Выбрать регион заново", callback_data="f2_back_region"),
                types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation")
            )
            bot.send_message(chat_id, "В выбранном регионе нет активных чатов.", reply_markup=kb)
            return

        user_steps[chat_id].update({
            "f2_chats": chats,
            "f2_chat_page": 0,
            "selected_chat_ids": set(),
            "last_list_msg_id": None
        })
        show_f2_chats_page(chat_id)

    # ---------- 3. отображаем страницу N из списка чатов ----------------
    def show_f2_chats_page(chat_id: int):
        """
        Рисует страницу чатов, редактируя старое сообщение, если оно есть.
        """
        d = user_steps[chat_id]
        chats = d["f2_chats"]
        page = d["f2_chat_page"]
        per = 10
        total = len(chats)
        pages = (total + per - 1) // per

        subset = chats[page * per:(page + 1) * per]

        kb = types.InlineKeyboardMarkup(row_width=1)
        for c in subset:
            flag = "✅" if c.id in d["selected_chat_ids"] else "◻️"
            kb.add(types.InlineKeyboardButton(
                f"{flag} {c.title} — {c.price_1:.0f}₽",
                callback_data=f"f2toggle_{c.id}"
            ))

        nav = []
        if page > 0:
            nav.append(types.InlineKeyboardButton("⏪ Назад", callback_data="f2page_prev"))
        if (page + 1) * per < total:
            nav.append(types.InlineKeyboardButton("Вперёд ⏩", callback_data="f2page_next"))
        if nav:
            kb.row(*nav)

        if d["selected_chat_ids"]:
            kb.add(types.InlineKeyboardButton("✅ Завершить выбор", callback_data="f2finish_chats"))
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation"))

        text = f"Выберите чаты ({page + 1}/{pages}). Отмечено: {len(d['selected_chat_ids'])}"

        if d.get("last_list_msg_id"):
            try:
                bot.edit_message_text(text, chat_id, d["last_list_msg_id"], reply_markup=kb)
                return
            except telebot.apihelper.ApiException:
                pass

        sent = bot.send_message(chat_id, text, reply_markup=kb)
        d["last_list_msg_id"] = sent.message_id

    @bot.callback_query_handler(func=lambda c: c.data in ("f2page_prev", "f2page_next"))
    def paginate_f2_chats(call: telebot.types.CallbackQuery):
        """
        Листание страниц списка чатов.
        """
        chat_id = call.message.chat.id
        d = user_steps.get(chat_id)
        if not d:
            return

        d["f2_chat_page"] += -1 if call.data == "f2page_prev" else 1
        bot.answer_callback_query(call.id)
        show_f2_chats_page(chat_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("f2toggle_"))
    def toggle_chat_selection(call: telebot.types.CallbackQuery):
        """
        Срабатывает при клике на чекбокс чата — переключает его в наборе.
        """
        chat_id = call.message.chat.id
        cg_id = int(call.data.split("_", 1)[1])
        sel = user_steps[chat_id]["selected_chat_ids"]

        if cg_id in sel:
            sel.remove(cg_id)
        else:
            sel.add(cg_id)

        bot.answer_callback_query(call.id)
        show_f2_chats_page(chat_id)

    # ---------- 4. закончили выбирать чаты  -----------------------------
    @bot.callback_query_handler(func=lambda c: c.data == "f2finish_chats")
    def finish_chat_selection(call: telebot.types.CallbackQuery):
        """
        Завершение выбора чатов, удаляем сообщение со списком и переходим дальше.
        """
        chat_id = call.message.chat.id
        d = user_steps[chat_id]

        if not d["selected_chat_ids"]:
            bot.answer_callback_query(call.id, "Нужно выбрать хотя бы один чат!", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        bot.delete_message(chat_id, d.get("last_list_msg_id", call.message.message_id))

        d.update({
            "selected_list": list(d["selected_chat_ids"]),
            "current_idx": 0,
            "selections": []
        })
        ask_count_for_current(chat_id)

    # ---------- 5. задаём кол-во/закреп для очередного чата -------------
    def ask_count_for_current(chat_id: int):
        d = user_steps[chat_id]
        if d["current_idx"] >= len(d["selected_list"]):
            return show_f2_summary(chat_id)

        cg_id = d["selected_list"][d["current_idx"]]
        with SessionLocal() as sess:
            cg = sess.query(ChatGroup).get(cg_id)
        d["current_cg"] = cg

        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("1 размещение", callback_data="f2cnt_1"),
            types.InlineKeyboardButton("5 размещений", callback_data="f2cnt_5"),
            types.InlineKeyboardButton("10 размещений", callback_data="f2cnt_10"),
            types.InlineKeyboardButton("Закреп × 1.6", callback_data="f2cnt_pin")
        )
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation"))

        bot.send_message(
            chat_id,
            f"Чат «{cg.title}» ({cg.price_1:.0f} ₽ за 1). Выберите вариант:",
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda c: c.data.startswith("f2cnt_"))
    def set_count_for_chat(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        d = user_steps[chat_id]
        cg = d["current_cg"]
        opt = call.data.split("_", 1)[1]

        if opt == "pin":
            count, mult = 1, 1.6
            label = "закреп"
        else:
            count, mult = int(opt), 1.0
            label = str(count)

        # здесь используем calc_chat_price
        base_cost = calc_chat_price(cg, count)
        cost = base_cost * mult
        unit_price = base_cost / count

        # вместо старого price=price_1 сохраняем именно unit_price
        d["selections"].append({
            "cg_id": cg.id,
            "title": cg.title,
            "price": unit_price,  # цена за единицу
            "count": count,
            "mult": mult,
            "cost": cost
        })

        bot.answer_callback_query(call.id, f"Добавлено: {cg.title} × {label}")
        bot.delete_message(chat_id, call.message.message_id)

        d["current_idx"] += 1
        ask_count_for_current(chat_id)

    # ---------- 6. итоговая сводка и оплата ------------------------------
    def show_f2_summary(chat_id: int):
        d = user_steps[chat_id]
        place_total = sum(s["cost"] for s in d["selections"])
        mark_fee = 350.0
        d["placement_total"] = place_total
        d["marking_fee"] = mark_fee

        lines = ["📋 Вы выбрали:"]
        for s in d["selections"]:
            if s["mult"] > 1:
                # Для закрепа показываем 1.6 × цена_за_1 = итог
                lines.append(f"• {s['title']}: {s['mult']}×{s['price']:.0f}₽ = {s['cost']:.2f}₽")
            else:
                # Обычные пакеты остаются с прежним форматом
                lines.append(f"• {s['title']}: {s['count']}×{s['price']:.0f}₽ → {s['cost']:.2f}₽")

        lines += [
            f"\n💰 Размещение: {place_total:.2f} ₽",
            f"🔖 Маркировка:  {mark_fee:.2f} ₽",
            "\nОплатить всё?"
        ]

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton("💳 Оплатить", callback_data="f2pay_all"),
            types.InlineKeyboardButton("🔙 Вернуться к чатам", callback_data="f2_back_to_chats"),
            types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation")
        )
        bot.send_message(chat_id, "\n".join(lines), reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data == "f2_back_to_chats")
    def back_to_chats(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        bot.answer_callback_query(call.id)
        user_steps[chat_id]["selection_stage"] = "picking_chats"
        show_f2_chats_page(chat_id)

    # ---------- 7. оплата (размещение + маркировка одним платежом) -------
    @bot.callback_query_handler(func=lambda c: c.data == "f2pay_all")
    def handle_f2pay_all(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        d = user_steps.get(chat_id)
        if not d: return

        total = Decimal(str(d["placement_total"] + d["marking_fee"]))

        with SessionLocal() as sess:
            user = sess.query(User).get(chat_id)
            if not user:
                return bot.answer_callback_query(call.id, "Пользователь не найден.", show_alert=True)
            if user.balance < total:
                return bot.answer_callback_query(call.id, "Недостаточно средств!", show_alert=True)

            user.balance -= total
            sess.commit()

        bot.answer_callback_query(call.id, f"Списано {total} ₽")

        # сохраняем все объявления и отправляем в чат маркировки
        finalize_format2_multi(chat_id)

    # ---------- 8. финальное сохранение всех объявлений ------------------
    def finalize_format2_multi(chat_id: int):
        """
        Сохраняем ВСЕ объявления из selections,
        а в маркировочный чат отправляем ОДНО сообщение‑сводку.
        """
        d = user_steps[chat_id]
        photos = d["photos"]
        descr = d["description"]
        title = d["title"]

        ad_ids = []
        selections = d["selections"]  # список словарей (chat, count, mult …)

        with SessionLocal() as sess:
            user = sess.query(User).get(chat_id)

            # для подписи
            fio_info = user.full_name or user.company_name or d.get("fio") or "—"
            inn_info = user.inn or d.get("inn") or "—"
            username = f"@{user.username}" if user.username else "—"

            # ---- создаём объявления (по одному на каждый чат) -------------
            for s in selections:
                ad = Ad(
                    user_id=chat_id,
                    inline_button_text=s["title"],
                    text=descr,
                    photos=",".join(photos) if photos else "",
                    status="pending",
                    ad_type="format2"
                )
                sess.add(ad)
                sess.commit()
                ad_ids.append(ad.id)

            # ---------------- строим ОДНО сообщение‑сводку -----------------
            place_total = sum(s["cost"] for s in selections)
            mark_fee = d["marking_fee"]
            grand_total = place_total + mark_fee

            lines = [
                f"<b>Биржа ADIX (Формат №2)</b>",
                f"Название: {title}",
                f"Описание: {descr}",
                f"ФИО: {fio_info}",
                f"ИНН: {inn_info}",
                f"Контакты: {username}",
                "\n<b>Выбранные чаты:</b>"
            ]
            for s in selections:
                lbl = "Закреп × 1.6" if s["mult"] > 1 else f"{s['count']}×"
                lines.append(f"• {s['title']} — {lbl}{s['price']:.0f}₽ → {s['cost']:.2f}₽")
            lines += [
                f"\n💰 Размещение: {place_total:.2f} ₽",
                f"🔖 Маркировка:  {mark_fee:.2f} ₽",
                f"<b>Итого: {grand_total:.2f} ₽</b>",
                f"\nСтатус: {rus_status('pending')}"
            ]
            caption = "\n".join(lines)

            # ---------- клавиатура: по 2 кнопки на КАЖДЫЙ ad_id ------------
            kb = types.InlineKeyboardMarkup(row_width=2)
            for ad_id in ad_ids:
                kb.add(
                    types.InlineKeyboardButton(f"✅ Принять #{ad_id}", callback_data=f"approve_ad_{ad_id}"),
                    types.InlineKeyboardButton(f"❌ Отклонить #{ad_id}", callback_data=f"reject_ad_{ad_id}")
                )

        # ---------- отправляем сводку (с фото‑альбомом, если нужно) --------
        if photos:
            media = []
            for idx, fid in enumerate(photos):
                if idx == 0:
                    media.append(types.InputMediaPhoto(media=fid, caption=caption, parse_mode="HTML"))
                else:
                    media.append(types.InputMediaPhoto(media=fid))
            bot.send_media_group(MARKIROVKA_GROUP_ID, media)
            bot.send_message(MARKIROVKA_GROUP_ID, "Действия модератора:", reply_markup=kb)
        else:
            bot.send_message(MARKIROVKA_GROUP_ID, caption, parse_mode="HTML", reply_markup=kb)

        # ---------- сообщение автору и очистка state -----------------------
        bot.send_message(chat_id,
                         "✅ Ваши объявления отправлены на проверку!",
                         reply_markup=main_menu_keyboard())
        user_steps.pop(chat_id, None)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("f2chatpick_"))
    def handle_pick_chat_for_region(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            return

        cg_id_str = call.data.replace("f2chatpick_", "")
        try:
            cg_id = int(cg_id_str)
        except:
            bot.answer_callback_query(call.id, "Некорректный ID чата", show_alert=True)
            return

        with SessionLocal() as session:
            cg = session.query(ChatGroup).filter_by(id=cg_id).first()
            if not cg:
                bot.answer_callback_query(call.id, "Чат не найден", show_alert=True)
                return

        user_steps[chat_id]["chatgroup_id"] = cg_id
        user_steps[chat_id]["chatgroup_price"] = float(cg.price)

        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id)

        ask_format2_post_count(chat_id)

    def ask_format2_post_count(chat_id):
        """
        Выбор: 1, 5, 10 размещений или «Закреп (×1.6)».
        """
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("1 размещение", callback_data="f2count_1"),
            types.InlineKeyboardButton("5 размещений", callback_data="f2count_5")
        )
        kb.add(
            types.InlineKeyboardButton("10 размещений", callback_data="f2count_10"),
            types.InlineKeyboardButton("Закреп ( × 1.6)", callback_data="f2count_pin")
        )
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation"))
        bot.send_message(chat_id, "Сколько размещений хотите оплатить?", reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("f2count_"))
    def handle_format2_post_count(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            return

        num_str = call.data.replace("f2count_", "")
        if num_str == "pin":
            user_steps[chat_id]["pin_option"] = True
            user_steps[chat_id]["post_count"] = 1
        else:
            user_steps[chat_id]["pin_option"] = False
            try:
                cnt = int(num_str)
            except:
                cnt = 1
            user_steps[chat_id]["post_count"] = cnt

        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id)
        confirm_format2_payment(chat_id)

    def confirm_format2_payment(chat_id):
        """
        Итоговая сумма = цена * (кол-во или 1.6 при «Закреп»)
        """
        price_one = user_steps[chat_id]["chatgroup_price"]
        cnt = user_steps[chat_id]["post_count"]
        pin = user_steps[chat_id].get("pin_option", False)

        if pin:
            total_sum = float(price_one) * 1.6
        else:
            total_sum = float(price_one) * cnt

        user_steps[chat_id]["total_sum"] = total_sum

        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("Оплатить", callback_data="f2pay_now"),
            types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation")
        )
        text = (
            f"Цена за 1 размещение: {price_one} руб.\n"
            f"Вы выбрали: {'Закреп (× 1.6 )' if pin else str(cnt) + ' размещений'}.\n"
            f"Итого: {total_sum} руб.\n\nОплатить?"
        )
        bot.send_message(chat_id, text, reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data == "f2pay_now")
    def handle_f2_pay_now(call: telebot.types.CallbackQuery):
        """
        1) Списываем total_sum
        2) Переходим к оплате маркировки
        """
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            return
        total_sum = user_steps[chat_id]["total_sum"]

        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            if not user:
                bot.answer_callback_query(call.id, "Пользователь не найден в БД.", show_alert=True)
                return

            balance_decimal = user.balance
            total_decimal = Decimal(str(total_sum))

            if balance_decimal < total_decimal:
                bot.answer_callback_query(call.id, "Недостаточно средств. Пополните баланс!", show_alert=True)
                return

            user.balance = balance_decimal - total_decimal
            session.commit()

        bot.answer_callback_query(call.id, "Оплата за размещение произведена.")
        ask_format2_marking_fee(chat_id)

    def ask_format2_marking_fee(chat_id):
        """
        Дополнительная оплата маркировки (фикс. 50 руб).
        """
        marking_fee = 50.0
        user_steps[chat_id]["marking_fee"] = marking_fee

        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("Оплатить маркировку", callback_data="f2pay_marking"),
            types.InlineKeyboardButton("Отмена", callback_data="cancel_ad_creation")
        )
        bot.send_message(
            chat_id,
            f"Теперь необходимо оплатить маркировку объявления ({marking_fee} руб.). Оплатить?",
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda call: call.data == "f2pay_marking")
    def handle_f2pay_marking(call: telebot.types.CallbackQuery):
        """
        Списываем оплату за маркировку, затем финальное создание объявления.
        """
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            return
        marking_fee = user_steps[chat_id].get("marking_fee", 50.0)

        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            if not user:
                bot.answer_callback_query(call.id, "Пользователь не найден.", show_alert=True)
                return

            if user.balance < Decimal(str(marking_fee)):
                bot.answer_callback_query(call.id, "Недостаточно средств для оплаты маркировки!", show_alert=True)
                return

            user.balance = user.balance - Decimal(str(marking_fee))
            session.commit()

        bot.answer_callback_query(call.id, "Маркировка оплачена.")
        finalize_format2_save(chat_id)

    def finalize_format2_save(chat_id):
        """
        Сохраняем объявление Формата №2 и отправляем в MARKIROVKA_GROUP_ID весь альбом.
        """
        d = user_steps[chat_id]
        photos = d["photos"]
        title = d["title"]
        desc = d["description"]
        cg_id = d["chatgroup_id"]
        post_cnt = d["post_count"]
        total_sum = d["total_sum"]

        # 1) Сохраняем в БД и вытаскиваем user/чат до закрытия сессии
        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            cg = session.query(ChatGroup).filter_by(id=cg_id).first()
            if not user:
                bot.send_message(chat_id, "Пользователь не найден в БД.", reply_markup=main_menu_keyboard())
                user_steps.pop(chat_id, None)
                return

            username = d["username_link"]
            inn_info = user.inn or "—"
            fio_info = user.full_name or user.company_name or "—"
            chat_title = cg.title if cg else "—"

            ad_obj = Ad(
                user_id=user.id,
                inline_button_text=title,
                text=desc,
                photos=",".join(photos) if photos else "",
                status="pending",
                ad_type="format2"
            )
            session.add(ad_obj)
            session.commit()
            ad_id = ad_obj.id

        # 2) Формируем подпись
        cap = (
            f"<b>Биржа ADIX (Формат №2) #{ad_id}</b>\n"
            f"Название: {title}\n"
            f"Описание: {desc}\n"
            f"ФИО: {fio_info}\n"
            f"ИНН: {inn_info}\n"
            f"Контакты: {username}\n"
            f"Чат: {chat_title}\n"
            f"Размещений: {post_cnt}\n"
            f"Итого: {total_sum} руб.\n"
            f"Статус: {rus_status('pending')}"
        )
        kb_mod = types.InlineKeyboardMarkup()
        kb_mod.add(
            types.InlineKeyboardButton("✅ Принять", callback_data=f"approve_ad_{ad_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_ad_{ad_id}")
        )
        kb_mod.add(types.InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_ad_{ad_id}"))

        # 3) Отправляем весь альбом в чат маркировки
        if photos:
            media = []
            for idx, file_id in enumerate(photos):
                if idx == 0:
                    media.append(types.InputMediaPhoto(media=file_id, caption=cap, parse_mode="HTML"))
                else:
                    media.append(types.InputMediaPhoto(media=file_id))
            bot.send_media_group(MARKIROVKA_GROUP_ID, media)
            bot.send_message(MARKIROVKA_GROUP_ID, "Действия модератора:", reply_markup=kb_mod)
        else:
            bot.send_message(MARKIROVKA_GROUP_ID, cap, parse_mode="HTML", reply_markup=kb_mod)

        bot.send_message(
            chat_id,
            f"Ваше объявление (Формат 2) #{ad_id} отправлено на проверку!",
            reply_markup=main_menu_keyboard()
        )
        user_steps.pop(chat_id, None)

def start_format2_flow_direct(bot: telebot.TeleBot,
                              message: telebot.types.Message,
                              user_steps: dict):
    """
    Запуск «Формата №2» напрямую из личного кабинета
    (кнопка «Выложить на БИРЖЕ ADIX» в profile.py).
    """
    chat_id = message.chat.id

    # --- базовая проверка блокировки -----------------------------------
    with SessionLocal() as sess:
        usr = sess.query(User).get(chat_id)
        if usr and usr.is_banned:
            bot.send_message(
                chat_id,
                "Ваш аккаунт заблокирован, объявления размещать нельзя.",
                reply_markup=main_menu_keyboard()
            )
            return

    # --- инициализируем state так же, как  adix_market_start ------------
    user_steps[chat_id] = {
        "format": "format2",
        "title": None,
        "description": None,
        "photos": [],
        "fio": None,
        "company_name": None,
        "inn": None,
        "username_link": None,

        "region": None,
        "chatgroup_id": None,
        "chatgroup_price": 0.0,
        "post_count": 1,
        "total_sum": 0.0,
        "selections": [],
        "marking_fee": 0.0
    }

    # --- вытаскиваем ссылку на первый шаг, которую экспортировал
    #     register_add_ads_handlers
    start_fn = globals().get("_start_format2_flow_fn")
    if not start_fn:
        # Это случится только если  register_add_ads_handlers
        # почему‑то ещё не был вызван.
        bot.send_message(chat_id,
                         "Ошибка инициализации Формата №2. "
                         "Попробуйте /restart или сообщите администратору.",
                         reply_markup=main_menu_keyboard())
        return

    # --- запускаем поток ------------------------------------------------
    start_fn(chat_id)