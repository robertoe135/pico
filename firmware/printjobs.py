# printjobs.py — validates incoming print job requests and relays them to
# the target printer's raw TCP port.
#
# Deliberately dumb, matching what PrintNode itself actually does: job
# `content` is expected to already be fully-formed, printer-ready bytes
# (Brother QL raster-mode commands for the QL-810W) — this server does not
# parse or re-encode it. See docs/BROTHER_PROTOCOL.md for why that
# encoding step belongs off-device, in a proven library, rather than
# reimplemented here from scratch.

import time
import ubinascii as binascii
import ujson as json
import uasyncio as asyncio

_MAX_JOBS_KEPT = 50
_SEND_TIMEOUT_S = 10

_next_id = 1
_jobs = {}


class JobError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _remember(record):
    _jobs[record["id"]] = record
    if len(_jobs) > _MAX_JOBS_KEPT:
        oldest_id = min(_jobs)
        if oldest_id != record["id"]:
            _jobs.pop(oldest_id, None)


def get_job(job_id):
    return _jobs.get(job_id)


async def submit_job(printers, body):
    global _next_id

    try:
        job = json.loads(body)
    except Exception:
        raise JobError(400, "invalid JSON body")

    printer_id = job.get("printerId")
    content_b64 = job.get("content")
    content_type = job.get("contentType", "raw_base64")
    title = job.get("title", "")

    if not printer_id or printer_id not in printers:
        raise JobError(404, "unknown printerId %r" % (printer_id,))
    if content_type != "raw_base64":
        raise JobError(
            400,
            "unsupported contentType %r (only \"raw_base64\" is supported — "
            "pre-encode the Brother raster job before sending it)" % (content_type,),
        )
    if not content_b64:
        raise JobError(400, "missing content")

    try:
        raw = binascii.a2b_base64(content_b64)
    except Exception:
        raise JobError(400, "content is not valid base64")

    if len(raw) == 0:
        raise JobError(400, "content decodes to zero bytes")

    job_id = _next_id
    _next_id += 1
    record = {
        "id": job_id,
        "printerId": printer_id,
        "title": title,
        "bytes": len(raw),
        "state": "sending",
        "createdAt": time.time(),
        "error": None,
    }
    _remember(record)

    printer = printers[printer_id]
    try:
        await _send_to_printer(printer["ip"], printer["port"], raw)
    except Exception as e:
        record["state"] = "error"
        record["error"] = str(e)
        _remember(record)
        raise JobError(502, "failed to reach printer %r: %s" % (printer_id, e))

    record["state"] = "sent"
    _remember(record)
    return record


async def _send_to_printer(ip, port, data):
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ip, port), _SEND_TIMEOUT_S
    )
    try:
        writer.write(data)
        await asyncio.wait_for(writer.drain(), _SEND_TIMEOUT_S)
    finally:
        try:
            writer.close()
        except Exception:
            pass
