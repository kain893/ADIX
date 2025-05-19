#!/usr/bin/env python3

from aiogram import Bot, types
from database import SessionLocal, Sale, User
from decimal import Decimal

# Словарь для перевода статусов в русскую форму:
STATUS_TRANSLATIONS = {
    "pending": "Ожидание",
    "approved": "Одобрено",
    "rejected": "Отклонено",
    "completed": "Завершено",
    "canceled": "Отменено",
    "open": "Открыт",
    "closed": "Закрыт"
}


def calc_chat_price(chat, qty: int) -> float:
    """
    Возвращает стоимость размещения `qty` (=1,5,10 …) постов
    в объекте ChatGroup `chat`.

    • если в БД явно указана цена для пакета (price_5 / price_10),
      берём её;
    • если колонка пуста (0 или None) — корректно откатываемся
      к «цена_за_1 × qty», чтобы не сломать старые записи.
    """
    if qty == 1:
        return float(chat.price_1)

    if qty == 5 and (chat.price_5 or 0) > 0:
        return float(chat.price_5)

    if qty == 10 and (chat.price_10 or 0) > 0:
        return float(chat.price_10)

    # fallback для любых других qty или отсутствующих цен
    return float(chat.price_1) * qty

def rus_status(status: str) -> str:
    """Возвращает русский вариант статуса."""
    return STATUS_TRANSLATIONS.get(status, status)

def main_menu_keyboard():
    """
    Главное меню (Reply-клавиатура).
    """
    return types.ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [ types.KeyboardButton(text="➕Разместить объявление"), types.KeyboardButton(text="🔍Поиск объявлений") ],
        [ types.KeyboardButton(text="📜Личный кабинет"), types.KeyboardButton(text="Обратная связь") ]
    ])

async def post_ad_to_chat(bot: Bot, chat_id, ad_object, user):
    """
    Публикуем объявление в указанный чат/канал.
    Вместо "[РЕКЛАМА]" теперь выводим название инлайн-кнопки (если есть).
    """
    inn_info = user.inn or "—"
    fio_info = user.full_name or user.company_name or "—"

    # Если у объявления есть inline_button_text, используем её сверху:
    title_line = ad_object.inline_button_text if ad_object.inline_button_text else "Объявление"

    caption = (
        f"{title_line}\n"  # Вместо [РЕКЛАМА] выводим кнопку/название
        f"{ad_object.text}\n\n"
        f"Цена: {ad_object.price} руб.\n"
        f"Кол-во: {ad_object.quantity}\n"
        f"Категория: {ad_object.category or '—'}"
        + (f" / {ad_object.subcategory}" if ad_object.subcategory else "")
        + f"\nГород: {ad_object.city or '—'}\n\n"
        f"ИНН: {inn_info}\n"
        f"ФИО/Компания: {fio_info}\n"
        f"Контакты: @{user.username if user.username else '—'}\n\n"
        "Нажмите «Купить», чтобы оформить сделку через бота."
    )

    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text=f"Купить «{ad_object.inline_button_text}»" if ad_object.inline_button_text else "Купить",
            callback_data=f"buy_ad_{ad_object.id}"
        ),
        types.InlineKeyboardButton(
            text="Подробнее",
            callback_data=f"details_ad_{ad_object.id}"
        )
    ]])

    photos_list = ad_object.photos.split(",") if ad_object.photos else []
    if photos_list and photos_list[0]:
        await bot.send_photo(chat_id, photos_list[0], caption=caption, reply_markup=kb)
    else:
        await bot.send_message(chat_id, caption, reply_markup=kb)

def reserve_funds_for_sale(bot: Bot, buyer_id, seller_id, ad_obj):
    with SessionLocal() as session:
        buyer = session.query(User).filter_by(id=buyer_id).first()
        if not buyer:
            return "Покупатель не найден."
        seller = session.query(User).filter_by(id=seller_id).first()
        if not seller:
            return "Продавец не найден."

        price = ad_obj.price if ad_obj.price else Decimal("0")
        if buyer.balance < price:
            return f"Недостаточно средств. Нужно {price}, а у вас {buyer.balance}."

        buyer.balance = buyer.balance - price

        sale = Sale(
            ad_id=ad_obj.id,
            buyer_id=buyer_id,
            seller_id=seller_id,
            amount=price,
            status="pending"  # В БД хранится "pending", а пользователю показываем через rus_status()
        )
        session.add(sale)
        session.commit()

    return "ok"
