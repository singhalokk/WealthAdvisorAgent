"""
WealthAdvisorAgent — conversational chat web app.
Run with: python3 web_app.py
Then open: http://localhost:8080
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, send_file, session, stream_with_context, url_for)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from agent import stream_response

app = Flask(__name__)

# ── Security config ───────────────────────────────────────────────────────────
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(32))
app.config["WTF_CSRF_TIME_LIMIT"]     = 3600
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"]   = \
    os.environ.get("HTTPS", "false").lower() == "true"

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

csrf    = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "user_data"
DB_PATH  = BASE_DIR / "instance" / "wealth_advisor.db"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH.parent.mkdir(exist_ok=True)

_histories: dict[int, list] = {}
MAX_HISTORY = 20

# Analysis status keyed by portfolio id
_analysis_status: dict[str, str] = {}

MAX_MESSAGE_LEN   = 2000
MAX_PORTFOLIO_LEN = 50_000


# ── Helpers ───────────────────────────────────────────────────────────────────

def _portfolios_dir(user_id: int) -> Path:
    d = DATA_DIR / str(user_id) / "portfolios"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _auto_name(source: str) -> str:
    label = {"uploaded": "Uploaded", "entered": "Entered", "screen_read": "ScreenRead"}.get(source, "Portfolio")
    return f"Portfolio_{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _set_active(user_id: int, portfolio_id: str) -> None:
    """Write the active portfolio id so tools.py can find it."""
    (DATA_DIR / str(user_id) / ".active_portfolio").write_text(portfolio_id)


def _get_active_id(user_id: int) -> str | None:
    p = DATA_DIR / str(user_id) / ".active_portfolio"
    return p.read_text().strip() if p.exists() else None


# ── Security headers ──────────────────────────────────────────────────────────

@app.after_request
def set_security_headers(response: Response) -> Response:
    if request.path.startswith("/report"):
        return response
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src  'self' 'unsafe-inline'; "
        "img-src    'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    response.set_cookie("csrf_token", generate_csrf(), samesite="Lax",
                        httponly=False,
                        secure=app.config["SESSION_COOKIE_SECURE"])
    return response


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS portfolios (
                id         TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                name       TEXT NOT NULL,
                source     TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


# ── Background portfolio analysis ─────────────────────────────────────────────

def _run_analysis(user_id: int, portfolio_id: str) -> None:
    try:
        _analysis_status[portfolio_id] = "running"

        from market_data import analyze_portfolio as _analyze_portfolio
        from advisor import get_portfolio_advice
        from generate_report import build_html
        from portfolio_reader import read_portfolio
        import dataclasses

        csv_path = _portfolios_dir(user_id) / f"{portfolio_id}.csv"
        portfolio_df = read_portfolio(str(csv_path))
        tickers      = portfolio_df["ticker"].tolist()
        analyses     = _analyze_portfolio(tickers)
        advice       = get_portfolio_advice(analyses, portfolio_df)

        report_data = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "advice":       advice,
            "analyses":     {t: dataclasses.asdict(a) for t, a in analyses.items()},
        }

        html = build_html(report_data, str(csv_path))
        report_path = _portfolios_dir(user_id) / f"{portfolio_id}_report.html"
        report_path.write_text(html, encoding="utf-8")

        _analysis_status[portfolio_id] = "done"

    except Exception as exc:
        _analysis_status[portfolio_id] = f"error: {exc}"


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("chat") if "user_id" in session else url_for("login"))


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def register():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()[:80]
        email    = request.form.get("email",    "").strip()[:254]
        password = request.form.get("password", "")
        if not username or not email or not password:
            error = "All fields are required."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        else:
            try:
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                        (username, email, generate_password_hash(password)),
                    )
                return redirect(url_for("login", registered=1))
            except sqlite3.IntegrityError:
                error = "Username or email already taken."
    return render_template("login.html", mode="register", error=error)


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()[:80]
        password = request.form.get("password", "")
        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"]  = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("chat"))
        error = "Invalid username or password."
    registered = request.args.get("registered")
    return render_template("login.html", mode="login", error=error, registered=registered)


@app.route("/logout")
def logout():
    uid = session.get("user_id")
    if uid and uid in _histories:
        del _histories[uid]
    session.clear()
    return redirect(url_for("login"))


# ── Chat routes ───────────────────────────────────────────────────────────────

@app.route("/chat")
@login_required
def chat():
    user_id    = session["user_id"]
    active_id  = _get_active_id(user_id)
    if active_id:
        analysis_status = _analysis_status.get(active_id, "idle")
        report_exists   = (_portfolios_dir(user_id) / f"{active_id}_report.html").exists()
    else:
        analysis_status = "idle"
        report_exists   = False
    return render_template("chat.html",
                           active_portfolio_id=active_id,
                           report_exists=report_exists,
                           analysis_status=analysis_status)


@app.route("/chat/stream")
@login_required
@csrf.exempt
@limiter.limit("60 per minute")
def chat_stream():
    message = request.args.get("message", "").strip()[:MAX_MESSAGE_LEN]
    if not message:
        return Response("data: {}\n\n", mimetype="text/event-stream")

    user_id = session["user_id"]
    history = _histories.get(user_id, [])

    def generate():
        assistant_text = ""
        for chunk in stream_response(message, history, user_id):
            yield chunk
            try:
                payload = json.loads(chunk.removeprefix("data: ").strip())
                if payload.get("type") == "text":
                    assistant_text += payload.get("content", "")
            except Exception:
                pass

        new_history = list(history)
        new_history.append({"role": "user",     "content": message})
        new_history.append({"role": "assistant", "content": assistant_text})
        if len(new_history) > MAX_HISTORY * 2:
            new_history = new_history[-(MAX_HISTORY * 2):]
        _histories[user_id] = new_history

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/chat/clear", methods=["POST"])
@login_required
def chat_clear():
    _histories.pop(session["user_id"], None)
    return ("", 204)


# ── Portfolio CRUD ────────────────────────────────────────────────────────────

@app.route("/api/portfolios")
@login_required
@csrf.exempt
def list_portfolios():
    user_id  = session["user_id"]
    active_id = _get_active_id(user_id)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, source, created_at FROM portfolios WHERE user_id=? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
    result = []
    for r in rows:
        pid = r["id"]
        status = _analysis_status.get(pid, "idle")
        report_ready = (_portfolios_dir(user_id) / f"{pid}_report.html").exists()
        result.append({
            "id":           pid,
            "name":         r["name"],
            "source":       r["source"],
            "created_at":   r["created_at"],
            "active":       pid == active_id,
            "status":       status,
            "report_ready": report_ready,
        })
    return jsonify(result)


@app.route("/portfolio/upload", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def portfolio_upload():
    user_id = session["user_id"]

    uploaded   = request.files.get("portfolio_file")
    text_input = request.form.get("portfolio_text", "").strip()
    source     = request.form.get("source", "uploaded")
    name       = request.form.get("name", "").strip()[:120] or _auto_name(source)

    portfolio_id = str(uuid.uuid4())
    csv_path     = _portfolios_dir(user_id) / f"{portfolio_id}.csv"

    if uploaded and uploaded.filename:
        if len(uploaded.read()) > MAX_PORTFOLIO_LEN:
            return ("File too large (max 50 KB)", 413)
        uploaded.seek(0)
        uploaded.save(str(csv_path))
    elif text_input:
        if len(text_input.encode()) > MAX_PORTFOLIO_LEN:
            return ("Text too large (max 50 KB)", 413)
        csv_path.write_text(text_input)
    else:
        return ("No data provided", 400)

    with get_db() as conn:
        conn.execute(
            "INSERT INTO portfolios (id, user_id, name, source) VALUES (?, ?, ?, ?)",
            (portfolio_id, user_id, name, source),
        )

    _set_active(user_id, portfolio_id)
    _analysis_status[portfolio_id] = "running"
    t = threading.Thread(target=_run_analysis, args=(user_id, portfolio_id), daemon=True)
    t.start()

    return jsonify({"id": portfolio_id, "name": name})


@app.route("/api/portfolio/<pid>/select", methods=["POST"])
@login_required
def portfolio_select(pid):
    user_id = session["user_id"]
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM portfolios WHERE id=? AND user_id=?", (pid, user_id)
        ).fetchone()
    if not row:
        abort(404)
    _set_active(user_id, pid)
    return ("", 204)


@app.route("/api/portfolio/<pid>/analyze", methods=["POST"])
@login_required
def portfolio_reanalyze(pid):
    user_id = session["user_id"]
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM portfolios WHERE id=? AND user_id=?", (pid, user_id)
        ).fetchone()
    if not row:
        abort(404)
    if _analysis_status.get(pid) == "running":
        return jsonify({"status": "already_running"})
    _analysis_status[pid] = "running"
    t = threading.Thread(target=_run_analysis, args=(user_id, pid), daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/api/portfolio/<pid>", methods=["DELETE"])
@login_required
def portfolio_delete(pid):
    user_id = session["user_id"]
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM portfolios WHERE id=? AND user_id=?", (pid, user_id)
        ).fetchone()
    if not row:
        abort(404)
    with get_db() as conn:
        conn.execute("DELETE FROM portfolios WHERE id=?", (pid,))
    # Remove files
    for suffix in [".csv", "_report.html"]:
        f = _portfolios_dir(user_id) / f"{pid}{suffix}"
        if f.exists():
            f.unlink()
    _analysis_status.pop(pid, None)
    # If this was the active portfolio, clear it
    if _get_active_id(user_id) == pid:
        active_file = DATA_DIR / str(user_id) / ".active_portfolio"
        active_file.unlink(missing_ok=True)
    return ("", 204)


# ── Analysis status polling ───────────────────────────────────────────────────

@app.route("/api/analysis/status")
@login_required
@csrf.exempt
def analysis_status():
    user_id   = session["user_id"]
    active_id = _get_active_id(user_id)
    if not active_id:
        return jsonify({"status": "idle", "report_ready": False, "portfolio_id": None})
    status       = _analysis_status.get(active_id, "idle")
    report_ready = (_portfolios_dir(user_id) / f"{active_id}_report.html").exists()
    return jsonify({"status": status, "report_ready": report_ready, "portfolio_id": active_id})


@app.route("/api/analysis/status/<pid>")
@login_required
@csrf.exempt
def analysis_status_by_id(pid):
    user_id = session["user_id"]
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM portfolios WHERE id=? AND user_id=?", (pid, user_id)
        ).fetchone()
    if not row:
        abort(404)
    status       = _analysis_status.get(pid, "idle")
    report_ready = (_portfolios_dir(user_id) / f"{pid}_report.html").exists()
    return jsonify({"status": status, "report_ready": report_ready})


# ── Report viewer ─────────────────────────────────────────────────────────────

@app.route("/report/<pid>")
@login_required
def view_report(pid):
    user_id = session["user_id"]
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM portfolios WHERE id=? AND user_id=?", (pid, user_id)
        ).fetchone()
    if not row:
        abort(404)
    report_path = _portfolios_dir(user_id) / f"{pid}_report.html"
    if not report_path.exists():
        return ("Report not ready yet.", 404)
    return send_file(str(report_path), mimetype="text/html")


@app.route("/report/<pid>/download")
@login_required
def download_report(pid):
    user_id = session["user_id"]
    with get_db() as conn:
        row = conn.execute(
            "SELECT name FROM portfolios WHERE id=? AND user_id=?", (pid, user_id)
        ).fetchone()
    if not row:
        abort(404)
    report_path = _portfolios_dir(user_id) / f"{pid}_report.html"
    if not report_path.exists():
        return ("Report not ready yet.", 404)
    safe_name = re.sub(r"[^\w\-]", "_", row["name"]) + ".html"
    return send_file(str(report_path), mimetype="text/html",
                     as_attachment=True, download_name=safe_name)


# ── Portfolio screenshot reader ───────────────────────────────────────────────

@app.route("/portfolio/screenshot", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def portfolio_screenshot():
    import anthropic as _anthropic

    data      = request.get_json(silent=True) or {}
    image_b64 = data.get("image", "")
    if not image_b64:
        return jsonify({"error": "No image provided"}), 400
    if "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]

    try:
        client = _anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Extract every stock, ETF, or mutual fund position visible in this image. "
                            "For each row, read BOTH the code/ticker on the first line AND the full fund "
                            "or company name on the second line. "
                            "Return ONLY a JSON array — no explanation, no markdown. "
                            'Each element: {"ticker":"AAPL","name":"Apple Inc","shares":50,"current_value":9250,"cost_basis":6000}. '
                            "The 'name' field is the full fund or company name — include it even if long. "
                            "Use null for numeric fields not visible. Exclude header rows, totals, cash."
                        ),
                    },
                ],
            }],
        )
        text  = msg.content[0].text.strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        holdings = json.loads(match.group()) if match else []
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"holdings": holdings})


# ── Price polling ─────────────────────────────────────────────────────────────

@app.route("/api/price/<ticker>")
@login_required
@csrf.exempt
@limiter.limit("30 per minute")
def api_price(ticker):
    if not ticker.isalnum() or len(ticker) > 10:
        abort(400)
    from tools import tool_get_current_price
    return tool_get_current_price(ticker.upper())


# ── Rate-limit error handler ──────────────────────────────────────────────────

@app.errorhandler(429)
def too_many_requests(e):
    return ("Too many requests — please slow down.", 429)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("\n🤖  WealthAdvisor Agent running at http://localhost:8080\n")
    app.run(debug=False, host="0.0.0.0", port=8080, threaded=True)
