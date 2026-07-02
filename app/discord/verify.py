"""Ed25519 signature verification for Discord interactions.

Discord signs each interaction request; the signature covers
``timestamp + raw_body`` and is verified with the application's public key
(hex). Unverified requests must be rejected with 401 (Discord requirement).
"""

from __future__ import annotations

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


def verify_signature(public_key: str, signature: str, timestamp: str, body: bytes) -> bool:
    """Return True iff ``signature`` is valid for ``timestamp + body``.

    Returns False on a bad signature or malformed hex inputs — never raises,
    so callers can uniformly answer 401.
    """
    if not signature or not timestamp:
        return False
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key))
        verify_key.verify(timestamp.encode() + body, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False
