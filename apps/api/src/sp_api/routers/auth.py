"""Authentication: email+password and Google (AUTH.md).

Identity is decoupled from Strava — Strava is a data connection made *after* an
account exists, so signup is never blocked by Strava's athlete cap.

Two properties this module must not lose:

* **Enumeration resistance.** Signup, login and password-reset responses never
  distinguish "no such account" from "wrong credentials".
* **No auto-linking on email match.** A Google sign-in whose email matches an
  existing password account does *not* silently take it over.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sp_core.config import get_settings
from sp_core.security.passwords import (
    hash_password,
    is_breached,
    needs_rehash,
    validate_password,
    verify_password,
)
from sp_core.security.tokens import generate_token, hash_token
from sp_db.models import (
    AuthEvent,
    AuthIdentity,
    EmailVerificationToken,
    PasswordResetToken,
    StravaConnection,
    User,
)
from sp_db.models import (
    Session as SessionRow,
)
from sqlalchemy import select

from sp_api import ratelimit
from sp_api.deps import (
    SESSION_COOKIE,
    AppSettings,
    CurrentUser,
    DbSession,
    OptionalUser,
    bearer_token,
    client_ip,
)
from sp_api.schemas import (
    AuthProviders,
    LoginRequest,
    Message,
    PasswordResetConfirm,
    PasswordResetRequest,
    SessionOut,
    SignupRequest,
    UserOut,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint URL
_OAUTH_STATE_COOKIE = "sp_oauth_state"
_OAUTH_STATE_MAX_AGE_S = 600

# Starlette renamed HTTP_422_UNPROCESSABLE_ENTITY; the literal is version-agnostic.
HTTP_422_UNPROCESSABLE = 422

_GENERIC_SIGNUP = (
    "Check your email — if that address can be used, we've sent a link to finish setting up."
)
_GENERIC_RESET = "If an account exists for that address, we've sent a reset link."
_GENERIC_LOGIN_FAILURE = "That email or password isn't right."


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="oauth-state")


async def _log_event(
    session: DbSession,
    *,
    event: str,
    request: Request,
    user_id: UUID | None = None,
    email: str | None = None,
) -> None:
    session.add(
        AuthEvent(
            user_id=user_id,
            email_tried=email,
            event=event,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:500] or None,
        )
    )


async def _issue_session(
    session: DbSession, user: User, request: Request, response: Response
) -> str:
    """Create an opaque server-side session, set the cookie, return the raw token.

    We store only the token's hash, so a database read cannot be replayed as a
    login (AUTH.md §4). The raw token is returned for the bearer-token path — see
    `_bearer_token_for_client`, which decides whether the client may see it.
    """
    settings = get_settings()
    token = generate_token()

    session.add(
        SessionRow(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(days=settings.session_ttl_days),
            ip_created_from=client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:500] or None,
        )
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )

    # Commit before the response leaves. FastAPI runs a yield-dependency's cleanup
    # *after* the response is sent, so relying on it to commit races the client's
    # very next request — the browser would follow the post-signup redirect and be
    # told it isn't signed in.
    await session.commit()
    return token


def _bearer_token_for_client(token: str) -> str | None:
    """The token, but only if this deployment hands it to JavaScript at all."""
    return token if get_settings().auth_bearer_tokens else None


async def _user_out(session: DbSession, user: User) -> UserOut:
    has_google = (
        await session.execute(
            select(AuthIdentity.id).where(
                AuthIdentity.user_id == user.id, AuthIdentity.provider == "google"
            )
        )
    ).first() is not None
    strava_connected = (
        await session.execute(
            select(StravaConnection.user_id).where(StravaConnection.user_id == user.id)
        )
    ).first() is not None

    return UserOut(
        id=user.id,
        email=user.email,
        email_verified=user.email_verified_at is not None,
        first_name=user.first_name,
        last_name=user.last_name,
        profile_photo_url=user.profile_photo_url,
        measurement_pref=user.measurement_pref,
        weight_kg=user.weight_kg,
        ftp_w=user.ftp_w,
        max_hr_bpm=user.max_hr_bpm,
        resting_hr_bpm=user.resting_hr_bpm,
        sex=user.sex,
        has_password=user.password_hash is not None,
        has_google=has_google,
        strava_connected=strava_connected,
        created_at=user.created_at,
    )


def _dev_hint(kind: str, token: str) -> str | None:
    """Local development has no SMTP, so surface the link instead of emailing it.

    Gated on ENVIRONMENT=development — this must never reach a deployed instance,
    where it would be a full account-takeover primitive.
    """
    settings = get_settings()
    if settings.environment != "development":
        return None
    return f"{settings.web_base_url}/{kind}?token={token}"


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


@router.get("/providers", response_model=AuthProviders)
async def providers(settings: AppSettings) -> AuthProviders:
    """Which sign-in options are configured, so the UI hides buttons that can't work."""
    return AuthProviders(google=settings.google_enabled, strava=settings.strava_enabled)


# --------------------------------------------------------------------------- #
# Email + password
# --------------------------------------------------------------------------- #


@router.post("/signup", response_model=Message, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest, request: Request, response: Response, session: DbSession
) -> Message:
    email = payload.email.strip().lower()

    throttle = await ratelimit.check("signup", client_ip(request))
    if not throttle.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again shortly.",
            headers={"Retry-After": str(throttle.retry_after_s)},
        )

    policy = validate_password(payload.password, email=email)
    if not policy.ok:
        raise HTTPException(HTTP_422_UNPROCESSABLE, detail=policy.reason)

    if await is_breached(payload.password):
        raise HTTPException(
            HTTP_422_UNPROCESSABLE,
            detail=(
                "That password has appeared in a known data breach. Please pick a different one."
            ),
        )

    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()

    if existing is not None:
        # Respond exactly as for a new account. The *email* discloses that an
        # account exists; the API does not (AUTH.md §5).
        await ratelimit.record_failure("signup", client_ip(request))
        await _log_event(session, event="signup_duplicate", request=request, email=email)
        log.info("auth.signup.duplicate", email_domain=email.rsplit("@", 1)[-1])
        return Message(message=_GENERIC_SIGNUP)

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        first_name=(payload.first_name or "").strip() or None,
    )
    session.add(user)
    await session.flush()

    token = generate_token()
    session.add(
        EmailVerificationToken(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )

    # Product access is not blocked on verification; sensitive actions are.
    session_token = await _issue_session(session, user, request, response)
    await _log_event(session, event="signup", request=request, user_id=user.id, email=email)
    log.info("auth.signup.ok", user_id=str(user.id))

    bearer = _bearer_token_for_client(session_token)
    hint = _dev_hint("verify-email", token)
    if hint:
        log.warning("auth.signup.dev_verification_link", link=hint)
        return Message(message=f"{_GENERIC_SIGNUP} [dev] {hint}", session_token=bearer)
    return Message(message=_GENERIC_SIGNUP, session_token=bearer)


@router.post("/login", response_model=UserOut)
async def login(
    payload: LoginRequest, request: Request, response: Response, session: DbSession
) -> UserOut:
    email = payload.email.strip().lower()
    ip = client_ip(request)

    for scope, identifier in (("login_email", email), ("login_ip", ip)):
        state = await ratelimit.check(scope, identifier)
        if not state.allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Try again in a moment.",
                headers={"Retry-After": str(state.retry_after_s)},
            )

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()

    # verify_password burns a real Argon2 verify against a dummy hash when the user
    # is missing, so a nonexistent address costs the same wall-clock time as a
    # wrong password and timing can't enumerate accounts.
    ok = verify_password(payload.password, user.password_hash if user else None)

    if not ok or user is None or user.deleted_at is not None:
        await ratelimit.record_failure("login_email", email)
        await ratelimit.record_failure("login_ip", ip)
        await _log_event(session, event="login_failed", request=request, email=email)
        # Distinguish only the genuinely helpful case: a Google-only account.
        if user is not None and user.password_hash is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="This account uses Google sign-in. Use the Google button above.",
            )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_FAILURE)

    if needs_rehash(user.password_hash or ""):
        user.password_hash = hash_password(payload.password)

    await ratelimit.clear("login_email", email)
    session_token = await _issue_session(session, user, request, response)
    await _log_event(session, event="login_password", request=request, user_id=user.id, email=email)
    log.info("auth.login.ok", user_id=str(user.id), method="password")
    out = await _user_out(session, user)
    out.session_token = _bearer_token_for_client(session_token)
    return out


@router.post("/logout", response_model=Message)
async def logout(
    request: Request,
    response: Response,
    session: DbSession,
    user: OptionalUser,
    sp_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> Message:
    # Revoke whichever carrier the client actually presented, or a bearer-token
    # session would survive its own logout.
    presented = sp_session or bearer_token(request)
    if presented:
        row = (
            await session.execute(
                select(SessionRow).where(SessionRow.token_hash == hash_token(presented))
            )
        ).scalar_one_or_none()
        if row is not None:
            row.revoked_at = datetime.now(UTC)
    if user is not None:
        await _log_event(session, event="logout", request=request, user_id=user.id)

    response.delete_cookie(SESSION_COOKIE, path="/")
    return Message(message="Signed out.")


@router.get("/me", response_model=UserOut)
async def me(session: DbSession, user: CurrentUser) -> UserOut:
    return await _user_out(session, user)


@router.post("/password-reset/request", response_model=Message)
async def request_password_reset(
    payload: PasswordResetRequest, request: Request, session: DbSession
) -> Message:
    email = payload.email.strip().lower()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()

    hint: str | None = None
    if user is not None and user.deleted_at is None:
        token = generate_token()
        session.add(
            PasswordResetToken(
                token_hash=hash_token(token),
                user_id=user.id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                requested_ip=client_ip(request),
            )
        )
        await _log_event(
            session, event="password_reset_requested", request=request, user_id=user.id, email=email
        )
        hint = _dev_hint("reset-password", token)
        if hint:
            log.warning("auth.reset.dev_link", link=hint)

    # Same response either way.
    return Message(message=f"{_GENERIC_RESET} [dev] {hint}" if hint else _GENERIC_RESET)


@router.post("/password-reset/confirm", response_model=Message)
async def confirm_password_reset(
    payload: PasswordResetConfirm, request: Request, session: DbSession
) -> Message:
    row = (
        await session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == hash_token(payload.token)
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="That reset link has expired or already been used. Request a new one.",
        )

    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one()

    policy = validate_password(payload.new_password, email=user.email)
    if not policy.ok:
        raise HTTPException(HTTP_422_UNPROCESSABLE, detail=policy.reason)
    if await is_breached(payload.new_password):
        raise HTTPException(
            HTTP_422_UNPROCESSABLE,
            detail="That password has appeared in a known data breach. Please pick another.",
        )

    user.password_hash = hash_password(payload.new_password)
    row.used_at = now

    # A reset is usually recovery from a compromise — don't leave the attacker's
    # session alive (AUTH.md §2).
    for existing in (
        await session.execute(
            select(SessionRow).where(SessionRow.user_id == user.id, SessionRow.revoked_at.is_(None))
        )
    ).scalars():
        existing.revoked_at = now

    await _log_event(
        session, event="password_reset", request=request, user_id=user.id, email=user.email
    )
    log.info("auth.reset.ok", user_id=str(user.id))
    return Message(message="Password updated. Sign in with your new password.")


@router.post("/verify-email", response_model=Message)
async def verify_email(token: str, session: DbSession) -> Message:
    row = (
        await session.execute(
            select(EmailVerificationToken).where(
                EmailVerificationToken.token_hash == hash_token(token)
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="That link has expired. We can send a new one."
        )

    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one()
    user.email_verified_at = now
    row.used_at = now
    return Message(message="Email verified.")


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    session: DbSession,
    user: CurrentUser,
    sp_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> list[SessionOut]:
    current_hash = hash_token(sp_session) if sp_session else None
    rows = (
        await session.execute(
            select(SessionRow)
            .where(SessionRow.user_id == user.id, SessionRow.revoked_at.is_(None))
            .order_by(SessionRow.last_seen_at.desc())
        )
    ).scalars()

    return [
        SessionOut(
            id=row.id,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            ip_created_from=str(row.ip_created_from) if row.ip_created_from else None,
            user_agent=row.user_agent,
            is_current=row.token_hash == current_hash,
        )
        for row in rows
    ]


@router.delete("/sessions/{session_id}", response_model=Message)
async def revoke_session(session_id: UUID, session: DbSession, user: CurrentUser) -> Message:
    row = (
        await session.execute(
            select(SessionRow).where(SessionRow.id == session_id, SessionRow.user_id == user.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such session.")
    row.revoked_at = datetime.now(UTC)
    return Message(message="Signed out on that device.")


@router.post("/sessions/revoke-others", response_model=Message)
async def revoke_other_sessions(
    session: DbSession,
    user: CurrentUser,
    sp_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> Message:
    current_hash = hash_token(sp_session) if sp_session else ""
    now = datetime.now(UTC)
    count = 0
    for row in (
        await session.execute(
            select(SessionRow).where(SessionRow.user_id == user.id, SessionRow.revoked_at.is_(None))
        )
    ).scalars():
        if row.token_hash != current_hash:
            row.revoked_at = now
            count += 1
    return Message(message=f"Signed out of {count} other device(s).")


# --------------------------------------------------------------------------- #
# Google (OIDC)
# --------------------------------------------------------------------------- #


@router.get("/google/start")
async def google_start(request: Request, settings: AppSettings) -> RedirectResponse:
    if not settings.google_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in isn't configured on this server.",
        )

    state = secrets.token_urlsafe(24)
    url = f"{_GOOGLE_AUTH_URL}?" + urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": f"{settings.api_base_url}/auth/google/callback",
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
    )

    response = RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    # State is bound to the browser in a signed, short-lived cookie — this is CSRF
    # protection for the OAuth dance itself.
    response.set_cookie(
        _OAUTH_STATE_COOKIE,
        _serializer().dumps(state),
        max_age=_OAUTH_STATE_MAX_AGE_S,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    sp_oauth_state: Annotated[str | None, Cookie(alias=_OAUTH_STATE_COOKIE)] = None,
) -> RedirectResponse:
    def fail(reason: str) -> RedirectResponse:
        return RedirectResponse(
            f"{settings.web_base_url}/login?error={reason}",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    if error or not code or not state or not sp_oauth_state:
        return fail("google_cancelled")

    try:
        expected = _serializer().loads(sp_oauth_state, max_age=_OAUTH_STATE_MAX_AGE_S)
    except BadSignature:
        return fail("google_state")
    if not secrets.compare_digest(str(expected), state):
        return fail("google_state")

    async with httpx.AsyncClient(timeout=10.0) as client:
        token_response = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": f"{settings.api_base_url}/auth/google/callback",
                "grant_type": "authorization_code",
            },
        )
    if token_response.status_code != 200:
        log.warning("auth.google.token_exchange_failed", status=token_response.status_code)
        return fail("google_exchange")

    raw_id_token = token_response.json().get("id_token")
    if not raw_id_token:
        return fail("google_exchange")

    # Verify the signature against Google's JWKS, plus aud/iss/exp. Never trust an
    # id_token's claims without this.
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        claims = google_id_token.verify_oauth2_token(
            raw_id_token, google_requests.Request(), settings.google_client_id
        )
    except ValueError:
        # google-auth raises ValueError for every verification failure: bad
        # signature, wrong audience, expired, malformed.
        log.warning("auth.google.id_token_invalid")
        return fail("google_token")

    subject = claims.get("sub")
    email = (claims.get("email") or "").strip().lower()
    if not subject or not email:
        return fail("google_claims")

    identity = (
        await session.execute(
            select(AuthIdentity).where(
                AuthIdentity.provider == "google", AuthIdentity.provider_sub == subject
            )
        )
    ).scalar_one_or_none()

    if identity is not None:
        user = (await session.execute(select(User).where(User.id == identity.user_id))).scalar_one()
    else:
        clash = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if clash is not None:
            # Never auto-link on an email match: an attacker controlling a Google
            # account with the victim's address would otherwise take over their
            # password account (AUTH.md §3).
            log.info("auth.google.link_required", user_id=str(clash.id))
            return fail("account_exists")

        user = User(
            email=email,
            password_hash=None,
            first_name=claims.get("given_name"),
            last_name=claims.get("family_name"),
            profile_photo_url=claims.get("picture"),
            # Google asserts the address; no verification email needed.
            email_verified_at=datetime.now(UTC) if claims.get("email_verified") else None,
        )
        session.add(user)
        await session.flush()
        session.add(
            AuthIdentity(
                user_id=user.id, provider="google", provider_sub=subject, email_at_link=email
            )
        )

    redirect = RedirectResponse(
        f"{settings.web_base_url}/", status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    await _issue_session(session, user, request, redirect)
    await _log_event(session, event="login_google", request=request, user_id=user.id, email=email)
    redirect.delete_cookie(_OAUTH_STATE_COOKIE, path="/")
    log.info("auth.login.ok", user_id=str(user.id), method="google")
    return redirect


@router.post("/google/link", response_model=Message)
async def link_google(user: CurrentUser, settings: AppSettings) -> Message:
    """Placeholder for linking Google to an existing signed-in account.

    Reuses the same callback but requires an active session; wired up in M3.
    """
    if not settings.google_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Not configured.")
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="Linking lands in M3.")
