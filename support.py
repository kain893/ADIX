#!/usr/bin/env python3
# support.py
import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException
from collections import defaultdict

from database   import SessionLocal, SupportTicket, SupportMessage
from utils      import main_menu_keyboard, rus_status

ADMIN_SUPPORT_CHAT_ID = -1002288960086
user_steps = defaultdict(dict)            # временное состояние «ответа»

# ────────────────────────────────────────────────────────────────────
def register_support_handlers(bot: telebot.TeleBot):
# ────────────────────────────────────────────────────────────────────
    # ── безопасный edit (подавляем “message is not modified”) ──────
    def _safe_edit(chat_id, msg_id, *txt_args, **txt_kwargs):
        try:
            bot.edit_message_text(
                *txt_args, chat_id=chat_id, message_id=msg_id, **txt_kwargs
            )
        except ApiTelegramException as e:
            if "message is not modified" not in str(e):
                raise

    # ── Главное меню «Обратная связь» ───────────────────────────────
    @bot.message_handler(func=lambda m: m.text.lower() == "обратная связь")
    def _open_menu(msg: telebot.types.Message):
        kb = types.InlineKeyboardMarkup(row_width=1)          # ← ключевое!
        kb.add(types.InlineKeyboardButton("📝 Новое обращение", callback_data="st:new"))
        kb.add(types.InlineKeyboardButton("📂 Мои обращения",  callback_data="st:list"))
        bot.send_message(msg.chat.id, "Раздел обратной связи:", reply_markup=kb)

    # ── Роутер callback-ов ──────────────────────────────────────────
    @bot.callback_query_handler(lambda c: c.data.startswith("st:"))
    def _router(call: telebot.types.CallbackQuery):
        _, act, *rest = call.data.split(":", 2)
        if   act == "new":   _start_new(call)
        elif act == "list":  _show_list(call)
        elif act == "view":  _show_card(call, int(rest[0]))
        elif act == "back":  _show_list(call, redraw=True)
        elif act == "close": _close_ticket(call, int(rest[0]))
        elif act == "reply": _prep_reply(call, int(rest[0]))

    # ────────────────────────────────────────────────────────────────
    #   СОЗДАНИЕ НОВОГО ТИКЕТА
    # ────────────────────────────────────────────────────────────────
    def _start_new(call):
        bot.answer_callback_query(call.id)
        cid = call.message.chat.id
        _safe_edit(cid, call.message.message_id, "Опишите проблему одним сообщением:")
        bot.register_next_step_handler_by_chat_id(cid, _save_new)

    def _save_new(msg: telebot.types.Message):
        uid, text = msg.chat.id, (msg.text or "").strip()
        if not text:
            return bot.send_message(uid, "Пустое обращение не создано.",
                                     reply_markup=main_menu_keyboard())

        with SessionLocal() as s:
            tk = SupportTicket(user_id=uid, status="open")
            s.add(tk); s.flush()               # получаем tk.id без закрытия сессии
            ticket_id = tk.id
            s.add(SupportMessage(ticket_id=ticket_id, sender_id=uid, text=text))
            s.commit()

        bot.send_message(ADMIN_SUPPORT_CHAT_ID, f"🆕 Тикет #{ticket_id} от {uid}:\n{text}")
        bot.send_message(uid, f"Спасибо! Ваш тикет #{ticket_id} создан.",
                         reply_markup=main_menu_keyboard())

    # ────────────────────────────────────────────────────────────────
    #   СПИСОК ТИКЕТОВ
    # ────────────────────────────────────────────────────────────────
    def _show_list(call, redraw=False):
        uid, mid = call.from_user.id, call.message.message_id
        with SessionLocal() as s:
            rows = (s.query(SupportTicket)
                      .filter_by(user_id=uid)
                      .order_by(SupportTicket.id.desc())
                      .all())
            tickets = [(t.id, t.status) for t in rows]

        if not tickets:
            txt = "У вас нет обращений."
            if redraw:
                _safe_edit(uid, mid, txt, reply_markup=main_menu_keyboard())
            else:
                bot.answer_callback_query(call.id)
                bot.send_message(uid, txt, reply_markup=main_menu_keyboard())
            return

        kb = types.InlineKeyboardMarkup(row_width=1)
        for t_id, st in tickets:
            kb.add(types.InlineKeyboardButton(f"#{t_id} — {rus_status(st)}",
                                              callback_data=f"st:view:{t_id}"))
        kb.add(types.InlineKeyboardButton("❌ Закрыть", callback_data="delete_msg"))

        if redraw:
            _safe_edit(uid, mid, "Ваши обращения:", reply_markup=kb)
        else:
            bot.answer_callback_query(call.id)
            bot.delete_message(uid, mid)
            bot.send_message(uid, "Ваши обращения:", reply_markup=kb)

    # ────────────────────────────────────────────────────────────────
    #   КАРТОЧКА ТИКЕТА
    # ────────────────────────────────────────────────────────────────
    def _show_card(call, t_id: int):
        uid, mid = call.from_user.id, call.message.message_id
        tk, msgs = _fetch_ticket(t_id, uid, call.id)
        if tk is None: return

        body = "\n\n".join(
            f"{'Админ' if sid != uid else 'Вы'} ({ts:%d.%m.%y %H:%M}):\n{txt}"
            for sid, txt, ts in msgs
        ) or "Сообщений пока нет."

        kb = types.InlineKeyboardMarkup(row_width=1)
        if tk["status"] == "open":
            kb.add(types.InlineKeyboardButton("✉ Ответить", callback_data=f"st:reply:{t_id}"),
                   types.InlineKeyboardButton("🛑 Закрыть тикет", callback_data=f"st:close:{t_id}"))
        kb.add(types.InlineKeyboardButton("↩️ Назад", callback_data="st:back"))

        bot.answer_callback_query(call.id)
        _safe_edit(uid, mid, f"📄 Тикет #{t_id} — {rus_status(tk['status'])}\n\n{body}",
                   reply_markup=kb)

    # ────────────────────────────────────────────────────────────────
    #   ЗАКРЫТИЕ ТИКЕТА
    # ────────────────────────────────────────────────────────────────
    def _close_ticket(call, t_id: int):
        uid = call.from_user.id
        tk, _ = _fetch_ticket(t_id, uid, call.id)
        if tk is None or tk["status"] == "closed":
            return

        with SessionLocal() as s:
            s.query(SupportTicket).filter_by(id=t_id, user_id=uid).update({"status": "closed"})
            s.commit()

        bot.answer_callback_query(call.id, "Тикет закрыт.")
        _show_card(call, t_id)
        bot.send_message(ADMIN_SUPPORT_CHAT_ID, f"⛔️ Пользователь {uid} закрыл тикет #{t_id}")

    # ────────────────────────────────────────────────────────────────
    #   ОТВЕТ В ТИКЕТ
    # ────────────────────────────────────────────────────────────────
    def _prep_reply(call, t_id: int):
        uid = call.from_user.id
        tk, _ = _fetch_ticket(t_id, uid, call.id)
        if tk is None or tk["status"] == "closed":
            return
        bot.answer_callback_query(call.id)
        bot.send_message(uid, f"Введите сообщение для тикета #{t_id}:")
        user_steps[uid]["reply_to"] = t_id
        bot.register_next_step_handler_by_chat_id(uid, _save_reply)

    def _save_reply(msg: telebot.types.Message):
        uid  = msg.chat.id
        t_id = user_steps[uid].pop("reply_to", None)
        if not t_id:
            return bot.send_message(uid, "Нет выбранного тикета.")

        with SessionLocal() as s:
            tk = s.query(SupportTicket).filter_by(id=t_id, user_id=uid).first()
            if not tk or tk.status == "closed":
                return bot.send_message(uid, "Тикет не найден или закрыт.")
            s.add(SupportMessage(ticket_id=t_id, sender_id=uid, text=msg.text.strip()))
            s.commit()

        bot.send_message(uid, "Сообщение отправлено.")
        bot.send_message(ADMIN_SUPPORT_CHAT_ID,
                         f"💬 Новое сообщение в тикете #{t_id} от {uid}:\n{msg.text}")

    # ────────────────────────────────────────────────────────────────
    #   УТИЛИТА: получаем тикет + сообщения (только простые типы)
    # ────────────────────────────────────────────────────────────────
    def _fetch_ticket(t_id: int, uid: int, cb_id=None):
        with SessionLocal() as s:
            t = s.query(SupportTicket).filter_by(id=t_id, user_id=uid).first()
            if not t:
                if cb_id:
                    bot.answer_callback_query(cb_id, "Тикет не найден.", show_alert=True)
                return None, None

            tk_data = {"id": t.id, "status": t.status}
            msgs = [
                (m.sender_id, m.text, m.created_at)
                for m in s.query(SupportMessage)
                          .filter_by(ticket_id=t.id)
                          .order_by(SupportMessage.created_at.asc())
                          .all()
            ]
        return tk_data, msgs

    # ────────────────────────────────────────────────────────────────
    #   Кнопка «удалить сообщение»
    # ────────────────────────────────────────────────────────────────
    @bot.callback_query_handler(lambda c: c.data == "delete_msg")
    def _del(call):
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)