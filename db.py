import hashlib
import os

from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import engine, SessionLocal
from models import Base, AppUser, Todo


def hash_password(password: str, salt_hex: str) -> str:
    return hashlib.sha256((salt_hex + password).encode("utf-8")).hexdigest()


def make_salt() -> str:
    return os.urandom(16).hex()


def init_db():
    Base.metadata.create_all(bind=engine)


def _session() -> Session:
    return SessionLocal()


def register_user(username: str, password: str) -> bool:
    username = username.strip()
    if not username or not password:
        return False

    salt = make_salt()
    pwh = hash_password(password, salt)

    user = AppUser(username=username, salt=salt, password_hash=pwh)

    try:
        with _session() as db:
            db.add(user)
            db.commit()
        return True
    except IntegrityError:
        return False


def login_user(username: str, password: str):
    username = username.strip()
    if not username or not password:
        return None

    with _session() as db:
        stmt = select(AppUser).where(AppUser.username == username)
        user = db.execute(stmt).scalar_one_or_none()

        if not user:
            return None

        if hash_password(password, user.salt) != user.password_hash:
            return None

        return {"id": int(user.id), "username": user.username}


def add_task(user_id: int, text: str):
    text = text.strip()
    if not text:
        return

    with _session() as db:
        db.add(Todo(user_id=user_id, task=text, done=False))
        db.commit()


def show_all(user_id: int):
    with _session() as db:
        stmt = select(Todo.id, Todo.task, Todo.done).where(Todo.user_id == user_id).order_by(Todo.id)
        rows = db.execute(stmt).all()
        return [(int(r[0]), r[1], bool(r[2])) for r in rows]


def show_not_done(user_id: int):
    with _session() as db:
        stmt = (
            select(Todo.id, Todo.task, Todo.done)
            .where(Todo.user_id == user_id, Todo.done == False)  # noqa: E712
            .order_by(Todo.id)
        )
        rows = db.execute(stmt).all()
        return [(int(r[0]), r[1], bool(r[2])) for r in rows]


def change_status(user_id: int, task_id: int, done: bool) -> bool:
    with _session() as db:
        stmt = (
            update(Todo)
            .where(Todo.id == task_id, Todo.user_id == user_id)
            .values(done=done)
        )
        res = db.execute(stmt)
        db.commit()
        return res.rowcount > 0


def delete_task(user_id: int, task_id: int) -> bool:
    with _session() as db:
        stmt = delete(Todo).where(Todo.id == task_id, Todo.user_id == user_id)
        res = db.execute(stmt)
        db.commit()
        return res.rowcount > 0
