"""Hashed service credentials and least-privilege API scope enforcement."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

ALLOWED_SCOPES = frozenset({"cases:read", "cases:write", "ops:read"})
DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class AuthConfigError(ValueError):
    """Raised when service credential configuration is unsafe or malformed."""


@dataclass(frozen=True, slots=True)
class ApiCredential:
    key_id: str
    token_sha256: str
    scopes: frozenset[str]
    expires_at: datetime | None = None

    @classmethod
    def from_token(
        cls,
        key_id: str,
        token: str,
        scopes: set[str] | frozenset[str],
        *,
        expires_at: datetime | None = None,
    ) -> ApiCredential:
        """Build a credential for tests or local tooling without retaining the token."""
        if len(token) < 16:
            raise AuthConfigError("token must contain at least 16 characters")
        return cls(
            key_id=key_id,
            token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            scopes=frozenset(scopes),
            expires_at=expires_at,
        ).validated()

    def validated(self) -> ApiCredential:
        if not KEY_ID_PATTERN.fullmatch(self.key_id):
            raise AuthConfigError(f"invalid key_id: {self.key_id!r}")
        if not DIGEST_PATTERN.fullmatch(self.token_sha256):
            raise AuthConfigError(
                f"{self.key_id}: token_sha256 must be 64 lowercase hex characters"
            )
        if not self.scopes:
            raise AuthConfigError(f"{self.key_id}: at least one scope is required")
        unknown = sorted(self.scopes - ALLOWED_SCOPES)
        if unknown:
            raise AuthConfigError(f"{self.key_id}: unknown scopes: {', '.join(unknown)}")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise AuthConfigError(f"{self.key_id}: expires_at must include a timezone")
        return self


@dataclass(frozen=True, slots=True)
class ApiPrincipal:
    key_id: str
    scopes: frozenset[str]


def load_api_credentials(value: str) -> tuple[ApiCredential, ...]:
    """Parse the JSON environment boundary; plaintext tokens are never accepted."""
    if not value.strip():
        raise AuthConfigError("CIVICFLOW_API_KEYS must configure at least one credential")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise AuthConfigError("CIVICFLOW_API_KEYS must be valid JSON") from error
    if not isinstance(payload, dict) or not payload:
        raise AuthConfigError("CIVICFLOW_API_KEYS must be a non-empty object")

    credentials: list[ApiCredential] = []
    digests: set[str] = set()
    for key_id, settings in payload.items():
        if not isinstance(key_id, str) or not isinstance(settings, dict):
            raise AuthConfigError("credential entries must map key IDs to objects")
        if "token" in settings:
            raise AuthConfigError(f"{key_id}: plaintext token configuration is forbidden")
        allowed_fields = {"token_sha256", "scopes", "expires_at"}
        extra = sorted(set(settings) - allowed_fields)
        if extra:
            raise AuthConfigError(f"{key_id}: unsupported fields: {', '.join(extra)}")
        raw_scopes = settings.get("scopes")
        if not isinstance(raw_scopes, list) or not all(
            isinstance(scope, str) for scope in raw_scopes
        ):
            raise AuthConfigError(f"{key_id}: scopes must be a list of strings")
        raw_expiry = settings.get("expires_at")
        expires_at: datetime | None = None
        if raw_expiry is not None:
            if not isinstance(raw_expiry, str):
                raise AuthConfigError(f"{key_id}: expires_at must be an ISO-8601 string")
            try:
                expires_at = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
            except ValueError as error:
                raise AuthConfigError(f"{key_id}: expires_at must be ISO-8601") from error
        credential = ApiCredential(
            key_id=key_id,
            token_sha256=settings.get("token_sha256", ""),
            scopes=frozenset(raw_scopes),
            expires_at=expires_at,
        ).validated()
        if credential.token_sha256 in digests:
            raise AuthConfigError("token digests must be unique")
        digests.add(credential.token_sha256)
        credentials.append(credential)
    return tuple(credentials)


class ApiAuthenticator:
    """Authenticate bearer tokens with constant-time digest comparisons."""

    def __init__(self, credentials: tuple[ApiCredential, ...]) -> None:
        if not credentials:
            raise AuthConfigError("at least one API credential is required")
        self._credentials = tuple(credential.validated() for credential in credentials)

    def authenticate(
        self, token: str, *, as_of: datetime | None = None
    ) -> tuple[ApiPrincipal | None, str | None]:
        digest = hashlib.sha256(token.encode()).hexdigest()
        matched: ApiCredential | None = None
        for credential in self._credentials:
            if hmac.compare_digest(digest, credential.token_sha256):
                matched = credential
        if matched is None:
            return None, "invalid"
        now = as_of or datetime.now(UTC)
        if matched.expires_at is not None and matched.expires_at <= now:
            return None, "expired"
        return ApiPrincipal(key_id=matched.key_id, scopes=matched.scopes), None
