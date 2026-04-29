"""
Wealth Advisor web application.
Run with: python3 web_app.py
Then open: http://localhost:5000
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from functools import wraps

import numpy as np
from flask import (Flask, abort, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from portfolio_reader import read_portfolio
from market_data import analyze_portfolio
from advisor import get_portfolio_advice
from generate_report import build_html

# ── App setup ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", os.urandom(32))

BASE_DIR  = Path(__file__).parent
DATA_DIR  = BASE_DIR / "user_data"
DB_PATH   = BASE_DIR / "instance" / "wealth_advisor.db"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH.parent.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".csv", ".txt"}

# In-memory job progress (lost on restart, DB is source of truth for status)
_jobs: dict[str, dict] = {}


# ── Database ──────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS runs (
                id                TEXT PRIMARY KEY,
                user_id           INTEGER NOT NULL,
                original_filename TEXT,
                status            TEXT DEFAULT 'pending',
                error_message     TEXT,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at      TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)


# ── Helpers ───────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


def run_dir(user_id: int, run_id: str) -> Path:
    d = DATA_DIR / str(user_id) / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _numpy_safe(obj):
    if isinstance(obj, dict):
        return {k: _numpy_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_numpy_safe(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if math.isnan(float(obj)) else float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


# ── Background analysis job ───────────────────────────────────────────────────

def _run_analysis(run_id: str, user_id: int, csv_path: Path) -> None:
    def update(status: str, message: str) -> None:
        _jobs[run_id] = {"status": status, "message": message}

    update("running", "Reading portfolio...")
    try:
        portfolio_df = read_portfolio(str(csv_path))
        tickers = portfolio_df["ticker"].tolist()

        update("running", f"Fetching live market data for {len(tickers)} holdings...")
        analyses = analyze_portfolio(tickers)

        update("running", "Consulting Claude AI for portfolio advice...")
        advice = get_portfolio_advice(analyses, portfolio_df)

        update("running", "Generating report...")
        rdir = run_dir(user_id, run_id)
        json_path = rdir / "results.json"
        html_path = rdir / "report.html"

        payload = {
            "portfolio": portfolio_df.to_dict(orient="records"),
            "analyses":  {k: _numpy_safe(asdict(v)) for k, v in analyses.items()},
            "advice":    advice,
        }
        json_path.write_text(json.dumps(payload, indent=2))
        html_path.write_text(build_html(payload, str(json_path)), encoding="utf-8")

        with get_db() as conn:
            conn.execute(
                "UPDATE runs SET status='completed', completed_at=? WHERE id=?",
                (datetime.now().isoformat(), run_id),
            )
        update("completed", "Analysis complete!")

    except Exception as exc:
        with get_db() as conn:
            conn.execute(
                "UPDATE runs SET status='failed', error_message=? WHERE id=?",
                (str(exc), run_id),
            )
        update("failed", str(exc))


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email    = request.form.get("email", "").strip()
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
    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"]  = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))
        error = "Invalid username or password."
    return render_template("login.html", error=error, registered=request.args.get("registered"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Main app routes ───────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    with get_db() as conn:
        runs = conn.execute(
            "SELECT * FROM runs WHERE user_id=? ORDER BY created_at DESC LIMIT 30",
            (session["user_id"],),
        ).fetchall()
    return render_template("dashboard.html", runs=runs)


@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    run_id  = str(uuid.uuid4())
    user_id = session["user_id"]
    rdir    = run_dir(user_id, run_id)
    csv_path = rdir / "portfolio.csv"
    original_filename = "manual_entry.csv"

    uploaded = request.files.get("portfolio_file")
    text_input = request.form.get("portfolio_text", "").strip()

    if uploaded and uploaded.filename:
        ext = Path(uploaded.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            return redirect(url_for("dashboard"))
        original_filename = secure_filename(uploaded.filename)
        uploaded.save(str(csv_path))
    elif text_input:
        csv_path.write_text(text_input)
    else:
        return redirect(url_for("dashboard"))

    with get_db() as conn:
        conn.execute(
            "INSERT INTO runs (id, user_id, original_filename) VALUES (?, ?, ?)",
            (run_id, user_id, original_filename),
        )

    thread = threading.Thread(
        target=_run_analysis, args=(run_id, user_id, csv_path), daemon=True
    )
    thread.start()
    return redirect(url_for("status", run_id=run_id))


@app.route("/status/<run_id>")
@login_required
def status(run_id):
    with get_db() as conn:
        run = conn.execute(
            "SELECT * FROM runs WHERE id=? AND user_id=?",
            (run_id, session["user_id"]),
        ).fetchone()
    if not run:
        abort(404)
    return render_template("status.html", run=run, run_id=run_id)


@app.route("/api/status/<run_id>")
@login_required
def api_status(run_id):
    job = _jobs.get(run_id)
    if job:
        return jsonify(job)
    with get_db() as conn:
        run = conn.execute(
            "SELECT status, error_message FROM runs WHERE id=?", (run_id,)
        ).fetchone()
    if run:
        return jsonify({"status": run["status"], "message": run["error_message"] or ""})
    return jsonify({"status": "unknown", "message": ""})


@app.route("/report/<run_id>")
@login_required
def report(run_id):
    with get_db() as conn:
        run = conn.execute(
            "SELECT * FROM runs WHERE id=? AND user_id=?",
            (run_id, session["user_id"]),
        ).fetchone()
    if not run or run["status"] != "completed":
        abort(404)
    html_path = DATA_DIR / str(session["user_id"]) / "runs" / run_id / "report.html"
    return send_file(str(html_path))


@app.route("/download/json/<run_id>")
@login_required
def download_json(run_id):
    with get_db() as conn:
        run = conn.execute(
            "SELECT * FROM runs WHERE id=? AND user_id=?",
            (run_id, session["user_id"]),
        ).fetchone()
    if not run or run["status"] != "completed":
        abort(404)
    path = DATA_DIR / str(session["user_id"]) / "runs" / run_id / "results.json"
    return send_file(str(path), as_attachment=True, download_name="portfolio_analysis.json")


@app.route("/download/html/<run_id>")
@login_required
def download_html(run_id):
    with get_db() as conn:
        run = conn.execute(
            "SELECT * FROM runs WHERE id=? AND user_id=?",
            (run_id, session["user_id"]),
        ).fetchone()
    if not run or run["status"] != "completed":
        abort(404)
    path = DATA_DIR / str(session["user_id"]) / "runs" / run_id / "report.html"
    return send_file(str(path), as_attachment=True, download_name="portfolio_report.html")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("\n🚀  Wealth Advisor running at http://localhost:8080\n")
    app.run(debug=False, host="0.0.0.0", port=8080)
