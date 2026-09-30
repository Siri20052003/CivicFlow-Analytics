from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from civicflow.auth import ApiAuthenticator, AuthConfigError, load_api_credentials


def credential_json(*, expires_at: str | None = None) -> str:
    settings: dict[str, object] = {
        "token_sha256": hashlib.sha256(b"rotation-secret").hexdigest(),
        "scopes": ["cases:read", "cases:write"],
    }
    if expires_at is not None:
        settings["expires_at"] = expires_at
    return json.dumps({"batch-producer": settings})


def test_hashed_configuration_authenticates_without_retaining_plaintext() -> None:
    credentials = load_api_credentials(credential_json())
    authenticator = ApiAuthenticator(credentials)

    principal, reason = authenticator.authenticate("rotation-secret")

    assert reason is None
    assert principal is not None
    assert principal.key_id == "batch-producer"
    assert principal.scopes == frozenset({"cases:read", "cases:write"})
    assert "rotation-secret" not in repr(credentials)


def test_expiration_and_key_rotation_are_deterministic() -> None:
    expiry = datetime.now(UTC) + timedelta(hours=1)
    credentials = load_api_credentials(credential_json(expires_at=expiry.isoformat()))
    authenticator = ApiAuthenticator(credentials)

    current, _ = authenticator.authenticate("rotation-secret", as_of=expiry - timedelta(seconds=1))
    expired, reason = authenticator.authenticate("rotation-secret", as_of=expiry)

    assert current is not None
    assert expired is None
    assert reason == "expired"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "at least one"),
        ('{"client":{"token":"plaintext","scopes":["cases:read"]}}', "plaintext"),
        (
            json.dumps(
                {
                    "client": {
                        "token_sha256": "0" * 64,
                        "scopes": ["administrator"],
                    }
                }
            ),
            "unknown scopes",
        ),
        (
            json.dumps(
                {
                    "first": {"token_sha256": "1" * 64, "scopes": ["cases:read"]},
                    "second": {"token_sha256": "1" * 64, "scopes": ["cases:write"]},
                }
            ),
            "unique",
        ),
    ],
)
def test_unsafe_auth_configuration_is_rejected(value, message) -> None:
    with pytest.raises(AuthConfigError, match=message):
        load_api_credentials(value)
