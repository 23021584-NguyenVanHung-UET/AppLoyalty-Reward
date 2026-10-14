"""Verify the original bytes, never re-encoded JSON."""
import base64
import hashlib
import hmac


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    )
    try:
        return hmac.compare_digest(expected, signature.encode("ascii"))
    except (UnicodeEncodeError, AttributeError):
        return False
