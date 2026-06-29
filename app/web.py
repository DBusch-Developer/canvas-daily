"""The web app: sign up / log in, manage connections, view the grouped report,
open a stored detail page, and generate an AI breakdown on demand.

Pages are server-rendered Jinja2. Detail pages read from storage — no live
Canvas call on click. The breakdown fires only when the button is pressed.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware

from app.ai import AIError, AITimeoutError, generate_bullets
from app.auth import hash_password, verify_password
from app.canvas import verify_token
from app.dates import group_by_week
from app.db import make_engine
from app.models import Assignment, Connection, Course, User
from app.rag.answer import REFUSAL, answer_question
from app.rag.retrieve import retrieve
from app.reports import excuse_assignment, report_for_user
from app.sync import sync_connection
from app.sync_content import sync_course_content

logger = logging.getLogger(__name__)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL") or os.environ["TEST_DATABASE_URL"]
        _engine = make_engine(url)
    return _engine


def get_engine():
    return _get_engine()


def get_session(engine=Depends(get_engine)):
    with Session(engine) as session:
        yield session


def get_groq_client():
    client = httpx.Client(timeout=30.0)
    try:
        yield client
    finally:
        client.close()


def get_canvas_client_factory():
    return lambda: httpx.Client(timeout=30.0)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _current_user(request, session):
    user_id = request.session.get("user_id")
    return session.get(User, user_id) if user_id is not None else None


def _ask_course_enabled() -> bool:
    return os.environ.get("ASK_COURSE_ENABLED", "").lower() in ("1", "true", "yes")


def _utc_epoch(dt) -> float:
    """Epoch seconds for a stored timestamp, treating naive values as UTC.

    The app stores naive-UTC times (see _utcnow), so a naive datetime's
    .timestamp() would otherwise be misread as local time.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _sync_baseline(course) -> str:
    """Epoch seconds of the course's last content sync, or "" if never synced.

    URL-safe (digits and a dot) so it survives the query string the syncing
    notice polls with — unlike an ISO timestamp, whose '+' becomes a space.
    """
    ts = course.last_content_synced_at
    return str(_utc_epoch(ts)) if ts else ""


def _content_synced_since(course, since: str) -> bool:
    """True once the course's content sync has finished past the baseline.

    The background sync stamps last_content_synced_at with a fresh time when it
    completes, so the sync is done when that time is strictly newer than the
    baseline captured when the user pressed Sync.
    """
    ts = course.last_content_synced_at
    if ts is None:
        return False
    try:
        baseline = float(since) if since else 0.0
    except ValueError:
        baseline = 0.0
    return _utc_epoch(ts) > baseline


def _owned_assignment_or_404(session, assignment_id, user):
    assignment = session.get(Assignment, assignment_id)
    if assignment is None or assignment.connection.user_id != user.id:
        raise HTTPException(status_code=404)
    return assignment


def _owned_course_or_404(session, course_id, user):
    course = session.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404)
    connection = session.get(Connection, course.connection_id)
    if connection is None or connection.user_id != user.id:
        raise HTTPException(status_code=404)
    return course


def _owned_connection_or_404(session, connection_id, user):
    connection = session.get(Connection, connection_id)
    if connection is None or connection.user_id != user.id:
        raise HTTPException(status_code=404)
    return connection


def run_connection_sync(engine, connection_id, client_factory):
    """Pull one connection's assignments in the background and record the
    outcome. Opens its own session (the request's is gone). Never logs the token."""
    with Session(engine) as session:
        connection = session.get(Connection, connection_id)
        if connection is None:
            return
        client = client_factory()
        try:
            sync_connection(session, connection, client)
            connection.sync_status = "ok"
        except Exception:
            connection.sync_status = "error"
            logger.warning("background sync failed for connection %s", connection_id)
        finally:
            client.close()
        session.add(connection)
        session.commit()


def run_course_content_sync(engine, course_id, client_factory):
    """Sync one course's Canvas content in the background.

    Opens its own session (the request's is gone). Never logs the token.
    """
    with Session(engine) as session:
        course = session.get(Course, course_id)
        if course is None:
            return
        connection = session.get(Connection, course.connection_id)
        if connection is None:
            return
        client = client_factory()
        try:
            sync_course_content(session, connection, course, client)
        except Exception:
            logger.warning("background content sync failed for course %s", course_id)
        finally:
            client.close()
        session.commit()


def create_app():
    TEMPLATES.env.globals["ask_course_enabled"] = _ask_course_enabled
    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key=os.environ.get("SESSION_SECRET", "dev-insecure-secret"),
    )
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "static")),
        name="static",
    )

    @app.get("/signup")
    def signup_form(request: Request):
        return TEMPLATES.TemplateResponse(request, "signup.html", {})

    @app.post("/signup")
    def signup(request: Request, email: str = Form(), password: str = Form(),
               session: Session = Depends(get_session)):
        if session.exec(select(User).where(User.email == email)).first():
            return TEMPLATES.TemplateResponse(
                request, "signup.html", {"error": "That email is already registered."},
                status_code=400,
            )
        user = User(email=email, password_hash=hash_password(password))
        session.add(user)
        session.commit()
        session.refresh(user)
        request.session["user_id"] = user.id
        return RedirectResponse("/", status_code=303)

    @app.get("/login")
    def login_form(request: Request):
        return TEMPLATES.TemplateResponse(request, "login.html", {})

    @app.post("/login")
    def login(request: Request, email: str = Form(), password: str = Form(),
              session: Session = Depends(get_session)):
        user = session.exec(select(User).where(User.email == email)).first()
        if user is None or not verify_password(password, user.password_hash):
            return TEMPLATES.TemplateResponse(
                request, "login.html", {"error": "Invalid email or password."},
                status_code=401,
            )
        request.session["user_id"] = user.id
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/")
    def report(request: Request, session: Session = Depends(get_session)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        buckets = report_for_user(session, user.id, _now())
        upcoming_weeks = group_by_week(buckets["upcoming"], _now())
        return TEMPLATES.TemplateResponse(
            request, "report.html",
            {"buckets": buckets, "upcoming_weeks": upcoming_weeks})

    @app.get("/connections")
    def connections_list(request: Request, session: Session = Depends(get_session)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        connections = session.exec(
            select(Connection)
            .where(Connection.user_id == user.id)
            .order_by(Connection.created_at)
        ).all()
        return TEMPLATES.TemplateResponse(
            request, "settings.html", {"connections": connections})

    @app.get("/connections/new")
    def connection_form(request: Request, session: Session = Depends(get_session)):
        if _current_user(request, session) is None:
            return RedirectResponse("/login", status_code=303)
        return TEMPLATES.TemplateResponse(request, "connection_new.html", {})

    @app.post("/connections")
    def add_connection(request: Request, background_tasks: BackgroundTasks,
                       label: str = Form(), base_url: str = Form(),
                       account_type: str = Form(), access_token: str = Form(),
                       session: Session = Depends(get_session),
                       engine=Depends(get_engine),
                       client_factory=Depends(get_canvas_client_factory)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        # Verify the token with Canvas before storing anything — a rejected or
        # unverifiable token fails fast in the form, never as a silent
        # background error. The token is never logged.
        client = client_factory()
        try:
            result = verify_token(base_url, access_token, client)
        finally:
            client.close()
        if result != "ok":
            message = (
                "Canvas rejected this access token. In Canvas, go to "
                "Account → Settings, generate a new access token, and paste it again."
                if result == "invalid" else
                "We couldn't reach Canvas to verify this connection. "
                "Double-check the base URL and try again."
            )
            return TEMPLATES.TemplateResponse(
                request, "connection_new.html",
                {"error": message, "label": label, "base_url": base_url,
                 "account_type": account_type},
                status_code=400,
            )
        connection = Connection(
            user_id=user.id, label=label, base_url=base_url,
            account_type=account_type, access_token=access_token,
        )
        session.add(connection)
        session.commit()
        session.refresh(connection)
        background_tasks.add_task(
            run_connection_sync, engine, connection.id, client_factory)
        return RedirectResponse(
            f"/connections/{connection.id}/setup", status_code=303)

    @app.post("/connections/{connection_id}/delete")
    def delete_connection(request: Request, connection_id: int,
                          session: Session = Depends(get_session)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        connection = _owned_connection_or_404(session, connection_id, user)
        session.delete(connection)
        session.commit()
        return RedirectResponse("/connections", status_code=303)

    @app.post("/connections/{connection_id}/sync")
    def sync_connection_now(request: Request, connection_id: int,
                            background_tasks: BackgroundTasks,
                            session: Session = Depends(get_session),
                            engine=Depends(get_engine),
                            client_factory=Depends(get_canvas_client_factory)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        connection = _owned_connection_or_404(session, connection_id, user)
        background_tasks.add_task(
            run_connection_sync, engine, connection.id, client_factory)
        return RedirectResponse("/connections", status_code=303)

    @app.get("/connections/{connection_id}/setup")
    def connection_setup(request: Request, connection_id: int,
                         session: Session = Depends(get_session)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        connection = _owned_connection_or_404(session, connection_id, user)
        if connection.sync_status == "ok":
            return RedirectResponse("/connections", status_code=303)
        return TEMPLATES.TemplateResponse(
            request, "setup.html", {"connection": connection})

    @app.get("/connections/{connection_id}/status")
    def connection_status(request: Request, connection_id: int,
                          session: Session = Depends(get_session)):
        user = _current_user(request, session)
        if user is None:
            raise HTTPException(status_code=401)
        connection = _owned_connection_or_404(session, connection_id, user)
        return JSONResponse({"status": connection.sync_status})

    @app.get("/assignments/{assignment_id}")
    def detail(request: Request, assignment_id: int,
               session: Session = Depends(get_session)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        assignment = _owned_assignment_or_404(session, assignment_id, user)
        return TEMPLATES.TemplateResponse(request, "detail.html", {"a": assignment})

    @app.post("/assignments/{assignment_id}/excuse")
    def excuse(request: Request, assignment_id: int,
               session: Session = Depends(get_session)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        _owned_assignment_or_404(session, assignment_id, user)
        excuse_assignment(session, assignment_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/assignments/{assignment_id}/breakdown")
    def breakdown(request: Request, assignment_id: int,
                  session: Session = Depends(get_session),
                  client: httpx.Client = Depends(get_groq_client)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        assignment = _owned_assignment_or_404(session, assignment_id, user)
        # An HTMX request swaps just the result in place; a normal request gets
        # the full page. Don't hard-require the header — degrade to full page.
        template = (
            "breakdown_result.html"
            if request.headers.get("HX-Request")
            else "breakdown.html"
        )
        # No AI breakdown for quizzes — refuse before any Groq call.
        if assignment.is_quiz:
            return TEMPLATES.TemplateResponse(
                request, template,
                {"a": assignment, "error": "AI breakdown isn't available for quizzes."},
                status_code=400,
            )
        context = {
            "title": assignment.name,
            "description": assignment.description,
            "points": assignment.points_possible,
            "due_date": assignment.due_at.isoformat() if assignment.due_at else None,
            "course": assignment.connection.label,
        }
        try:
            sections = generate_bullets(context, client, os.environ.get("GROQ_API_KEY", ""))
        except AITimeoutError:
            return TEMPLATES.TemplateResponse(
                request, template,
                {"a": assignment, "error": "The AI breakdown took too long. Please try again."},
                status_code=504,
            )
        except AIError:
            return TEMPLATES.TemplateResponse(
                request, template,
                {"a": assignment, "error": "The AI breakdown is unavailable right now."},
                status_code=502,
            )
        return TEMPLATES.TemplateResponse(
            request, template, {"a": assignment, "sections": sections})

    @app.get("/ask")
    def ask_picker(request: Request, session: Session = Depends(get_session)):
        if not _ask_course_enabled():
            raise HTTPException(status_code=404)
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        courses = session.exec(
            select(Course).join(Connection, Course.connection_id == Connection.id)
            .where(Connection.user_id == user.id)
        ).all()
        return TEMPLATES.TemplateResponse(request, "course_picker.html",
                                          {"courses": courses})

    @app.post("/courses/{course_id}/sync-content")
    def sync_content(request: Request, course_id: int,
                     background_tasks: BackgroundTasks,
                     session: Session = Depends(get_session),
                     engine=Depends(get_engine),
                     client_factory=Depends(get_canvas_client_factory)):
        if not _ask_course_enabled():
            raise HTTPException(status_code=404)
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        _owned_course_or_404(session, course_id, user)
        background_tasks.add_task(run_course_content_sync, engine, course_id, client_factory)
        return RedirectResponse(f"/courses/{course_id}/ask?syncing=1", status_code=303)

    @app.get("/courses/{course_id}/ask")
    def course_ask_page(request: Request, course_id: int,
                        syncing: str | None = None,
                        session: Session = Depends(get_session)):
        if not _ask_course_enabled():
            raise HTTPException(status_code=404)
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        course = _owned_course_or_404(session, course_id, user)
        return TEMPLATES.TemplateResponse(request, "course_chat.html",
                                          {"course": course, "question": None,
                                           "answer": None, "sources": [],
                                           "syncing": bool(syncing),
                                           "sync_baseline": _sync_baseline(course)})

    @app.get("/courses/{course_id}/sync-status")
    def sync_status(request: Request, course_id: int,
                    since: str = "",
                    session: Session = Depends(get_session)):
        if not _ask_course_enabled():
            raise HTTPException(status_code=404)
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        course = _owned_course_or_404(session, course_id, user)
        done = _content_synced_since(course, since)
        return TEMPLATES.TemplateResponse(request, "_sync_notice.html",
                                          {"course": course, "syncing": not done,
                                           "sync_baseline": _sync_baseline(course)})

    @app.post("/courses/{course_id}/ask")
    def course_ask(request: Request, course_id: int,
                   question: str = Form(...),
                   session: Session = Depends(get_session),
                   client: httpx.Client = Depends(get_groq_client)):
        if not _ask_course_enabled():
            raise HTTPException(status_code=404)
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        course = _owned_course_or_404(session, course_id, user)
        question = question.strip()[:500]
        if not question:
            return TEMPLATES.TemplateResponse(
                request, "course_chat.html",
                {"course": course, "question": "",
                 "answer": REFUSAL,
                 "sources": []},
            )
        chunks = retrieve(session, course.id, question, k=5)
        result = answer_question(question, chunks, client)
        return TEMPLATES.TemplateResponse(request, "course_chat.html",
                                          {"course": course, "question": question,
                                           "answer": result["answer"],
                                           "sources": result["sources"]})

    return app
