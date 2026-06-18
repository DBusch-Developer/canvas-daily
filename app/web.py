"""The web app: sign up / log in, manage connections, view the grouped report,
open a stored detail page, and generate an AI breakdown on demand.

Pages are server-rendered Jinja2. Detail pages read from storage — no live
Canvas call on click. The breakdown fires only when the button is pressed.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware

from app.ai import AIError, AITimeoutError, generate_breakdown
from app.auth import hash_password, verify_password
from app.db import make_engine
from app.models import Assignment, Connection, User
from app.reports import report_for_user
from app.sync import sync_connection

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL") or os.environ["TEST_DATABASE_URL"]
        _engine = make_engine(url)
    return _engine


def get_session():
    with Session(_get_engine()) as session:
        yield session


def get_groq_client():
    client = httpx.Client(timeout=30.0)
    try:
        yield client
    finally:
        client.close()


def get_canvas_client():
    client = httpx.Client(timeout=30.0)
    try:
        yield client
    finally:
        client.close()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _current_user(request, session):
    user_id = request.session.get("user_id")
    return session.get(User, user_id) if user_id is not None else None


def _owned_assignment_or_404(session, assignment_id, user):
    assignment = session.get(Assignment, assignment_id)
    if assignment is None or assignment.connection.user_id != user.id:
        raise HTTPException(status_code=404)
    return assignment


def create_app():
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
        return TEMPLATES.TemplateResponse(request, "report.html", {"buckets": buckets})

    @app.get("/connections/new")
    def connection_form(request: Request, session: Session = Depends(get_session)):
        if _current_user(request, session) is None:
            return RedirectResponse("/login", status_code=303)
        return TEMPLATES.TemplateResponse(request, "connection_new.html", {})

    @app.post("/connections")
    def add_connection(request: Request, label: str = Form(), base_url: str = Form(),
                       account_type: str = Form(), access_token: str = Form(),
                       session: Session = Depends(get_session),
                       canvas: httpx.Client = Depends(get_canvas_client)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        connection = Connection(
            user_id=user.id, label=label, base_url=base_url,
            account_type=account_type, access_token=access_token,
        )
        session.add(connection)
        session.flush()  # assign connection.id before syncing
        try:
            sync_connection(session, connection, canvas)
        except Exception:
            # Keep the connection; a bad token or Canvas outage must not lose it.
            # Never surface or log the token. last_synced_at stays None as the flag.
            pass
        session.commit()
        return RedirectResponse("/", status_code=303)

    @app.get("/assignments/{assignment_id}")
    def detail(request: Request, assignment_id: int,
               session: Session = Depends(get_session)):
        user = _current_user(request, session)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        assignment = _owned_assignment_or_404(session, assignment_id, user)
        return TEMPLATES.TemplateResponse(request, "detail.html", {"a": assignment})

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
        context = {
            "title": assignment.name,
            "description": assignment.description,
            "points": assignment.points_possible,
            "due_date": assignment.due_at.isoformat() if assignment.due_at else None,
            "course": assignment.connection.label,
        }
        try:
            markdown = generate_breakdown(context, client, os.environ.get("GROQ_API_KEY", ""))
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
            request, template, {"a": assignment, "markdown": markdown})

    return app
