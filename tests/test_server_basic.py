import importlib
import os
import tempfile

import pytest

TEST_API_KEY = "test_secure_key"
TEST_METRICS_TOKEN = "test_prometheus_token"
TEST_ADMIN_TOKEN = "test_admin_secret"
TEST_DASHBOARD_PASSWORD = "test_dashboard_pass"


@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp()

    old_env = {
        "BUS_SECRET": os.environ.get("BUS_SECRET"),
        "BUS_ADMIN_SECRET": os.environ.get("BUS_ADMIN_SECRET"),
        "DASHBOARD_PASSWORD": os.environ.get("DASHBOARD_PASSWORD"),
        "BUS_METRICS_TOKEN": os.environ.get("BUS_METRICS_TOKEN"),
        "BUS_REQUIRE_SIGNATURES": os.environ.get("BUS_REQUIRE_SIGNATURES"),
        "BUS_ENFORCE_HTTPS": os.environ.get("BUS_ENFORCE_HTTPS"),
        "BUS_DB_PATH": os.environ.get("BUS_DB_PATH"),
        "BUS_TRUST_PROXY": os.environ.get("BUS_TRUST_PROXY"),
    }

    os.environ["BUS_SECRET"] = TEST_API_KEY
    os.environ["BUS_ADMIN_SECRET"] = TEST_ADMIN_TOKEN
    os.environ["DASHBOARD_PASSWORD"] = TEST_DASHBOARD_PASSWORD
    os.environ["BUS_METRICS_TOKEN"] = TEST_METRICS_TOKEN
    os.environ["BUS_REQUIRE_SIGNATURES"] = "false"
    os.environ["BUS_ENFORCE_HTTPS"] = "false"
    os.environ["BUS_DB_PATH"] = db_path

    import flask_app
    importlib.reload(flask_app)

    flask_app.app.config["TESTING"] = True

    try:
        with flask_app.app.test_client() as test_client:
            with flask_app.app.app_context():
                flask_app.init_db()

            yield test_client

    finally:
        flask_app.app.config["TESTING"] = False

        os.close(db_fd)

        if os.path.exists(db_path):
            os.unlink(db_path)

        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

        importlib.reload(flask_app)


def test_health_check(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json["ok"] is True
    assert response.json["version"] == "7.61"


def test_unauthorized_access(client):
    response = client.post(
        "/intent",
        json={"goal": "test", "payload": {}},
    )

    assert response.status_code == 401
    assert "Missing API key" in response.json["error"]["message"]


def test_publish_and_claim_lifecycle(client):
    headers = {"X-API-KEY": TEST_API_KEY}

    pub_res = client.post(
        "/intent",
        headers=headers,
        json={
            "goal": "process_image",
            "payload": {"url": "http://example.com/image.png"},
            "namespace": "media",
        },
    )

    assert pub_res.status_code == 201

    intent_id = pub_res.json["id"]

    claim_res = client.post(
        "/claim?goal=process_image&namespace=media",
        headers=headers,
    )

    assert claim_res.status_code == 200
    assert claim_res.json["id"] == intent_id
    assert claim_res.json["payload"]["url"] == "http://example.com/image.png"

    claim_token = claim_res.json["claim_token"]

    assert claim_token

    empty_claim = client.post(
        "/claim?goal=process_image&namespace=media",
        headers=headers,
    )

    assert empty_claim.status_code == 204

    fulfill_res = client.post(
        f"/fulfill/{intent_id}",
        headers=headers,
        json={
            "claim_token": claim_token,
            "result": {"status": "done"},
            "result_type": "json",
        },
    )

    assert fulfill_res.status_code == 200


def test_claim_token_required(client):
    headers = {"X-API-KEY": TEST_API_KEY}

    pub_res = client.post(
        "/intent",
        headers=headers,
        json={
            "goal": "secure_job",
            "payload": {"task": "test"},
        },
    )

    assert pub_res.status_code == 201

    intent_id = pub_res.json["id"]

    claim_res = client.post(
        "/claim?goal=secure_job",
        headers=headers,
    )

    assert claim_res.status_code == 200

    fulfill_res = client.post(
        f"/fulfill/{intent_id}",
        headers=headers,
        json={
            "result": "done",
            "result_type": "text",
        },
    )

    assert fulfill_res.status_code in (400, 403)


def test_invalid_claim_token_rejected(client):
    headers = {"X-API-KEY": TEST_API_KEY}

    pub_res = client.post(
        "/intent",
        headers=headers,
        json={
            "goal": "secure_job",
            "payload": {"task": "test"},
        },
    )

    intent_id = pub_res.json["id"]

    claim_res = client.post(
        "/claim?goal=secure_job",
        headers=headers,
    )

    assert claim_res.status_code == 200

    fulfill_res = client.post(
        f"/fulfill/{intent_id}",
        headers=headers,
        json={
            "claim_token": "invalid_token",
            "result": "done",
            "result_type": "text",
        },
    )

    assert fulfill_res.status_code == 404


def test_idempotency_keys(client):
    headers = {
        "X-API-KEY": TEST_API_KEY,
        "Idempotency-Key": "test-key-123",
    }

    payload = {
        "goal": "test_idempotency",
        "payload": {"data": 1},
    }

    res1 = client.post(
        "/intent",
        headers=headers,
        json=payload,
    )

    assert res1.status_code == 201

    res2 = client.post(
        "/intent",
        headers=headers,
        json=payload,
    )

    assert res2.status_code == 201
    assert res1.json["id"] == res2.json["id"]

    modified_payload = {
        "goal": "test_idempotency",
        "payload": {"data": 2},
    }

    res3 = client.post(
        "/intent",
        headers=headers,
        json=modified_payload,
    )

    assert res3.status_code == 422
    assert "Key reused with different payload" in res3.json["error"]["message"]


def test_metrics_endpoint(client):
    res1 = client.get("/metrics")

    assert res1.status_code == 401

    res2 = client.get(
        "/metrics",
        headers={
            "Authorization": f"Bearer {TEST_METRICS_TOKEN}"
        },
    )

    assert res2.status_code == 200
    assert "intent_bus_intents_total" in res2.text
