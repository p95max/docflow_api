import jwt
import pytest

from app.services.security import (
    create_access_token,
    create_document_preview_token,
    decode_access_token,
)


def test_access_token_decoder_rejects_document_preview_token() -> None:
    preview_token, _ = create_document_preview_token(document_id=1, owner_id=1)

    with pytest.raises(jwt.InvalidTokenError, match="purpose"):
        decode_access_token(preview_token)


def test_access_token_decoder_accepts_access_token() -> None:
    payload = decode_access_token(create_access_token(subject="1"))

    assert payload["sub"] == "1"
    assert payload["purpose"] == "access"
