import os
import sqlite3
import time
import json
import uuid
import secrets
import logging
from flask import Flask, request, jsonify, g, Response

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024  # 8KB max request size

# --- CONFIG ---
API_KEY = os.environ["BUS_SECRET"]
DB_PATH = os.environ.get("BUS_DB_PATH", os.path.join(os.path.dirname(__file__), "infrastructure.db"))
MAINTENANCE_MODE = os.environ.get("BUS_MAINTENANCE_MODE", "False").lower() == "true"

RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 60
CLAIM_TIMEOUT = 60
MAX_PAYLOAD = 5 * 1024
MAX_TTL = 86400
MAX_OPEN_INTENTS_PER_KEY = 100

logging.basicConfig(level=logging.INFO)

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

    if request.path == "/":
        return

    if not is_local():
        if request.headers.get("X-Forwarded-Proto", "http") != "https":
            return jsonify({"error": "HTTPS required"}), 403

    key = request.headers.get("X-API-KEY")
    if not key:
        return jsonify({"error": "Unauthorized"}), 401

    role = get_role(key)
    if not role:
        return jsonify({"error": "Unauthorized"}), 401

    g.role = role
    g.api_key = key

    if role == "tester" and rate_limited(key):
        return jsonify({"error": "Rate limited"}), 429

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
