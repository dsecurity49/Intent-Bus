# Intent Bus

POST a job → any script (anywhere) can execute it.

A tiny, headless, SQLite-backed intent bus for cross-device script coordination. I built this so my background scrapers could reliably ping my phone in Termux without needing Firebase or heavy message queues.

It uses a primitive atomic lock and topic routing. Hosted on PythonAnywhere.

### How it works

**1. Push an Intent:**
```bash
curl -X POST "https://dsecurity.pythonanywhere.com/intent" \
  -H "Content-Type: application/json" \
  -d '{"goal":"send_notification","payload":{"message":"Hello World"}}'
```

---

**2. Claim an Intent (Worker):**

Workers pull specific goals. The system locks the job so only one worker executes it.

```bash
curl -s -X POST "https://dsecurity.pythonanywhere.com/claim?goal=send_notification"
```

Example response:

```json
{
  "id": "abc123",
  "goal": "send_notification",
  "payload": {
    "message": "Hello World"
  }
}
```

---

**3. Fulfill an Intent:**

```bash
curl -s -X POST "https://dsecurity.pythonanywhere.com/fulfill/<INTENT_ID>"
```

---

*Note: If an intent is claimed but not fulfilled within 60 seconds, the lock expires and it goes back into the queue.*

---

If you find a use case for this, I’d love to hear it.
