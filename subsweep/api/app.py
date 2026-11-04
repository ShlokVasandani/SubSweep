"""SubSweep web API + static frontend.

Everything the UI shows is computed here from the in-memory analysis; nothing here cancels a subscription or sends a message.
Statements never touch disk (only the user's dismiss / "still using" decisions are stored, in SQLite).
"""
from __future__ import annotations

import json
import secrets
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Request, UploadFile
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
from .serialize import jsonable

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_EXT = {".csv", ".txt", ".tsv"}
MAX_SESSIONS = 12
MAX_AI_CALLS_PER_ANALYSIS = 300
COOKIE = "subsweep_session"
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; font-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")
_BINARY_MAGIC = (b"%PDF", b"PK\x03\x04", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"MZ", b"\x7fELF", b"\xd0\xcf\x11\xe0")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class Dismiss(BaseModel):
    reason: Literal["dismissed", "still_using"] = "dismissed"


def _check_upload(name: str, data: bytes) -> None:
    ext = Path(name or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ApiError(415, "wrong_file_type", "That doesn't look like a statement export. Please choose a .csv file (a .txt or .tsv export also works).")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(413, "too_large", f"That file is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB, which is more than a statement should need. Try exporting a shorter date range.")
    if not data.strip():
        raise ApiError(400, "empty_file", "That file looks empty. Please export your statement again and choose the CSV option.")
    if b"\x00" in data[:4096] or data.startswith(_BINARY_MAGIC):
        raise ApiError(415, "wrong_file_type", "That file looks like a binary file (such as a PDF or spreadsheet), not a CSV. Please export a CSV from your bank or PayPal.")


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


def create_app(db_path: str | Path | None = None, ai: AIAssistant | None = None, audit_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="SubSweep", version=__version__, docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    store = Store(db_path)
    assistant = ai if ai is not None else AIAssistant(build_default_llm(), AuditLog(audit_path))
    sessions = Sessions()
    app.state.store, app.state.ai, app.state.sessions = store, assistant, sessions

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

    def current(request: Request) -> tuple[str, Analysis]:
        sid = request.cookies.get(COOKIE)
        a = sessions.get(sid)
        if a is None:
            raise ApiError(404, "no_analysis", "There's no statement loaded yet. Upload one, or try the sample data.")
        return sid, a

    def findings_payload(a: Analysis) -> dict:
        d = a.overview_json(store.dismissals())
        return {"summary": d["summary"], "findings": d["findings"]}

    def ai_gate(a: Analysis) -> None:
        a.ai_calls += 1
        if a.ai_calls > MAX_AI_CALLS_PER_ANALYSIS:
            raise ApiError(429, "ai_limit", "That's a lot of AI requests for one statement. Start a new analysis to continue.")

    # ------------------------------------------------------------------ meta
    @app.get("/api/health")
    def health():
        llm = assistant.llm
        return {"ok": True, "version": __version__, "ai": {"mode": assistant.mode, "model": getattr(llm, "model", None)},
                "paypal_mode": current_mode(), "limits": {"max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024)}}

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
        warnings: list[str] = []
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
        try:
            col_map = None
            if mapping:
                try:
                    parsed = json.loads(mapping)
                    if not isinstance(parsed, dict):
                        raise ValueError("mapping must be an object")
                    col_map = ColumnMapping.from_dict(parsed)
                except (ValueError, TypeError):
                    raise ApiError(400, "bad_mapping", "The column choices couldn't be read. Please pick them again.") from None
                col_map.invert_sign = col_map.invert_sign or invert_sign
            elif invert_sign:
                det = detect_format(read_rows(text, sniff_delimiter(text)), sniff_delimiter(text))
                if det.ok:
                    col_map = det.mapping
                    col_map.invert_sign = True
            statement = load_statement(text, col_map, default_currency=default_currency.upper(), source_label=source["kind"] if demo else None)
        except FormatDetectionError as exc:
            raise ApiError(422, "needs_mapping", str(exc), {"headers": exc.headers, "suggestions": exc.suggestions}) from None
        except StatementTooLargeError as exc:
            raise ApiError(413, "too_large", str(exc)) from None
        if len(statement.transactions) == 0:
            raise ApiError(422, "no_transactions", "We couldn't find any transactions in that file. Check it's a statement export with dates and amounts.",
                           {"warnings": statement.warnings})
        warnings += statement.warnings
        source.update({"format": statement.format, "rows": int(len(statement.transactions)), "warnings": warnings[:20],
                       "date_order": statement.date_order, "dropped": statement.dropped})
        sid = secrets.token_urlsafe(24)
        analysis = build_analysis(secrets.token_hex(6), statement, source, store.dismissals(), demo=demo, use_paypal=use_paypal)
        sessions.put(sid, analysis)
        resp = JSONResponse(analysis.overview_json(store.dismissals()))
        resp.set_cookie(COOKIE, sid, httponly=True, samesite="strict", path="/", max_age=6 * 3600)
        return resp

    @app.get("/api/analysis")
    def get_analysis(request: Request):
        _, a = current(request)
        return a.overview_json(store.dismissals())

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
    def dismiss(fid: str, body: Dismiss, request: Request):
        _, a = current(request)
        f = _finding(a, fid)
        if body.reason == "still_using" and f.type != "POSSIBLY_FORGOTTEN":
            raise ApiError(400, "not_applicable", "'Still using' only applies to 'possibly forgotten' findings. Use Dismiss instead.")
        store.dismiss(fid, body.reason)
        a.refresh(store.dismissals())
        return findings_payload(a)

    @app.post("/api/findings/{fid}/restore")
    def restore(fid: str, request: Request):
        _, a = current(request)
        _finding(a, fid)
        store.restore(fid)
        a.refresh(store.dismissals())
        return findings_payload(a)

    @app.delete("/api/dismissals")
    def reset_decisions(request: Request):
        _, a = current(request)
        store.clear()
        a.refresh({})
        return findings_payload(a)

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
