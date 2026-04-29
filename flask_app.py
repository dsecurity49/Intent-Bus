import os
import sqlite3
import time
import json
import uuid
import secrets
import logging
from flask import Flask, request, jsonify, g, Response, render_template_string

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024

# --- CONFIG ---
API_KEY = os.environ["BUS_SECRET"]
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
DB_PATH = os.environ.get("BUS_DB_PATH", os.path.join(os.path.dirname(__file__), "infrastructure.db"))
MAINTENANCE_MODE = os.environ.get("BUS_MAINTENANCE_MODE", "False").lower() == "true"

RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 60
CLAIM_TIMEOUT = 60
MAX_PAYLOAD = 5 * 1024
MAX_TTL = 86400
MAX_OPEN_INTENTS_PER_KEY = 100

logging.basicConfig(level=logging.INFO)

# --- DASHBOARD TEMPLATE ---
ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>dsecurity | Intent-Bus</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@1/css/pico.min.css">
    <style>
        :root { --primary: #00ff41; --background-color: #0d1117; }
        body { background: var(--background-color); color: #c9d1d9; font-family: 'Courier New', Courier, monospace; }
        ins { color: var(--primary); text-decoration: none; }
        .status-open { color: #ffcc00; }
        .status-claimed { color: #00d4ff; }
        .status-fulfilled { color: var(--primary); }
    </style>
</head>
<body class="container">
    <nav>
        <ul><li><strong><ins>DSECURITY // INTENT-BUS_V7</ins></strong></li></ul>
        <ul><li><a href="?auth={{ auth_key }}" class="secondary">Refresh</a></li></ul>
    </nav>
    <main>
        <div class="grid">
            <article><h5>Open</h5><h2 class="status-open">{{ stats.open }}</h2></article>
            <article><h5>Claimed</h5><h2 class="status-claimed">{{ stats.claimed }}</h2></article>
            <article><h5>Fulfilled</h5><h2 class="status-fulfilled">{{ stats.fulfilled }}</h2></article>
        </div>

        <article>
            <header><strong>Active Tester Keys</strong></header>
            <table role="grid">
                <thead><tr><th>Owner</th><th>Usage</th><th>Created</th></tr></thead>
                <tbody>
                    {% for k in keys %}
                    <tr><td>{{ k.owner }}</td><td>{{ k.total_requests }} reqs</td><td>{{ k.created_at|int }}</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </article>

        <article>
            <header><strong>Live Intent Queue (Last 10)</strong></header>
            <table role="grid">
                <thead><tr><th>ID</th><th>Goal</th><th>Status</th></tr></thead>
                <tbody>
                    {% for i in intents %}
                    <tr>
                        <td><code>{{ i.id }}</code></td>
                        <td>{{ i.goal }}</td>
                        <td class="status-{{ i.status }}">{{ i.status }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </article>
    </main>
</body>
</html>
"""

# --- HELPERS ---
def now():
    return time.time()

def safe_int(value, default, min_val=None, max_val=None):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    if min_val is not None:
        v = max(min_val, v)
    if max_val is not None:
        v = min(max_val, v)
    return v

def get_real_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.remote_addr or "unknown"

def is_local():
    return request.remote_addr in ("127.0.0.1", "::1")

def is_json_safe(obj, max_depth=10, depth=0):
    if depth > max_depth:
        return False
    if isinstance(obj, dict):
        return all(is_json_safe(v, max_depth, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        return all(is_json_safe(v, max_depth, depth + 1) for v in obj)
    return True

# --- DB ---
def get_db():
    if "db" not in g:
        db = sqlite3.connect(DB_PATH, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL;")
        db.execute("PRAGMA busy_timeout=30000;")

        db.execute("""CREATE TABLE IF NOT EXISTS store (
            key TEXT PRIMARY KEY,
            value TEXT,
            expires_at REAL
        )""")

        db.execute("""CREATE TABLE IF NOT EXISTS intents (
            id TEXT PRIMARY KEY,
            goal TEXT,
            payload TEXT,
            status TEXT,
            reward INTEGER,
            created_at REAL,
            expires_at REAL,
            claimed_at REAL,
            claimed_by TEXT
        )""")

        db.execute("""CREATE TABLE IF NOT EXISTS tester_keys (
            api_key TEXT PRIMARY KEY,
            owner TEXT,
            total_requests INTEGER DEFAULT 0,
            created_at REAL
        )""")

        db.execute("""CREATE TABLE IF NOT EXISTS rate_limits (
            identifier TEXT PRIMARY KEY,
            count INTEGER,
            window REAL
        )""")

        db.execute("CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_intents_goal ON intents(goal)")

        db.commit()
        g.db = db
    return g.db

@app.teardown_appcontext
def close_db(e):
    db = g.pop("db", None)
    if db:
        db.close()

# --- CLEANUP ---
last_cleanup = 0

def cleanup():
    global last_cleanup
    if now() - last_cleanup < 60:
        return
    last_cleanup = now()

    try:
        db = get_db()
        db.execute("DELETE FROM store WHERE expires_at < ?", (now(),))
        db.execute("DELETE FROM intents WHERE expires_at < ?", (now(),))
        db.execute("DELETE FROM rate_limits WHERE window < ?", (now() - 3600,))
        db.commit()
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            return
        raise

# --- AUTH ---
def get_role(key):
    if not key:
        return None
    if key == API_KEY:
        return "admin"
    row = get_db().execute(
        "SELECT 1 FROM tester_keys WHERE api_key=?", (key,)
    ).fetchone()
    return "tester" if row else None

def rate_limited(key):
    db = get_db()
    identifier = f"{key}:{get_real_ip()}"
    t = now()

    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT count, window FROM rate_limits WHERE identifier=?",
            (identifier,)
        ).fetchone()

        if not row or t - row["window"] > RATE_LIMIT_WINDOW:
            db.execute(
                "REPLACE INTO rate_limits VALUES (?,1,?)",
                (identifier, t)
            )
            db.commit()
            return False

        if row["count"] >= RATE_LIMIT_MAX:
            db.rollback()
            return True

        db.execute(
            "UPDATE rate_limits SET count=count+1 WHERE identifier=?",
            (identifier,)
        )
        db.commit()
        return False
    except sqlite3.OperationalError:
        try:
            db.rollback()
        except Exception:
            pass
        return False

# --- MIDDLEWARE ---
@app.before_request
def security():
    cleanup()

    if MAINTENANCE_MODE:
        return jsonify({"error": "maintenance"}), 503

    if request.path in ("/", "/admin/dashboard"):
        return

    key = request.headers.get("X-API-KEY")
    if not key:
        return jsonify({"error": "Unauthorized"}), 401

    role = get_role(key)
    if not role:
        return jsonify({"error": "Unauthorized"}), 401

    g.role = role
    g.api_key = key

    if role == "tester":
        if rate_limited(key):
            return jsonify({"error": "Rate limited"}), 429
        get_db().execute(
            "UPDATE tester_keys SET total_requests=total_requests+1 WHERE api_key=?",
            (key,)
        )
        get_db().commit()

    logging.info(f"{get_real_ip()} -> {request.method} {request.path}")

@app.after_request
def headers(r: Response):
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r

# --- ROUTES ---
@app.route("/")
def index():
    return "Intent Bus v7.0", 200

# --- DASHBOARD ---
@app.route("/admin/dashboard")
def admin_dashboard():
    auth = request.args.get("auth")
    if not DASHBOARD_PASSWORD or auth != DASHBOARD_PASSWORD:
        return "Unauthorized Access Denied.", 401

    db = get_db()
    stats = db.execute("""
        SELECT
            (SELECT COUNT(*) FROM intents WHERE status='open') as open,
            (SELECT COUNT(*) FROM intents WHERE status='claimed') as claimed,
            (SELECT COUNT(*) FROM intents WHERE status='fulfilled') as fulfilled
    """).fetchone()

    keys = db.execute(
        "SELECT owner, total_requests, created_at FROM tester_keys ORDER BY total_requests DESC"
    ).fetchall()

    intents = db.execute(
        "SELECT id, goal, status FROM intents ORDER BY created_at DESC LIMIT 10"
    ).fetchall()

    return render_template_string(
        ADMIN_HTML,
        stats=stats,
        keys=keys,
        intents=intents,
        auth_key=auth
    )

# --- ADMIN ---
@app.route("/admin/generate_key", methods=["POST"])
def gen_key():
    if g.role != "admin":
        return jsonify({"error": "forbidden"}), 403

    owner = (request.get_json(silent=True) or {}).get("owner", "anon")
    key = "test_" + secrets.token_hex(16)

    db = get_db()
    db.execute(
        "INSERT INTO tester_keys VALUES (?,?,0,?)",
        (key, owner, now())
    )
    db.commit()

    return jsonify({"api_key": key, "owner": owner})

@app.route("/admin/purge", methods=["POST"])
def purge():
    if g.role != "admin":
        return jsonify({"error": "forbidden"}), 403

    db = get_db()
    db.execute("DELETE FROM intents")
    db.commit()

    return jsonify({"ok": True})

# --- STORE ---
@app.route("/set/<key>", methods=["POST"])
def set_val(key):
    data = request.get_json(silent=True) or {}
    val = data.get("value")

    if val is None:
        return jsonify({"error": "no value"}), 400

    ttl = safe_int(data.get("ttl"), 600, 1, MAX_TTL)

    db = get_db()
    db.execute(
        "REPLACE INTO store VALUES (?,?,?)",
        (key, str(val), now() + ttl)
    )
    db.commit()

    return jsonify({"ok": True})

@app.route("/get/<key>")
def get_val(key):
    row = get_db().execute(
        "SELECT * FROM store WHERE key=?", (key,)
    ).fetchone()

    if not row or now() > row["expires_at"]:
        return jsonify({"error": "not found"}), 404

    return jsonify({"value": row["value"]})

# --- INTENTS ---
@app.route("/intent", methods=["POST"])
def create_intent():
    data = request.get_json(silent=True)
    if not data or "goal" not in data or "payload" not in data:
        return jsonify({"error": "missing goal or payload"}), 400

    if not isinstance(data["goal"], str) or not data["goal"].strip():
        return jsonify({"error": "goal must be a non-empty string"}), 400

    if not is_json_safe(data["payload"]):
        return jsonify({"error": "payload too deeply nested"}), 400

    payload = json.dumps(data["payload"])
    if len(payload.encode()) > MAX_PAYLOAD:
        return jsonify({"error": "payload too large"}), 413

    db = get_db()

    row = db.execute("""
        SELECT COUNT(*) as c FROM intents
        WHERE status='open' AND claimed_by=?
    """, (g.api_key,)).fetchone()

    if row["c"] >= MAX_OPEN_INTENTS_PER_KEY:
        return jsonify({"error": "too many open intents"}), 429

    reward = safe_int(data.get("reward"), 0, 0)
    iid = str(uuid.uuid4())[:8]

    db.execute("""
        INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        iid,
        data["goal"].strip(),
        payload,
        "open",
        reward,
        now(),
        now() + MAX_TTL,
        0.0,
        g.api_key
    ))
    db.commit()

    return jsonify({"id": iid, "status": "published"}), 201

@app.route("/claim", methods=["POST"])
def claim():
    db = get_db()
    t = now()
    stale = t - CLAIM_TIMEOUT
    target_goal = request.args.get("goal")

    try:
        db.execute("BEGIN IMMEDIATE")

        query = """
            SELECT * FROM intents
            WHERE expires_at > ?
            AND (
                status='open'
                OR (status='claimed' AND claimed_at < ?)
            )
        """
        params = [t, stale]

        if target_goal:
            query += " AND goal=?"
            params.append(target_goal)

        query += " ORDER BY rowid ASC LIMIT 1"

        row = db.execute(query, params).fetchone()

        if not row:
            db.rollback()
            return "", 204

        updated = db.execute("""
            UPDATE intents
            SET status='claimed', claimed_at=?, claimed_by=?
            WHERE id=?
            AND (status='open' OR (status='claimed' AND claimed_at < ?))
        """, (t, g.api_key, row["id"], stale))

        if updated.rowcount == 0:
            db.rollback()
            return "", 204

        db.commit()

        return jsonify({
            "id": row["id"],
            "goal": row["goal"],
            "payload": json.loads(row["payload"])
        }), 200

    except sqlite3.OperationalError:
        try:
            db.rollback()
        except Exception:
            pass
        return "", 204

@app.route("/fulfill/<iid>", methods=["POST"])
def fulfill(iid):
    db = get_db()

    cur = db.execute("""
        UPDATE intents
        SET status='fulfilled'
        WHERE id=? AND status='claimed' AND claimed_by=?
    """, (iid, g.api_key))

    db.commit()

    if cur.rowcount == 0:
        return jsonify({"error": "not found or not allowed"}), 404

    return jsonify({"ok": True, "id": iid}), 200
