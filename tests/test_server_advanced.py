import os
import tempfile
import pytest
import hmac
import hashlib
import time
from unittest.mock import patch

# =================================================================
# SET ENVIRONMENT VARIABLES BEFORE IMPORT
# =================================================================

TEST_API_KEY = "admin_master_key"
TEST_ADMIN_TOKEN = "super_secret_admin_token"

os.environ["BUS_SECRET"] = TEST_API_KEY
os.environ["BUS_ADMIN_SECRET"] = TEST_ADMIN_TOKEN
os.environ["BUS_REQUIRE_SIGNATURES"] = "false"

import flask_app


@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp()

    flask_app.app.config["TESTING"] = True

    # Prevent accidental use of real infrastructure.db
    original_db_path = flask_app.DB_PATH
    flask_app.DB_PATH = db_path

    try:
        with flask_app.app.test_client() as test_client:
            with flask_app.app.app_context():
                flask_app.init_db()

            yield test_client

    finally:
        flask_app.app.config["TESTING"] = False
        flask_app.DB_PATH = original_db_path

        os.close(db_fd)

        if os.path.exists(db_path):
            os.unlink(db_path)


# =================================================================
# 1. CAPABILITY ROUTING TEST
# =================================================================

def test_capability_routing(client):
    headers = {"X-API-KEY": TEST_API_KEY}

    # Publish a job that strictly requires an 'ffmpeg' worker
    pub_res = client.post(
        "/intent",
        headers=headers,
        json={
            "goal": "process_video",
            "payload": {},
            "required_capability": "ffmpeg",
        },
    )

    assert pub_res.status_code == 201

    # Worker with NO capabilities
    res1 = client.post(
        "/claim?goal=process_video",
        headers=headers,
    )

    assert res1.status_code == 204

    # Worker with WRONG capability
    res2 = client.post(
        "/claim?goal=process_video",
        headers={
            "X-API-KEY": TEST_API_KEY,
            "X-Worker-Capabilities": "gpu,imagemagick",
        },
    )

    assert res2.status_code == 204

    # Worker with CORRECT capability
    res3 = client.post(
        "/claim?goal=process_video",
        headers={
            "X-API-KEY": TEST_API_KEY,
            "X-Worker-Capabilities": "cpu,ffmpeg,gpu",
        },
    )

    assert res3.status_code == 200
    assert res3.json["goal"] == "process_video"
    assert "claim_token" in res3.json


# =================================================================
# 2. RATE LIMITING TEST
# =================================================================

def test_rate_limiting(client):
    admin_headers = {"X-Admin-Token": TEST_ADMIN_TOKEN}

    # Generate restricted tester key
    res = client.post(
        "/admin/generate_key",
        headers=admin_headers,
        json={"owner": "ci_test"},
    )

    assert res.status_code == 201
    tester_key = res.json["api_key"]

    tester_headers = {
        "X-API-KEY": tester_key,
    }

    # Consume quota
    for _ in range(60):
        r = client.post(
            "/intent",
            headers=tester_headers,
            json={
                "goal": "spam",
                "payload": {},
            },
        )

        assert r.status_code == 201

    # 61st request must fail
    r_fail = client.post(
        "/intent",
        headers=tester_headers,
        json={
            "goal": "spam",
            "payload": {},
        },
    )

    assert r_fail.status_code == 429
    assert "Too many requests" in r_fail.json["error"]["message"]


# =================================================================
# 3. HMAC SIGNATURE & REPLAY TEST
# =================================================================

def test_cryptographic_signatures(client):
    timestamp = str(int(time.time()))
    nonce = "unique-nonce-001"

    body = b'{"goal":"secure_task","payload":{}}'

    canonical_path = "/intent"

    # Construct the canonical message for signing: METHOD\nPATH\nTS\nNONCE\nBODY
    msg = (
        f"POST\n{canonical_path}\n{timestamp}\n{nonce}\n".encode()
        + body
    )

    sig = hmac.new(
        TEST_API_KEY.encode(),
        msg,
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "X-API-KEY": TEST_API_KEY,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": sig,
    }

    # Valid signature
    res = client.post(
        "/intent",
        headers=headers,
        data=body,
        content_type="application/json",
    )

    assert res.status_code == 201

    # Replay attack: Same headers and body should be rejected
    res_replay = client.post(
        "/intent",
        headers=headers,
        data=body,
        content_type="application/json",
    )

    assert res_replay.status_code == 403
    assert "Replay detected" in res_replay.json["error"]["message"]

    # Forged signature: Change nonce but keep old signature
    headers["X-Nonce"] = "unique-nonce-002"
    headers["X-Signature"] = "deadbeef1234567890badsignature"

    res_bad = client.post(
        "/intent",
        headers=headers,
        data=body,
        content_type="application/json",
    )

    assert res_bad.status_code == 403
    assert "Bad signature" in res_bad.json["error"]["message"]


# =================================================================
# 4. DEAD LETTER & BACKOFF TEST
# =================================================================

@patch("flask_app.now")
def test_dead_letters_and_backoff(mock_now, client):
    current_time = time.time()
    mock_now.return_value = current_time

    headers = {"X-API-KEY": TEST_API_KEY}
    admin_headers = {"X-Admin-Token": TEST_ADMIN_TOKEN}

    # Publish job
    pub_res = client.post(
        "/intent",
        headers=headers,
        json={
            "goal": "fragile_task",
            "payload": {},
            "max_attempts": 2,
        },
    )

    # =========================================================
    assert pub_res.status_code == 201

    # ATTEMPT 1
    # =========================================================

    claim1 = client.post(
        "/claim?goal=fragile_task",
        headers=headers,
    )

    assert claim1.status_code == 200

    iid = claim1.json["id"]
    token1 = claim1.json["claim_token"]

    fail1 = client.post(
        f"/fail/{iid}",
        headers=headers,
        json={
            "claim_token": token1,
            "error": "first crash",
        },
    )

    assert fail1.status_code == 200

    # Advance mocked time beyond retry backoff
    current_time += 100
    mock_now.return_value = current_time

    # =========================================================
    # ATTEMPT 2
    # =========================================================

    claim2 = client.post(
        "/claim?goal=fragile_task",
        headers=headers,
    )

    assert claim2.status_code == 200

    token2 = claim2.json["claim_token"]

    # Ensure tokens are unique between attempts
    assert token1 != token2

    fail2 = client.post(
        f"/fail/{iid}",
        headers=headers,
        json={
            "claim_token": token2,
            "error": "fatal crash",
        },
    )

    assert fail2.status_code == 200

    # Advance mocked time again
    current_time += 100
    mock_now.return_value = current_time

    # Queue should now be empty
    claim3 = client.post(
        "/claim?goal=fragile_task",
        headers=headers,
    )

    assert claim3.status_code == 204

    # Verify dead letter archive
    dead_res = client.get(
        "/admin/dead",
        headers=admin_headers,
    )

    assert dead_res.status_code == 200
    assert len(dead_res.json) == 1
    assert dead_res.json[0]["reason"] == "fatal crash"


# =================================================================
# 5. ADMIN ISOLATION TEST
# =================================================================

def test_admin_isolation(client):
    # Generate standard tester key
    res = client.post(
        "/admin/generate_key",
        headers={
            "X-Admin-Token": TEST_ADMIN_TOKEN,
        },
        json={
            "owner": "hacker",
        },
    )

    assert res.status_code == 201
    tester_key = res.json["api_key"]

    # Attempt admin action using normal API key
    hacker_res = client.post(
        "/admin/purge",
        headers={
            "X-API-KEY": tester_key,
        },
        json={
            "confirm": True,
        },
    )

    assert hacker_res.status_code == 401
    assert "Authentication required" in hacker_res.text


# =================================================================
# 6. CROSS-ATTEMPT TOKEN ISOLATION TEST
# =================================================================

@patch("flask_app.now")
def test_cross_attempt_token_isolation(mock_now, client):
    current_time = time.time()
    mock_now.return_value = current_time

    headers = {"X-API-KEY": TEST_API_KEY}

    # Step 1: Publish an intent
    pub_res = client.post(
        "/intent",
        headers=headers,
        json={
            "goal": "retry_task",
            "payload": {"data": "test"},
        },
    )

    assert pub_res.status_code == 201
    iid = pub_res.json["id"]

    # Step 2: Claim it once and save token1
    claim1_res = client.post(
        "/claim?goal=retry_task",
        headers=headers,
    )

    assert claim1_res.status_code == 200
    assert claim1_res.json["id"] == iid
    token1 = claim1_res.json["claim_token"]

    # Step 3: Fail it to return to queue
    fail_res = client.post(
        f"/fail/{iid}",
        headers=headers,
        json={
            "claim_token": token1,
            "error": "temporary failure",
        },
    )

    assert fail_res.status_code == 200

    # Advance mocked time beyond retry backoff
    current_time += 100
    mock_now.return_value = current_time

    # Step 4: Claim it again and save token2
    claim2_res = client.post(
        "/claim?goal=retry_task",
        headers=headers,
    )

    assert claim2_res.status_code == 200
    assert claim2_res.json["id"] == iid
    token2 = claim2_res.json["claim_token"]

    assert token1 != token2

    # Step 5: Attempt to fulfill using token1 (should fail with 404)
    fulfill_old_token_res = client.post(
        f"/fulfill/{iid}",
        headers=headers,
        json={
            "claim_token": token1,
            "result": "completed with old token",
            "result_type": "text",
        },
    )

    assert fulfill_old_token_res.status_code == 404
    assert "not found" in fulfill_old_token_res.json["error"]["message"].lower()

    # Step 6: Fulfill using token2 (should succeed)
    fulfill_res = client.post(
        f"/fulfill/{iid}",
        headers=headers,
        json={
            "claim_token": token2,
            "result": "completed with valid token",
            "result_type": "text",
        },
    )

    assert fulfill_res.status_code == 200

    # Verify the intent is fulfilled
    status_res = client.get(
        f"/status/{iid}",
        headers=headers,
    )

    assert status_res.status_code == 200
    assert status_res.json["status"] == "fulfilled"
