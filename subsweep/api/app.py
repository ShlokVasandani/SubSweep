"""SubSweep web API + static frontend.

Everything the UI shows is computed here from the in-memory analysis; nothing here cancels a subscription or sends a message.
Statements never touch disk (only the user's dismiss / "still using" decisions are stored, in SQLite).
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import secrets
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from subsweep import __version__
from subsweep.ai import AIAssistant, AuditLog, build_default_llm
from subsweep.demo import demo_statement_csv
from subsweep.ingest import FormatDetectionError, StatementTooLargeError, load_statement
from subsweep.ingest.formats import ColumnMapping, decode_bytes, detect_format, read_rows, sniff_delimiter
from subsweep.paypal import current_mode
from subsweep.store import Store

from .analysis import Analysis, build_analysis
from .decisions import COOKIE as DECISIONS_COOKIE
from .decisions import CookieDecisions
from .serialize import jsonable

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_EXT = {".csv", ".txt", ".tsv"}
MAX_SESSIONS = 12
MAX_AI_CALLS_PER_ANALYSIS = 300
COOKIE = "subsweep_session"
HOSTED_MAX_UPLOAD_BYTES = 4 * 1024 * 1024    # a serverless host caps a request at about 4.5 MB, and a statement is sent with every action
HOSTED_SAMPLE_ONLY_BODY = 64 * 1024            # when uploads are switched off the only request bodies are tiny forms and JSON
HOSTED_MAX_AI_CALLS = 500                      # per server instance; the hosted build uses built-in text unless the operator turns the model on
HOSTED_CACHE_SIZE = 2                          # analyses kept warm per server instance; the browser can always re-send its statement
STATELESS_HEADER = b"x-subsweep-stateless"
METHOD_HEADER = b"x-subsweep-method"
HOSTED_MESSAGE = ("Uploads are switched off on this hosted demo, so nothing of yours is sent anywhere. Try the sample statement, "
                  "or run SubSweep on your own computer to analyse your own statement: it never leaves your machine.")
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; font-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")
_BINARY_MAGIC = (b"%PDF", b"PK\x03\x04", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"MZ", b"\x7fELF", b"\xd0\xcf\x11\xe0")


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Cuts off oversized request bodies while they stream in, so a huge upload is never written to disk or held in memory before the
    friendly size check in /api/analyze gets to run. Checks Content-Length up front and counts bytes for chunked bodies."""

    def __init__(self, app, max_bytes: int, *, code: str = "too_large", message: str | None = None):
        self.app, self.max_bytes, self.code = app, max_bytes, code
        self.message = message or (f"That's more than a statement should need (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB). "
                                   "Try exporting a shorter date range.")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length", b"")

        async def reject():
            body = json.dumps({"error": {"code": self.code, "message": self.message, "details": {}}}).encode()
            await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                                                                  (b"connection", b"close"), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": body})

        if declared.isdigit() and int(declared) > self.max_bytes:
            return await reject()
        seen, tripped, replied = 0, False, False

        async def limited_receive():
            nonlocal seen, tripped
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max_bytes:
                    tripped = True
                    raise _TooLarge()
            return msg

        async def guarded_send(msg):
            nonlocal replied
            if tripped:                      # the app may have turned our exception into its own 400/500; the client gets one clear 413 instead
                if not replied:
                    replied = True
                    await reject()
                return
            await send(msg)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _TooLarge:
            pass
        if tripped and not replied:
            await reject()


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class Dismiss(BaseModel):
    reason: Literal["dismissed", "still_using"] = "dismissed"


def _check_upload(name: str, data: bytes, limit: int | None = None) -> None:
    limit = MAX_UPLOAD_BYTES if limit is None else limit          # read at call time, not definition time
    ext = Path(name or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ApiError(415, "wrong_file_type", "That doesn't look like a statement export. Please choose a .csv file (a .txt or .tsv export also works).")
    if len(data) > limit:
        raise ApiError(413, "too_large", f"That file is over {limit // (1024 * 1024)} MB, which is more than a statement should need. Try exporting a shorter date range.")
    if not data.strip():
        raise ApiError(400, "empty_file", "That file looks empty. Please export your statement again and choose the CSV option.")
    if b"\x00" in data[:4096] or data.startswith(_BINARY_MAGIC):
        raise ApiError(415, "wrong_file_type", "That file looks like a binary file (such as a PDF or spreadsheet), not a CSV. Please export a CSV from your bank or PayPal.")


def build_demo_analysis() -> Analysis:
    """The sample statement's analysis. Deterministic (fixed seed, dates relative to the last charge), so any server instance can rebuild it."""
    statement = load_statement(demo_statement_csv(), None, default_currency="USD", source_label="demo")
    source = {"kind": "demo", "filename": "sample statement", "format": statement.format, "rows": int(len(statement.transactions)),
              "warnings": statement.warnings[:20], "date_order": statement.date_order, "dropped": statement.dropped}
    return build_analysis("demo", statement, source, {}, demo=True)


def parse_mapping(raw: str | None) -> dict | None:
    """The `mapping` form field is JSON; anything but an object is a friendly 400."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("mapping must be an object")
    except (ValueError, TypeError):
        raise ApiError(400, "bad_mapping", "The column choices couldn't be read. Please pick them again.") from None
    return parsed


def analysis_from_text(text: str, source: dict, *, demo: bool, mapping: dict | None, invert_sign: bool, currency: str, use_paypal: bool,
                       dismissals: dict[str, str], analysis_id: str) -> Analysis:
    """Statement text -> Analysis. Shared by the normal upload route and the hosted build, so both behave identically."""
    warnings: list[str] = []
    try:
        col_map = None
        if mapping is not None:
            try:
                col_map = ColumnMapping.from_dict(mapping)
            except (ValueError, TypeError):
                raise ApiError(400, "bad_mapping", "The column choices couldn't be read. Please pick them again.") from None
            col_map.invert_sign = col_map.invert_sign or invert_sign
        elif invert_sign:
            det = detect_format(read_rows(text, sniff_delimiter(text)), sniff_delimiter(text))
            if det.ok:
                col_map = det.mapping
                col_map.invert_sign = True
        statement = load_statement(text, col_map, default_currency=currency.upper(), source_label=source["kind"] if demo else None)
    except FormatDetectionError as exc:
        raise ApiError(422, "needs_mapping", str(exc), {"headers": exc.headers, "suggestions": exc.suggestions}) from None
    except StatementTooLargeError as exc:
        raise ApiError(413, "too_large", str(exc)) from None
    except (csv.Error, UnicodeError):
        raise ApiError(422, "unreadable_file", "We couldn't read that file as a CSV (a cell is far too long, or the text encoding is broken). "
                       "Please export the statement again as a plain CSV.") from None
    if len(statement.transactions) == 0:
        raise ApiError(422, "no_transactions", "We couldn't find any transactions in that file. Check it's a statement export with dates and amounts.",
                       {"warnings": statement.warnings})
    warnings += statement.warnings
    source.update({"format": statement.format, "rows": int(len(statement.transactions)), "warnings": warnings[:20],
                   "date_order": statement.date_order, "dropped": statement.dropped})
    return build_analysis(analysis_id, statement, source, dismissals, demo=demo, use_paypal=use_paypal)


def parse_options(raw: object) -> dict:
    """The browser's description of the statement it is re-sending. Validated, because it arrives on a public endpoint."""
    try:
        o = json.loads(raw) if isinstance(raw, str) and raw else {}
    except ValueError:
        o = None
    bad = ApiError(400, "bad_request", "That request didn't look right. Please reload the page and try again.")
    if not isinstance(o, dict):
        raise bad
    mapping = o.get("mapping")
    cur = o.get("default_currency", "USD")
    if (mapping is not None and not isinstance(mapping, dict)) or not isinstance(cur, str) or len(cur) != 3 or not cur.isalpha():
        raise bad
    return {"demo": o.get("demo") is True, "mapping": mapping, "invert_sign": o.get("invert_sign") is True,
            "default_currency": cur.upper(), "filename": Path(str(o.get("filename") or "statement.csv")).name[:120]}


def cache_key(data: bytes, options: dict) -> str:
    meta = {k: options.get(k) for k in ("mapping", "invert_sign", "default_currency", "filename")}
    return hashlib.sha256(data + b"\0" + json.dumps(meta, sort_keys=True, default=str).encode()).hexdigest()


class StatelessMiddleware:
    """Hosted build only. A serverless host may answer every request from a different instance, so nothing about a visitor can live on the
    server. The browser keeps the statement and re-sends it with each action as a multipart POST (`statement`, `options`, and the original JSON
    body as `json`) carrying X-SubSweep-Stateless and X-SubSweep-Method. This turns that back into the ordinary request the routes expect and
    hands them the statement through `request.state.stateless`, so the routes themselves do not change. The custom headers also mean a
    cross-site page cannot make this request without a CORS preflight."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (scope["type"] != "http" or not any(k == STATELESS_HEADER for k, _ in scope["headers"])
                or not path.startswith("/api/") or path.startswith("/api/analyze")):
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        try:
            method = (request.headers.get(METHOD_HEADER.decode()) or "POST").upper()
            if method not in ("GET", "POST", "DELETE"):
                raise ApiError(400, "bad_request", "That request didn't look right. Please reload the page and try again.")
            form = await request.form()
            options = parse_options(form.get("options"))
            statement = form.get("statement")
            data = await statement.read() if hasattr(statement, "read") else None
            payload = form.get("json")
            payload = payload if isinstance(payload, str) else ""
        except ApiError as exc:
            return await JSONResponse({"error": {"code": exc.code, "message": exc.message, "details": exc.details}}, status_code=exc.status)(scope, receive, send)
        except Exception:
            return await JSONResponse({"error": {"code": "bad_request", "message": "That request didn't look right. Please reload the page and try again.", "details": {}}},
                                      status_code=400)(scope, receive, send)
        state = dict(scope.get("state") or {})
        if options["demo"] or data:
            state["stateless"] = {"demo": options["demo"], "data": data, "options": options}
        body = payload.encode()
        skip = {b"content-type", b"content-length", STATELESS_HEADER, METHOD_HEADER}
        headers = [(k, v) for k, v in scope["headers"] if k not in skip] + [(b"content-length", str(len(body)).encode())]
        if body:
            headers.append((b"content-type", b"application/json"))
        sent = False

        async def new_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app({**scope, "method": method, "headers": headers, "state": state}, new_receive, send)


class Sessions:
    def __init__(self):
        self._d: OrderedDict[str, Analysis] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, sid: str, a: Analysis) -> None:
        with self._lock:
            self._d[sid] = a
            self._d.move_to_end(sid)
            while len(self._d) > MAX_SESSIONS:
                self._d.popitem(last=False)

    def get(self, sid: str | None) -> Analysis | None:
        with self._lock:
            return self._d.get(sid) if sid else None

    def drop(self, sid: str | None) -> None:
        with self._lock:
            self._d.pop(sid, None)


def create_app(db_path: str | Path | None = None, ai: AIAssistant | None = None, audit_path: str | Path | None = None, *,
               hosted: bool = False, uploads: bool = True) -> FastAPI:
    """`hosted=True` is the build for a serverless host (see app.py and the README). Every request may be served by a different instance,
    so nothing about a visitor lives on the server: the browser keeps the statement and re-sends it with each action (see
    StatelessMiddleware), the server rebuilds the analysis in memory, keeps a couple warm, and writes nothing to disk. Each visitor's dismiss
    decisions live in their own cookie. `uploads=False` makes it a sample-data-only demo."""
    app = FastAPI(title="SubSweep", version=__version__, docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    store = Store(":memory:" if hosted else db_path)
    assistant = ai if ai is not None else AIAssistant(build_default_llm(), AuditLog(audit_path))
    sessions = Sessions()
    app.state.store, app.state.ai, app.state.sessions = store, assistant, sessions
    if hosted:
        app.add_middleware(StatelessMiddleware)
        if uploads:
            app.add_middleware(BodyLimitMiddleware, max_bytes=HOSTED_MAX_UPLOAD_BYTES + 256 * 1024,
                               message=f"That's more than a statement should need (limit {HOSTED_MAX_UPLOAD_BYTES // (1024 * 1024)} MB). Try exporting a shorter date range.")
        else:
            app.add_middleware(BodyLimitMiddleware, max_bytes=HOSTED_SAMPLE_ONLY_BODY, code="hosted_demo", message=HOSTED_MESSAGE)
    else:
        app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_UPLOAD_BYTES + 256 * 1024)      # the file plus multipart overhead and the small form fields

    # ------------------------------------------------------------------ plumbing
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message, "details": exc.details}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError):
        return JSONResponse({"error": {"code": "invalid_request", "message": "That request didn't look right. Please check what you entered and try again.", "details": {}}}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        msg = "We couldn't find that." if exc.status_code == 404 else "That request isn't allowed here." if exc.status_code == 405 else "Something went wrong."
        return JSONResponse({"error": {"code": "not_found" if exc.status_code == 404 else "http_error", "message": msg, "details": {}}}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        return JSONResponse({"error": {"code": "internal", "message": "Something went wrong on our side. Your data is safe; please try again.", "details": {}}}, status_code=500)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.update({"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                             "X-Frame-Options": "DENY", "Permissions-Policy": "camera=(), microphone=(), geolocation=()"})
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    hosted_base: dict[str, Analysis] = {}
    upload_cache: OrderedDict[str, Analysis] = OrderedDict()
    hosted_lock = threading.Lock()
    hosted_ai_calls = [0]

    def decisions(request: Request):
        return CookieDecisions(request) if hosted else store

    def build_upload(data: bytes, o: dict, key: str) -> Analysis:
        _check_upload(o["filename"], data, HOSTED_MAX_UPLOAD_BYTES)
        # use_paypal=False: a visitor's own statement is never merged with the bundled (fictional) PayPal demo data
        return analysis_from_text(decode_bytes(data), {"kind": "upload", "filename": o["filename"]}, demo=False, mapping=o["mapping"],
                                  invert_sign=o["invert_sign"], currency=o["default_currency"], use_paypal=False, dismissals={}, analysis_id=key[:12])

    def base_for(ss: dict) -> Analysis:
        """The analysis for the statement the browser sent (or the sample). A warm per-instance cache; a cold instance simply rebuilds it."""
        if ss["demo"]:
            with hosted_lock:
                if "demo" not in hosted_base:
                    hosted_base["demo"] = build_demo_analysis()
                return hosted_base["demo"]
        key = cache_key(ss["data"], ss["options"])
        with hosted_lock:
            if key in upload_cache:
                upload_cache.move_to_end(key)
                return upload_cache[key]
        built = build_upload(ss["data"], ss["options"], key)
        with hosted_lock:
            upload_cache[key] = built
            upload_cache.move_to_end(key)
            while len(upload_cache) > HOSTED_CACHE_SIZE:
                upload_cache.popitem(last=False)
        return built

    def view_for(ss: dict, d) -> Analysis:
        """The analysis as this visitor sees it: a shallow copy, so one visitor's decisions never change another's view."""
        a = copy.copy(base_for(ss))
        a.refresh(d.dismissals())
        return a

    if hosted:
        app.state.hosted_view = view_for              # exposed so a test can prove that two visitors' views are independent objects

    def current(request: Request) -> tuple[str, Analysis]:
        if hosted:
            ss = getattr(request.state, "stateless", None)
            if ss is None:
                raise ApiError(404, "no_analysis", "There's no statement loaded yet. Upload one, or try the sample data.")
            return "stateless", view_for(ss, decisions(request))
        sid = request.cookies.get(COOKIE)
        a = sessions.get(sid)
        if a is None:
            raise ApiError(404, "no_analysis", "There's no statement loaded yet. Upload one, or try the sample data.")
        return sid, a

    def findings_payload(a: Analysis, d) -> dict:
        view = a.overview_json(d.dismissals())
        return {"summary": view["summary"], "findings": view["findings"]}

    def ai_gate(a: Analysis) -> None:
        if hosted:
            hosted_ai_calls[0] += 1
            if hosted_ai_calls[0] > HOSTED_MAX_AI_CALLS:
                raise ApiError(429, "ai_limit", "This hosted demo has answered a lot of AI requests; please try again later.")
            return
        a.ai_calls += 1
        if a.ai_calls > MAX_AI_CALLS_PER_ANALYSIS:
            raise ApiError(429, "ai_limit", "That's a lot of AI requests for one statement. Start a new analysis to continue.")

    # ------------------------------------------------------------------ meta
    @app.get("/api/health")
    def health():
        llm = assistant.llm
        return {"ok": True, "version": __version__, "ai": {"mode": assistant.mode, "model": getattr(llm, "model", None)},
                "paypal_mode": current_mode(), "limits": {"max_upload_mb": (HOSTED_MAX_UPLOAD_BYTES if hosted else MAX_UPLOAD_BYTES) // (1024 * 1024)},
                "hosted": hosted, "uploads": uploads or not hosted}

    # ------------------------------------------------------------------ analysis
    @app.post("/api/analyze")
    async def analyze(
        request: Request,
        file: UploadFile | None = File(None),
        demo: bool = Form(False),
        mapping: str | None = Form(None),
        invert_sign: bool = Form(False),
        default_currency: str = Form("USD"),
        use_paypal: bool = Form(True),
    ):
        if len(default_currency) != 3 or not default_currency.isalpha():
            raise ApiError(400, "bad_currency", "Currency should be a three-letter code such as USD, EUR or INR.")
        if hosted:
            d = decisions(request)
            if demo:
                return JSONResponse(view_for({"demo": True, "data": None, "options": {}}, d).overview_json(d.dismissals()))
            if not uploads:
                raise ApiError(403, "hosted_demo", HOSTED_MESSAGE)
            if file is None or not file.filename:
                raise ApiError(400, "no_file", "Choose a statement file first, or try the sample data.")
            data = await file.read(HOSTED_MAX_UPLOAD_BYTES + 1)
            _check_upload(file.filename, data, HOSTED_MAX_UPLOAD_BYTES)
            options = {"demo": False, "mapping": parse_mapping(mapping), "invert_sign": invert_sign, "default_currency": default_currency.upper(),
                       "filename": Path(file.filename).name[:120]}
            return JSONResponse(view_for({"demo": False, "data": data, "options": options}, d).overview_json(d.dismissals()))
        if demo:
            text, name = demo_statement_csv(), "sample statement"
            source = {"kind": "demo", "filename": name}
        else:
            if file is None or not file.filename:
                raise ApiError(400, "no_file", "Choose a statement file first, or try the sample data.")
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            _check_upload(file.filename, data)
            text, name = decode_bytes(data), file.filename
            source = {"kind": "upload", "filename": Path(name).name[:120]}
        sid = secrets.token_urlsafe(24)
        analysis = analysis_from_text(text, source, demo=demo, mapping=parse_mapping(mapping), invert_sign=invert_sign, currency=default_currency,
                                      use_paypal=use_paypal, dismissals=store.dismissals(), analysis_id=secrets.token_hex(6))
        sessions.put(sid, analysis)
        resp = JSONResponse(analysis.overview_json(store.dismissals()))
        resp.set_cookie(COOKIE, sid, httponly=True, samesite="strict", path="/", max_age=6 * 3600)
        return resp

    @app.get("/api/analysis")
    def get_analysis(request: Request):
        _, a = current(request)
        return a.overview_json(decisions(request).dismissals())

    @app.delete("/api/analysis")
    def forget(request: Request):
        sessions.drop(request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/subscriptions/{sub_id}/history")
    def history(sub_id: str, request: Request):
        _, a = current(request)
        h = a.history_json(sub_id)
        if h is None:
            raise ApiError(404, "not_found", "That subscription isn't in this analysis.")
        return h

    @app.get("/api/paypal")
    def paypal(request: Request):
        _, a = current(request)
        return a.paypal_json()

    # ------------------------------------------------------------------ decisions (nothing is cancelled or sent)
    def _finding(a: Analysis, fid: str):
        f = next((x for x in a.report.findings if x.id == fid), None)
        if f is None:
            raise ApiError(404, "not_found", "That finding isn't in this analysis any more.")
        return f

    @app.post("/api/findings/{fid}/dismiss")
    def dismiss(fid: str, body: Dismiss, request: Request, response: Response):
        _, a = current(request)
        f = _finding(a, fid)
        if body.reason == "still_using" and f.type != "POSSIBLY_FORGOTTEN":
            raise ApiError(400, "not_applicable", "'Still using' only applies to 'possibly forgotten' findings. Use Dismiss instead.")
        d = decisions(request)
        d.dismiss(fid, body.reason)
        a.refresh(d.dismissals())
        d.write(response)
        return findings_payload(a, d)

    @app.post("/api/findings/{fid}/restore")
    def restore(fid: str, request: Request, response: Response):
        _, a = current(request)
        _finding(a, fid)
        d = decisions(request)
        d.restore(fid)
        a.refresh(d.dismissals())
        d.write(response)
        return findings_payload(a, d)

    @app.delete("/api/dismissals")
    def reset_decisions(request: Request, response: Response):
        _, a = current(request)
        d = decisions(request)
        d.clear()
        a.refresh({})
        d.write(response)
        return findings_payload(a, d)

    # ------------------------------------------------------------------ AI
    @app.post("/api/findings/{fid}/explain")
    def explain(fid: str, request: Request):
        _, a = current(request)
        f = _finding(a, fid)
        ai_gate(a)
        r = assistant.explain(f)
        return jsonable({"text": r.text, "generated_by": r.generated_by, "status": r.status, "notes": r.notes, "data_used": r.data_used, "model": r.model})

    @app.post("/api/findings/{fid}/draft")
    def draft(fid: str, request: Request):
        _, a = current(request)
        f = _finding(a, fid)
        if f.type not in ("DUPLICATE", "PRICE_HIKE", "POSSIBLY_FORGOTTEN"):
            raise ApiError(400, "not_draftable", "Drafts are available for duplicate, price-increase and possibly-forgotten findings.")
        ai_gate(a)
        d = assistant.draft(f)
        return jsonable({"subject": d.subject, "body": d.body, "generated_by": d.generated_by, "status": d.status, "notes": d.notes,
                         "data_used": d.data_used, "model": d.model, "sent": d.sent})

    @app.post("/api/ask")
    def ask(body: Question, request: Request):
        _, a = current(request)
        ai_gate(a)
        r = assistant.ask(body.question, a.context())
        return jsonable({"question": r.question, "answer": r.answer, "status": r.status, "intent": r.intent, "routed_by": r.routed_by,
                         "generated_by": r.generated_by, "columns": r.columns, "table": r.table, "data_used": r.data_used,
                         "notes": r.notes, "model": r.model})

    # ------------------------------------------------------------------ frontend (last, so /api wins)
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app
