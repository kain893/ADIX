#!/usr/bin/env python3
from sqlalchemy import (
    create_engine, Column, Integer, BigInteger, String, Text,
    Numeric, ForeignKey, DateTime, Boolean, Float
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from datetime import datetime
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS

DATABASE_URI = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_engine(DATABASE_URI, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)
    username = Column(String, nullable=True)
    balance = Column(Numeric(10, 2), default=0)
    ref_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    is_banned = Column(Boolean, default=False)

    inn = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    company_name = Column(String, nullable=True)

    ban_reason = Column(String, nullable=True)
    ban_until = Column(DateTime, nullable=True)
    last_active = Column(DateTime, default=datetime.utcnow)

    ads = relationship("Ad", back_populates="user", cascade="all, delete-orphan")
    referrals = relationship(
        "User",
        backref="referred_by",
        remote_side=[id]
    )


class Ad(Base):
    __tablename__ = "ads"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)

    inline_button_text = Column(String, nullable=True)
    text = Column(Text, nullable=False)

    price = Column(Numeric(10, 2), nullable=True)
    quantity = Column(Integer, default=1)
    category = Column(String, nullable=True)
    subcategory = Column(String, nullable=True)
    city = Column(String, nullable=True)
    photos = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, nullable=False, default='pending')
    ad_type = Column(String, nullable=False, default='standard')
    is_active = Column(Boolean, default=True, nullable=False)
    selected_chat_ids = Column(Text, nullable=True)

    user = relationship("User", back_populates="ads")
    feedbacks = relationship("AdFeedback", back_populates="ad", cascade="all, delete-orphan")
    chats = relationship("AdChat", back_populates="ad")


class AdFeedback(Base):
    __tablename__ = "ad_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ad_id = Column(Integer, ForeignKey("ads.id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)

    rating = Column(Integer, nullable=True)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="pending")

    ad = relationship("Ad", back_populates="feedbacks")


class ChatGroup(Base):
    __tablename__ = "chat_groups"

    id           = Column(Integer, primary_key=True, index=True)
    chat_id      = Column(BigInteger, unique=True, nullable=False)
    title        = Column(String, nullable=False)
    region       = Column(String, nullable=False)       # "moscow" | "mo" | "rf"
    price_1      = Column(Float,   default=0.0)
    price_5      = Column(Float,   default=0.0)
    price_10     = Column(Float,   default=0.0)
    price_pin    = Column(Float,   default=0.0)
    participants = Column(Integer, default=0)
    is_active    = Column(Boolean, default=True)


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ad_id = Column(Integer, ForeignKey("ads.id"), nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    next_post_time = Column(DateTime, nullable=False)
    posts_left = Column(Integer, default=0)
    interval_minutes = Column(Integer, default=1440)


class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ad_id = Column(Integer, ForeignKey("ads.id"), nullable=False)
    buyer_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    seller_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(10, 2), default=0)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)


class TopUp(Base):
    __tablename__ = "topups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(10, 2), default=0)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    payment_system = Column(String, nullable=True)
    card_number    = Column(String, nullable=True)


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(10, 2), default=0)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    status = Column(String, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)

    messages = relationship("SupportMessage", back_populates="ticket", cascade="all, delete-orphan")


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey("support_tickets.id"), nullable=False)
    sender_id = Column(BigInteger, nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("SupportTicket", back_populates="messages")


class AdChat(Base):
    __tablename__ = "ad_chats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ad_id = Column(Integer, ForeignKey("ads.id"), nullable=False)
    buyer_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    seller_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    status = Column(String, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)

    ad = relationship("Ad", back_populates="chats")
    messages = relationship("AdChatMessage", back_populates="chat", cascade="all, delete-orphan")


class AdChatMessage(Base):
    __tablename__ = "ad_chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(Integer, ForeignKey("ad_chats.id"), nullable=False)
    sender_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    chat = relationship("AdChat", back_populates="messages")


class AdComplaint(Base):
    __tablename__ = "ad_complaints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ad_id = Column(Integer, ForeignKey("ads.id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    text = Column(Text, nullable=False)
    status = Column(String, default="new")
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    try:
        Base.metadata.create_all(bind=engine)
        print("Таблицы успешно созданы/обновлены.")
    except Exception as e:
        print(f"Ошибка при создании таблиц: {e}")