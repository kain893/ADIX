#!/usr/bin/env python3
import dataclasses
from typing import Optional, List

from aiogram import Bot, Dispatcher, types
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery

from config import ADMIN_SUPPORT_CHAT_ID
from database import SessionLocal, SupportTicket, SupportMessage
from utils import main_menu_keyboard, rus_status

class SupportStates(StatesGroup):
    waiting_for_problem_description = State()
    waiting_for_reply = State()

@dataclasses.dataclass
class ResolvedTicketMessage:
    sender_id: int
    text: str
    created_at: int

@dataclasses.dataclass
class ResolvedTicket:
    id: int
    status: str
    msgs: List[ResolvedTicketMessage]

# ────────────────────────────────────────────────────────────────────
def register_support_handlers(bot: Bot, dp: Dispatcher):
# ────────────────────────────────────────────────────────────────────
    # ── безопасный edit (подавляем “message is not modified”) ──────
    async def _safe_edit(chat_id, msg_id, text, **txt_kwargs):
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, **txt_kwargs)
        except TelegramAPIError as e:
            if "message is not modified" not in str(e):
                raise

    # ── Главное меню «Обратная связь» ───────────────────────────────
    @dp.message(lambda m: m.text.lower() == "обратная связь")
    async def _open_menu(msg: types.Message):
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [ types.InlineKeyboardButton(text="📝 Новое обращение", callback_data="st:new") ],
                [ types.InlineKeyboardButton(text="📂 Мои обращения",  callback_data="st:list") ]
            ]
        )
        await bot.send_message(msg.chat.id, "Раздел обратной связи:", reply_markup=kb)

    # ────────────────────────────────────────────────────────────────
    #   СОЗДАНИЕ НОВОГО ТИКЕТА
    # ────────────────────────────────────────────────────────────────
    @dp.callback_query(lambda c: c.data == f"st:new")
    async def _start_new(call: types.CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(call.id)
        await state.set_state(SupportStates.waiting_for_problem_description) # Set state
        await _safe_edit(call.message.chat.id, call.message.message_id, "Опишите проблему одним сообщением:")

    @dp.message(SupportStates.waiting_for_problem_description)
    async def _save_new(msg: types.Message, state: FSMContext):
        uid, text = msg.chat.id, (msg.text or "").strip()
        if not text:
            await state.clear()  # Important: clear state
            return await bot.send_message(uid, "Пустое обращение не создано.", reply_markup=main_menu_keyboard())

        with SessionLocal() as s:
            tk = SupportTicket(user_id=uid, status="open")
            s.add(tk)               # получаем tk.id без закрытия сессии
            s.flush()
            ticket_id = tk.id
            s.add(SupportMessage(ticket_id=ticket_id, sender_id=uid, text=text))
            s.commit()

        await bot.send_message(ADMIN_SUPPORT_CHAT_ID, f"🆕 Тикет #{ticket_id} от {uid}:\n{text}")
        await state.clear()  # Clear state after successful processing
        return await bot.send_message(uid, f"Спасибо! Ваш тикет #{ticket_id} создан.", reply_markup=main_menu_keyboard())

    # ────────────────────────────────────────────────────────────────
    #   СПИСОК ТИКЕТОВ
    # ────────────────────────────────────────────────────────────────
    @dp.callback_query(lambda c: c.data == f"st:list" or c.data == f"st:back")
    async def _show_list(call: types.CallbackQuery):
        uid, mid, redraw = call.from_user.id, call.message.message_id, call.data == f"st:back"
        with SessionLocal() as s:
            rows = (s.query(SupportTicket)
                      .filter_by(user_id=uid)
                      .order_by(SupportTicket.id.desc())
                      .all())
            tickets = [(t.id, t.status) for t in rows]

        if not tickets:
            txt = "У вас нет обращений."
            if redraw:
                return await _safe_edit(uid, mid, txt, reply_markup=main_menu_keyboard())
            else:
                await bot.answer_callback_query(call.id)
                return await bot.send_message(uid, txt, reply_markup=main_menu_keyboard())

        buttons = [
            [ types.InlineKeyboardButton(text=f"#{t_id} — {rus_status(st)}", callback_data=f"st:view:{t_id}") ]
            for t_id, st in tickets
        ]
        buttons.append(
            [ types.InlineKeyboardButton(text="❌ Закрыть", callback_data="delete_msg") ]
        )
        kb = types.InlineKeyboardMarkup(inline_keyboard=buttons)
        if redraw:
            return await _safe_edit(uid, mid, "Ваши обращения:", reply_markup=kb)
        else:
            await bot.answer_callback_query(call.id)
            await bot.delete_message(uid, mid)
            return await bot.send_message(uid, "Ваши обращения:", reply_markup=kb)

    # ────────────────────────────────────────────────────────────────
    #   КАРТОЧКА ТИКЕТА
    # ────────────────────────────────────────────────────────────────
    view_route_prefix="st:view:"

    @dp.callback_query(lambda c: c.data.startswith(view_route_prefix))
    async def _view_card(call: types.CallbackQuery):
        uid, mid = call.from_user.id, call.message.message_id
        tk = await _fetch_ticket(call.id, call.data, uid, view_route_prefix)
        if tk is not None:
            await bot.answer_callback_query(call.id)
            await _show_card(uid, mid, tk)

    async def _show_card(user_id: int, message_id: int, tk: ResolvedTicket):
        body = "\n\n".join(
            f"{'Админ' if msg.sender_id != user_id else 'Вы'} ({msg.created_at:%d.%m.%y %H:%M}):\n{msg.text}"
            for msg in tk.msgs
        ) or "Сообщений пока нет."
        buttons = []
        if tk.status == "open":
            buttons.append([
                types.InlineKeyboardButton(text="✉ Ответить", callback_data=f"st:reply:{tk.id}")
            ])
            buttons.append([
                types.InlineKeyboardButton(text="🛑 Закрыть тикет", callback_data=f"st:close:{tk.id}")
            ])
        buttons.append([
            types.InlineKeyboardButton(text="↩️ Назад", callback_data="st:back")
        ])
        kb = types.InlineKeyboardMarkup(inline_keyboard=buttons)
        await _safe_edit(user_id, message_id, f"📄 Тикет #{tk.id} — {rus_status(tk.status)}\n\n{body}", reply_markup=kb)

    # ────────────────────────────────────────────────────────────────
    #   ЗАКРЫТИЕ ТИКЕТА
    # ────────────────────────────────────────────────────────────────
    close_route_prefix = "st:close:"

    @dp.callback_query(lambda c: c.data.startswith(close_route_prefix))
    async def _close_ticket(call: types.CallbackQuery):
        uid, mid = call.from_user.id, call.message.message_id
        tk = await _fetch_ticket(call.id, call.data, uid, close_route_prefix)
        if tk is None:
            return None
        if tk.status == "closed":
            return await bot.answer_callback_query(call.id, "Тикет уже закрыт.", show_alert=True)
        with SessionLocal() as s:
            s.query(SupportTicket).filter_by(id=tk.id, user_id=uid).update({"status": "closed"})
            s.commit()
        await bot.answer_callback_query(call.id, "Тикет закрыт.")
        await bot.send_message(ADMIN_SUPPORT_CHAT_ID, f"⛔️ Пользователь {uid} закрыл тикет #{tk.id}")
        return await _show_card(uid, mid, tk)

    # ────────────────────────────────────────────────────────────────
    #   ОТВЕТ В ТИКЕТ
    # ────────────────────────────────────────────────────────────────
    reply_route_prefix = "st:reply:"

    @dp.callback_query(lambda c: c.data.startswith(reply_route_prefix))
    async def _prep_reply(call: types.CallbackQuery, state: FSMContext):
        uid = call.from_user.id
        tk = await _fetch_ticket(call.id, call.data, uid, reply_route_prefix)
        if tk is None:
            return None
        if tk.status == "closed":
            return await bot.answer_callback_query(call.id, "Тикет закрыт.", show_alert=True)
        await bot.answer_callback_query(call.id)
        await state.set_state(SupportStates.waiting_for_reply) # Set state
        await state.update_data(tk_id=tk.id)
        return await bot.send_message(uid, f"Введите сообщение для тикета #{tk.id}:")

    @dp.message(SupportStates.waiting_for_reply)
    async def _save_reply(msg: types.Message, state: FSMContext):
        uid = msg.chat.id
        data = await state.get_data()
        t_id: Optional[int] = int(t_id_raw) if (t_id_raw := data.get("tk_id")) is not None else None
        if not t_id:
            return await bot.send_message(uid, "Нет выбранного тикета.")

        with SessionLocal() as s:
            tk = s.query(SupportTicket).filter_by(id=t_id, user_id=uid).first()
            if not tk or tk.status == "closed":
                return await bot.send_message(uid, "Тикет не найден или закрыт.")
            s.add(SupportMessage(ticket_id=t_id, sender_id=uid, text=msg.text.strip()))
            s.commit()

        await bot.send_message(uid, "Сообщение отправлено.")
        return await bot.send_message(ADMIN_SUPPORT_CHAT_ID,
                               f"💬 Новое сообщение в тикете #{t_id} от {uid}:\n{msg.text}")

    # ────────────────────────────────────────────────────────────────
    #   УТИЛИТА: получаем тикет + сообщения (только простые типы)
    # ────────────────────────────────────────────────────────────────
    async def _fetch_ticket(cb_id: str, call_data: str, uid: int, prefix: str) -> Optional[ResolvedTicket]:
        t_id = int(call_data[prefix.__len__():call_data.__len__()])
        with SessionLocal() as s:
            t = s.query(SupportTicket).filter_by(id=t_id, user_id=uid).first()
            if not t:
                if cb_id:
                    await bot.answer_callback_query(cb_id, "Тикет не найден.", show_alert=True)
                return None

            msgs = [
                ResolvedTicketMessage(m.sender_id, m.text, m.created_at)
                for m in s.query(SupportMessage)
                          .filter_by(ticket_id=t.id)
                          .order_by(SupportMessage.created_at.asc())
                          .all()
            ]
        return ResolvedTicket(id=t.id, status=t.status, msgs=msgs)

    # ────────────────────────────────────────────────────────────────
    #   Кнопка «удалить сообщение»
    # ────────────────────────────────────────────────────────────────
    @dp.callback_query(lambda c: c.data == "delete_msg")
    async def _del(call: CallbackQuery):
        await bot.delete_message(call.message.chat.id, call.message.message_id)
        await bot.answer_callback_query(call.id)
