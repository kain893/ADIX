#!/usr/bin/env python3
import telebot
from telebot import types
from decimal import Decimal
from datetime import datetime
from datetime import datetime, timedelta
from database import SessionLocal, User, Ad, TopUp, Withdrawal, AdChat, AdChatMessage, ChatGroup
from utils import main_menu_keyboard, rus_status
from config import CITY_STRUCTURE, MARKETING_GROUP_ID

ADMIN_EXTENSION_CHAT_ID = -1002288960086  # куда летят заявки на продление
ADMIN_PROFILE_CHAT_ID = -1002288960086   # чат, куда летят заявки на смену ФИО / ИНН / компании
ADMIN_TOPUP_CHAT_ID = -1002586768630 # пополнение
ADMIN_WITHDRAW_CHAT_ID = -1002586768630 # вывод

# заявки, ожидающие одобрения админом
pending_profile_changes = {}         # change_id -> {"user_id": …, "field": …, "value": …}
def register_profile_handlers(bot: telebot.TeleBot, user_steps: dict):
    # ------------------- Главное меню / Личный кабинет -------------------
    @bot.message_handler(func=lambda m: m.text == "📜Личный кабинет")
    def cabinet_menu(message: telebot.types.Message):
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.row("Мои объявления", "Настройки профиля")
        kb.row("Пополнить баланс", "Вывод баланса")
        kb.row("🔙 Главное меню")
        bot.send_message(message.chat.id, "Личный кабинет:", reply_markup=kb)

    @bot.message_handler(func=lambda m: m.text == "🔙 Главное меню")
    def back_to_main(message: telebot.types.Message):
        bot.send_message(message.chat.id, "Главное меню:", reply_markup=main_menu_keyboard())

    # ------------------- Мои объявления -------------------
    @bot.message_handler(func=lambda m: m.text == "Мои объявления")
    def my_ads(message: telebot.types.Message):
        user_id = message.chat.id
        with SessionLocal() as sess:
            ads = sess.query(Ad).filter_by(user_id=user_id).all()

        if not ads:
            return bot.send_message(user_id, "У вас нет объявлений.", reply_markup=main_menu_keyboard())

        kb = types.InlineKeyboardMarkup(row_width=1)
        for ad in ads:
            status_ru = rus_status(ad.status)
            note = "" if ad.is_active else " / Неактивно"
            btn = f"#{ad.id} ({status_ru}{note})"
            kb.add(types.InlineKeyboardButton(btn, callback_data=f"profile_my_ad_{ad.id}"))
        kb.add(types.InlineKeyboardButton("Закрыть", callback_data="profile_myads_close"))

        bot.send_message(user_id, "Ваши объявления:", reply_markup=kb)

    # ---------- Просмотр одного объявления и кнопка «Продлить» ----------
    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("profile_my_ad_") or c.data == "profile_myads_close"
    )
    def handle_profile_my_ads(call: types.CallbackQuery):
        user_id = call.from_user.id
        data = call.data

        # закрыть список
        if data == "profile_myads_close":
            bot.delete_message(user_id, call.message.message_id)
            return bot.answer_callback_query(call.id)

        # вытянуть ID
        ad_id = int(data.split("_")[-1])
        with SessionLocal() as sess:
            ad = sess.query(Ad).get(ad_id)

        if not ad or ad.user_id != user_id:
            return bot.answer_callback_query(call.id, "Объявление не найдено.", show_alert=True)

        # расчёт дней
        days_passed = (datetime.utcnow() - ad.created_at).days
        days_left   = max(0, 30 - days_passed)
        expired     = days_passed >= 30
        price       = ad.price or Decimal("0")
        fee         = (price * Decimal("0")).quantize(Decimal("0"))

        caption = (
            f"<b>Объявление #{ad.id}</b>\n"
            f"Статус: {rus_status(ad.status)}{' / Неактивно' if not ad.is_active else ''}\n\n"
            f"{ad.text}\n\n"
            f"Цена: {price} ₽\n"
            f"Размещено: {ad.created_at.strftime('%d.%m.%Y')}\n"
            + ("⛔️ Срок истёк!\n" if expired else f"Осталось дней: {days_left}\n")
        )

        kb = types.InlineKeyboardMarkup(row_width=1)
        # кнопка продления, если уже неактивно, срок вышел или осталось <5 дней
        if not ad.is_active or expired or days_left < 5:
            kb.add(types.InlineKeyboardButton(
                f"Продлить на 30 дней (Бесплатно)",
                callback_data=f"extend_ad_{ad.id}"
            ))
        kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="profile_back_to_ads"))

        # обновляем сообщение
        bot.delete_message(user_id, call.message.message_id)
        bot.send_message(user_id, caption, parse_mode="HTML", reply_markup=kb)
        bot.answer_callback_query(call.id)

    # ------------------- «Назад» к списку объявлений -------------------
    @bot.callback_query_handler(func=lambda c: c.data == "profile_back_to_ads")
    def back_to_ads(call: types.CallbackQuery):
        bot.answer_callback_query(call.id)
        # просто вызываем логику «Мои объявления»
        my_ads(call.message)

    # ------------------- Запрос на продление -------------------
    @bot.callback_query_handler(func=lambda c: c.data.startswith("extend_ad_"))
    def extend_ad_callback(call: types.CallbackQuery):
        user_id = call.from_user.id
        ad_id   = int(call.data.split("_")[-1])

        with SessionLocal() as sess:
            ad = sess.query(Ad).get(ad_id)

        if not ad or ad.user_id != user_id:
            return bot.answer_callback_query(call.id, "Объявление не найдено.", show_alert=True)

        # шлём заявку админу
        kb_admin = types.InlineKeyboardMarkup(row_width=2)
        kb_admin.add(
            types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_ext_{ad_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_ext_{ad_id}")
        )
        bot.answer_callback_query(call.id)
        bot.send_message(
            ADMIN_EXTENSION_CHAT_ID,
            f"Пользователь @{call.from_user.username or user_id} запрашивает продление объявления #{ad_id} на 30 дней.",
            reply_markup=kb_admin
        )
        bot.send_message(user_id, "Запрос на продление отправлен администрации. Ожидайте решения.")

    # ------------------- Обработчик одобрения/отклонения -------------------
    @bot.callback_query_handler(
        func=lambda c: c.data.startswith("approve_ext_") or c.data.startswith("reject_ext_")
    )
    def handle_extension_decision(call: types.CallbackQuery):
        admin_id = call.from_user.id
        if admin_id not in ADMIN_IDS:
            return bot.answer_callback_query(call.id, "Нет прав.", show_alert=True)

        action, _, ad_id_str = call.data.partition("_ext_")
        ad_id = int(ad_id_str)

        with SessionLocal() as sess:
            ad = sess.query(Ad).get(ad_id)
            if not ad:
                bot.answer_callback_query(call.id, "Объявление не найдено.", show_alert=True)
                return

            # удаляем кнопки под заявкой
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)

            if action == "approve":
                ad.is_active  = True
                ad.created_at = datetime.utcnow()
                sess.commit()
                bot.send_message(call.message.chat.id, f"✅ Продление #{ad_id} одобрено.")
                bot.send_message(ad.user_id,     f"Ваше объявление #{ad_id} продлено на 30 дней и снова активно!")
            else:
                bot.send_message(call.message.chat.id, f"❌ Продление #{ad_id} отклонено.")
                bot.send_message(ad.user_id,     f"Продление объявления #{ad_id} было отклонено.")

        bot.answer_callback_query(call.id)

        # Повторяем логику my_ads
        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            if not user:
                bot.send_message(chat_id, "Вы не зарегистрированы.", reply_markup=main_menu_keyboard())
                return

            ads_list = session.query(Ad).filter_by(user_id=user.id).all()
            if not ads_list:
                bot.send_message(chat_id, "У вас нет объявлений.", reply_markup=main_menu_keyboard())
                return

            kb = types.InlineKeyboardMarkup(row_width=1)
            for ad_obj in ads_list:
                btn_text = f"Объявление #{ad_obj.id} ({rus_status(ad_obj.status)})"
                callback_data = f"profile_my_ad_{ad_obj.id}"
                kb.add(types.InlineKeyboardButton(btn_text, callback_data=callback_data))

            kb.add(types.InlineKeyboardButton("Закрыть", callback_data="profile_myads_close"))
            bot.send_message(chat_id, "Ваши объявления:", reply_markup=kb)

    # ---------- продление на 30 дней --------------------
    @bot.callback_query_handler(func=lambda c: c.data.startswith("extend_ad_"))
    def extend_ad_callback(call: telebot.types.CallbackQuery):
        """
        Пользователь запросил бесплатное продление на 30 дней.
        Отправляем заявку в админ-чат.
        """
        ad_id = int(call.data.replace("extend_ad_", ""))
        user_id = call.from_user.id

        # Проверяем владельца и существование
        with SessionLocal() as sess:
            ad = sess.query(Ad).get(ad_id)
            if not ad or ad.user_id != user_id:
                bot.answer_callback_query(call.id, "Объявление не найдено.", show_alert=True)
                return

        # Формируем запрос для админа
        kb_admin = types.InlineKeyboardMarkup(row_width=2)
        kb_admin.add(
            types.InlineKeyboardButton("✅ Одобрить продление", callback_data=f"approve_ext_{ad_id}"),
            types.InlineKeyboardButton("❌ Отклонить продление", callback_data=f"reject_ext_{ad_id}")
        )

        bot.answer_callback_query(call.id)
        bot.send_message(
            ADMIN_EXTENSION_CHAT_ID,
            f"Пользователь @{call.from_user.username or user_id} (ID {user_id})\n"
            f"запрашивает бесплатное продление объявления #{ad_id} на 30 дней.",
            reply_markup=kb_admin
        )
        bot.send_message(
            user_id,
            "Запрос на продление отправлен администрации. Ожидайте решения."
        )

    # ------------------- Разместить существующее объявление на бирже -------------------
    @bot.callback_query_handler(func=lambda call: call.data.startswith("profile_myad_exchange_"))
    def profile_myad_exchange_callback(call: telebot.types.CallbackQuery):
        """
        Запуск "мини-флоу" для существующего объявления, чтобы перевести его в Формат2.
        """
        chat_id = call.message.chat.id
        ad_id_str = call.data.replace("profile_myad_exchange_", "")
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
            if ad_obj.ad_type == "format2":
                bot.answer_callback_query(call.id, "Это объявление уже на бирже!", show_alert=True)
                return

            user = session.query(User).filter_by(id=chat_id).first()
            if not user or user.is_banned:
                bot.answer_callback_query(call.id, "Вы не можете размещать объявления на бирже (бан или нет регистрации).", show_alert=True)
                return

        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id)

        user_steps[chat_id] = {
            "exchange_existing_ad": True,
            "ad_id": ad_id,
            "region": None,
            "chatgroup_id": None,
            "chatgroup_price": 0.0,
            "post_count": 1,
            "total_sum": 0.0,
            "need_fio": False,
            "need_inn": False,
            "need_company": False
        }
        check_and_ask_missing_profile_data(chat_id)

    def check_and_ask_missing_profile_data(chat_id):
        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            if not user:
                bot.send_message(chat_id, "Ошибка: пользователь не найден.")
                user_steps.pop(chat_id, None)
                return

            # ФИО
            if not user.full_name:
                user_steps[chat_id]["need_fio"] = True
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_exchange_flow"))
                bot.send_message(chat_id, "Укажите ФИО (например, Иванов Иван Иванович):", reply_markup=kb)
                bot.register_next_step_handler_by_chat_id(chat_id, process_exchange_fio)
                return

            # Компания
            if not user.company_name:
                user_steps[chat_id]["need_company"] = True
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("Пропустить", callback_data="exchange_company_skip"))
                kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_exchange_flow"))
                bot.send_message(chat_id, "Укажите название компании (если есть) или пропустите:", reply_markup=kb)
                bot.register_next_step_handler_by_chat_id(chat_id, process_exchange_company)
                return

            # ИНН
            if not user.inn:
                user_steps[chat_id]["need_inn"] = True
                digits_needed = 13 if user.company_name else 12
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_exchange_flow"))
                bot.send_message(
                    chat_id,
                    f"Укажите ИНН ({digits_needed} цифр):",
                    reply_markup=kb
                )
                bot.register_next_step_handler_by_chat_id(chat_id, process_exchange_inn)
                return

        # Если всё есть – сразу идём к выбору региона
        ask_exchange_region(chat_id)

    def process_exchange_fio(message: telebot.types.Message):
        chat_id = message.chat.id
        fio = message.text.strip()
        if not fio:
            check_and_ask_missing_profile_data(chat_id)
            return
        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            if user:
                user.full_name = fio
                session.commit()
        check_and_ask_missing_profile_data(chat_id)

    @bot.callback_query_handler(func=lambda call: call.data == "exchange_company_skip")
    def exchange_company_skip(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        bot.delete_message(chat_id, call.message.message_id)
        bot.clear_step_handler_by_chat_id(chat_id)
        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            if user:
                user.company_name = None
                session.commit()
        bot.answer_callback_query(call.id, "Компания пропущена.")
        check_and_ask_missing_profile_data(chat_id)

    def process_exchange_company(message: telebot.types.Message):
        chat_id = message.chat.id
        company_name = message.text.strip()
        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            if user:
                user.company_name = company_name
                session.commit()
        check_and_ask_missing_profile_data(chat_id)

    def process_exchange_inn(message: telebot.types.Message):
        chat_id = message.chat.id
        inn_str = message.text.strip()
        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            if not user:
                bot.send_message(chat_id, "Ошибка: пользователь не найден.")
                user_steps.pop(chat_id, None)
                return
            digits_needed = 13 if user.company_name else 12
            if len(inn_str) != digits_needed or not inn_str.isdigit():
                check_and_ask_missing_profile_data(chat_id)
                return
            user.inn = inn_str
            session.commit()
        check_and_ask_missing_profile_data(chat_id)

    @bot.callback_query_handler(func=lambda call: call.data == "cancel_exchange_flow")
    def cancel_exchange_flow(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        bot.delete_message(chat_id, call.message.message_id)
        bot.clear_step_handler_by_chat_id(chat_id)
        user_steps.pop(chat_id, None)
        bot.answer_callback_query(call.id, "Размещение на бирже отменено.")
        bot.send_message(chat_id, "Операция отменена.", reply_markup=main_menu_keyboard())

    # ---- Выбор региона и чата ----------------------------------------
    def ask_exchange_region(chat_id):
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Москва", callback_data="exchg_region_moscow"))
        kb.add(types.InlineKeyboardButton("Московская область", callback_data="exchg_region_mo"))
        kb.add(types.InlineKeyboardButton("Города РФ", callback_data="exchg_region_rf"))
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_exchange_flow"))
        bot.send_message(chat_id, "Выберите регион для размещения:", reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data in ["exchg_region_moscow", "exchg_region_mo", "exchg_region_rf"])
    def handle_exchange_region_choice(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            return

        if call.data == "exchg_region_moscow":
            user_steps[chat_id]["region"] = "moscow"
        elif call.data == "exchg_region_mo":
            user_steps[chat_id]["region"] = "mo"
        else:
            user_steps[chat_id]["region"] = "rf"

        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id)
        ask_exchange_chatgroup(chat_id)

    def ask_exchange_chatgroup(chat_id):
        region_key = user_steps[chat_id]["region"]

        def detect_region(title: str) -> str:
            low = title.lower()
            if "москв" in low and "область" not in low:
                return "moscow"
            elif "область" in low:
                return "mo"
            else:
                return "rf"

        with SessionLocal() as session:
            all_chats = session.query(ChatGroup).filter_by(is_active=True).all()

        filtered = []
        for c in all_chats:
            r = detect_region(c.title)
            if r == region_key:
                filtered.append(c)

        if not filtered:
            bot.send_message(chat_id, "В выбранном регионе нет доступных чатов. Обратитесь к администратору.")
            user_steps.pop(chat_id, None)
            return

        user_steps[chat_id]["exchg_chat_list"] = filtered
        user_steps[chat_id]["exchg_chat_page"] = 0
        show_exchange_chats_page(chat_id)

    def show_exchange_chats_page(chat_id):
        data = user_steps[chat_id]
        chats = data["exchg_chat_list"]
        page = data["exchg_chat_page"]
        page_size = 10

        start_i = page * page_size
        end_i = min(start_i + page_size, len(chats))
        sublist = chats[start_i:end_i]

        kb = types.InlineKeyboardMarkup()
        for c in sublist:
            btn_text = f"{c.title} (Цена: {c.price} руб.)"
            cb_data = f"exchg_pickchat_{c.id}"
            kb.add(types.InlineKeyboardButton(btn_text, callback_data=cb_data))

        nav_row = []
        if page > 0:
            nav_row.append(types.InlineKeyboardButton("⏪Назад", callback_data="exchg_chatpage_prev"))
        if end_i < len(chats):
            nav_row.append(types.InlineKeyboardButton("Вперёд⏩", callback_data="exchg_chatpage_next"))
        if nav_row:
            kb.row(*nav_row)

        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_exchange_flow"))

        bot.send_message(chat_id, f"Выберите чат для размещения (стр. {page + 1}):", reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data in ["exchg_chatpage_prev", "exchg_chatpage_next"])
    def handle_exchg_chatpage_nav(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            return

        if call.data == "exchg_chatpage_prev":
            user_steps[chat_id]["exchg_chat_page"] -= 1
        else:
            user_steps[chat_id]["exchg_chat_page"] += 1

        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id)
        show_exchange_chats_page(chat_id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("exchg_pickchat_"))
    def handle_exchg_pick_chat(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            return

        cg_id_str = call.data.replace("exchg_pickchat_", "")
        try:
            cg_id = int(cg_id_str)
        except:
            bot.answer_callback_query(call.id, "Некорректный чат", show_alert=True)
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
        ask_exchange_post_count(chat_id)

    def ask_exchange_post_count(chat_id):
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("1 размещение", callback_data="exchg_cnt_1"),
            types.InlineKeyboardButton("5 размещений", callback_data="exchg_cnt_5")
        )
        kb.add(
            types.InlineKeyboardButton("10 размещений", callback_data="exchg_cnt_10"),
            types.InlineKeyboardButton("Закреп (×1.6)", callback_data="exchg_cnt_pin")
        )
        kb.add(types.InlineKeyboardButton("Отмена", callback_data="cancel_exchange_flow"))
        bot.send_message(chat_id, "Сколько размещений хотите оплатить?", reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("exchg_cnt_"))
    def handle_exchg_cnt(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            return

        val_str = call.data.replace("exchg_cnt_", "")
        if val_str == "pin":
            user_steps[chat_id]["exchg_pin_option"] = True
            user_steps[chat_id]["post_count"] = 1
        else:
            user_steps[chat_id]["exchg_pin_option"] = False
            try:
                cnt = int(val_str)
            except:
                cnt = 1
            user_steps[chat_id]["post_count"] = cnt

        bot.delete_message(chat_id, call.message.message_id)
        bot.answer_callback_query(call.id)
        confirm_exchange_payment(chat_id)

    def confirm_exchange_payment(chat_id):
        price_one = user_steps[chat_id]["chatgroup_price"]
        cnt = user_steps[chat_id]["post_count"]
        pin = user_steps[chat_id].get("exchg_pin_option", False)

        if pin:
            total_sum = float(price_one) * 1.6
        else:
            total_sum = float(price_one) * cnt

        user_steps[chat_id]["total_sum"] = total_sum

        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("Оплатить", callback_data="exchg_pay_now"),
            types.InlineKeyboardButton("Отмена", callback_data="cancel_exchange_flow")
        )
        text = (
            f"Цена за 1 размещение: {price_one} руб.\n"
            f"Вы выбрали: {'Закреп (×1.6)' if pin else str(cnt) + ' размещений'}.\n"
            f"Итого к оплате: {total_sum} руб.\n\nОплатить?"
        )
        bot.send_message(chat_id, text, reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: call.data == "exchg_pay_now")
    def handle_exchg_pay_now(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            return
        total_sum = user_steps[chat_id]["total_sum"]

        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            if not user:
                bot.answer_callback_query(call.id, "Ошибка: пользователь не найден.", show_alert=True)
                return

            balance_dec = user.balance
            need_dec = Decimal(str(total_sum))
            if balance_dec < need_dec:
                bot.answer_callback_query(call.id, "Недостаточно средств. Пополните баланс!", show_alert=True)
                return

            user.balance = balance_dec - need_dec
            session.commit()

        bot.answer_callback_query(call.id, "Оплата размещения произведена.")
        ask_exchange_marking_fee(chat_id)

    def ask_exchange_marking_fee(chat_id):
        marking_fee = 50.0
        user_steps[chat_id]["exchg_marking_fee"] = marking_fee
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("Оплатить маркировку", callback_data="exchg_pay_marking"),
            types.InlineKeyboardButton("Отмена", callback_data="cancel_exchange_flow")
        )
        bot.send_message(
            chat_id,
            f"Теперь необходимо оплатить маркировку объявления ({marking_fee} руб.). Оплатить?",
            reply_markup=kb
        )

    @bot.callback_query_handler(func=lambda call: call.data == "exchg_pay_marking")
    def handle_exchg_pay_marking(call: telebot.types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id not in user_steps:
            return
        marking_fee = user_steps[chat_id].get("exchg_marking_fee", 50.0)

        with SessionLocal() as session:
            user = session.query(User).filter_by(id=chat_id).first()
            if not user:
                bot.answer_callback_query(call.id, "Ошибка: пользователь не найден.", show_alert=True)
                return
            if user.balance < Decimal(str(marking_fee)):
                bot.answer_callback_query(call.id, "Недостаточно средств для оплаты маркировки!", show_alert=True)
                return
            user.balance = user.balance - Decimal(str(marking_fee))
            session.commit()

        bot.answer_callback_query(call.id, "Маркировка оплачена.")
        finalize_exchange_ad(chat_id)

    def finalize_exchange_ad(chat_id):
        data = user_steps[chat_id]
        ad_id = data["ad_id"]
        post_cnt = data["post_count"]
        total_sum = data["total_sum"]
        cg_id = data["chatgroup_id"]

        with SessionLocal() as session:
            ad_obj = session.query(Ad).filter_by(id=ad_id).first()
            if not ad_obj:
                bot.send_message(chat_id, "Ошибка: объявление не найдено.", reply_markup=main_menu_keyboard())
                user_steps.pop(chat_id, None)
                return

            user = session.query(User).filter_by(id=chat_id).first()
            if not user:
                bot.send_message(chat_id, "Ошибка: пользователь не найден.", reply_markup=main_menu_keyboard())
                user_steps.pop(chat_id, None)
                return

            # Переводим объявление в формат2
            ad_obj.ad_type = "format2"
            ad_obj.status = "pending"
            session.commit()

            cg = session.query(ChatGroup).filter_by(id=cg_id).first()
            inn_info = user.inn or "—"
            fio_info = user.full_name or user.company_name or "—"

            photos_list = []
            if ad_obj.photos:
                photos_list = ad_obj.photos.split(",")

            cap = (
                f"Биржа ADIX (Формат2 - существующее объявление)\n\n"
                f"ID объявления: {ad_obj.id}\n"
                f"Кнопка: {ad_obj.inline_button_text or '—'}\n"
                f"Текст: {ad_obj.text}\n"
                f"ФИО/Компания: {fio_info}\n"
                f"ИНН: {inn_info}\n"
                f"Контакты (username): @{user.username if user.username else user.id}\n"
                f"Выбран чат: {cg.title if cg else '—'}\n"
                f"Кол-во размещений: {post_cnt}\n"
                f"Итоговая сумма: {total_sum} руб.\n\n"
                "Требуется проверка!"
            )

            kb_mod = types.InlineKeyboardMarkup()
            kb_mod.add(
                types.InlineKeyboardButton("Принять", callback_data=f"approve_ad_{ad_obj.id}"),
                types.InlineKeyboardButton("Отклонить", callback_data=f"reject_ad_{ad_obj.id}")
            )
            kb_mod.add(types.InlineKeyboardButton("Редактировать", callback_data=f"edit_ad_{ad_obj.id}"))

            MARKIROVKA_GROUP_ID = -1002288960086
            if photos_list:
                bot.send_photo(MARKIROVKA_GROUP_ID, photos_list[0], caption=cap, reply_markup=kb_mod)
            else:
                bot.send_message(MARKIROVKA_GROUP_ID, cap, reply_markup=kb_mod)

        bot.send_message(
            chat_id,
            f"Объявление #{ad_obj.id} отправлено на модерацию (Формат2). Статус: {rus_status('pending')}.",
            reply_markup=main_menu_keyboard()
        )
        user_steps.pop(chat_id, None)

    # ---------------- Настройки профиля + изменение реквизитов -----------------
    @bot.message_handler(func=lambda m: m.text == "Настройки профиля")
    def profile_settings(message: telebot.types.Message):
        user_id = message.chat.id
        with SessionLocal() as sess:
            user = sess.query(User).get(user_id)
            if not user:
                return bot.send_message(user_id, "Вы не зарегистрированы.", reply_markup=main_menu_keyboard())

            ad_cnt = sess.query(Ad).filter_by(user_id=user_id).count()

            txt = (
                f"<b>ID</b>: <code>{user.id}</code>\n"
                f"<b>Username</b>: @{user.username or '—'}\n"
                f"<b>Баланс</b>: {user.balance} ₽\n"
                f"<b>Объявлений</b>: {ad_cnt}\n"
                f"<b>Статус</b>: {'🚫 Забанен' if user.is_banned else '✅ Активен'}\n\n"
                f"<b>ФИО</b>: {user.full_name or '—'}\n"
                f"<b>ИНН</b>: {user.inn or '—'}\n"
                f"<b>Компания</b>: {user.company_name or '—'}"
            )

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton(
                "🖊 Изменить ФИО" if user.full_name else "➕ Добавить ФИО",
                callback_data="edit_profile_fio"
            ),
            types.InlineKeyboardButton(
                "🖊 Изменить ИНН" if user.inn else "➕ Добавить ИНН",
                callback_data="edit_profile_inn"
            ),
            types.InlineKeyboardButton(
                "🖊 Изменить компанию" if user.company_name else "➕ Добавить компанию",
                callback_data="edit_profile_company"
            )
        )
        kb.add(types.InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_main"))

        bot.send_message(user_id, txt, parse_mode="HTML", reply_markup=kb)

    @bot.callback_query_handler(func=lambda c: c.data == "back_to_main")
    def back_from_profile(call: telebot.types.CallbackQuery):
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, "Главное меню:", reply_markup=main_menu_keyboard())
        bot.answer_callback_query(call.id)

    # ---------- шаг 1: пользователь хочет изменить поле ----------
    @bot.callback_query_handler(func=lambda c: c.data.startswith("edit_profile_"))
    def ask_new_profile_value(call: telebot.types.CallbackQuery):
        field = call.data.replace("edit_profile_", "")  # fio / inn / company
        user_steps[call.from_user.id] = {"edit_field": field}

        hints = {
            "fio": "Введите новое ФИО (пример: Иванов Иван Иванович):",
            "inn": "Введите новый ИНН (12 или 13 цифр):",
            "company": "Введите название компании (или «−», чтобы удалить):"
        }
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.from_user.id, hints[field])
        bot.register_next_step_handler(msg, receive_new_profile_value)

    # ---------- шаг 2: получили значение – создаём заявку на одобрение ----------
    def receive_new_profile_value(message: telebot.types.Message):
        uid = message.chat.id
        if uid not in user_steps or "edit_field" not in user_steps[uid]:
            return bot.send_message(uid, "Не найден контекст изменения.")

        field = user_steps[uid]["edit_field"]
        value = message.text.strip()

        # валидация ИНН
        if field == "inn" and not (value.isdigit() and len(value) in (12, 13)):
            return bot.send_message(uid, "ИНН должен содержать 12 или 13 цифр.")

        # создаём уникальный ID заявки
        change_id = int(datetime.utcnow().timestamp() * 1000)
        pending_profile_changes[change_id] = {"user_id": uid, "field": field, "value": value}

        nice_name = {"fio": "ФИО", "inn": "ИНН", "company": "компания"}[field]

        # отправляем админу
        kb_admin = types.InlineKeyboardMarkup(row_width=2)
        kb_admin.add(
            types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_profile_{change_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_profile_{change_id}")
        )
        bot.send_message(
            ADMIN_PROFILE_CHAT_ID,
            f"Заявка #{change_id}\n"
            f"Пользователь <code>{uid}</code> хочет изменить <b>{nice_name}</b> на:\n\n"
            f"<code>{value}</code>",
            parse_mode="HTML",
            reply_markup=kb_admin
        )

        bot.send_message(uid, f"Заявка на изменение «{nice_name}» отправлена администратору.")
        user_steps.pop(uid, None)

    # ---------- шаг 3: админ одобряет / отклоняет ----------
    @bot.callback_query_handler(func=lambda c: c.data.startswith(("approve_profile_", "reject_profile_")))
    def admin_profile_decision(call: telebot.types.CallbackQuery):
        approve = call.data.startswith("approve_profile_")
        change_id = int(call.data.split("_")[-1])

        data = pending_profile_changes.pop(change_id, None)
        if not data:
            return bot.answer_callback_query(call.id, "Заявка не найдена / уже обработана.", show_alert=True)

        user_id = data["user_id"]
        field = data["field"]
        value = data["value"]
        nice = {"fio": "ФИО", "inn": "ИНН", "company": "компания"}[field]

        if approve:
            with SessionLocal() as sess:
                user = sess.query(User).get(user_id)
                if user:
                    if field == "fio":
                        user.full_name = value
                    elif field == "inn":
                        user.inn = value
                    else:
                        user.company_name = None if value == "−" else value
                    sess.commit()

            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            bot.send_message(call.message.chat.id, f"✅ Заявка #{change_id} одобрена.")
            bot.send_message(user_id, f"Ваше {nice_name(nice)} изменено на:\n{value}")
        else:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            bot.send_message(call.message.chat.id, f"❌ Заявка #{change_id} отклонена.")
            bot.send_message(user_id, f"Администратор отклонил изменение «{nice}».")

        bot.answer_callback_query(call.id)

    # ---------- helper для корректного склонения ----------
    def nice_name(src: str) -> str:
        return {"ФИО": "ФИО", "ИНН": "ИНН", "компания": "компанию"}[src]

    # =============== ПОПОЛНЕНИЕ БАЛАНСА ===============
    #
    # 1. /📜Личный кабинет → «Пополнить баланс»
    # 2. указываем сумму → выбираем карту
    # 3. бот просит прислать скрин/чек ► пользователь шлёт фото/док
    # 4. бот показывает кнопку «✅ Подтвердить перевод» / «❌ Отменить»
    # 5. при подтверждении заявка + скрин летят в админ‑чат
    # 6. админ жмёт «Одобрить / Отклонить» (коллбэки были реализованы ранее)

    # -----------------------------------------------------------------------------
    @bot.message_handler(func=lambda m: m.text == "Пополнить баланс")
    def add_balance(message: telebot.types.Message):
        msg = bot.send_message(
            message.chat.id,
            "Введите сумму , на которую хотите пополнить баланс:"
        )
        bot.register_next_step_handler(msg, process_topup_amount)

    def process_topup_amount(message: telebot.types.Message):
        chat_id = message.chat.id
        try:
            amount = float(message.text.replace(",", "."))
            if not 50 <= amount <= 100000:
                raise ValueError
        except ValueError:
            return bot.send_message(chat_id,
                                    "Некорректная сумма. Нужно от 50 до 100000 руб.\nПопробуйте снова.")

        user_steps[chat_id] = {"topup": {"amount": amount}}
        ask_which_card(chat_id)

    def ask_which_card(chat_id: int):
        amount = user_steps[chat_id]["topup"]["amount"]
        tmp_id = str(int(datetime.now().timestamp()))

        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Сбер", callback_data=f"topup_card_sber_{tmp_id}"),
               types.InlineKeyboardButton("Тинькофф", callback_data=f"topup_card_tnk_{tmp_id}"))
        kb.add(types.InlineKeyboardButton("Альфа‑Банк", callback_data=f"topup_card_alfa_{tmp_id}"),
               types.InlineKeyboardButton("Отмена", callback_data=f"topup_cancel_{tmp_id}"))

        bot.send_message(
            chat_id,
            f"Сумма пополнения: <b>{amount} руб.</b>\nВыберите карту для перевода:",
            parse_mode="HTML",
            reply_markup=kb
        )

    # ---------- шаг 1: пользователь выбрал карту ----------
    @bot.callback_query_handler(func=lambda c: c.data.startswith("topup_card_"))
    def handle_choose_card(call: telebot.types.CallbackQuery):
        chat_id = call.from_user.id
        card_type, tmp_id = call.data.split("_")[2:]  # sber / tnk / alfa

        cards = {
            "sber": ("Сбер", "2202208053337920"),
            "tnk": ("Тинькофф", "2200701904625982"),
            "alfa": ("Альфа‑Банк", "2200150982580836")
        }
        if card_type not in cards:
            return bot.answer_callback_query(call.id, "Неизвестный тип карты.", show_alert=True)

        system, number = cards[card_type]
        user_steps[chat_id]["topup"].update({"tmp_id": tmp_id,
                                             "card_system": system,
                                             "card_number": number})

        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=(f"Вы выбрали карту <b>{system}</b>\n"
                  f"Номер: <code>{number}</code>\n\n"
                  "Переведите указанную сумму и пришлите скриншот/чек одним сообщением."),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)

    # ---------- шаг 2: ждём фото/док с чеком ----------
    @bot.message_handler(content_types=["photo", "document"],
                         func=lambda m: m.chat.id in user_steps
                                        and "topup" in user_steps[m.chat.id]
                                        and "receipt_file_id" not in user_steps[m.chat.id]["topup"])
    def receive_topup_receipt(message: telebot.types.Message):
        uid = message.chat.id
        flow = user_steps[uid]["topup"]

        # берём file_id
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
        else:
            file_id = message.document.file_id

        flow["receipt_file_id"] = file_id

        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("✅ Подтвердить перевод", callback_data=f"topup_confirm_{flow['tmp_id']}"),
            types.InlineKeyboardButton("❌ Отменить", callback_data=f"topup_cancel_{flow['tmp_id']}")
        )

        bot.send_message(uid,
                         "Спасибо! Проверьте всё и нажмите «Подтвердить» "
                         "или «Отменить».", reply_markup=kb)

    # ---------- шаг 3: подтверждение / отмена ----------
    @bot.callback_query_handler(func=lambda c: c.data.startswith(("topup_confirm_", "topup_cancel_")))
    def finish_topup_flow(call: telebot.types.CallbackQuery):
        uid = call.from_user.id
        flow = user_steps.get(uid, {}).get("topup")
        if not flow:
            return bot.answer_callback_query(call.id, "Сессия пополнения не найдена.", show_alert=True)

        confirm = call.data.startswith("topup_confirm_")
        bot.answer_callback_query(call.id)

        if not confirm:
            user_steps.pop(uid, None)
            return bot.send_message(uid, "Пополнение отменено.", reply_markup=main_menu_keyboard())

        # --- создаём запись в БД ---
        amount = flow["amount"]
        with SessionLocal() as sess:
            topup = TopUp(
                user_id=uid,
                amount=amount,
                status="pending",
                payment_system=flow["card_system"],
                card_number=flow["card_number"]
            )
            sess.add(topup)
            sess.commit()
            topup_id = topup.id
            # подгружаем пользователя, чтобы взять username
            user_obj = sess.query(User).filter_by(id=uid).first()

        # --- уведомляем пользователя ---
        bot.send_message(
            uid,
            f"✅ Заявка на пополнение #{topup_id} на {amount} руб. отправлена администратору.",
            reply_markup=main_menu_keyboard()
        )

        # --- уведомляем админов (со скрином) ---
        user_name = f"@{user_obj.username}" if user_obj and user_obj.username else str(uid)
        caption = (
            f"Заявка #{topup_id}\n"
            f"Пользователь: {user_name}\n"
            f"Сумма: <b>{amount} руб.</b>\n"
            f"Система: {flow['card_system']}\n"
            f"Карта: <code>{flow['card_number']}</code>"
        )
        kb_admin = types.InlineKeyboardMarkup(row_width=2)
        kb_admin.add(
            types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_topup_{topup_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_topup_{topup_id}")
        )
        bot.send_photo(
            ADMIN_TOPUP_CHAT_ID,
            flow["receipt_file_id"],
            caption=caption,
            parse_mode="HTML",
            reply_markup=kb_admin
        )

        # чистим шаг
        user_steps.pop(uid, None)

    # ===================== ВЫВОД БАЛАНСА =====================

    @bot.message_handler(func=lambda m: m.text == "Вывод баланса")
    def withdraw_balance_step1(message: telebot.types.Message):
        """
        Шаг 1 — спрашиваем сумму.
        """
        msg = bot.send_message(message.chat.id,
                               "Введите сумму, которую хотите вывести (минимум 100 руб.):")
        bot.register_next_step_handler(msg, withdraw_balance_step2)

    def withdraw_balance_step2(message: telebot.types.Message):
        """
        Валидируем сумму и спрашиваем карту.
        """
        uid = message.chat.id
        try:
            amount = float(message.text.replace(",", "."))
            if amount < 100:
                raise ValueError
        except ValueError:
            return bot.send_message(uid,
                                    "Некорректная сумма. Минимум — 100 руб. Попробуйте ещё раз.")

        # проверяем баланс
        with SessionLocal() as sess:
            user = sess.query(User).get(uid)
            if not user:
                return bot.send_message(uid, "Вы не зарегистрированы.",
                                        reply_markup=main_menu_keyboard())
            if float(user.balance) < amount:
                return bot.send_message(uid,
                                        f"Недостаточно средств (баланс: {user.balance} руб.).",
                                        reply_markup=main_menu_keyboard())

        # сохраняем этап
        user_steps[uid] = {"withdraw": {"amount": amount}}
        msg = bot.send_message(uid,
                               "Введите <b>номер карты</b> (16 цифр) или счёта, "
                               "на который перевести деньги:",
                               parse_mode="HTML")
        bot.register_next_step_handler(msg, withdraw_balance_step3)

    def withdraw_balance_step3(message: telebot.types.Message):
        """
        Получаем карту, создаём заявку, шлём админу.
        """
        uid = message.chat.id
        flow = user_steps.get(uid, {}).get("withdraw")
        if not flow:
            return bot.send_message(uid, "Не найден контекст вывода средств.")

        card = message.text.strip().replace(" ", "")
        # простая валидация: 16 цифр
        if not (card.isdigit() and len(card) == 16):
            return bot.send_message(uid,
                                    "Номер карты должен содержать 16 цифр. Попробуйте снова.")

        amount = flow["amount"]

        # создаём запись в БД (сохраняем номер карты в дополнительных полях)
        with SessionLocal() as sess:
            wd = Withdrawal(user_id=uid,
                            amount=amount,
                            status="pending")
            # добавим две пользовательские колонки, если вы уже
            # расширяли модель Withdrawal (иначе удалите эти строки)
            wd.card_number = card
            wd.payment_system = None
            sess.add(wd)
            sess.commit()
            wd_id = wd.id

        # уведомляем пользователя
        bot.send_message(uid,
                         f"✅ Заявка на вывод #{wd_id} на сумму {amount} руб. "
                         "отправлена администратору.\nОжидайте подтверждения.",
                         reply_markup=main_menu_keyboard())

        # ------- сообщение для администраторов -------
        # красивая ссылка на пользователя (если username есть — @name,
        # иначе tg://user?id=<id>)
        with SessionLocal() as sess:
            user = sess.query(User).get(uid)

        if user.username:
            user_link = f"@{user.username}"
        else:
            user_link = f"<a href=\"tg://user?id={uid}\">{uid}</a>"

        caption = (
            f"💸 <b>Запрос на вывод средств</b>\n"
            f"Заявка: <code>#{wd_id}</code>\n"
            f"Пользователь: {user_link}\n"
            f"Сумма: <b>{amount} руб.</b>\n"
            f"Карта/реквизиты: <code>{card}</code>"
        )

        kb_admin = types.InlineKeyboardMarkup()
        kb_admin.add(
            types.InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_withdraw_{wd_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_withdraw_{wd_id}")
        )

        bot.send_message(ADMIN_WITHDRAW_CHAT_ID,
                         caption,
                         parse_mode="HTML",
                         reply_markup=kb_admin)

        # очищаем steps
        user_steps.pop(uid, None)

    # ------------------- Выложить на БИРЖЕ (Формат2 напрямую) -------------------
    @bot.message_handler(func=lambda m: m.text == "Выложить на БИРЖЕ ADIX")
    def place_on_adix_exchange(message: telebot.types.Message):
        """
        Прямой запуск Формата2 (биржа) через add_ads.py
        """
        from add_ads import start_format2_flow_direct
        start_format2_flow_direct(bot, message, user_steps)

    # ------------------- Чаты -------------------
    @bot.message_handler(func=lambda m: m.text == "Чаты")
    def show_user_chats(message: telebot.types.Message):
        user_id = message.chat.id
        with SessionLocal() as session:
            chats = session.query(AdChat).filter(
                (AdChat.buyer_id == user_id) | (AdChat.seller_id == user_id)
            ).filter(AdChat.status != "closed").all()

            if not chats:
                bot.send_message(user_id, "У вас нет активных чатов.")
                return

            kb = types.InlineKeyboardMarkup(row_width=1)
            for ch in chats:
                role = "продавец" if ch.seller_id == user_id else "покупатель"
                other_id = ch.seller_id if ch.buyer_id == user_id else ch.buyer_id
                other_user = session.query(User).filter_by(id=other_id).first()
                if other_user:
                    other_name = f"@{other_user.username}" if other_user.username else f"User {other_id}"
                else:
                    other_name = f"User {other_id}"

                ad_title = ch.ad.inline_button_text or f"Объявление #{ch.ad_id}"
                btn_text = f"[{ad_title}] (Вы - {role}, собеседник -> {other_name})"
                kb.add(types.InlineKeyboardButton(btn_text, callback_data=f"open_chat_{ch.id}"))

            bot.send_message(user_id, "Ваши открытые чаты:", reply_markup=kb)

    # ------------------- открыть выбранный чат -------------------
    @bot.callback_query_handler(func=lambda call: call.data.startswith("open_chat_"))
    def open_chat_callback(call: telebot.types.CallbackQuery):
        """
        Показывает историю диалога и даёт кнопки «✏️ Написать» / «🔒 Закрыть чат».
        """
        user_id = call.from_user.id
        chat_id_str = call.data.replace("open_chat_", "")

        try:
            chat_db_id = int(chat_id_str)
        except ValueError:
            return bot.answer_callback_query(call.id, "Некорректный ID чата.", show_alert=True)

        with SessionLocal() as sess:
            chat_obj = sess.query(AdChat).filter_by(id=chat_db_id).first()

            if not chat_obj or chat_obj.status == "closed":
                return bot.answer_callback_query(call.id, "Чат не найден или закрыт.", show_alert=True)

            # доступ только покупателю или продавцу
            if user_id not in (chat_obj.buyer_id, chat_obj.seller_id):
                return bot.answer_callback_query(call.id, "У вас нет доступа к этому чату!", show_alert=True)

            # собираем переписку
            messages = (sess.query(AdChatMessage)
                        .filter_by(chat_id=chat_db_id)
                        .order_by(AdChatMessage.created_at.asc())
                        .all())

            text_block = ""
            for m in messages:
                who = "Вы" if m.sender_id == user_id else "Собеседник"
                ts = m.created_at.strftime("%d.%m.%y %H:%M")
                text_block += f"<b>{who}</b> <i>{ts}</i>:\n{m.text}\n\n"

            if not text_block:
                text_block = "Сообщений пока нет."

        # Кнопки управления чат‑диалогом
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("✏️ Написать", callback_data=f"chat_write_{chat_db_id}"),
            types.InlineKeyboardButton("🔒 Закрыть чат", callback_data=f"chat_close_{chat_db_id}")
        )

        bot.send_message(
            user_id,
            f"Чат #{chat_db_id}\n\n{text_block}",
            parse_mode="HTML",
            reply_markup=kb
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("chat_write_"))
    def chat_write_callback(call: telebot.types.CallbackQuery):
        user_id = call.from_user.id
        ch_id_str = call.data.replace("chat_write_", "")
        try:
            ch_id = int(ch_id_str)
        except:
            bot.answer_callback_query(call.id, "Некорректный ID чата", show_alert=True)
            return

        bot.answer_callback_query(call.id)
        msg = bot.send_message(user_id, "Напишите текст сообщения:")
        user_steps[user_id] = {"chat_write": ch_id}
        bot.register_next_step_handler(msg, process_chat_message)

    def process_chat_message(message: telebot.types.Message):
        """
        Сохраняем сообщение, рассылаем второй стороне уведомление
        и даём кнопку «Ответить».
        """
        user_id = message.chat.id
        if user_id not in user_steps or "chat_write" not in user_steps[user_id]:
            return bot.send_message(user_id, "Ошибка: не найден контекст чата.")

        ch_id = user_steps[user_id]["chat_write"]
        text = message.text.strip()

        with SessionLocal() as sess:
            chat = sess.query(AdChat).filter_by(id=ch_id).first()
            if not chat or chat.status == "closed":
                bot.send_message(user_id, "Чат не найден или закрыт.")
                user_steps.pop(user_id, None)
                return
            if chat.buyer_id != user_id and chat.seller_id != user_id:
                bot.send_message(user_id, "У вас нет доступа к этому чату.")
                user_steps.pop(user_id, None)
                return

            # страхуемся: оба участника точно в users
            for uid in (chat.buyer_id, chat.seller_id):
                if not sess.query(User).filter_by(id=uid).first():
                    sess.add(User(id=uid))
            sess.flush()  # FK safety

            # сохраняем сообщение
            sess.add(AdChatMessage(chat_id=ch_id,
                                   sender_id=user_id,
                                   text=text))
            sess.commit()

            other_id = chat.seller_id if user_id == chat.buyer_id else chat.buyer_id

        # клавиатура для ответа
        kb_reply = types.InlineKeyboardMarkup(row_width=1)
        kb_reply.add(
            types.InlineKeyboardButton("💬 Открыть чат", callback_data=f"open_chat_{ch_id}"),
            types.InlineKeyboardButton("✏️ Ответить", callback_data=f"chat_write_{ch_id}")
        )

        bot.send_message(other_id,
                         f"Новое сообщение в чате #{ch_id}:\n{text}",
                         reply_markup=kb_reply)

        bot.send_message(user_id, "Сообщение отправлено.")
        user_steps.pop(user_id, None)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("chat_close_"))
    def close_chat_callback(call: telebot.types.CallbackQuery):
        user_id = call.from_user.id
        ch_id_str = call.data.replace("chat_close_", "")
        try:
            ch_id = int(ch_id_str)
        except:
            bot.answer_callback_query(call.id, "Некорректный ID чата", show_alert=True)
            return

        with SessionLocal() as session:
            chat_obj = session.query(AdChat).filter_by(id=ch_id).first()
            if not chat_obj or chat_obj.status == "closed":
                bot.answer_callback_query(call.id, "Чат не найден или уже закрыт.", show_alert=True)
                return
            if chat_obj.buyer_id != user_id and chat_obj.seller_id != user_id:
                bot.answer_callback_query(call.id, "Нет доступа к чату.", show_alert=True)
                return

            chat_obj.status = "closed"
            session.commit()
            bot.answer_callback_query(call.id, "Чат закрыт.")
            other_id = chat_obj.seller_id if user_id == chat_obj.buyer_id else chat_obj.buyer_id
            bot.send_message(other_id, f"Чат #{chat_obj.id} был закрыт пользователем.")