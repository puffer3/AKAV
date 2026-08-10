"""Chunked upload to the AKAV Apps Script endpoint.

Uses urllib (stdlib) — no external HTTP dependency. Apps Script answers a
POST with a 302 to script.googleusercontent.com; urllib follows it and the
redirected GET returns the JSON body, so unlike the browser's no-cors
fetch we get real per-chunk acks.
"""

import json
import ssl
import time
import urllib.parse
import urllib.request
import uuid

CHUNK_SIZE = 150
BACKOFFS = [2, 8, 30]


def _ssl_context():
    """python.org macOS builds ship without root CAs wired up — use
    certifi's bundle when the default trust store can't verify."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_CTX = _ssl_context()


def _post_json(url, payload, timeout=120):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "text/plain;charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout, context=_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _with_retries(fn, what):
    last = None
    for attempt, delay in enumerate([0] + BACKOFFS):
        if delay:
            print("  retrying %s in %ds... (last error: %s)" % (what, delay, last))
            time.sleep(delay)
        try:
            result = fn()
            if isinstance(result, dict) and result.get("ok") is False:
                err = str(result.get("error", ""))
                if "lock" in err.lower() or "timeout" in err.lower():
                    last = RuntimeError(err)
                    continue          # transient — retry
                raise RuntimeError("endpoint error: %s" % err)
            return result
        except RuntimeError:
            raise
        except Exception as e:          # network / HTTP 5xx
            last = e
    raise RuntimeError("%s failed after retries: %s" % (what, last))


def fetch_roster(endpoint, token):
    url = "%s?%s" % (endpoint, urllib.parse.urlencode(
        {"action": "roster", "token": token}))
    data = _with_retries(lambda: _get_json(url), "roster fetch")
    if not data.get("ok"):
        raise RuntimeError("roster: %s" % data.get("error"))
    return data.get("roster", [])


def upload_contacts(contacts, endpoint, token, chunk_size=100):
    """Chunked contact upsert (rolly import). Returns receipt dict."""
    chunks = [contacts[i:i + chunk_size]
              for i in range(0, len(contacts), chunk_size)] or [[]]
    batch_id = str(uuid.uuid4())
    receipt = {"batchId": batch_id, "endpoint": endpoint, "chunks": []}
    for ci, chunk in enumerate(chunks):
        payload = {
            "type": "contactImport",
            "token": token,
            "batchId": batch_id,
            "chunkIndex": ci,
            "chunkCount": len(chunks),
            "contacts": [
                {"name": c["name"], "email": c["email"],
                 "phoneDigits": c["phoneDigits"], "city": c["city"],
                 "notes": c.get("noteText", ""),
                 "grade": c.get("grade", ""),
                 "lists": c.get("sheets", [])}
                for c in chunk],
        }
        print("  chunk %d/%d: %d contacts..." % (ci + 1, len(chunks), len(chunk)))
        ack = _with_retries(
            lambda: _post_json(endpoint, payload), "chunk %d" % (ci + 1))
        print("    ack: matched=%s created=%s cityWritten=%s cityKept=%s"
              % (ack.get("matched"), ack.get("created"),
                 ack.get("cityWritten"), ack.get("cityKept")))
        receipt["chunks"].append(ack)
    return receipt


def upload(batch, endpoint, token, chunk_size=CHUNK_SIZE):
    """Sequential chunked upload. Returns receipt dict."""
    records = batch["records"]
    chunks = [records[i:i + chunk_size]
              for i in range(0, len(records), chunk_size)] or [[]]
    batch_id = str(uuid.uuid4())
    receipt = {"batchId": batch_id, "endpoint": endpoint,
               "sourceFile": batch["sourceFile"],
               "show": batch["show"], "chunks": []}

    for ci, chunk in enumerate(chunks):
        final = ci == len(chunks) - 1
        payload = {
            "type": "jobImport",
            "token": token,
            "batchId": batch_id,
            "chunkIndex": ci,
            "chunkCount": len(chunks),
            "finalize": final,
            "sourceFile": batch["sourceFile"],
            "show": batch["show"],
            "records": chunk,
        }
        if final:
            # people meta (grades, notes, fallback totals) ride the
            # finalize chunk — that's when the summary recompute runs
            payload["people"] = [
                {k: p.get(k) for k in
                 ("personKey", "name", "email", "phoneDigits",
                  "grade", "notes", "total")}
                for p in batch["people"]]

        print("  chunk %d/%d: %d records%s..."
              % (ci + 1, len(chunks), len(chunk),
                 " + finalize" if final else ""))
        ack = _with_retries(
            lambda: _post_json(endpoint, payload),
            "chunk %d" % (ci + 1))
        print("    ack: appended=%s skipped=%s%s"
              % (ack.get("appended"), ack.get("skipped"),
                 (" matched=%s created=%s" % (ack.get("peopleMatched"),
                                              ack.get("peopleCreated")))
                 if final else ""))
        receipt["chunks"].append(ack)
    return receipt
