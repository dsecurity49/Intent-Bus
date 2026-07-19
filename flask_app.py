import os
import sqlite3
import time
import json
import secrets
import logging
import hashlib
import hmac
import random
import re
import threading
import uuid
from urllib.parse import urlencode, parse_qsl, quote

from flask import Flask, request, jsonify, g, Response, render_template_string, has_request_context

try:
    from werkzeug.middleware.proxy_fix import ProxyFix
except ImportError:
    ProxyFix = None

# =========================================================
# APP CONFIGURATION
# =========================================================

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024  # Limit request body size to prevent DoS attacks via large uploads.

# --- Structured JSON Logging Setup ---
# Whitelists extra fields to prevent sensitive or unexpected data from being logged.


class JSONFormatter(logging.Formatter):
    ALLOWED_EXTRA = {
        "status_code", "duration_ms", "intent_id", "worker_id", "remote_addr",
        "expired_open_deleted", "expired_claims_requeued", "expired_claims_dead",
        "fulfilled_deleted", "dead_deleted", "dead_letters_deleted", "store_deleted",
        "rate_limits_deleted", "idempotency_deleted", "nonces_deleted"
    }

    def format(self, record):
        try:
            log_record = {
                "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(record.created)),
                "level": record.levelname,
                "message": record.getMessage(),
                "module": record.module
            }

            if has_request_context():
                log_record["endpoint"] = request.path
                log_record["method"] = request.method
                log_record["request_id"] = getattr(g, "request_id", "unknown")
                log_record["remote_addr"] = get_real_ip()

            if record.exc_info:
                log_record["exception"] = self.formatException(record.exc_info)

            for key in self.ALLOWED_EXTRA:
                if key in record.__dict__:
                    log_record[key] = record.__dict__[key]

            return json.dumps(log_record, default=str)
        except Exception as e:
            return json.dumps({
                "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                "level": "ERROR",
                "message": "Logging failure",
                "logger_error": str(e)
            })


app.logger.setLevel(logging.INFO)
app.logger.handlers.clear()

json_handler = logging.StreamHandler()
json_handler.setFormatter(JSONFormatter())
app.logger.addHandler(json_handler)
app.logger.propagate = False
# -------------------------------------

TRUST_PROXY = os.environ.get("BUS_TRUST_PROXY", "false").lower() == "true"
if TRUST_PROXY and ProxyFix is not None:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.logger.info("ProxyFix enabled. Trusting upstream proxy headers.")

if sqlite3.sqlite_version_info < (3, 35, 0):
    raise RuntimeError("SQLite 3.35.0+ required for RETURNING clauses.")  # Required for atomic claiming logic.

API_KEY = os.environ.get("BUS_SECRET", "dev_secret")
if API_KEY == "dev_secret":
    # Prevent accidental use of default, insecure API key in production.
    raise RuntimeError(
        "CRITICAL: Refusing to start. Running with default BUS_SECRET in production is unsafe.")

ADMIN_SECRET = os.environ.get("BUS_ADMIN_SECRET", "")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
DB_PATH = os.environ.get("BUS_DB_PATH", os.path.join(
    os.path.dirname(__file__), "infrastructure.db"))
MAINTENANCE_MODE = os.environ.get(
    "BUS_MAINTENANCE_MODE", "false").lower() == "true"
METRICS_TOKEN = os.environ.get("BUS_METRICS_TOKEN", "")

REQUIRE_SIGNATURES = os.environ.get(
    "BUS_REQUIRE_SIGNATURES", "false").lower() == "true"
ENFORCE_HTTPS = os.environ.get("BUS_ENFORCE_HTTPS", "false").lower() == "true"

try:
    CLEANUP_INTERVAL_SECONDS = max(
        300,
        min(86400, int(os.environ.get("BUS_CLEANUP_INTERVAL_SECONDS", "21600")))
    )
except ValueError:
    CLEANUP_INTERVAL_SECONDS = 21600

# =========================================================
# SYSTEM LIMITS & CONSTANTS
# =========================================================

RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 60

DEFAULT_CLAIM_TIMEOUT = 60
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE = 5.0
DEFAULT_PRIORITY = 100
MAX_PRIORITY = 1000

NONCE_WINDOW_SECONDS = 300  # Valid window for a nonce to prevent replay attacks.
NONCE_RETENTION_SECONDS = NONCE_WINDOW_SECONDS * 2  # Retention period for nonce tracking.

FULFILLED_RETENTION_SECONDS = 7 * 24 * 60 * 60  # Retention for completed intents.
DEAD_RETENTION_SECONDS = 7 * 24 * 60 * 60  # Retention for dead intents.

MAX_PAYLOAD = 7 * 1024  # Max size for 'payload' and 'result' fields.
MAX_TTL = 86400
MAX_OPEN_INTENTS_PER_KEY = 2000

CLEANUP_ERROR_COOLDOWN_SECONDS = 60

last_cleanup_time = time.time()
last_cleanup_error_time = 0
cleanup_lock = threading.Lock()

# =========================================================
# HELPERS
# =========================================================


def now():
    return time.time()


def api_error(code, message, status_code=400):
    return jsonify({"error": {"code": code, "message": message}}), status_code


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


def safe_float(value, default, min_val=None, max_val=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if min_val is not None:
        v = max(min_val, v)
    if max_val is not None:
        v = min(max_val, v)
    return v


def get_real_ip():
    ip = request.remote_addr or "unknown"
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    return ip


def is_local():
    ip = get_real_ip()
    return ip in ("127.0.0.1", "::1", "localhost")


def is_busy_or_locked(exc):
    # Use sqlite3 error codes for reliable, locale-independent detection.
    if hasattr(exc, 'sqlite_errorcode'):
        return exc.sqlite_errorcode in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    # Fallback for non-sqlite3 exceptions.
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def is_json_safe(obj, max_depth=10, depth=0):
    if depth > max_depth:
        return False
    if isinstance(obj, dict):
        return all(is_json_safe(v, max_depth, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        return all(is_json_safe(v, max_depth, depth + 1) for v in obj)
    return True
# Prevent deeply nested JSON payloads to mitigate recursion-based DoS.


def valid_namespace(ns: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9_.-]{1,64}$", ns))  # Validate namespace format to prevent injection.


def valid_label(value: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9_.:-]{1,64}$", value))  # Validate label format for safe query usage.


def strict_quote(s, safe='', encoding=None, errors=None):
    """Enforces strict RFC 3986 percent-encoding for HMAC canonicalization."""
    # Ensure consistent URL encoding to prevent signature mismatches.
    return quote(s, safe='', encoding=encoding or 'utf-8', errors=errors or 'strict')


def admin_auth_ok():
    # Constant-time comparison to prevent timing attacks.
    token = request.headers.get("X-Admin-Token")

    if token:
        if ADMIN_SECRET:
            return hmac.compare_digest(token, ADMIN_SECRET)
        return False

    auth = request.authorization

    if auth and auth.username == "admin":
        return bool(
            DASHBOARD_PASSWORD and
            hmac.compare_digest(auth.password or "", DASHBOARD_PASSWORD)
        )

    return False


def require_admin():
    if admin_auth_ok():
        return None

    response = Response(
        "Authentication required.",
        401,
    )
    response.headers["WWW-Authenticate"] = 'Basic realm="IntentBus Admin"'
    return response


def metrics_auth_ok():
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if bool(METRICS_TOKEN) and hmac.compare_digest(token, METRICS_TOKEN):
            return True
    return admin_auth_ok()


def maybe_cleanup():
    global last_cleanup_time, last_cleanup_error_time

    if request.path == "/admin/cleanup":
        return

    t = now()

    if t - last_cleanup_time < CLEANUP_INTERVAL_SECONDS:
        return

    if t - last_cleanup_error_time < CLEANUP_ERROR_COOLDOWN_SECONDS:
        return

    if not cleanup_lock.acquire(blocking=False):
        return

    try:
        if now() - last_cleanup_time < CLEANUP_INTERVAL_SECONDS:
            return

        success, _ = run_cleanup_once()

        if success:
            last_cleanup_time = now()
            last_cleanup_error_time = 0
        else:
            last_cleanup_error_time = now()
    finally:
        cleanup_lock.release()

# =========================================================
# DATABASE ENGINE
# =========================================================


def get_db():
    if "db" not in g:
        # WAL mode for concurrency, synchronous=NORMAL for performance,
        # and isolation_level=None for explicit transaction control.
        db = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL;")
        db.execute("PRAGMA synchronous=NORMAL;")
        db.execute("PRAGMA busy_timeout=30000;")
        db.execute("PRAGMA foreign_keys=ON;")
        g.db = db
    return g.db


@app.teardown_appcontext
def close_db(e):
    db = g.pop("db", None)
    if db:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def ensure_columns(db, table, columns):
    allowed_tables = {
        "store", "intents", "tester_keys", "rate_limits",
        "idempotency_keys", "request_nonces", "dead_letters"
    }
    # Prevent SQL injection into the table name.
    if table not in allowed_tables:
        raise ValueError(f"Security Exception: Untrusted table name '{table}'")

    existing = {row["name"] for row in db.execute(
        f"PRAGMA table_info({table})").fetchall()}
    for col_def in columns:
        col_name = col_def.split()[0]
        if col_name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")


def setup_schema(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS store (
        key TEXT PRIMARY KEY,
        value TEXT,
        expires_at REAL
    )
    """)

    db.execute("""
    CREATE TABLE IF NOT EXISTS intents (
        id                   TEXT PRIMARY KEY,
        namespace            TEXT DEFAULT 'default',
        goal                 TEXT NOT NULL,
        payload              TEXT NOT NULL,
        status               TEXT NOT NULL,
        priority             INTEGER DEFAULT 100,
        target_worker        TEXT,
        required_capability  TEXT,
        created_at           REAL NOT NULL,
        expires_at           REAL NOT NULL,
        run_at               REAL NOT NULL,
        claimed_at           REAL,
        claim_expires_at     REAL,
        claimed_by           TEXT,
        claim_token          TEXT,
        publisher            TEXT NOT NULL,
        claim_attempts       INTEGER DEFAULT 0,
        max_attempts         INTEGER DEFAULT 3,
        backoff_base         REAL DEFAULT 5.0,
        visibility           TEXT DEFAULT 'private',
        last_error           TEXT,
        failed_at            REAL,
        result               TEXT,
        result_type          TEXT,
        completed_at         REAL
    )
    """)

    db.execute("""
    CREATE TABLE IF NOT EXISTS tester_keys (
        api_key TEXT PRIMARY KEY,
        owner TEXT,
        total_requests INTEGER DEFAULT 0,
        created_at REAL
    )
    """)

    db.execute("""
    CREATE TABLE IF NOT EXISTS rate_limits (
        identifier TEXT PRIMARY KEY,
        count INTEGER,
        window REAL
    )
    """)

    db.execute("""
    CREATE TABLE IF NOT EXISTS idempotency_keys (
        api_key TEXT,
        key TEXT,
        body_hash TEXT,
        response TEXT,
        status_code INTEGER,
        created_at REAL,
        PRIMARY KEY (api_key, key)
    )
    """)

    db.execute("""
    CREATE TABLE IF NOT EXISTS request_nonces (
        api_key TEXT,
        nonce TEXT,
        created_at REAL,
        PRIMARY KEY (api_key, nonce)
    )
    """)

    db.execute("""
    CREATE TABLE IF NOT EXISTS dead_letters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        intent_id TEXT UNIQUE,
        namespace TEXT,
        goal TEXT,
        payload TEXT,
        publisher TEXT,
        visibility TEXT,
        attempts INTEGER,
        reason TEXT,
        created_at REAL
    )
    """)

    ensure_columns(db, "intents", [
        "namespace TEXT DEFAULT 'default'",
        "goal TEXT",
        "payload TEXT",
        "status TEXT",
        "priority INTEGER DEFAULT 100",
        "target_worker TEXT",
        "required_capability TEXT",
        "created_at REAL NOT NULL",
        "expires_at REAL NOT NULL",
        "run_at REAL DEFAULT 0",
        "claimed_at REAL",
        "claim_expires_at REAL",
        "claimed_by TEXT",
        "claim_token TEXT",
        "publisher TEXT",
        "claim_attempts INTEGER DEFAULT 0",
        "max_attempts INTEGER DEFAULT 3",
        "backoff_base REAL DEFAULT 5.0",
        "visibility TEXT DEFAULT 'private'",
        "last_error TEXT",
        "failed_at REAL",
        "result TEXT",
        "result_type TEXT",
        "completed_at REAL",
    ])

    needs_migration = db.execute("""
        SELECT 1
        FROM intents
        WHERE namespace IS NULL
           OR run_at IS NULL
           OR run_at = 0
           OR priority IS NULL
           OR max_attempts IS NULL
           OR backoff_base IS NULL
        LIMIT 1
    """).fetchone()

    if needs_migration:
        db.execute(
            "UPDATE intents SET namespace='default' WHERE namespace IS NULL")
        db.execute(
            "UPDATE intents SET run_at=created_at WHERE run_at IS NULL OR run_at=0")
        db.execute("UPDATE intents SET priority=100 WHERE priority IS NULL")
        db.execute("UPDATE intents SET max_attempts=3 WHERE max_attempts IS NULL")
        db.execute(
            "UPDATE intents SET backoff_base=5.0 WHERE backoff_base IS NULL")

    db.execute("DROP INDEX IF EXISTS idx_intents_routing")
    db.execute("DROP INDEX IF EXISTS idx_intents_routing_v2")
    db.execute("DROP INDEX IF EXISTS idx_intents_routing_v3")
    db.execute("DROP INDEX IF EXISTS idx_intents_routing_pub")
    db.execute("DROP INDEX IF EXISTS idx_intents_routing_vis")
    db.execute("DROP INDEX IF EXISTS idx_intents_claim")

    db.execute("CREATE INDEX IF NOT EXISTS idx_intents_pub_claim ON intents(status, namespace, publisher, priority DESC, run_at, claim_attempts, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_intents_vis_claim ON intents(status, namespace, visibility, priority DESC, run_at, claim_attempts, created_at)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_intents_publisher ON intents(publisher, status)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_intents_cleanup ON intents(status, claim_expires_at)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_intents_failed ON intents(status, failed_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_store_expires ON store(expires_at)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_rate_limits_window ON rate_limits(window)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_idempotency_created ON idempotency_keys(created_at)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_request_nonces_created ON request_nonces(created_at)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_dead_letters_created ON dead_letters(created_at)")


def init_db():
    with app.app_context():
        setup_schema(get_db())

# =========================================================
# AUTH & SECURITY
# =========================================================


def get_role(key):
    # Constant-time comparison to prevent timing attacks.
    if not key:
        return None
    if hmac.compare_digest(key, API_KEY):
        return "admin"
    row = get_db().execute("SELECT 1 FROM tester_keys WHERE api_key=?", (key,)).fetchone()
    return "tester" if row else None


def verify_signed_request(api_key):
    sig = request.headers.get("X-Signature")
    ts = request.headers.get("X-Timestamp")
    nonce = request.headers.get("X-Nonce")

    if not sig or not ts or not nonce:
        return False, "Missing required signature headers."

    try:
        ts_int = int(ts)
    except Exception:
        return False, "Invalid timestamp"

    if abs(now() - ts_int) > NONCE_WINDOW_SECONDS:
        # Reject requests with timestamps outside the allowed window to prevent replay.
        return False, "Stale timestamp"

    raw_body = request.get_data(cache=True, as_text=False) or b""
    parsed = parse_qsl(request.query_string.decode(
        "utf-8"), keep_blank_values=True)

    # Canonicalize query parameters for consistent signature verification.
    canonical_query = urlencode(
        sorted(parsed, key=lambda x: x[0]), doseq=True, quote_via=strict_quote)
    canonical_path = request.path + \
        ("?" + canonical_query if canonical_query else "")

    msg = b"\n".join([
        request.method.upper().encode(),
        canonical_path.encode(),
        ts.encode(),
        nonce.encode(),
        raw_body,  # Include raw body to prevent payload tampering.
    ])

    expected = hmac.new(api_key.encode(), msg, hashlib.sha256).hexdigest()
    # Constant-time comparison to mitigate timing attacks.
    if not hmac.compare_digest(sig, expected):
        return False, "Bad signature"

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")  # Atomic nonce insertion.
        db.execute("INSERT INTO request_nonces VALUES (?, ?, ?)",
                   (api_key, nonce, now()))
        db.commit()
        return True, None
    except sqlite3.IntegrityError:
        # Nonce already exists; replay attack detected.
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
        return False, "Database busy, please retry"
    except (sqlite3.OperationalError, sqlite3.IntegrityError):
        try:
            db.rollback()
        except (sqlite3.OperationalError, sqlite3.IntegrityError):
            pass
        return False, "Internal error during signature validation"

# =========================================================
# CLEANUP
# =========================================================


def archive_dead_letter(db, row, reason):
    db.execute("""
        INSERT OR REPLACE INTO dead_letters (
            intent_id, namespace, goal, payload, publisher, visibility,
            attempts, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row["id"],
        row["namespace"],
        row["goal"],
        row["payload"],
        row["publisher"],
        row["visibility"],
        row["claim_attempts"],
        reason,
        now(),
    ))


def run_cleanup_once():
    db = None
    stats = {
        "expired_open_deleted": 0,
        "expired_claims_requeued": 0,
        "expired_claims_dead": 0,
        "fulfilled_deleted": 0,
        "dead_deleted": 0,
        "dead_letters_deleted": 0,
        "store_deleted": 0,
        "rate_limits_deleted": 0,
        "idempotency_deleted": 0,
        "nonces_deleted": 0,
    }

    try:
        db = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL;")
        db.execute("PRAGMA synchronous=NORMAL;")
        db.execute("PRAGMA busy_timeout=30000;")
        db.execute("PRAGMA foreign_keys=ON;")

        t = now()
        db.execute("BEGIN IMMEDIATE")

        cur = db.execute("DELETE FROM store WHERE expires_at < ?", (t,))
        stats["store_deleted"] = cur.rowcount

        cur = db.execute(
            "DELETE FROM rate_limits WHERE window < ?", (t - 3600,))
        stats["rate_limits_deleted"] = cur.rowcount

        cur = db.execute(
            "DELETE FROM idempotency_keys WHERE created_at < ?", (t - 3600,))
        stats["idempotency_deleted"] = cur.rowcount

        cur = db.execute(
            "DELETE FROM request_nonces WHERE created_at < ?", (t - NONCE_RETENTION_SECONDS,))
        stats["nonces_deleted"] = cur.rowcount

        # Archive expired open intents to dead_letters before deletion
        expired_open = db.execute(
            "SELECT id, namespace, goal, payload, publisher, visibility, claim_attempts "
            "FROM intents WHERE status='open' AND expires_at < ?",
            (t,)
        ).fetchall()

        for row in expired_open:
            archive_dead_letter(db, row, "Expired unclaimed")
            stats["expired_open_deleted"] += 1

        # Delete after archiving
        cur = db.execute(
            "DELETE FROM intents WHERE status='open' AND expires_at < ?", (t,))
        

        expired_claims = db.execute("""
            SELECT id, namespace, goal, payload, publisher, visibility,
                   claim_attempts, max_attempts, backoff_base, last_error
            FROM intents
            WHERE status='claimed'
              AND COALESCE(claim_expires_at, claimed_at + ?) < ?
            ORDER BY COALESCE(claim_expires_at, claimed_at) ASC
        """, (DEFAULT_CLAIM_TIMEOUT, t)).fetchall()

        for r in expired_claims:
            if r["claim_attempts"] >= r["max_attempts"]:
                db.execute("""
                    UPDATE intents
                    SET status='dead',
                        failed_at=?,
                        last_error=COALESCE(last_error, 'Max retries exceeded'),
                        claimed_at=NULL,
                        claim_expires_at=NULL,
                        claim_token=NULL,
                        result=NULL,
                        result_type=NULL,
                        completed_at=NULL
                    WHERE id=?
                """, (t, r["id"]))

                archive_dead_letter(db, {
                    "id": r["id"],
                    "namespace": r["namespace"],
                    "goal": r["goal"],
                    "payload": r["payload"],
                    "publisher": r["publisher"],
                    "visibility": r["visibility"],
                    "claim_attempts": r["claim_attempts"],
                }, r["last_error"] or "Max retries exceeded")
                stats["expired_claims_dead"] += 1
            else:
                jitter = random.uniform(0, 2)
                next_run = t + (r["backoff_base"] *
                                (2 ** r["claim_attempts"])) + jitter
                db.execute("""
                    UPDATE intents
                    SET status='open',
                        run_at=?,
                        claimed_by=NULL,
                        claimed_at=NULL,
                        claim_expires_at=NULL,
                        claim_token=NULL,
                        last_error=COALESCE(last_error, 'Lease expired. Backing off.'),
                        result=NULL,
                        result_type=NULL,
                        completed_at=NULL
                    WHERE id=?
                """, (next_run, r["id"]))
                stats["expired_claims_requeued"] += 1

        cur = db.execute("DELETE FROM intents WHERE status='fulfilled' AND completed_at < ?",
                         (t - FULFILLED_RETENTION_SECONDS,))
        stats["fulfilled_deleted"] = cur.rowcount

        cur = db.execute(
            "DELETE FROM intents WHERE status='dead' AND failed_at < ?", (t - DEAD_RETENTION_SECONDS,))
        stats["dead_deleted"] = cur.rowcount

        cur = db.execute(
            "DELETE FROM dead_letters WHERE created_at < ?", (t - DEAD_RETENTION_SECONDS,))
        stats["dead_letters_deleted"] = cur.rowcount

        db.commit()

        app.logger.info("cleanup_complete", extra=stats)
        return True, stats

    except sqlite3.OperationalError as e:
        if not is_busy_or_locked(e):
            app.logger.error(f"Cleanup failed (OperationalError): {e}")
        if db:
            try:
                db.rollback()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass
        return False, stats
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        app.logger.error(f"Cleanup failed: {e}")
        if db:
            try:
                db.rollback()
            except Exception:
                pass
        return False, stats
    finally:
        if db:
            try:
                db.close()
            except Exception:
                pass

# =========================================================
# REQUEST LIFECYCLE
# =========================================================


@app.before_request
def security():
    # Sanitize X-Request-ID to prevent log injection.
    if getattr(g, "request_id", None) is None:
        req_id = request.headers.get("X-Request-ID", "")
        if re.match(r"^[a-zA-Z0-9_.:-]{1,128}$", req_id):
            g.request_id = req_id
        else:
            g.request_id = uuid.uuid4().hex

    g.request_start = time.perf_counter()

    if request.path in ("/", "/health"):
        return

    if ENFORCE_HTTPS and not is_local() and not request.is_secure:
        # Enforce HTTPS to prevent MitM attacks.
        return api_error("https_required", "HTTPS required.", 403)

    if request.path != "/admin/cleanup":
        maybe_cleanup()

    if request.path == "/metrics":
        if not metrics_auth_ok():
            return api_error("unauthorized", "Metrics access denied.", 401)
        return

    admin_path = request.path.startswith("/admin/")
    if MAINTENANCE_MODE and not admin_path:
        return api_error("maintenance", "Server in maintenance mode.", 503)

    if admin_path:
        return

    key = request.headers.get("X-API-KEY")
    if not key:
        return api_error("unauthorized", "Missing API key.", 401)

    role = get_role(key)
    if not role:
        return api_error("unauthorized", "Invalid API key.", 401)

    g.api_key = key
    g.role = role

    has_sig_headers = bool(request.headers.get("X-Signature"))
    if REQUIRE_SIGNATURES or has_sig_headers:
        ok, err = verify_signed_request(key)
        if not ok:
            return api_error("invalid_signature", err, 403)

    if role == "tester":
        db = get_db()
        t = now()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT count, window FROM rate_limits WHERE identifier=?", (key,)).fetchone()
            if not row or t - row["window"] > RATE_LIMIT_WINDOW:
                db.execute(
                    "REPLACE INTO rate_limits VALUES (?, 1, ?)", (key, t))
            elif row["count"] >= RATE_LIMIT_MAX:
                db.rollback()
                return api_error("rate_limited", "Too many requests.", 429)
            else:
                db.execute(
                    "UPDATE rate_limits SET count=count+1 WHERE identifier=?", (key,))

            db.execute(
                "UPDATE tester_keys SET total_requests=total_requests+1 WHERE api_key=?", (key,))
            db.commit()
        except sqlite3.OperationalError:
            try:
                db.rollback()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass
            return api_error("database_busy", "Database busy, please retry.", 503)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            return api_error("internal_error", "An internal error occurred.", 500)


@app.after_request
def log_response(response):
    start_time = getattr(g, "request_start", None)
    duration_ms = round((time.perf_counter() - start_time)
                        * 1000, 2) if start_time else 0.0

    app.logger.info(
        "request_complete",
        extra={
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )

    # Standard security headers to prevent clickjacking, sniffing, and caching.
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Intent-Version"] = "2.1"
    return response

# =========================================================
# ROOT / HEALTH
# =========================================================


@app.route("/")
def index():
    return "Intent Bus V7.61", 200


@app.route("/health")
def health():
    return jsonify({"ok": True, "ts": now(), "version": "7.61"}), 200

# =========================================================
# DASHBOARD
# =========================================================


DASHBOARD_HTML = """
{% autoescape true %} {# Prevent XSS in rendered templates. #}
<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Intent Bus Dashboard</title>
  <style>
    body { font-family: system-ui, sans-serif; background:#0d1117; color:#c9d1d9; padding:20px; }
    h1,h2 { margin: 0.2em 0; }
    .grid { display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:12px; margin: 16px 0; }
    .card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:12px; }
    table { width:100%; border-collapse: collapse; margin-top: 10px; }
    th, td { border-bottom:1px solid #30363d; padding:8px; text-align:left; font-size: 14px; }
    code { background:#161b22; padding:2px 4px; border-radius:4px; }
    .muted { color:#8b949e; font-size: 13px; }
  </style>
</head>
<body>
  <h1>Intent Bus</h1>
  <div class="muted">Version {{ version }}</div>
  <div class="muted">Dead letters: {{ stats.dead_letters }}</div>

  <div class="grid">
    <div class="card"><h2>{{ stats.open }}</h2><div>Open</div></div>
    <div class="card"><h2>{{ stats.claimed }}</h2><div>Claimed</div></div>
    <div class="card"><h2>{{ stats.fulfilled }}</h2><div>Fulfilled</div></div>
    <div class="card"><h2>{{ stats.dead }}</h2><div>Dead</div></div>
  </div>

  <div class="card">
    <h2>Recent Intents</h2>
    <table>
      <thead>
        <tr>
          <th>ID</th><th>Namespace</th><th>Goal</th><th>Status</th>
          <th>Priority</th><th>Worker</th><th>Capability</th><th>Attempts</th>
        </tr>
      </thead>
      <tbody>
      {% for i in intents %}
        <tr>
          <td><code>{{ i.id }}</code></td>
          <td>{{ i.namespace }}</td>
          <td>{{ i.goal }}</td>
          <td>{{ i.status }}</td>
          <td>{{ i.priority }}</td>
          <td>{{ i.target_worker or "" }}</td>
          <td>{{ i.required_capability or "" }}</td>
          <td>{{ i.claim_attempts }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>Tester Keys</h2>
    <table>
      <thead><tr><th>Owner</th><th>Requests</th><th>Created</th></tr></thead>
      <tbody>
      {% for k in keys %}
        <tr>
          <td>{{ k.owner }}</td>
          <td>{{ k.total_requests }}</td>
          <td>{{ k.created_at|int }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>Dead Letters</h2>
    <table>
      <thead><tr><th>Intent</th><th>Namespace</th><th>Goal</th><th>Attempts</th><th>Reason</th></tr></thead>
      <tbody>
      {% for d in dead %}
        <tr>
          <td><code>{{ d.intent_id }}</code></td>
          <td>{{ d.namespace }}</td>
          <td>{{ d.goal }}</td>
          <td>{{ d.attempts }}</td>
          <td>{{ d.reason }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</body>
</html>
{% endautoescape %}
"""


@app.route("/admin/dashboard")
def admin_dashboard():
    denied = require_admin()
    if denied:
        return denied

    db = get_db()
    stat_rows = db.execute("""
        SELECT status, COUNT(*) AS c
        FROM intents
        GROUP BY status
    """).fetchall()

    stats = {
        "open": 0,
        "claimed": 0,
        "fulfilled": 0,
        "dead": 0,
        "failed": 0,
        "dead_letters": db.execute("SELECT COUNT(*) AS c FROM dead_letters").fetchone()["c"],
    }
    for r in stat_rows:
        stats[r["status"]] = r["c"]

    intents = db.execute("""
        SELECT id, namespace, goal, status, priority, target_worker, required_capability, claim_attempts
        FROM intents
        ORDER BY created_at DESC
        LIMIT 12
    """).fetchall()

    keys = db.execute("""
        SELECT owner, total_requests, created_at
        FROM tester_keys
        ORDER BY total_requests DESC
        LIMIT 12
    """).fetchall()

    dead = db.execute("""
        SELECT intent_id, namespace, goal, attempts, reason
        FROM dead_letters
        ORDER BY created_at DESC
        LIMIT 12
    """).fetchall()

    return render_template_string(
        DASHBOARD_HTML,
        version="7.61",
        stats=stats,
        intents=intents,
        keys=keys,
        dead=dead,
    )

# =========================================================
# METRICS
# =========================================================


@app.route("/metrics")
def metrics():
    db = get_db()
    lines = [
        "# HELP intent_bus_intents_total Total intents by status and namespace",
        "# TYPE intent_bus_intents_total gauge",
    ]
    for r in db.execute("""
        SELECT status, namespace, COUNT(*) AS c
        FROM intents
        GROUP BY status, namespace
    """).fetchall():
        lines.append(
            f'intent_bus_intents_total{{status="{r["status"]}",namespace="{r["namespace"]}"}} {r["c"]}'
        )

    dead_count = db.execute(
        "SELECT COUNT(*) AS c FROM dead_letters").fetchone()["c"]
    tester_count = db.execute(
        "SELECT COUNT(*) AS c FROM tester_keys").fetchone()["c"]

    lines += [
        "# HELP intent_bus_dead_letters_total Total dead-letter intents",
        "# TYPE intent_bus_dead_letters_total gauge",
        f"intent_bus_dead_letters_total {dead_count}",
        "# HELP intent_bus_tester_keys_total Total active tester keys",
        "# TYPE intent_bus_tester_keys_total gauge",
        f"intent_bus_tester_keys_total {tester_count}",
    ]

    return Response("\n".join(lines) + "\n", mimetype="text/plain")

# =========================================================
# KV STORE
# =========================================================


@app.route("/set/<key>", methods=["POST"])
def set_value(key):
    data = request.get_json(silent=True)
    if data is not None and not isinstance(data, dict):
        return api_error("invalid_payload", "JSON body must be an object.", 400)
    data = data or {}

    if "value" not in data:
        return api_error("invalid_request", "Missing value.")

    ttl = safe_int(data.get("ttl"), 600, 1, MAX_TTL)
    db = get_db()

    # Prefix keys with API key to ensure user isolation.

    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "REPLACE INTO store VALUES (?, ?, ?)",
            (f"{g.api_key}:{key}", str(data["value"]), now() + ttl),
        )
        db.commit()
    except sqlite3.OperationalError:
        try:
            db.rollback()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass
        return api_error("database_busy", "Database busy.", 503)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return api_error("internal_error", "An internal error occurred.", 500)

    return jsonify({"ok": True})


@app.route("/get/<key>")
def get_value(key):
    row = get_db().execute(
        "SELECT value FROM store WHERE key=? AND expires_at > ?",
        (f"{g.api_key}:{key}", now()),
    ).fetchone()
    if not row:
        return api_error("not_found", "Key not found.", 404)
    return jsonify({"value": row["value"]})

# =========================================================
# INTENT CREATION
# =========================================================


@app.route("/intent", methods=["POST"])
def create_intent():
    db = get_db()

    idem_key = request.headers.get("Idempotency-Key")
    data = request.get_json(silent=True)

    if data is not None and not isinstance(data, dict):
        return api_error("invalid_payload", "JSON body must be an object.", 400)
    data = data or {}

    if "goal" not in data or "payload" not in data:
        return api_error("invalid_request", "Missing goal or payload.")

    namespace = str(data.get("namespace", "default"))
    if not valid_namespace(namespace):
        return api_error("invalid_namespace", "Namespace must be 1-64 alphanumeric chars, dots, dashes, or underscores.")

    goal = str(data.get("goal", "")).strip()
    if not goal or len(goal) > 256:
        return api_error("invalid_goal", "Goal must be between 1 and 256 characters.")

    if not is_json_safe(data["payload"]):
        return api_error("invalid_payload", "Payload exceeds maximum nesting depth.")

    payload_str = json.dumps(data["payload"], separators=(",", ":"))
    if len(payload_str.encode()) > MAX_PAYLOAD:
        return api_error("payload_too_large", "Payload too large.", 413)

    priority = safe_int(data.get("priority"),
                        DEFAULT_PRIORITY, 0, MAX_PRIORITY)
    delay = safe_float(data.get("delay"), 0.0, 0.0)
    run_at = now() + delay
    max_attempts = safe_int(data.get("max_attempts"),
                            DEFAULT_MAX_ATTEMPTS, 1, 20)
    backoff_base = safe_float(data.get("backoff_base"),
                              DEFAULT_BACKOFF_BASE, 1.0, 3600.0)
    visibility = "public" if data.get("visibility") == "public" else "private"

    target_worker = str(data.get("target_worker", "")).strip()[:64]
    if target_worker and not valid_label(target_worker):
        return api_error("invalid_target_worker", "target_worker must be 1-64 safe characters.")

    required_capability = str(data.get("required_capability", "")).strip()[:64]
    if required_capability and not valid_label(required_capability):
        return api_error("invalid_required_capability", "required_capability must be 1-64 safe characters.")

    body_hash = None
    if idem_key:
        body_hash = hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(
                ",", ":")).encode("utf-8")
        ).hexdigest()

    iid = secrets.token_hex(16)

    try:
        db.execute("BEGIN IMMEDIATE")

        if idem_key:
            cached = db.execute(
                "SELECT body_hash, response, status_code FROM idempotency_keys WHERE api_key=? AND key=?",
                (g.api_key, idem_key),
            ).fetchone()

            if cached:
                if cached["body_hash"] != body_hash:
                    db.rollback()
                    return api_error("idempotency_conflict", "Key reused with different payload.", 422)
                db.rollback()
                return Response(cached["response"], status=cached["status_code"], mimetype="application/json")

        if g.role != "admin":
            open_count = db.execute(
                "SELECT COUNT(1) FROM intents WHERE publisher=? AND status IN ('open', 'claimed')",
                (g.api_key,),
            ).fetchone()[0]

            if open_count >= MAX_OPEN_INTENTS_PER_KEY:
                db.rollback()
                # Prevent a single API key from flooding the system.
                return api_error(
                    "limit_exceeded",
                    f"Maximum of {MAX_OPEN_INTENTS_PER_KEY} open intents reached.",
                    429,
                )

        db.execute("""
            INSERT INTO intents (
                id, namespace, goal, payload, status, priority,
                target_worker, required_capability,
                created_at, expires_at, run_at,
                publisher, max_attempts, backoff_base, visibility
            ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            iid,
            namespace,
            goal,
            payload_str,
            priority,
            target_worker or None,
            required_capability or None,
            now(),
            now() + MAX_TTL,
            run_at,
            g.api_key,
            max_attempts,
            backoff_base,
            visibility,
        ))

        response_body = json.dumps({
            "id": iid,
            "status": "published",
            "namespace": namespace,
        })

        if idem_key:
            db.execute("""
                INSERT OR REPLACE INTO idempotency_keys
                    (api_key, key, body_hash, response, status_code, created_at)
                VALUES (?, ?, ?, ?, 201, ?)
            """, (g.api_key, idem_key, body_hash, response_body, now()))

        db.commit()
        return Response(response_body, status=201, mimetype="application/json")

    except sqlite3.OperationalError:
        try:
            db.rollback()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass
        return api_error("database_busy", "Database busy.", 503)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return api_error("internal_error", "An internal error occurred.", 500)

# =========================================================
# CLAIM
# =========================================================


@app.route("/claim", methods=["POST"])
def claim():
    db = get_db()

    target_goal = request.args.get("goal")
    target_namespace = request.args.get("namespace", "default")
    target_publisher = request.args.get("publisher")

    worker_id = (
        request.headers.get("X-Worker-ID", "").strip()
        or request.args.get("worker_id", "").strip()
    )[:64]

    worker_capabilities = (
        request.headers.get("X-Worker-Capabilities", "").strip()
        or request.headers.get("X-Worker-Capability", "").strip()
        or request.args.get("capabilities", "").strip()
        or request.args.get("capability", "").strip()
    )[:256]
    worker_caps_normalized = ",".join(
        [c.strip() for c in worker_capabilities.split(",") if c.strip()])

    if not valid_namespace(target_namespace):
        return api_error("invalid_namespace", "Namespace must be 1-64 alphanumeric chars, dots, dashes, or underscores.")

    if target_publisher and g.role != "admin" and target_publisher != g.api_key:
        # Enforce data segregation; only admins or the publisher can filter by publisher.
        return api_error("forbidden", "publisher filtering is restricted.", 403)

    t = now()
    where_parts = [
        "expires_at > :now",
        "run_at <= :now",
        "claim_attempts < max_attempts",
        "namespace = :namespace",
        "(status='open' OR (status='claimed' AND COALESCE(claim_expires_at, claimed_at + :timeout) < :now))",
        "(target_worker IS NULL OR target_worker = :worker_id)",
        "(required_capability IS NULL OR required_capability = '' OR instr(',' || :caps || ',', ',' || required_capability || ',') > 0)",
    ]

    token = secrets.token_hex(16)

    params = {
        "now": t,
        "timeout": DEFAULT_CLAIM_TIMEOUT,
        "namespace": target_namespace,
        "claimer": g.api_key,
        "token": token,
        "lease_exp": t + DEFAULT_CLAIM_TIMEOUT,
        "worker_id": worker_id,
        "caps": worker_caps_normalized,
    }

    if target_goal:
        where_parts.append("goal = :goal")
        params["goal"] = target_goal

    if target_publisher:
        where_parts.append("publisher = :publisher")
        params["publisher"] = target_publisher
    else:
        where_parts.append("(publisher = :key OR visibility = 'public')")
        params["key"] = g.api_key

    routing_sql = " AND ".join(where_parts)

    query = f"""
    WITH candidate AS (
        SELECT id
        FROM intents
        WHERE {routing_sql}
        ORDER BY priority DESC, run_at ASC, claim_attempts ASC, created_at ASC, id ASC
        LIMIT 1
    )
    UPDATE intents
    SET
        status='claimed',
        claimed_at=:now,
        claim_expires_at=:lease_exp,
        claimed_by=:claimer,
        claim_token=:token,
        claim_attempts=claim_attempts+1
    WHERE id = (SELECT id FROM candidate)
      AND (status='open' OR (status='claimed' AND COALESCE(claim_expires_at, claimed_at + :timeout) < :now))
    RETURNING id, namespace, goal, payload, claim_attempts, priority, target_worker, required_capability, claim_token
    """

    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(query, params).fetchone()

        if not row:
            db.rollback()
            r = Response(status=204)
            r.headers["Retry-After"] = "1"
            return r

        db.commit()
        return jsonify({
            "id": row["id"],
            "namespace": row["namespace"],
            "goal": row["goal"],
            "payload": json.loads(row["payload"]),
            "claim_attempts": row["claim_attempts"],
            "priority": row["priority"],
            "target_worker": row["target_worker"],
            "required_capability": row["required_capability"],
            "claim_token": row["claim_token"],
            "claim_timeout": DEFAULT_CLAIM_TIMEOUT,
        })

    except sqlite3.OperationalError as e:
        try:
            db.rollback()
        except Exception:
            pass
        if is_busy_or_locked(e):
            r = Response(status=204)
            r.headers["Retry-After"] = "1"
            return r
        return api_error("database_error", "Database error.", 500)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return api_error("internal_error", "An internal error occurred.", 500)

# =========================================================
# EXTEND CLAIM
# =========================================================


@app.route("/extend_claim/<iid>", methods=["POST"])
def extend_claim(iid):
    data = request.get_json(silent=True)
    if data is not None and not isinstance(data, dict):
        return api_error("invalid_payload", "JSON body must be an object.", 400)
    data = data or {}

    claim_token = data.get("claim_token")
    if not claim_token:
        return api_error("invalid_request", "Missing claim_token.")

    seconds = safe_int(data.get("seconds"), DEFAULT_CLAIM_TIMEOUT, 10, 3600)
    db = get_db()

    try:
        db.execute("BEGIN IMMEDIATE")
        cur = db.execute("""
            UPDATE intents
            SET claim_expires_at = ?
            WHERE id = ?
              AND status = 'claimed'
              AND claimed_by = ?
              AND claim_token = ?
              AND COALESCE(claim_expires_at, claimed_at + ?) > ?
        """, (now() + seconds, iid, g.api_key, claim_token, DEFAULT_CLAIM_TIMEOUT, now()))

        if cur.rowcount == 0:
            db.rollback()
            # Ensure only the current legitimate claimant can extend the lease.
            return api_error("not_found", "Intent not found, not owned by you, or invalid claim_token.", 404)

        db.commit()
        return jsonify({"ok": True, "id": iid, "extended_by": seconds}), 200
    except sqlite3.OperationalError:
        try:
            db.rollback()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass
        return api_error("database_busy", "Database busy.", 503)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return api_error("internal_error", "An internal error occurred.", 500)

# =========================================================
# FAIL
# =========================================================


@app.route("/fail/<iid>", methods=["POST"])
def fail(iid):
    data = request.get_json(silent=True)
    if data is not None and not isinstance(data, dict):
        return api_error("invalid_payload", "JSON body must be an object.", 400)
    data = data or {}

    claim_token = data.get("claim_token")
    if not claim_token:
        return api_error("invalid_request", "Missing claim_token.")

    error_text = str(data.get("error", "unknown")).strip()[:500]
    t = now()

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""
            SELECT id, namespace, goal, payload, publisher, visibility,
                   claim_attempts, max_attempts, backoff_base
            FROM intents
            WHERE id = ? AND claimed_by = ? AND claim_token = ? AND status = 'claimed'
              AND COALESCE(claim_expires_at, claimed_at + ?) > ?
        """, (iid, g.api_key, claim_token, DEFAULT_CLAIM_TIMEOUT, now())).fetchone()

        if not row:
            db.rollback()
            return api_error("not_found", "Intent not found, not owned by you, or invalid claim_token.", 404)

        if row["claim_attempts"] >= row["max_attempts"]:
            db.execute("""
                UPDATE intents
                SET status = 'dead',
                    failed_at = ?,
                    last_error = ?,
                    claimed_at = NULL,
                    claim_expires_at = NULL,
                    claim_token = NULL,
                    result = NULL,
                    result_type = NULL,
                    completed_at = NULL
                WHERE id = ?
            """, (t, error_text, iid))

            archive_dead_letter(db, row, error_text)
        else:
            jitter = random.uniform(0, 2)
            next_run = t + (row["backoff_base"] *
                            (2 ** row["claim_attempts"])) + jitter
            db.execute("""
                UPDATE intents
                SET status = 'open',
                    run_at = ?,
                    last_error = ?,
                    claimed_by = NULL,
                    claimed_at = NULL,
                    claim_expires_at = NULL,
                    claim_token = NULL,
                    result = NULL,
                    result_type = NULL,
                    completed_at = NULL
                WHERE id = ?
            """, (next_run, error_text, iid))

        db.commit()
        return jsonify({"ok": True, "id": iid}), 200

    except sqlite3.OperationalError:
        try:
            db.rollback()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass
        return api_error("database_busy", "Database busy.", 503)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return api_error("internal_error", "An internal error occurred.", 500)

# =========================================================
# FULFILL & RESULT POLLING
# =========================================================


@app.route("/fulfill/<iid>", methods=["POST"])
def fulfill(iid):
    db = get_db()
    data = request.get_json(silent=True)

    if data is not None and not isinstance(data, dict):
        return api_error("invalid_payload", "JSON body must be an object.", 400)
    data = data or {}

    claim_token = data.get("claim_token")
    if not claim_token:
        return api_error("invalid_request", "Missing claim_token.")

    result_payload = data.get("result")
    result_type = str(data.get("result_type", "json"))
    result_text = None

    if result_type not in ("json", "text"):
        return api_error("invalid_result_type", "Result type must be 'json' or 'text'.")

    if result_payload is not None:
        if result_type == "text" and not isinstance(result_payload, str):
            return api_error("invalid_result", "Text results must be a string.")

        if not is_json_safe(result_payload):
            return api_error("invalid_payload", "Result exceeds maximum nesting depth.", 400)

        try:
            result_text = result_payload if result_type == "text" else json.dumps(
                result_payload, separators=(",", ":"))
        except Exception:
            return api_error("invalid_result", "Result must be serializable.")

        if len(result_text.encode()) > MAX_PAYLOAD:
            return api_error("result_too_large", "Result too large.", 413)

    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""
            SELECT id
            FROM intents
            WHERE id = ? AND claimed_by = ? AND claim_token = ? AND status = 'claimed'
              AND COALESCE(claim_expires_at, claimed_at + ?) > ?
        """, (iid, g.api_key, claim_token, DEFAULT_CLAIM_TIMEOUT, now())).fetchone()

        if not row:
            db.rollback()
            return api_error("not_found", "Intent not found, not owned by you, or invalid claim_token.", 404)

        db.execute("""
            UPDATE intents
            SET status = 'fulfilled',
                result = ?,
                result_type = ?,
                completed_at = ?,
                claim_token = NULL,
                last_error = NULL,
                failed_at = NULL
            WHERE id = ?
        """, (result_text, result_type if result_text is not None else None, now(), iid))
        db.commit()
        return jsonify({"ok": True, "id": iid}), 200

    except sqlite3.OperationalError:
        try:
            db.rollback()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass
        return api_error("database_busy", "Database busy.", 503)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return api_error("internal_error", "An internal error occurred.", 500)


@app.route("/result/<iid>")
def result(iid):
    row = get_db().execute("""
        SELECT id, namespace, publisher, claimed_by, status,
               result, result_type, last_error, completed_at,
               goal, priority, claim_attempts, visibility, run_at, claim_expires_at,
               target_worker, required_capability
        FROM intents
        WHERE id = ?
    """, (iid,)).fetchone()

    if not row:
        return api_error("not_found", "Intent not found.", 404)

    # Access control: only admin, publisher, or the current claimer can view results.
    if g.role != "admin" and g.api_key not in (row["publisher"], row["claimed_by"]):
        return api_error("forbidden", "Access denied.", 403)

    payload = {
        "id": row["id"],
        "namespace": row["namespace"],
        "goal": row["goal"],
        "priority": row["priority"],
        "status": row["status"],
        "visibility": row["visibility"],
        "claim_attempts": row["claim_attempts"],
        "run_at": row["run_at"],
        "claim_expires_at": row["claim_expires_at"],
        "target_worker": row["target_worker"],
        "required_capability": row["required_capability"],
        "result_type": row["result_type"],
        "completed_at": row["completed_at"],
    }

    if row["result"] is not None:
        if row["result_type"] == "text":
            payload["result"] = row["result"]
        else:
            try:
                payload["result"] = json.loads(row["result"])
            except Exception:
                payload["result"] = row["result"]

    if row["last_error"]:
        payload["error"] = row["last_error"]

    return jsonify(payload), 200


@app.route("/status/<iid>")
def status(iid):
    row = get_db().execute("""
        SELECT id, namespace, goal, publisher, claimed_by, status,
               priority, claim_attempts, run_at, claim_expires_at,
               visibility, completed_at, last_error, target_worker, required_capability
        FROM intents
        WHERE id = ?
    """, (iid,)).fetchone()

    if not row:
        return api_error("not_found", "Intent not found.", 404)

    if g.role != "admin" and g.api_key not in (row["publisher"], row["claimed_by"]):
        return api_error("forbidden", "Access denied.", 403)

    return jsonify({
        "id": row["id"],
        "namespace": row["namespace"],
        "goal": row["goal"],
        "status": row["status"],
        "priority": row["priority"],
        "visibility": row["visibility"],
        "claim_attempts": row["claim_attempts"],
        "run_at": row["run_at"],
        "claim_expires_at": row["claim_expires_at"],
        "target_worker": row["target_worker"],
        "required_capability": row["required_capability"],
        "completed_at": row["completed_at"],
        "last_error": row["last_error"],
    }), 200

# =========================================================
# ADMIN ENDPOINTS
# =========================================================


@app.route("/admin/generate_key", methods=["POST"])
def admin_generate_key():
    denied = require_admin()
    if denied:
        return denied

    data = request.get_json(silent=True)
    if data is not None and not isinstance(data, dict):
        return api_error("invalid_payload", "JSON body must be an object.", 400)
    data = data or {}

    owner = str(data.get("owner", "anonymous"))[:100]
    key = "tk_" + secrets.token_hex(20)

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO tester_keys (api_key, owner, created_at) VALUES (?, ?, ?)",
            (key, owner, now()),
        )
        db.commit()
    except sqlite3.OperationalError:
        try:
            db.rollback()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass
        return api_error("database_busy", "Database busy.", 503)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return api_error("internal_error", "An internal error occurred.", 500)

    return jsonify({"api_key": key, "owner": owner}), 201


@app.route("/admin/revoke_key", methods=["POST"])
def admin_revoke_key():
    denied = require_admin()
    if denied:
        return denied

    data = request.get_json(silent=True)
    if data is not None and not isinstance(data, dict):
        return api_error("invalid_payload", "JSON body must be an object.", 400)
    data = data or {}

    target_key = data.get("api_key")
    if not target_key:
        return api_error("invalid_request", "Missing api_key.")

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM tester_keys WHERE api_key = ?", (target_key,))
        db.execute("DELETE FROM rate_limits WHERE identifier = ?", (target_key,))
        db.execute("DELETE FROM idempotency_keys WHERE api_key = ?",
                   (target_key,))
        db.execute("DELETE FROM request_nonces WHERE api_key = ?", (target_key,))
        db.commit()
    except sqlite3.OperationalError:
        try:
            db.rollback()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass
        return api_error("database_busy", "Database busy.", 503)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return api_error("internal_error", "An internal error occurred.", 500)

    return jsonify({"ok": True}), 200


@app.route("/admin/purge", methods=["POST"])
def admin_purge():
    denied = require_admin()
    if denied:
        return denied

    data = request.get_json(silent=True)
    if data is not None and not isinstance(data, dict):
        return api_error("invalid_payload", "JSON body must be an object.", 400)
    data = data or {}

    # Require explicit confirmation for destructive actions.
    if data.get("confirm") is not True:
        return api_error("confirmation_required", 'Send {"confirm": true} to purge.', 400)

    namespace = data.get("namespace")
    if namespace and not valid_namespace(namespace):
        return api_error("invalid_namespace", "Namespace must be 1-64 alphanumeric chars, dots, dashes, or underscores.")

    db = get_db()

    try:
        db.execute("BEGIN IMMEDIATE")
        if namespace:
            db.execute(
                "DELETE FROM dead_letters WHERE namespace = ?", (namespace,))
            db.execute("DELETE FROM intents WHERE namespace = ?", (namespace,))
        else:
            db.execute("DELETE FROM dead_letters")
            db.execute("DELETE FROM intents")
            db.execute("DELETE FROM store")
            db.execute("DELETE FROM rate_limits")
            db.execute("DELETE FROM idempotency_keys")
            db.execute("DELETE FROM request_nonces")
        db.commit()
    except sqlite3.OperationalError:
        try:
            db.rollback()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass
        return api_error("database_busy", "Database busy.", 503)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return api_error("internal_error", "An internal error occurred.", 500)

    return jsonify({"ok": True}), 200


@app.route("/admin/cleanup", methods=["POST"])
def admin_cleanup():
    denied = require_admin()
    if denied:
        return denied

    if not cleanup_lock.acquire(blocking=False):
        return api_error("busy", "Cleanup already running.", 503)

    global last_cleanup_time, last_cleanup_error_time
    try:
        success, stats = run_cleanup_once()
        if success:
            last_cleanup_time = now()
            last_cleanup_error_time = 0
            return jsonify(stats), 200
        else:
            last_cleanup_error_time = now()
            return api_error("cleanup_failed", "Cleanup encountered an error.", 500)
    finally:
        cleanup_lock.release()


@app.route("/admin/intents/<iid>")
def admin_intent_detail(iid):
    denied = require_admin()
    if denied:
        return denied

    row = get_db().execute("""
        SELECT id, namespace, goal, payload, status, priority, target_worker, required_capability,
               publisher, claimed_by, claim_token, claim_attempts, max_attempts, backoff_base,
               visibility, last_error, created_at, expires_at, run_at, claimed_at,
               claim_expires_at, failed_at, completed_at, result_type
        FROM intents
        WHERE id = ?
    """, (iid,)).fetchone()

    if not row:
        return api_error("not_found", "Intent not found.", 404)

    data = dict(row)
    try:
        data["payload"] = json.loads(data["payload"])
    except Exception:
        pass

    return jsonify(data), 200


@app.route("/admin/intents/<iid>/cancel", methods=["POST"])
def admin_cancel_intent(iid):
    denied = require_admin()
    if denied:
        return denied

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""
            SELECT id, namespace, goal, payload, publisher, visibility, claim_attempts
            FROM intents
            WHERE id = ?
        """, (iid,)).fetchone()

        if not row:
            db.rollback()
            return api_error("not_found", "Intent not found.", 404)

        db.execute("""
            UPDATE intents
            SET status='dead',
                failed_at=?,
                last_error='Cancelled by admin',
                claimed_at=NULL,
                claim_expires_at=NULL,
                claim_token=NULL,
                result=NULL,
                result_type=NULL,
                completed_at=NULL
            WHERE id=?
        """, (now(), iid))
        archive_dead_letter(db, row, "Cancelled by admin")
        db.commit()
        return jsonify({"ok": True, "id": iid, "status": "dead"}), 200

    except sqlite3.OperationalError:
        try:
            db.rollback()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass
        return api_error("database_busy", "Database busy.", 503)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return api_error("internal_error", "An internal error occurred.", 500)


@app.route("/admin/intents/<iid>/retry", methods=["POST"])
def admin_retry_intent(iid):
    denied = require_admin()
    if denied:
        return denied

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""
            SELECT id
            FROM intents
            WHERE id = ?
        """, (iid,)).fetchone()

        if not row:
            db.rollback()
            return api_error("not_found", "Intent not found.", 404)

        db.execute("""
            UPDATE intents
            SET status='open',
                run_at=?,
                claim_attempts=0,
                claimed_by=NULL,
                claimed_at=NULL,
                claim_expires_at=NULL,
                claim_token=NULL,
                last_error='Retried by admin',
                failed_at=NULL,
                result=NULL,
                result_type=NULL,
                completed_at=NULL
            WHERE id=?
        """, (now(), iid))

        db.execute("DELETE FROM dead_letters WHERE intent_id=?", (iid,))
        db.commit()
        return jsonify({"ok": True, "id": iid, "status": "open"}), 200

    except sqlite3.OperationalError:
        try:
            db.rollback()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass
        return api_error("database_busy", "Database busy.", 503)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return api_error("internal_error", "An internal error occurred.", 500)


@app.route("/admin/dead")
def admin_dead_letters():
    denied = require_admin()
    if denied:
        return denied

    rows = get_db().execute("""
        SELECT intent_id, namespace, goal, attempts, reason, created_at
        FROM dead_letters
        ORDER BY created_at DESC
        LIMIT 100
    """).fetchall()

    return jsonify([dict(r) for r in rows]), 200


@app.route("/admin/dead/<intent_id>")
def admin_dead_letter_detail(intent_id):
    denied = require_admin()
    if denied:
        return denied

    row = get_db().execute("""
        SELECT intent_id, namespace, goal, payload, publisher,
               visibility, attempts, reason, created_at
        FROM dead_letters
        WHERE intent_id = ?
    """, (intent_id,)).fetchone()

    if not row:
        return api_error("not_found", "Dead letter not found.", 404)

    result = dict(row)
    try:
        result["payload"] = json.loads(result["payload"])
    except Exception:
        pass

    return jsonify(result), 200

# =========================================================
# MAIN
# =========================================================


if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    init_db()

if __name__ == "__main__":
    if app.debug:
        init_db()
    app.run(host="0.0.0.0", port=5000)
