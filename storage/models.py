"""
ORM модели для SQLite базы данных.
"""

from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Account(Base):
    """Telegram аккаунт для комментирования."""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(20), unique=True, nullable=False)
    session_file = Column(String(255), nullable=False)
    proxy_id = Column(Integer, ForeignKey("proxies.id"), nullable=True)
    status = Column(String(20), default="active")  # active, cooldown, banned, flood_wait
    cooldown_until = Column(DateTime, nullable=True)
    comments_today = Column(Integer, default=0)
    total_comments = Column(Integer, default=0)
    days_active = Column(Integer, default=0)  # для прогрева
    persona_style = Column(String(50), default="casual")  # casual, formal, slang, tech
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active_at = Column(DateTime, nullable=True)

    proxy = relationship("Proxy", back_populates="accounts")
    comments = relationship("Comment", back_populates="account")


class Proxy(Base):
    """Прокси-сервер."""
    __tablename__ = "proxies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    proxy_type = Column(String(10), default="socks5")  # socks5, http
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String(255), nullable=True)
    password = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    last_checked = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    accounts = relationship("Account", back_populates="proxy")

    @property
    def url(self) -> str:
        auth = f"{self.username}:{self.password}@" if self.username else ""
        return f"{self.proxy_type}://{auth}{self.host}:{self.port}"


class Channel(Base):
    """Telegram канал для мониторинга."""
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String(255), nullable=True)
    title = Column(String(500), nullable=False)
    subscribers = Column(Integer, default=0)
    topic = Column(String(100), nullable=True)  # vpn, ai, services, etc.
    comments_enabled = Column(Boolean, default=True)
    discussion_group_id = Column(Integer, nullable=True)  # ID группы обсуждений
    is_active = Column(Boolean, default=True)
    is_blacklisted = Column(Boolean, default=False)
    last_post_checked = Column(Integer, default=0)  # ID последнего проверенного поста
    last_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    posts = relationship("Post", back_populates="channel")


class Post(Base):
    """Пост в канале."""
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(Integer, ForeignKey("channels.id"), nullable=False)
    telegram_post_id = Column(Integer, nullable=False)
    text = Column(Text, nullable=True)
    relevance_score = Column(Float, default=0.0)
    is_commented = Column(Boolean, default=False)
    posted_at = Column(DateTime, nullable=True)
    discovered_at = Column(DateTime, default=datetime.utcnow)

    channel = relationship("Channel", back_populates="posts")
    comments = relationship("Comment", back_populates="post")


class Comment(Base):
    """Отправленный комментарий."""
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    text = Column(Text, nullable=False)
    scenario = Column(String(1), nullable=False)  # A или B
    status = Column(String(20), default="sent")  # sent, failed, deleted
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    account = relationship("Account", back_populates="comments")
    post = relationship("Post", back_populates="comments")
