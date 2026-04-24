import os
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi.responses import FileResponse
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, update, delete, text
from sqlalchemy.exc import IntegrityError, InternalError
from sqlalchemy.orm import Session

from database import get_db, engine
from models import Base, AppUser, AuthToken, Todo

app = FastAPI(title="Todo Login/Register API")
SITE_FILE = Path(__file__).with_name("site.html")

@app.get("/", include_in_schema=False)
def site():
    if not SITE_FILE.exists():
        raise HTTPException(status_code=500, detail=f"site.html not found: {SITE_FILE}")
    return FileResponse(SITE_FILE)

@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    _create_db_objects()

def _create_db_objects():
    """Create PostgreSQL function, procedure and trigger at DB level."""
    statements = [
        # 1. Function: count pending (not done) tasks for a user
        """
        CREATE OR REPLACE FUNCTION count_pending_tasks(p_user_id BIGINT)
        RETURNS INT LANGUAGE sql STABLE AS $$
            SELECT COUNT(*)::INT FROM todo WHERE user_id = p_user_id AND done = false;
        $$
        """,
        # 2. Procedure: change done status of a task
        """
        CREATE OR REPLACE PROCEDURE set_task_status(p_task_id BIGINT, p_done BOOLEAN)
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE todo SET done = p_done WHERE id = p_task_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Task % not found', p_task_id;
            END IF;
        END;
        $$
        """,
        # 3. Trigger function: block deletion of incomplete tasks
        """
        CREATE OR REPLACE FUNCTION prevent_delete_pending()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.done = false THEN
                RAISE EXCEPTION 'Cannot delete incomplete task (id=%). Mark it done first.', OLD.id;
            END IF;
            RETURN OLD;
        END;
        $$
        """,
        "DROP TRIGGER IF EXISTS trg_no_delete_pending ON todo",
        """
        CREATE TRIGGER trg_no_delete_pending
            BEFORE DELETE ON todo
            FOR EACH ROW EXECUTE FUNCTION prevent_delete_pending()
        """,
    ]
    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()

def make_salt() -> str:
    return os.urandom(16).hex()

def hash_password(password: str, salt_hex: str) -> str:
    return hashlib.sha256((salt_hex + password).encode("utf-8")).hexdigest()

TOKEN_TTL_SECONDS = 3600

def make_token() -> str:
    return secrets.token_urlsafe(48)

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def normalize_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def get_current_token(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> AuthToken:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token_value = authorization.split(" ", 1)[1].strip()
    if not token_value:
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    stmt = select(AuthToken).where(AuthToken.token == token_value)
    token_row = db.execute(stmt).scalar_one_or_none()
    if not token_row:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if normalize_utc(token_row.expires_at) <= utcnow():
        db.delete(token_row)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return token_row

def get_current_user_id(current_token: AuthToken = Depends(get_current_token)) -> int:
    return int(current_token.user_id)

class RegisterIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)

class LoginIn(BaseModel):
    username: str
    password: str

class AuthOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime

class TaskCreate(BaseModel):
    task: str = Field(min_length=1, max_length=500)

class TaskOut(BaseModel):
    id: int
    task: str
    done: bool

class TaskUpdate(BaseModel):
    done: bool

class StatusIn(BaseModel):
    done: bool

@app.post("/auth/register", response_model=dict)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    username = data.username.strip()
    salt = make_salt()
    pwh = hash_password(data.password, salt)

    user = AppUser(username=username, salt=salt, password_hash=pwh)

    try:
        db.add(user)
        db.commit()
        return {"status": "ok"}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username already exists")

@app.post("/auth/login", response_model=AuthOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    username = data.username.strip()

    stmt = select(AppUser).where(AppUser.username == username)
    user = db.execute(stmt).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if hash_password(data.password, user.salt) != user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    expires_at = utcnow() + timedelta(seconds=TOKEN_TTL_SECONDS)
    token_value = make_token()

    db.add(AuthToken(user_id=user.id, token=token_value, expires_at=expires_at))
    db.commit()

    return AuthOut(access_token=token_value, expires_at=expires_at)

@app.post("/auth/logout", response_model=dict)
def logout(current_token: AuthToken = Depends(get_current_token), db: Session = Depends(get_db)):
    db.delete(current_token)
    db.commit()
    return {"status": "ok"}

@app.post("/tasks", response_model=dict)
def add_task(data: TaskCreate, user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    task_text = data.task.strip()
    if not task_text:
        raise HTTPException(status_code=400, detail="Empty task")

    db.add(Todo(user_id=user_id, task=task_text, done=False))
    db.commit()
    return {"status": "ok"}

@app.get("/tasks", response_model=List[TaskOut])
def list_tasks(user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    stmt = select(Todo.id, Todo.task, Todo.done).where(Todo.user_id == user_id).order_by(Todo.id)
    rows = db.execute(stmt).all()
    return [TaskOut(id=int(r[0]), task=r[1], done=bool(r[2])) for r in rows]

@app.get("/tasks/pending", response_model=List[TaskOut])
def list_pending(user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    stmt = (
        select(Todo.id, Todo.task, Todo.done)
        .where(Todo.user_id == user_id, Todo.done == False)  # noqa: E712
        .order_by(Todo.id)
    )
    rows = db.execute(stmt).all()
    return [TaskOut(id=int(r[0]), task=r[1], done=bool(r[2])) for r in rows]

@app.patch("/tasks/{task_id}", response_model=dict)
def set_done(task_id: int, data: TaskUpdate, user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    stmt = (
        update(Todo)
        .where(Todo.id == task_id, Todo.user_id == user_id)
        .values(done=data.done)
    )
    res = db.execute(stmt)
    db.commit()

    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "ok"}

@app.delete("/tasks/{task_id}", response_model=dict)
def delete_task(task_id: int, user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    stmt = delete(Todo).where(Todo.id == task_id, Todo.user_id == user_id)
    try:
        res = db.execute(stmt)
        db.commit()
    except InternalError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Cannot delete incomplete task. Mark it done first.")

    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "ok"}


# --- DB-level function endpoint ---

@app.get("/tasks/pending/count", response_model=dict)
def pending_count(user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    """Calls PostgreSQL function count_pending_tasks()."""
    result = db.execute(
        text("SELECT count_pending_tasks(:uid)"), {"uid": user_id}
    ).scalar()
    return {"pending_count": result}


# --- DB-level procedure endpoint ---

@app.post("/tasks/{task_id}/set-status", response_model=dict)
def set_status_proc(
    task_id: int,
    data: StatusIn,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Calls PostgreSQL procedure set_task_status()."""
    task = db.execute(
        select(Todo).where(Todo.id == task_id, Todo.user_id == user_id)
    ).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    try:
        db.execute(text("CALL set_task_status(:tid, :done)"), {"tid": task_id, "done": data.done})
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok"}
