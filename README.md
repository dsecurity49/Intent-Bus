# Intent Bus

POST a job → any script (anywhere) can execute it.

A tiny, headless, SQLite-backed intent bus for cross-device script coordination. I built this so my background scrapers could reliably ping my phone in Termux without needing Firebase or heavy message queues.

It uses a primitive atomic lock and topic routing. Hosted on PythonAnywhere.

### Security Setup (Required)
This API is secured to prevent unauthorized access.
1. **Server-side:** Set a `BUS_SECRET` environment variable on your host (e.g., in your WSGI file).
2. **Client-side:** Pass this secret using the `X-API-KEY` header for all requests. Local worker scripts should read this from a local `.apikey` file (which must be added to your `.gitignore`).

---

### How it works

**1. Push an Intent:**
```bash
curl -X POST "[https://dsecurity.pythonanywhere.com/intent](https://dsecurity.pythonanywhere.com/intent)" \
  -H "X-API-KEY: your_secret_here" \
  -H "Content-Type: application/json" \
  -d '{"goal":"send_notification","payload":{"message":"Hello World"}}'
```

---

**2. Claim an Intent (Worker):**

Workers pull specific goals. The system locks the job so only one worker executes it.

```bash
curl -s -X POST "[https://dsecurity.pythonanywhere.com/claim?goal=send_notification](https://dsecurity.pythonanywhere.com/claim?goal=send_notification)" \
  -H "X-API-KEY: your_secret_here"
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
curl -s -X POST "[https://dsecurity.pythonanywhere.com/fulfill/](https://dsecurity.pythonanywhere.com/fulfill/)<INTENT_ID>" \
  -H "X-API-KEY: your_secret_here"
```

---

*Note: If an intent is claimed but not fulfilled within 60 seconds, the lock expires and it goes back into the queue for another worker to pick up.*

---

If you find a use case for this, I’d love to hear it.
