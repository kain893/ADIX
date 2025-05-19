#!/usr/bin/env python3

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart

# Импорт обработчиков добавления объявлений (Формат №1 и Формат №2)
import add_ads
# Импорт обработчиков профиля/личного кабинета (с указанием карты при пополнении)
import profile
# Импорт обработчиков поиска (включает «Пожаловаться» / «Оставить отзыв»)
import search
# Импорт модуля обратной связи
import support
# Импорт админ-хендлеров (рассылка, бан, модерация и т.д.)
from admin import register_admin_handlers
from config import BOT_TOKEN
from database import init_db, SessionLocal, User, Ad, ScheduledPost, Sale
# Импорт функций-утилит (главное меню, post_ad_to_chat, reserve_funds_for_sale и т.п.)
from utils import main_menu_keyboard, post_ad_to_chat

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties())
dp = Dispatcher()
init_db()

# Хранилище для состояния (шагов) пользователей
user_steps = {}

# Храним информацию о предупреждениях в группах:
#  warn_messages[user_id] = (chat_id, warn_message_id, timer_object)
warn_messages = {}

# Регистрируем все хендлеры из соответствующих модулей
register_admin_handlers(bot, dp)
search.register_search_handlers(bot, dp, user_steps)
add_ads.register_add_ads_handlers(bot, dp, user_steps)
profile.register_profile_handlers(bot, dp, user_steps)
support.register_support_handlers(bot, dp)

def get_or_create_user(chat_id, username=None):
    """
    Проверяем наличие пользователя в БД (по chat_id).
    Если нет — создаём; если есть — при необходимости обновляем username.
    """
    with SessionLocal() as session:
        user = session.query(User).filter_by(id=chat_id).first()
        if not user:
            user = User(id=chat_id, username=username)
            session.add(user)
            session.commit()
        else:
            if username and user.username != username:
                user.username = username
                session.commit()
        return user

async def scheduled_post_worker():
    try:
        with SessionLocal() as session:
            now = datetime.now(timezone.utc)
            tasks = session.query(ScheduledPost).filter(ScheduledPost.next_post_time <= now).all()
            for task in tasks:
                ad_obj = session.query(Ad).filter_by(id=task.ad_id).first()
                if ad_obj and ad_obj.status == "approved":
                    user_obj = session.query(User).filter_by(id=ad_obj.user_id).first()
                    await post_ad_to_chat(bot, task.chat_id, ad_obj, user_obj)

                task.posts_left -= 1
                if task.posts_left > 0:
                    task.next_post_time = now + timedelta(minutes=task.interval_minutes)
                else:
                    session.delete(task)
            session.commit()
    except Exception as e:
        print("Ошибка в scheduled_post_worker:", e)

def scheduled_post_worker_sync():
    """
    Пример фонового потока для обработки таблицы ScheduledPost.
    Раз в минуту проверяем, не пора ли опубликовать что-то в чате/канале.
    """
    while True:
        asyncio.run(scheduled_post_worker())
        time.sleep(60)

# Запускаем поток, который публикует запланированные объявления
bg_thread = threading.Thread(target=scheduled_post_worker_sync, daemon=True)
bg_thread.start()

@dp.message(CommandStart())
async def start_handler(message: types.Message):
    """
    Регистрируем (или обновляем) пользователя и выводим приветствие
    + ссылки на оба соглашения.
    """
    get_or_create_user(message.chat.id, message.from_user.username)

    greeting = (
        "🎉 Приветствую вас в Adix! 🌟\n\n"
        "🛍️ Это площадка для продажи товаров и услуг.\n\n"
        "🔒 Используя бота, вы соглашаетесь со следующими документами:\n\n"
        "📄 **Пользовательское соглашение ADIX**\n"
        "💬 **Пользовательское соглашение Чатов Биржи ADIX**\n\n"
        "➡️ Для навигации используйте меню ниже."
    )
    await bot.send_message(
        message.chat.id,
        greeting,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [ types.InlineKeyboardButton(
                text="📄 Пользовательское соглашение ADIX",
                url="https://telegra.ph/Polzovatelskoe-soglashenie-03-25-9"
            ) ],
            [ types.InlineKeyboardButton(
                text="💬 Соглашение Чатов Биржи ADIX",
                url="https://telegra.ph/Obshchie-polozheniya-03-25"
            ) ]
        ]
    )
    await bot.send_message(
        message.chat.id,
        "📌 Ознакомьтесь, пожалуйста, с документами:",
        parse_mode="Markdown",
        reply_markup=kb
    )

# ------------------- Удаляем сообщения из групп/супергрупп, если нет /start (пункты 1 и 2) -------------------
@dp.message(F.chat.type.in_({ "group", "supergroup"}), F.content_type.in_({ "text", "photo", "sticker", "video", "document", "voice", "animation" }))
async def guard_group_messages(message: types.Message):
    """
    Если пользователь не зарегистрирован в боте (не делал /start), то удаляем его сообщение.
    Затем посылаем предупреждение со ссылкой на бота.
    Предыдущие предупреждения пользователя удаляем и ставим новое (удаляем его через 2 мин).
    """
    with SessionLocal() as session:
        user_db = session.query(User).filter_by(id=message.from_user.id).first()

    if not user_db:
        # 1) Удаляем сообщение пользователя
        try:
            await bot.delete_message(message.chat.id, message.message_id)
        except:
            pass

        # 2) Если у нас уже есть предупреждение для этого user_id – удаляем его, отменяем таймер
        if message.from_user.id in warn_messages:
            old_chat_id, old_msg_id, old_timer = warn_messages[message.from_user.id]
            # Удаляем старое предупреждение
            try:
                await bot.delete_message(old_chat_id, old_msg_id)
            except:
                pass
            if old_timer.is_alive():
                old_timer.cancel()
            del warn_messages[message.from_user.id]

        # 3) Отправляем новое предупреждение
        # Кнопка «↩️ Перейти в бота»
        bot_username = (await bot.get_me()).username
        inline_kb = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(
                text="↩️ Перейти в бота / Принять соглашение",
                url=f"https://t.me/{bot_username}?start=1"
            )
        ]])
        warn_text = (
            "В этом чате нельзя писать, пока вы не примете пользовательское соглашение.\n"
            "Нажав на кнопку «/start» в боте, вы автоматически соглашаетесь с условиями.\n\n"
            "Перейдите в бота и нажмите /start."
        )
        warn_msg = await bot.send_message(
            message.chat.id,
            warn_text,
            parse_mode="Markdown",
            reply_markup=inline_kb
        )

        # 4) Запускаем таймер на 2 минуты, после чего предупреждение удалится
        async def delete_warning(chat_id_val, msg_id_val, user_id_val):
            try:
                await bot.delete_message(chat_id_val, msg_id_val)
            except:
                pass
            # Убираем запись из словаря
            if user_id_val in warn_messages:
                del warn_messages[user_id_val]

        def delete_warning_sync(chat_id_val, msg_id_val, user_id_val):
            asyncio.run(delete_warning(chat_id_val, msg_id_val, user_id_val))

        t = threading.Timer(120, delete_warning_sync, args=(message.chat.id, warn_msg.message_id, message.from_user.id))
        t.start()

        # 5) Запоминаем, чтобы при повторной попытке удалить и заменить
        warn_messages[message.from_user.id] = (message.chat.id, warn_msg.message_id, t)

    else:
        # Пользователь зарегистрирован, разрешаем писать
        pass

# ========================= Сделки (покупка/продажа) =========================

@dp.callback_query(lambda call: call.data.startswith("buy_ad_"))
async def handle_buy_ad(call: types.CallbackQuery):
    """
    Пользователь нажал «Купить».
    1) Если нажатие было в группе/канале — просим перейти в ЛС бота.
    2) Если ЛС — уточняем «Вы действительно хотите купить?».
    """
    ad_id_str = call.data.replace("buy_ad_", "")
    try:
        ad_id = int(ad_id_str)
    except:
        await bot.answer_callback_query(call.id, "Некорректный ID объявления.", show_alert=True)
        return None

    # Если нажали в группе, просим перейти в ЛС
    if call.message.chat.type != "private":
        return await bot.answer_callback_query(
            call.id,
            "Чтобы купить, перейдите в личные сообщения с ботом!",
            show_alert=True
        )

    # Если это ЛС, уточняем
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="Подтвердить покупку", callback_data=f"confirm_buy_ad_{ad_id}"),
        types.InlineKeyboardButton(text="Отмена", callback_data=f"cancel_buy_ad_{ad_id}")
    ]])
    await bot.answer_callback_query(call.id)
    return await bot.send_message(
        call.from_user.id,
        f"Вы действительно хотите купить объявление #{ad_id}? Подтвердите:",
        reply_markup=kb
    )


@dp.callback_query(lambda call: call.data.startswith("confirm_buy_ad_") or call.data.startswith("cancel_buy_ad_"))
async def handle_confirm_buy_ad(call: types.CallbackQuery):
    """
    Обрабатываем «Подтвердить покупку» / «Отменить покупку».
    """
    if call.data.startswith("confirm_buy_ad_"):
        ad_id_str = call.data.replace("confirm_buy_ad_", "")
        action = "confirm"
    else:
        ad_id_str = call.data.replace("cancel_buy_ad_", "")
        action = "cancel"

    try:
        ad_id = int(ad_id_str)
    except:
        return await bot.answer_callback_query(call.id, "Некорректный ID объявления.", show_alert=True)

    with SessionLocal() as session:
        ad_obj = session.query(Ad).filter_by(id=ad_id).first()
        if not ad_obj:
            return await bot.answer_callback_query(call.id, "Объявление не найдено.", show_alert=True)
        if ad_obj.user_id == call.from_user.id:
            return await bot.answer_callback_query(call.id, "Нельзя купить собственное объявление!", show_alert=True)
        if ad_obj.status != "approved":
            return await bot.answer_callback_query(call.id, "Объявление не одобрено или уже недоступно.", show_alert=True)

        buyer_id = call.from_user.id
        seller_id = ad_obj.user_id

        if action == "cancel":
            # Покупка отменена
            await bot.answer_callback_query(call.id, "Вы отменили покупку.")
            return await bot.send_message(buyer_id, "Покупка отменена.")

        # Иначе подтверждение покупки -> резервируем деньги
        from utils import reserve_funds_for_sale
        result = reserve_funds_for_sale(bot, buyer_id, seller_id, ad_obj)
        if result == "ok":
            # Сделка -> pending
            kb_buyer = types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="Принять сделку", callback_data=f"confirm_deal_{ad_obj.id}"),
                types.InlineKeyboardButton(text="Отклонить сделку", callback_data=f"cancel_deal_{ad_obj.id}")
            ]])
            await bot.answer_callback_query(call.id, "Средства зарезервированы! Ожидается завершение сделки.")
            await bot.send_message(
                buyer_id,
                f"Вы хотите купить «{ad_obj.inline_button_text or ('товар #' + str(ad_id))}».\n"
                f"Сумма {ad_obj.price} руб. зарезервирована (статус сделки: pending).\n\n"
                "Когда получите товар/услугу, нажмите «Принять сделку», чтобы продавец получил оплату.\n"
                "Или «Отклонить сделку», чтобы отменить и вернуть деньги себе.",
                reply_markup=kb_buyer
            )
            # Оповещаем продавца
            mention_buyer = f"@{call.from_user.username}" if call.from_user.username else buyer_id
            return await bot.send_message(
                seller_id,
                f"Пользователь {mention_buyer} хочет купить ваше объявление #{ad_obj.id}.\n"
                f"Сумма {ad_obj.price} руб. зарезервирована.\nОжидается завершение сделки."
            )
        else:
            # Ошибка при резервировании
            return await bot.answer_callback_query(call.id, result, show_alert=True)

@dp.callback_query(lambda call: call.data.startswith("confirm_deal_") or call.data.startswith("cancel_deal_"))
async def handle_deal_confirmation(call: types.CallbackQuery):
    """
    «Принять сделку» -> деньги уходят продавцу
    «Отклонить сделку» -> деньги возвращаются покупателю
    """
    if call.data.startswith("confirm_deal_"):
        ad_id_str = call.data.replace("confirm_deal_", "")
        action = "confirm"
    else:
        ad_id_str = call.data.replace("cancel_deal_", "")
        action = "cancel"

    try:
        ad_id = int(ad_id_str)
    except:
        return await bot.answer_callback_query(call.id, "Некорректный ID сделки", show_alert=True)

    with SessionLocal() as session:
        sale_obj = session.query(Sale).filter_by(ad_id=ad_id, buyer_id=call.from_user.id, status="pending").first()
        if not sale_obj:
            return await bot.answer_callback_query(call.id, "Сделка не найдена или уже обработана.", show_alert=True)

        ad_obj = session.query(Ad).filter_by(id=ad_id).first()
        buyer = session.query(User).filter_by(id=sale_obj.buyer_id).first()
        seller = session.query(User).filter_by(id=sale_obj.seller_id).first()

        if not ad_obj or not buyer or not seller:
            return await bot.answer_callback_query(call.id, "Объявление или участники сделки не найдены.", show_alert=True)

        if action == "confirm":
            sale_obj.status = "completed"
            seller.balance = seller.balance + sale_obj.amount
            session.commit()

            await bot.answer_callback_query(call.id, "Сделка подтверждена! Деньги переведены продавцу.")
            # Уведомляем стороны
            mention_buyer = f"@{buyer.username}" if buyer.username else buyer.id
            mention_seller = f"@{seller.username}" if seller.username else seller.id

            await bot.send_message(
                seller.id,
                f"Покупатель {mention_buyer} подтвердил сделку по объявлению #{ad_id}.\n"
                f"Вам зачислено {sale_obj.amount} руб."
            )
            return await bot.send_message(
                buyer.id,
                f"Сделка #{sale_obj.id} подтверждена. {sale_obj.amount} руб. переведено продавцу ({mention_seller})."
            )

        else:
            sale_obj.status = "canceled"
            buyer.balance = buyer.balance + sale_obj.amount
            session.commit()

            await bot.answer_callback_query(call.id, "Сделка отменена, деньги возвращены покупателю.")
            mention_buyer = f"@{buyer.username}" if buyer.username else buyer.id
            mention_seller = f"@{seller.username}" if seller.username else seller.id

            await bot.send_message(
                seller.id,
                f"Покупатель {mention_buyer} отменил сделку по объявлению #{ad_id}.\n"
                "Деньги возвращены покупателю."
            )
            return await bot.send_message(
                buyer.id,
                f"Сделка #{sale_obj.id} отменена, {sale_obj.amount} руб. возвращены на ваш баланс."
            )

@dp.callback_query(lambda call: call.data.startswith("details_ad_"))
async def handle_details_ad(call: types.CallbackQuery):
    """
    Кнопка «Подробнее» по объявлению
    """
    ad_id_str = call.data.replace("details_ad_", "")
    try:
        ad_id = int(ad_id_str)
    except:
        return await bot.answer_callback_query(call.id, "Некорректный ID объявления", show_alert=True)

    with SessionLocal() as session:
        ad_obj = session.query(Ad).filter_by(id=ad_id).first()
        if not ad_obj:
            return await bot.answer_callback_query(call.id, "Объявление не найдено.", show_alert=True)

        user_obj = ad_obj.user
        caption = (
            f"Детали объявления #{ad_obj.id}\n"
            f"Название кнопки: {ad_obj.inline_button_text or '—'}\n"
            f"Текст: {ad_obj.text}\n"
            f"Цена: {ad_obj.price} руб.\n"
            f"Кол-во: {ad_obj.quantity}\n"
            f"Категория: {ad_obj.category}"
            + (f" / {ad_obj.subcategory}" if ad_obj.subcategory else "")
            + f"\nГород: {ad_obj.city}\n\n"
            f"Контакты продавца: @{user_obj.username if user_obj.username else '—'}\n\n"
            "Выберите действие:"
        )

        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=f"Купить «{ad_obj.inline_button_text}»" if ad_obj.inline_button_text else "Купить",
                    callback_data=f"buy_ad_{ad_obj.id}"
                )
            ],
            [
                types.InlineKeyboardButton(text="Оставить отзыв", callback_data=f"feedback_ad_{ad_obj.id}")
            ],
            [
                types.InlineKeyboardButton(text="Отзывы о продавце", callback_data=f"viewfeedback_seller_{ad_obj.user_id}")
            ]
        ])
    await bot.answer_callback_query(call.id)
    return await bot.send_message(call.message.chat.id, caption, reply_markup=kb)

#------------------------------
#DELETE MESSAGES
#------------------------------
@dp.message(F.chat.type.in_({ "group", "supergroup"}), F.content_type.in_({ "text", "photo", "sticker", "video", "document", "voice", "animation" }))
async def guard_group_messages(message: types.Message):
    """
    • Пропускаем администраторов/создателя группы и сообщения от имени канала.
    • Если пользователь ещё не нажимал /start – удаляем его сообщение
      и показываем предупреждение с кнопкой «Перейти в бота» + ссылки
      на оба соглашения.
    Предыдущее предупреждение этого юзера удаляем, таймер гасим.
    """
    # --- сообщение от имени канала — пропускаем
    if message.sender_chat is not None:
        return

    user = message.from_user
    if not user:      # теоретически бывает в пересланных
        return

    user_id = user.id

    # --- администраторы / создатель – им писать можно
    try:
        member = await bot.get_chat_member(message.chat.id, user_id)
        if member.status in ("administrator", "creator"):
            return
    except Exception:
        # нет права смотреть или другая ошибка — считаем обычным юзером
        pass

    # --- уже зарегистрирован? → можно писать
    with SessionLocal() as session:
        if session.query(User).filter_by(id=user_id).first():
            return

    # =========================== Блокировка сообщения =========================

    # 1) удаляем исходное сообщение
    try:
        await bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

    # 2) если висит старое предупреждение – убираем
    old = warn_messages.pop(user_id, None)
    if old:
        old_chat_id, old_msg_id, old_timer = old
        try:
            await bot.delete_message(old_chat_id, old_msg_id)
        except Exception:
            pass
        if old_timer.is_alive():
            old_timer.cancel()

    # 3) формируем новое предупреждение
    bot_username = (await bot.get_me()).username
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text="↩️ Перейти в бота / Принять соглашение",
                url=f"https://t.me/{bot_username}?start=1"
            )
        ],
        [
            types.InlineKeyboardButton(
                text="📄 Польз. соглашение ADIX",
                url="https://telegra.ph/Polzovatelskoe-soglashenie-03-25-9"
            )
        ],
        [
            types.InlineKeyboardButton(
                text="💬 Соглашение Чатов ADIX",
                url="https://telegra.ph/Obshchie-polozheniya-03-25"
            )
        ]
    ])
    warn_text = (
        "В этом чате нельзя писать, пока вы не примете пользовательское соглашение.\n"
        "Нажав на кнопку «/start» в боте, вы автоматически соглашаетесь с условиями.\n\n"
        "Перейдите в бота и нажмите /start."
    )

    warn_msg = await bot.send_message(
        message.chat.id,
        warn_text,
        parse_mode="Markdown",
        reply_markup=kb
    )

    # 4) таймер: удаляем предупреждение через 2 минуты
    async def delete_warning(chat_id_val, msg_id_val, uid_val):
        try:
            await bot.delete_message(chat_id_val, msg_id_val)
        except Exception:
            pass
        warn_messages.pop(uid_val, None)

    def delete_warning_sync(chat_id_val, msg_id_val, uid_val):
        asyncio.run(delete_warning(chat_id_val, msg_id_val, uid_val))

    timer = threading.Timer(
        120,
        delete_warning_sync,
        args=(message.chat.id, warn_msg.message_id, user_id)
    )
    timer.start()

    # 5) сохраняем, чтобы потом корректно удалить/обновить
    warn_messages[user_id] = (message.chat.id, warn_msg.message_id, timer)

async def main() -> None:
    # skip_pending=True, чтобы «очищать» старые «висящие» апдейты
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
