# poller.py — the core loop: ask Convex whether there's a pending print
# job, stream its raster bytes straight through to the target printer,
# and report back what happened. There is no inbound networking anywhere
# in this file — every call here is outbound, so this device needs no
# open port, no port-forward, and no tunnel. See
# docs/TDA_APP_INTEGRATION.md for the Convex-side contract this assumes.

import ujson as json

import httpclient
from printjobs import PrinterConnection


class PollError(Exception):
    pass


def _headers(api_key, extra=None):
    h = {"X-Api-Key": api_key}
    if extra:
        h.update(extra)
    return h


def fetch_pending_job(base_url, api_key, feed=None):
    """Returns a job dict, or None if nothing is currently pending."""
    if feed:
        feed()
    status, _headers_out, body = httpclient.request(
        "GET", base_url + "/pico/pending-jobs", headers=_headers(api_key)
    )
    if status == 204:
        return None
    if status != 200:
        raise PollError("pending-jobs returned HTTP %d" % status)
    try:
        return json.loads(body)
    except Exception:
        raise PollError("pending-jobs returned invalid JSON")


def run_job(base_url, api_key, job, printers, feed=None):
    """Fetches the job's content and streams it straight to the target
    printer. Raises PollError for anything that should be reported back
    as state="error"."""
    printer_id = job.get("printerId")
    printer = printers.get(printer_id)
    if printer is None:
        raise PollError("unknown printerId %r" % (printer_id,))

    content_url = "%s/pico/jobs/%s/content" % (base_url, job["id"])

    if feed:
        feed()

    with PrinterConnection(printer["ip"], printer["port"]) as conn:
        def _write_and_feed(chunk):
            conn.write(chunk)
            if feed:
                feed()  # chunks arrive steadily during a transfer, which
                # is what keeps the ~8s hardware watchdog fed through a
                # job that takes longer than that to send end-to-end.

        status = httpclient.get_stream(content_url, _headers(api_key), on_chunk=_write_and_feed)

    if status != 200:
        raise PollError("job content fetch returned HTTP %d" % status)


def report_complete(base_url, api_key, job_id, state, error=None, feed=None):
    if feed:
        feed()
    payload = {"state": state}
    if error is not None:
        payload["error"] = str(error)[:500]
    body = json.dumps(payload).encode()
    status, _h, _b = httpclient.request(
        "POST",
        "%s/pico/jobs/%s/complete" % (base_url, job_id),
        headers=_headers(api_key, {"Content-Type": "application/json"}),
        body=body,
    )
    return status
