import os
import sqlite3
import time
import json
import uuid
from flask import Flask, request, jsonify

app = Flask(__name__)

# 1. AUTH LOGIC
API_KEY = os.environ.get("BUS_SECRET")

def check_auth():
    if not API_KEY:
        return False
    return request.headers.get("X-API-KEY") == API_KEY

# 2. DATABASE LOGIC
DB_PATH = os.path.join(os.path.dirname(__file__), 'infrastructure.db')

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.execute('PRAGMA journal_mode=WAL;')
    db.execute('''CREATE TABLE IF NOT EXISTS store
                  (key TEXT PRIMARY KEY, value TEXT, expires_at REAL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS intents
                  (id TEXT PRIMARY KEY, goal TEXT, payload TEXT, status TEXT, reward INTEGER, claimed_at REAL)''')
    return db

@app.route('/')
def index():
    return "Intent Bus is Active (Private).", 200

# ==========================================
# PRIMITIVE 1: EPHEMERAL STATE (CLIPBOARD)
# ==========================================

@app.route('/set/<key>', methods=['POST', 'GET'])
def set_clipboard(key):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401

    value = request.args.get('value')
    ttl = int(request.args.get('ttl', 600))
    if not value: return jsonify({"error": "No value provided"}), 400

    db = get_db()
    db.execute('INSERT OR REPLACE INTO store (key, value, expires_at) VALUES (?, ?, ?)',
               (key, value, time.time() + ttl))
    db.commit()
    db.close()
    return jsonify({"status": "success", "key": key}), 200

@app.route('/get/<key>', methods=['GET'])
def get_clipboard(key):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401

    db = get_db()
    row = db.execute('SELECT value, expires_at FROM store WHERE key = ?', (key,)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "Key not found"}), 404

    if time.time() > row[1]:
        db.execute('DELETE FROM store WHERE key = ?', (key,))
        db.commit()
        db.close()
        return jsonify({"error": "Key expired"}), 404

    db.close()
    return jsonify({"key": key, "value": row[0]}), 200


# ==========================================
# PRIMITIVE 2: COORDINATION (INTENT BUS)
# ==========================================

@app.route('/intent', methods=['POST'])
def create_intent():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    intent_id = str(uuid.uuid4())[:8]
    db = get_db()
    db.execute('INSERT INTO intents (id, goal, payload, status, reward, claimed_at) VALUES (?, ?, ?, ?, ?, ?)',
               (intent_id, data['goal'], json.dumps(data['payload']), 'open', data.get('reward', 0), 0.0))
    db.commit()
    db.close()
    return jsonify({"id": intent_id, "status": "published"}), 201

@app.route('/claim', methods=['POST'])
def claim_intent():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401

    target_goal = request.args.get('goal')
    db = get_db()
    now = time.time()

    query = "SELECT id, goal, payload, reward FROM intents WHERE (status = 'open' OR (status = 'claimed' AND claimed_at < ?))"
    params = [now - 60]

    if target_goal:
        query += " AND goal = ?"
        params.append(target_goal)

    query += " ORDER BY rowid ASC LIMIT 1"
    cursor = db.execute(query, tuple(params))
    row = cursor.fetchone()

    if not row:
        db.close()
        return '', 204

    intent_id = row[0]
    update_query = "UPDATE intents SET status = 'claimed', claimed_at = ? WHERE id = ? AND (status = 'open' OR (status = 'claimed' AND claimed_at < ?))"
    update_cursor = db.execute(update_query, (now, intent_id, now - 60))

    if update_cursor.rowcount == 0:
        db.close()
        return '', 204

    db.commit()
    db.close()

    return jsonify({
        "id": row[0],
        "goal": row[1],
        "payload": json.loads(row[2]),
        "reward": row[3]
    }), 200

@app.route('/fulfill/<intent_id>', methods=['POST'])
def fulfill_intent(intent_id):
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401

    db = get_db()
    db.execute("UPDATE intents SET status = 'fulfilled' WHERE id = ?", (intent_id,))
    db.commit()
    db.close()
    return jsonify({"id": intent_id, "status": "fulfilled"}), 200

@app.route('/admin/purge', methods=['POST'])
def purge_queue():
    if not check_auth(): return jsonify({"error": "Unauthorized"}), 401

    db = get_db()
    db.execute("DELETE FROM intents")
    db.commit()
    db.close()
    return jsonify({"status": "Queue cleared"}), 200
