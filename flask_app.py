import os
import sqlite3
import time
import json
import secrets
import logging
import hashlib
import hmac
from urllib.parse import urlencode, parse_qsl

from flask import Flask, request, jsonify, g, Response, render_template_string
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024

if sqlite3.sqlite_version_info < (3, 35, 0):
    raise RuntimeError("SQLite 3.35.0+ is required for the /claim RETURNING clause.")

# --- CONFIG ---
API_KEY = os.environ["BUS_SECRET"]
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
DB_PATH = os.environ.get(
    "BUS_DB_PATH",
    os.path.join(os.path.dirname(__file__), "infrastructure.db"),
)
MAINTENANCE_MODE = os.environ.get("BUS_MAINTENANCE_MODE", "False").lower() == "true"

RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 60
CLAIM_TIMEOUT = 60
MAX_CLAIM_ATTEMPTS = 3
NONCE_WINDOW_SECONDS = 300
NONCE_RETENTION_SECONDS = NONCE_WINDOW_SECONDS * 2
FAILED_RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_PAYLOAD = 5 * 1024
MAX_TTL = 86400
MAX_OPEN_INTENTS_PER_KEY = 100

# --- LAZY CLEANUP CONFIG ---
CLEANUP_INTERVAL_SECONDS = 600  # 10 minutes
last_cleanup_time = time.time()

logging.basicConfig(level=logging.INFO)


def api_error(code, message, status_code=400):
    return jsonify({"error": {"code": code, "message": message}}), status_code


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
        .status-failed { color: #ff5555; }
    </style>
</head>
<body class="container">
    <nav>
        <ul><li><strong><ins>DSECURITY // INTENT-BUS_V7.0.1</ins></strong></li></ul>
        <ul><li><a href="/admin/dashboard" class="secondary">Refresh</a></li></ul>
    </nav>
    <main>
        <div class="grid">
            <article><h5>Open</h5><h2 class="status-open">{{ stats.open }}</h2></article>
            <article><h5>Claimed</h5><h2 class="status-claimed">{{ stats.claimed }}</h2></article>
            <article><h5>Fulfilled</h5><h2 class="status-fulfilled">{{ stats.fulfilled }}</h2></article>
            <article><h5>Failed</h5><h2 class="status-failed">{{ stats.failed }}</h2></article>
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
                <thead><tr><th>ID</th><th>Goal</th><th>Status</th><th>Attempts</th><th>Error</th></tr></thead>
                <tbody>
                    {% for i in intents %}
                    <tr>
                        <td><code>{{ i.id }}</code></td>
                        <td>{{ i.goal }}</td>
                        <td class="status-{{ i.status }}">{{ i.status }}</td>
                        <td>{{ i.claim_attempts }}</td>
                        <td>{{ i.last_error or '' }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </article>
    </main>
</body>
</html>
"""


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


def dashboard_unauthorized():
    resp = Response("Unauthorized Access Denied.", 401)
    resp.headers["WWW-Authenticate"] = 'Basic realm="Intent Bus Dashboard"'
    return resp


def dashboard_auth_ok():
    if not DASHBOARD_PASSWORD:
        return False
    auth = request.authorization
    if not auth or not isinstance(auth.password, str) or not isinstance(auth.username, str):
        return False
    if auth.username != "admin":
        return False
    return hmac.compare_digest(auth.password, DASHBOARD_PASSWORD)


def ensure_columns(db, table, columns):
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    for col_def in columns:
        col_name = col_def.split()[0]
        if col_name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")


def is_busy_or_locked(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def setup_schema(db):
    """Run DDL statements only once during app startup."""
    db.execute("""CREATE TABLE IF NOT EXISTS store (
        key TEXT PRIMARY KEY, value TEXT, expires_at REAL)""")

    db.execute("""CREATE TABLE IF NOT EXISTS intents (
        id TEXT PRIMARY KEY, goal TEXT, payload TEXT, status TEXT,
        reward INTEGER, created_at REAL, expires_at REAL, claimed_at REAL,
        claimed_by TEXT, publisher TEXT, claim_attempts INTEGER DEFAULT 0,
        last_error TEXT, failed_at REAL, visibility TEXT DEFAULT 'private')""")

    db.execute("""CREATE TABLE IF NOT EXISTS tester_keys (
        api_key TEXT PRIMARY KEY, owner TEXT,
        total_requests INTEGER DEFAULT 0, created_at REAL)""")

    db.execute("""CREATE TABLE IF NOT EXISTS rate_limits (
        identifier TEXT PRIMARY KEY, count INTEGER, window REAL)""")

    db.execute("""CREATE TABLE IF NOT EXISTS idempotency_keys (
        api_key TEXT, key TEXT, body_hash TEXT, response TEXT,
        status_code INTEGER, created_at REAL, PRIMARY KEY (api_key, key))""")

    db.execute("""CREATE TABLE IF NOT EXISTS request_nonces (
        api_key TEXT, nonce TEXT, created_at REAL, PRIMARY KEY (api_key, nonce))""")

    ensure_columns(db, "intents", [
        "publisher TEXT",
        "claim_attempts INTEGER DEFAULT 0",
        "last_error TEXT",
        "failed_at REAL",
        "visibility TEXT DEFAULT 'private'",
    ])

    db.execute("CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_intents_goal ON intents(goal)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_intents_publisher ON intents(publisher)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_intents_visibility ON intents(visibility)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_intents_expires ON intents(expires_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_intents_claimed_at ON intents(claimed_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_intents_failed_at ON intents(failed_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_intents_claim_priority ON intents(claim_attempts, created_at, id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_store_expires ON store(expires_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_idempotency_created ON idempotency_keys(created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_request_nonces_created ON request_nonces(created_at)")


def get_db():
    if "db" not in g:
        db = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL;")
        db.execute("PRAGMA busy_timeout=30000;")
        db.execute("PRAGMA synchronous=NORMAL;")
        g.db = db
    return g.db


@app.teardown_appcontext
def close_db(e):
    db = g.pop("db", None)
    if db:
        db.close()


def run_sync_cleanup():
    """Runs garbage collection synchronously on the active thread."""
    db = get_db()
    cutoff = now()
    
    try:
        db.execute("BEGIN IMMEDIATE")

        db.execute("DELETE FROM store WHERE expires_at < ?", (cutoff,))
        db.execute("DELETE FROM intents WHERE expires_at < ?", (cutoff,))
        db.execute("DELETE FROM rate_limits WHERE window < ?", (cutoff - 3600,))
        db.execute("DELETE FROM idempotency_keys WHERE created_at < ?", (cutoff - 3600,))
        db.execute("DELETE FROM request_nonces WHERE created_at < ?", (cutoff - NONCE_RETENTION_SECONDS,))

        db.execute("""
            UPDATE intents
            SET status='failed',
                failed_at=COALESCE(failed_at, ?),
                last_error=COALESCE(last_error, 'retry limit exceeded')
            WHERE status='claimed'
              AND claimed_at < ?
              AND claim_attempts >= ?
        """, (cutoff, cutoff - CLAIM_TIMEOUT, MAX_CLAIM_ATTEMPTS))

        db.execute(
            "DELETE FROM intents WHERE status='failed' AND failed_at < ?",
            (cutoff - FAILED_RETENTION_SECONDS,),
        )

        db.commit()
    except sqlite3.OperationalError as e:
        try:
            db.rollback()
        except Exception:
            pass
        if not is_busy_or_locked(e):
            logging.error(f"[!] Lazy Cleanup DB Error: {e}")


def init_db():
    with app.app_context():
        db = get_db()
        setup_schema(db)


if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    init_db()


def get_role(key):
    if not key:
        return None
    if key == API_KEY:
        return "admin"
    row = get_db().execute("SELECT 1 FROM tester_keys WHERE api_key=?", (key,)).fetchone()
    return "tester" if row else None


def verify_signed_request(api_key):
    sig = request.headers.get("X-Signature")
    ts = request.headers.get("X-Timestamp")
    nonce = request.headers.get("X-Nonce")

    if not sig or not ts or not nonce:
        return False, "Missing signature headers"

    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        return False, "Invalid timestamp"

    if abs(now() - ts_int) > NONCE_WINDOW_SECONDS:
        return False, "Stale timestamp"

    raw_body = request.get_data(cache=True, as_text=False) or b""

    parsed = parse_qsl(request.query_string.decode("utf-8"), keep_blank_values=True)
    canonical_query = urlencode(sorted(parsed), doseq=True)
    canonical_path = request.path
    if canonical_query:
        canonical_path += "?" + canonical_query

    msg = b"\n".join([
        request.method.upper().encode("utf-8"),
        canonical_path.encode("utf-8"),
        ts.encode("utf-8"),
        nonce.encode("utf-8"),
        raw_body,
    ])

    expected = hmac.new(api_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(sig, expected):
        return False, "Bad signature"

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO request_nonces VALUES (?,?,?)", (api_key, nonce, now()))
        db.commit()
        return True, None
    except sqlite3.IntegrityError:
        try:
            db.rollback()
        except Exception:
            pass
        return False, "Replay detected"
    except sqlite3.OperationalError:
        try:
            db.rollback()
        except Exception:
            pass
        return False, "Database busy"


def rate_limited(key):
    db = get_db()
    identifier = key  # tenant-scoped; IP rotation should not bypass quotas
    t = now()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT count, window FROM rate_limits WHERE identifier=?",
            (identifier,),
        ).fetchone()

        if not row or t - row["window"] > RATE_LIMIT_WINDOW:
            db.execute("REPLACE INTO rate_limits VALUES (?,1,?)", (identifier, t))
            db.commit()
            return False

        if row["count"] >= RATE_LIMIT_MAX:
            db.rollback()
            return True

        db.execute("UPDATE rate_limits SET count=count+1 WHERE identifier=?", (identifier,))
        db.commit()
        return False
    except sqlite3.OperationalError:
        try:
            db.rollback()
        except Exception:
            pass
        logging.warning(f"[!] Rate limit check failed closed due to DB lock for {identifier}")
        return True


@app.before_request
def security():
    # 1. LAZY CLEANUP TRIGGER
    global last_cleanup_time
    current_time = now()
    if current_time - last_cleanup_time > CLEANUP_INTERVAL_SECONDS:
        last_cleanup_time = current_time
        run_sync_cleanup()

    # 2. SECURITY CHECKS
    if MAINTENANCE_MODE:
        return api_error("maintenance", "The bus is currently undergoing maintenance.", 503)

    if not is_local() and not request.is_secure:
        return api_error("https_required", "HTTPS is required.", 403)

    if request.path in ("/", "/admin/dashboard"):
        return

    key = request.headers.get("X-API-KEY")
    if not key:
        return api_error("unauthorized", "Missing X-API-KEY header.", 401)

    role = get_role(key)
    if not role:
        return api_error("unauthorized", "Invalid API key.", 401)

    g.role = role
    g.api_key = key

    if request.headers.get("X-Signature"):
        ok, err = verify_signed_request(key)
        if not ok:
            return api_error("invalid_signature", err, 403)
    else:
        logging.info(f"Standard Auth request from {get_real_ip()} via {request.user_agent}")

    if role == "tester":
        if rate_limited(key):
            return api_error("rate_limited", "Too many requests. Back off.", 429)
        get_db().execute(
            "UPDATE tester_keys SET total_requests=total_requests+1 WHERE api_key=?",
            (key,),
        )
        get_db().commit()


@app.after_request
def headers(r: Response):
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Intent-Version"] = "1.0"
    r.headers["Cache-Control"] = "no-store"
    r.headers["Referrer-Policy"] = "no-referrer"
    return r


@app.route("/")
def index():
    return "Intent Bus v7.0.1", 200


@app.route("/admin/dashboard")
def admin_dashboard():
    if not dashboard_auth_ok():
        return dashboard_unauthorized()

    db = get_db()
    stats = db.execute("""
        SELECT
            (SELECT COUNT(*) FROM intents WHERE status='open') as open,
            (SELECT COUNT(*) FROM intents WHERE status='claimed') as claimed,
            (SELECT COUNT(*) FROM intents WHERE status='fulfilled') as fulfilled,
            (SELECT COUNT(*) FROM intents WHERE status='failed') as failed
    """).fetchone()

    keys = db.execute(
        "SELECT owner, total_requests, created_at FROM tester_keys ORDER BY total_requests DESC"
    ).fetchall()

    intents = db.execute(
        "SELECT id, goal, status, claim_attempts, last_error FROM intents ORDER BY created_at DESC LIMIT 10"
    ).fetchall()

    return render_template_string(ADMIN_HTML, stats=stats, keys=keys, intents=intents)


@app.route("/admin/generate_key", methods=["POST"])
def gen_key():
    if g.role != "admin":
        return api_error("forbidden", "Admin access required.", 403)

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return api_error("invalid_request", "Request body must be a JSON object.")

    owner = data.get("owner", "anon")
    key = "test_" + secrets.token_hex(16)

    db = get_db()
    db.execute("INSERT INTO tester_keys VALUES (?,?,0,?)", (key, owner, now()))
    db.commit()
    return jsonify({"api_key": key, "owner": owner})


@app.route("/admin/purge", methods=["POST"])
def purge():
    if g.role != "admin":
        return api_error("forbidden", "Admin access required.", 403)

    db = get_db()
    db.execute("DELETE FROM intents")
    db.commit()
    return jsonify({"ok": True})


@app.route("/set/<key>", methods=["POST"])
def set_val(key):
    data = request.get_json(silent=True)
    if data is None:
        return api_error("invalid_json", "Malformed JSON body.", 400)
    if not isinstance(data, dict):
        return api_error("invalid_request", "Request body must be a JSON object.")

    val = data.get("value")
    if val is None:
        return api_error("invalid_request", "Missing 'value' field.")

    scoped_key = f"{g.api_key}:{key}"
    db = get_db()
    db.execute(
        "REPLACE INTO store VALUES (?,?,?)",
        (scoped_key, str(val), now() + safe_int(data.get("ttl"), 600, 1, MAX_TTL)),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/get/<key>")
def get_val(key):
    row = get_db().execute(
        "SELECT * FROM store WHERE key=? AND expires_at > ?",
        (f"{g.api_key}:{key}", now()),
    ).fetchone()

    if not row:
        return api_error("not_found", "Key not found or expired.", 404)

    return jsonify({"value": row["value"]})


@app.route("/intent", methods=["POST"])
def create_intent():
    idem_key = request.headers.get("Idempotency-Key")
    db = get_db()
    data = request.get_json(silent=True)

    if data is None:
        return api_error("invalid_json", "Malformed JSON body.", 400)
    if not isinstance(data, dict):
        return api_error("invalid_request", "Request body must be a JSON object.")

    body_hash = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    try:
        db.execute("BEGIN IMMEDIATE")

        if idem_key:
            row = db.execute(
                "SELECT response, status_code, body_hash FROM idempotency_keys WHERE api_key=? AND key=?",
                (g.api_key, idem_key),
            ).fetchone()
            if row:
                if row["body_hash"] != body_hash:
                    db.rollback()
                    return api_error("conflict", "Idempotency-Key used with different request body.", 409)
                db.rollback()
                return Response(row["response"], status=row["status_code"], mimetype="application/json")

        if "goal" not in data or "payload" not in data:
            db.rollback()
            return api_error("invalid_request", "Missing 'goal' or 'payload'.")
        if not isinstance(data["goal"], str) or not data["goal"].strip():
            db.rollback()
            return api_error("invalid_request", "Goal must be a non-empty string.")
        if not isinstance(data["payload"], dict):
            db.rollback()
            return api_error("invalid_payload", "Payload must be a JSON object/dictionary.")
        if not is_json_safe(data["payload"]):
            db.rollback()
            return api_error("payload_too_deep", "Payload is too deeply nested.")

        payload = json.dumps(data["payload"], separators=(",", ":"))
        if len(payload.encode("utf-8")) > MAX_PAYLOAD:
            db.rollback()
            return api_error("payload_too_large", "Payload exceeds size limits.", 413)

        if db.execute(
            "SELECT COUNT(*) FROM intents WHERE status='open' AND publisher=?",
            (g.api_key,),
        ).fetchone()[0] >= MAX_OPEN_INTENTS_PER_KEY:
            db.rollback()
            return api_error("rate_limited", "Too many open intents for this key.", 429)

        iid = secrets.token_hex(16)
        response_data = {"id": iid, "status": "published"}

        visibility = "public" if data.get("visibility") == "public" else "private"

        db.execute("""
            INSERT INTO intents
            (id, goal, payload, status, reward, created_at, expires_at,
             claimed_at, claimed_by, publisher, claim_attempts, visibility)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            iid,
            data["goal"].strip(),
            payload,
            "open",
            safe_int(data.get("reward"), 0, 0),
            now(),
            now() + MAX_TTL,
            0.0,
            None,
            g.api_key,
            0,
            visibility,
        ))

        if idem_key:
            db.execute(
                "INSERT INTO idempotency_keys VALUES (?,?,?,?,?,?)",
                (g.api_key, idem_key, body_hash, json.dumps(response_data), 201, now()),
            )

        db.commit()
        return jsonify(response_data), 201

    except sqlite3.IntegrityError:
        db.rollback()
        return api_error("conflict", "Concurrent request conflict detected.", 409)
    except sqlite3.OperationalError as e:
        try:
            db.rollback()
        except Exception:
            pass
        if is_busy_or_locked(e):
            return api_error("database_busy", "Database is busy, please retry.", 503)
        logging.error(f"Intent creation OperationalError: {e}")
        return api_error("database_error", "Internal database error during intent creation.", 500)
    except Exception as e:
        db.rollback()
        logging.error(f"Intent creation error: {e}")
        return api_error("internal_error", "An internal error occurred.", 500)


@app.route("/claim", methods=["POST"])
def claim():
    db = get_db()
    t = now()
    stale = t - CLAIM_TIMEOUT
    target_goal = request.args.get("goal")
    target_publisher = request.args.get("publisher")

    if target_publisher and target_publisher != g.api_key and g.role != "admin":
        return api_error("forbidden", "publisher filtering is restricted.", 403)

    where_parts = []
    params = {
        "now": t,
        "stale": stale,
        "max_attempts": MAX_CLAIM_ATTEMPTS,
        "claimer": g.api_key,
    }

    if target_goal:
        where_parts.append("goal=:target_goal")
        params["target_goal"] = target_goal

    if target_publisher:
        where_parts.append("publisher=:target_publisher")
        params["target_publisher"] = target_publisher
    else:
        where_parts.append("(publisher=:worker_key OR visibility='public')")
        params["worker_key"] = g.api_key

    routing_sql = " AND ".join(where_parts)

    query = f"""
        WITH candidate AS (
            SELECT id
            FROM intents
            WHERE expires_at > :now
              AND status != 'failed'
              AND claim_attempts < :max_attempts
              AND (status='open' OR (status='claimed' AND claimed_at < :stale))
              AND {routing_sql}
            ORDER BY claim_attempts ASC, created_at ASC, id ASC
            LIMIT 1
        )
        UPDATE intents
        SET status='claimed',
            claimed_at=:now,
            claimed_by=:claimer,
            claim_attempts=claim_attempts+1
        WHERE id = (SELECT id FROM candidate)
          AND claim_attempts < :max_attempts
          AND (status='open' OR (status='claimed' AND claimed_at < :stale))
        RETURNING id, goal, payload, claim_attempts
    """

    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(query, params).fetchone()

        if not row:
            db.rollback()
            return "", 204

        db.commit()
        return jsonify({
            "id": row["id"],
            "goal": row["goal"],
            "payload": json.loads(row["payload"]),
            "claim_attempts": row["claim_attempts"],
        }), 200

    except sqlite3.OperationalError as e:
        try:
            db.rollback()
        except Exception:
            pass

        if is_busy_or_locked(e):
            return "", 204

        logging.error(f"[!] Database OperationalError in /claim: {e}")
        return api_error("database_error", "Internal database error during claim.", 500)
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logging.error(f"[!] Unexpected error in /claim: {e}")
        return api_error("internal_error", "An internal error occurred during claim.", 500)


@app.route("/fail/<iid>", methods=["POST"])
def fail(iid):
    db = get_db()
    data = request.get_json(silent=True) or {}
    cur = db.execute("""
        UPDATE intents SET status='failed', last_error=?, failed_at=?
        WHERE id=? AND status='claimed' AND claimed_by=?
    """, (str(data.get("error", "unknown")).strip()[:500], now(), iid, g.api_key))
    db.commit()

    if cur.rowcount == 0:
        return api_error("not_found", "Intent not found or not owned by claimer.", 404)

    return jsonify({"ok": True, "id": iid, "status": "failed"}), 200


@app.route("/fulfill/<iid>", methods=["POST"])
def fulfill(iid):
    db = get_db()
    cur = db.execute(
        "UPDATE intents SET status='fulfilled' WHERE id=? AND status='claimed' AND claimed_by=?",
        (iid, g.api_key),
    )
    db.commit()

    if cur.rowcount == 0:
        return api_error("not_found", "Intent not found or not owned by claimer.", 404)

    return jsonify({"ok": True, "id": iid}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
