# Spectarr webhook worker

This service delivers Spectarr outbox events to configured webhook destinations. It uses an atomic backend lease, signs the exact claimed body, and reports the result to the backend. Delivery is at least once. Receivers must use the delivery ID for idempotency.

The worker uses only the Python standard library at runtime and runs as an unprivileged process inside the Spectarr container.

## Delivery flow

1. Poll pending and due retry deliveries.
2. Claim one delivery with a worker ID. The backend grants a time-limited lease.
3. Validate that the destination uses HTTPS and resolves only to public addresses, unless either policy was explicitly relaxed for local development.
4. Sign and POST the exact UTF-8 body returned by the claim API.
5. Classify the response and report `delivered`, `retry`, or `failed` to the backend.

The backend owns retry scheduling. A worker crash can leave a delivery leased until its lease expires. Another worker can then claim it. This is why receivers must treat `X-Spectarr-Delivery` as an idempotency key.

## Authentication

Configure exactly one of these options:

- `SPECTARR_SERVICE_TOKEN`: A bearer token with `jobs:read` and `jobs:write` scopes.
- `SPECTARR_WORKER_TOKEN`: The legacy shared worker token sent through `X-Spectarr-Worker-Token`.

The worker ID is sent with claim and result requests in `X-Spectarr-Worker-Id`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPECTARR_URL` | `http://127.0.0.1:8000` | Spectarr API base URL |
| `SPECTARR_SERVICE_TOKEN` | none | Preferred scoped service bearer token |
| `SPECTARR_WORKER_TOKEN` | none | Legacy worker token |
| `SPECTARR_WORKER_ID` | hostname | Stable identity for lease ownership |
| `SPECTARR_WEBHOOK_POLL_SECONDS` | `3` | Idle polling interval |
| `SPECTARR_WEBHOOK_TIMEOUT_SECONDS` | `15` | Bounded API and destination timeout, maximum 120 |
| `SPECTARR_WEBHOOK_BATCH_SIZE` | `25` | Maximum candidates read per status, maximum 100 |
| `SPECTARR_WEBHOOK_MAX_RESPONSE_BYTES` | `65536` | Bounded response read, maximum 1 MiB |
| `SPECTARR_WEBHOOK_ALLOW_HTTP` | `false` | Permit plain HTTP destinations for local development |
| `SPECTARR_WEBHOOK_ALLOW_PRIVATE_NETWORKS` | `false` | Permit private, loopback, link-local, and reserved destination addresses for local development |

Run locally:

```bash
python -m pip install -e .
export SPECTARR_URL=http://localhost:8000
export SPECTARR_SERVICE_TOKEN=replace-me
export SPECTARR_WORKER_ID=webhooks-local-1
spectarr-webhook-worker
```

Process at most one ready delivery with `spectarr-webhook-worker --once`. Set logging with `--log-level DEBUG`.

The standard Spectarr image starts this worker automatically.

## Signature contract

Each destination receives these headers:

- `Content-Type: application/json`
- `X-Spectarr-Event: <event topic>`
- `X-Spectarr-Delivery: <delivery id>`
- `X-Spectarr-Timestamp: <unix timestamp>`
- `X-Spectarr-Signature: t=<unix timestamp>,v1=<hex HMAC>`

The HMAC input is the ASCII timestamp, one period byte, then the exact request body bytes. The algorithm is HMAC-SHA256. The key is the destination's `whsec_` secret. Do not parse and reserialize the body before verification.

Receiver verification example:

```python
import hashlib
import hmac
import time


def verify(body: bytes, timestamp: str, signature: str, secret: str) -> bool:
    if abs(time.time() - int(timestamp)) > 300:
        return False
    supplied = dict(part.split("=", 1) for part in signature.split(","))
    if supplied.get("t") != timestamp:
        return False
    signed = timestamp.encode("ascii") + b"." + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied.get("v1", ""), expected)
```

Verify the signature before decoding JSON. Also reject stale timestamps and deduplicate the delivery ID.

## Result policy

- Any 2xx response is delivered.
- HTTP 408, 425, 429, and 5xx responses are retried.
- Redirects and other 3xx or 4xx responses are terminal failures.
- Network and timeout errors are retried.
- Unsafe URLs and invalid claimed bodies are terminal failures.

Redirects are never followed. Destination credentials, URL fragments, control characters, non-public addresses, and schemes other than HTTPS are rejected. HTTP and private-network targets can only be enabled explicitly.

## Tests

```bash
python -m pytest -q
```
