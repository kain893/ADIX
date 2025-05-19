#!/usr/bin/env python3
import sys

from sqlalchemy import text, MetaData

# Импортируем ВСЕ ваши модели
from database import (
    User, Ad, AdFeedback,
    ChatGroup, ScheduledPost,
    Sale, TopUp, Withdrawal,
    SupportTicket, SupportMessage,
    AdChat, AdChatMessage,
    AdComplaint,
)
from database import engine


def reset_tables():
    """
    Полностью дропает схему public (CASCADE), создаёт схему заново
    и создаёт все таблицы из списка моделей. Все данные будут утеряны!
    """
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        print("Схема 'public' пересоздана (DROP + CREATE).")

    # Собираем все таблицы в один MetaData
    metadata = MetaData()
    for tbl in [
        User.__table__,
        Ad.__table__,
        AdFeedback.__table__,
        ChatGroup.__table__,
        ScheduledPost.__table__,
        Sale.__table__,
        TopUp.__table__,
        Withdrawal.__table__,
        SupportTicket.__table__,
        SupportMessage.__table__,
        AdChat.__table__,
        AdChatMessage.__table__,
        AdComplaint.__table__,
    ]:
        tbl.tometadata(metadata)

    # И создаём их сразу вместе
    metadata.create_all(bind=engine)
    print("Таблицы созданы заново (create_all).")

if __name__ == "__main__":
    print("ВНИМАНИЕ: Эта операция УДАЛИТ все данные во всех таблицах!!!")
    answer = input("Вы уверены, что хотите продолжить? (yes/no): ").strip().lower()
    if answer in ("yes", "y", "да"):
        reset_tables()
    else:
        print("Операция отменена.")
        sys.exit(0)