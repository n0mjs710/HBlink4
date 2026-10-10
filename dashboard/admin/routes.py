"""Protected administration pages in the existing dashboard application."""

import asyncio
import html
import json
import logging
import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .apply import ApplyError, BusyError, DriftError, OperatorManager, RollbackError
from .operators import OperatorValidationError
from .security import (
    LOGIN_CSRF_COOKIE, LOGIN_CSRF_LIFETIME, SESSION_COOKIE, LoginLimiter,
    csrf_digest, new_login_csrf, password_hash, password_valid, session_digest,
    valid_login_csrf,
)
from .storage import AdminStore, SESSION_LIFETIME


router = APIRouter(prefix="/admin")
TEMPLATES = Path(__file__).parent / "templates"
DEFAULT_CONFIG_PATH = Path(__file__).parents[2] / "config" / "config.json"
logger = logging.getLogger(__name__)


def initialize_admin(app):
    store = AdminStore(Path(__file__).parents[1] / "data")
    store.initialize()
    app.state.admin_store = store
    app.state.admin_limiter = LoginLimiter()
    app.state.admin_hash_semaphore = asyncio.Semaphore(2)
    app.state.admin_dummy_hash = password_hash(secrets.token_urlsafe(32))
    manager = OperatorManager(
        store, DEFAULT_CONFIG_PATH, Path.home() / "HBlink4-backup" / "buenodmr-config"
    )
    try:
        manager.initialize()
    except (OperatorValidationError, ApplyError) as exc:
        logger.error("BuenoDMR operator management unavailable: %s", type(exc).__name__)
        app.state.admin_operator_error = True
    else:
        app.state.admin_manager = manager
        app.state.admin_operator_error = False


def _store(request):
    return request.app.state.admin_store


def _secure_cookie():
    return os.environ.get("BUENODMR_ADMIN_COOKIE_SECURE") == "1"


def _response(content, status_code=200):
    response = HTMLResponse(content, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


def _redirect(url):
    response = RedirectResponse(url, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response


def _session_user(request):
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 128:
        return None
    try:
        digest = session_digest(_store(request).secret, token)
    except UnicodeError:
        return None
    return _store(request).get_session_admin(digest)


def _admin(request):
    user = _session_user(request)
    return user if user and user["role"] == "admin" else None


def _api_admin(request):
    user = _session_user(request)
    if not user:
        return None, JSONResponse({"error": "Authentication required."}, status_code=401)
    if user["role"] != "admin":
        return None, JSONResponse({"error": "Administrator role required."}, status_code=403)
    if getattr(request.app.state, "admin_operator_error", False):
        return None, JSONResponse({"error": "Operator management unavailable."}, status_code=503)
    return user, None


def _mutation_csrf(request):
    token = request.cookies.get(SESSION_COOKIE, "")
    supplied = request.headers.get("x-csrf-token", "")
    return secrets.compare_digest(csrf_digest(_store(request).secret, "operator-mutation", token), supplied)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


async def _json_payload(request):
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise OperatorValidationError("Expected a JSON request.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise OperatorValidationError("Request is too large.")
    try:
        data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_keys)
    except (UnicodeError, ValueError) as exc:
        raise OperatorValidationError("Invalid JSON request.") from exc
    if not isinstance(data, dict):
        raise OperatorValidationError("Expected a JSON object.")
    return data


async def _form(request):
    if not request.headers.get("content-type", "").lower().startswith("application/x-www-form-urlencoded"):
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16384:
            return None
    try:
        pairs = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeError, ValueError):
        return None
    return {key: values[0] for key, values in pairs.items() if len(values) == 1}


def _login_page(request, error="", status_code=200):
    store = _store(request)
    nonce, token = new_login_csrf(store.secret)
    template = (TEMPLATES / "login.html").read_text(encoding="utf-8")
    page = template.replace("{{CSRF}}", token).replace("{{ERROR}}", html.escape(error))
    response = _response(page, status_code)
    response.set_cookie(
        LOGIN_CSRF_COOKIE, nonce, max_age=LOGIN_CSRF_LIFETIME, path="/admin/login",
        httponly=True, secure=_secure_cookie(), samesite="lax",
    )
    return response


def _client_ip(request):
    return (request.client.host if request.client else "unknown")[:128]


def _audit_label(action):
    return {
        "admin_bootstrap": "administração inicial",
        "admin_login_failure": "falha de entrada",
        "admin_login_success": "entrada realizada",
        "admin_logout": "saída realizada",
        "operator_created": "operador adicionado",
        "operator_updated": "operador atualizado",
        "operator_enabled": "operador ativado",
        "operator_disabled": "operador desativado",
        "operator_deleted": "operador excluído",
        "config_apply_success": "configuração aplicada",
        "config_apply_failure": "falha ao aplicar configuração",
        "config_rollback": "configuração revertida",
        "hotspot_access_enabled": "acesso exclusivo por Hotspot ativado",
        "hotspot_access_disabled": "acesso exclusivo por Hotspot desativado",
        "client_admission_rejected": "cliente recusado na admissão",
    }.get(action, action)


def _radio_access_snapshot(request):
    observer = getattr(request.app.state, "radio_access_observer", None)
    try:
        observed = observer() if observer else None
    except Exception:
        observed = None
    if not isinstance(observed, dict):
        observed = {"hblink_connected": False, "clients": []}
    clients = observed.get("clients")
    if not isinstance(clients, list):
        clients = []
    status = request.app.state.admin_manager.radio_access_status()
    ready = (observed.get("hblink_connected") is True and bool(clients)
             and all(isinstance(client, dict)
                     and client.get("connection_type") == "hotspot"
                     for client in clients))
    return {
        **status,
        "hblink_connected": observed.get("hblink_connected") is True,
        "clients": clients,
        "ready_to_enable": ready,
    }


@router.get("/login")
async def login_page(request: Request):
    if _admin(request):
        return _redirect("/admin")
    return _login_page(request)


@router.post("/login")
async def login(request: Request):
    form = await _form(request)
    store = _store(request)
    csrf = form.get("csrf", "") if form else ""
    if not valid_login_csrf(store.secret, request.cookies.get(LOGIN_CSRF_COOKIE), csrf):
        store.audit(None, None, "admin_login_failure", False, _client_ip(request))
        return _login_page(request, "Formulário inválido. Tente novamente.", 403)

    username = form.get("username", "").strip().upper()[:64]
    password = form.get("password", "")
    ip = _client_ip(request)
    limiter = request.app.state.admin_limiter
    if limiter.limited(ip, username):
        store.audit(None, username, "admin_login_failure", False, ip)
        return _login_page(request, "Muitas tentativas. Aguarde e tente novamente.", 429)
    limiter.failure(ip, username)
    admin = store.get_admin(username) if username else None
    if len(password) > 1024:
        valid = False
    else:
        # Unknown accounts still perform a password verification to limit enumeration.
        dummy_hash = request.app.state.admin_dummy_hash
        async with request.app.state.admin_hash_semaphore:
            valid = await asyncio.to_thread(password_valid, admin["password_hash"] if admin else dummy_hash, password)
    if not admin or not valid or admin["role"] != "admin":
        store.audit(admin["id"] if admin else None, username, "admin_login_failure", False, ip)
        return _login_page(request, "Usuário ou senha inválidos.", 401)

    limiter.success(ip, username)
    token = secrets.token_urlsafe(32)
    store.create_session(session_digest(store.secret, token), admin["id"])
    store.audit(admin["id"], admin["username"], "admin_login_success", True, ip)
    response = _redirect("/admin")
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_LIFETIME, path="/admin",
        httponly=True, secure=_secure_cookie(), samesite="lax",
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE, path="/admin/login")
    return response


@router.get("")
@router.get("/")
async def admin_home(request: Request):
    admin = _admin(request)
    if not admin:
        return _redirect("/admin/login")
    if getattr(request.app.state, "admin_operator_error", False):
        return _response("Operator configuration unavailable", 503)
    operators = request.app.state.admin_manager.list_operators()
    rows = []
    for operator in operators:
        name = html.escape(operator["callsign"])
        state = "Ativo" if operator["active"] else "Desativado"
        rows.append(
            f'<article class="operator"><div><strong>{name}</strong><small>{state}</small></div>'
            f'<div class="operator-details"><span>ID base: {operator["base_id"]}</span>'
            f'<span>ESSID: {operator["essid_from"]:02d}-{operator["essid_to"]:02d}</span>'
            f'<span>Faixa: {operator["range_start"]}-{operator["range_end"]}</span></div></article>'
        )
    activity = []
    for entry in _store(request).recent_audit(10):
        detail = f' - {entry["operator_callsign"]}' if entry["operator_callsign"] else ""
        activity.append(f'<li><time>{html.escape(entry["created_at"][:19])}</time> '
                        f'{html.escape(_audit_label(entry["action"]))}{html.escape(detail)}</li>')
    template = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    page = (template.replace("{{USERNAME}}", html.escape(admin["username"]))
            .replace("{{OPERATORS}}", "".join(rows) or "<p>Nenhum operador configurado.</p>")
            .replace("{{ACTIVITY}}", "".join(activity))
            .replace("{{MUTATION_CSRF}}", csrf_digest(_store(request).secret, "operator-mutation", request.cookies[SESSION_COOKIE]))
            .replace("{{CSRF}}", csrf_digest(_store(request).secret, "logout", request.cookies[SESSION_COOKIE])))
    return _response(page)


@router.post("/logout")
async def logout(request: Request):
    admin = _admin(request)
    if not admin:
        return _redirect("/admin/login")
    form = await _form(request)
    expected = csrf_digest(_store(request).secret, "logout", request.cookies[SESSION_COOKIE])
    if not form or not secrets.compare_digest(form.get("csrf", ""), expected):
        return _response("Invalid form", 403)
    _store(request).delete_session(session_digest(_store(request).secret, request.cookies[SESSION_COOKIE]))
    _store(request).audit(admin["id"], admin["username"], "admin_logout", True, _client_ip(request))
    response = _redirect("/admin/login")
    response.delete_cookie(SESSION_COOKIE, path="/admin")
    return response


@router.get("/api/operators")
async def list_operators(request: Request):
    _, error = _api_admin(request)
    if error:
        return error
    return JSONResponse({"operators": request.app.state.admin_manager.list_operators()},
                        headers={"Cache-Control": "no-store"})


@router.get("/api/audit")
async def recent_activity(request: Request):
    _, error = _api_admin(request)
    if error:
        return error
    return JSONResponse({"events": _store(request).recent_audit(20)},
                        headers={"Cache-Control": "no-store"})


@router.get("/api/radio-access")
async def radio_access_status(request: Request):
    _, error = _api_admin(request)
    if error:
        return error
    return JSONResponse(_radio_access_snapshot(request), headers={"Cache-Control": "no-store"})


@router.post("/api/radio-access")
async def update_radio_access(request: Request):
    admin, error = _api_admin(request)
    if error:
        return error
    if not _mutation_csrf(request):
        return JSONResponse({"error": "Invalid CSRF token."}, status_code=403)
    try:
        payload = await _json_payload(request)
        if set(payload) != {"enabled", "confirmation"} or type(payload["enabled"]) is not bool:
            raise OperatorValidationError("Invalid access policy request.")
        expected_confirmation = "ATIVAR" if payload["enabled"] else "DESATIVAR"
        if payload["confirmation"] != expected_confirmation:
            raise OperatorValidationError("Confirmação ausente ou inválida.")
        if payload["enabled"] and not _radio_access_snapshot(request)["ready_to_enable"]:
            return JSONResponse({
                "error": "Ativação exige HBlink4 conectado e todas as conexões atuais classificadas como hotspot."
            }, status_code=409)
        result = await asyncio.to_thread(
            request.app.state.admin_manager.apply_radio_access_policy,
            payload["enabled"], admin, _client_ip(request),
        )
        broadcaster = getattr(request.app.state, "public_snapshot_broadcaster", None)
        if broadcaster:
            await broadcaster()
        return JSONResponse({"message": "Política de acesso atualizada.", **result},
                            headers={"Cache-Control": "no-store"})
    except OperatorValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except (BusyError, DriftError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except RollbackError:
        logger.error("BuenoDMR radio access rollback requires attention")
        return JSONResponse({"error": "A recuperação da política exige atenção do administrador."}, status_code=503)
    except ApplyError:
        return JSONResponse({"error": "Falha ao aplicar a política. A configuração anterior foi restaurada."},
                            status_code=503)


async def _operator_mutation(request, action, operator_id=None):
    admin, error = _api_admin(request)
    if error:
        return error
    if not _mutation_csrf(request):
        return JSONResponse({"error": "Invalid CSRF token."}, status_code=403)
    try:
        payload = await _json_payload(request)
        operators = await asyncio.to_thread(
            request.app.state.admin_manager.apply, action, operator_id, payload,
            admin, _client_ip(request),
        )
        broadcaster = getattr(request.app.state, "public_snapshot_broadcaster", None)
        if broadcaster:
            await broadcaster()
        return JSONResponse({"message": "Configuration applied successfully.",
                             "operators": operators}, headers={"Cache-Control": "no-store"})
    except OperatorValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except (BusyError, DriftError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except RollbackError:
        logger.error("BuenoDMR configuration rollback requires attention")
        return JSONResponse({"error": "Configuration recovery requires operator attention."}, status_code=503)
    except ApplyError:
        return JSONResponse({"error": "Failed to apply configuration. Previous configuration restored."},
                            status_code=503)


@router.post("/api/operators")
async def create_operator(request: Request):
    return await _operator_mutation(request, "create")


@router.put("/api/operators/{operator_id}")
async def update_operator(operator_id: int, request: Request):
    return await _operator_mutation(request, "update", operator_id)


@router.post("/api/operators/{operator_id}/enable")
async def enable_operator(operator_id: int, request: Request):
    return await _operator_mutation(request, "enable", operator_id)


@router.post("/api/operators/{operator_id}/disable")
async def disable_operator(operator_id: int, request: Request):
    return await _operator_mutation(request, "disable", operator_id)


@router.delete("/api/operators/{operator_id}")
async def delete_operator(operator_id: int, request: Request):
    return await _operator_mutation(request, "delete", operator_id)
