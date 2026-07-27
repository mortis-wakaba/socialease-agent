"""At-rest protection boundary for persisted conversation content."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.auth.tokens import auth_mode


class ConversationContentProtectionError(RuntimeError):
    """Raised when durable content protection is unavailable or invalid."""


@dataclass(frozen=True)
class ProtectedContent:
    """Storage projection containing plaintext or authenticated ciphertext."""

    plaintext: str | None
    ciphertext: bytes | None
    nonce: bytes | None
    key_version: str | None


class ConversationContentProtector:
    """Protect and recover conversation text for a configured runtime."""

    def protect(self, content: str, *, associated_data: bytes) -> ProtectedContent:
        """Return the storage projection for plaintext content."""
        raise NotImplementedError

    def recover(
        self,
        protected: ProtectedContent,
        *,
        associated_data: bytes,
    ) -> str:
        """Recover plaintext after validating its storage projection."""
        raise NotImplementedError


class LocalPlaintextContentProtector(ConversationContentProtector):
    """Keep local SQLite/demo content readable without claiming encryption."""

    def protect(self, content: str, *, associated_data: bytes) -> ProtectedContent:
        return ProtectedContent(
            plaintext=content,
            ciphertext=None,
            nonce=None,
            key_version=None,
        )

    def recover(
        self,
        protected: ProtectedContent,
        *,
        associated_data: bytes,
    ) -> str:
        if protected.plaintext is None:
            raise ConversationContentProtectionError("plaintext content is unavailable")
        return protected.plaintext


class AESGCMConversationContentProtector(ConversationContentProtector):
    """Encrypt production content with AES-256-GCM and versioned keys."""

    def __init__(self, *, key: bytes, key_version: str) -> None:
        if len(key) != 32:
            raise ConversationContentProtectionError(
                "conversation content key must contain exactly 32 bytes"
            )
        if not key_version:
            raise ConversationContentProtectionError(
                "conversation content key version is required"
            )
        self._cipher = AESGCM(key)
        self._key_version = key_version

    def protect(self, content: str, *, associated_data: bytes) -> ProtectedContent:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(
            nonce,
            content.encode("utf-8"),
            associated_data,
        )
        return ProtectedContent(
            plaintext=None,
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=self._key_version,
        )

    def recover(
        self,
        protected: ProtectedContent,
        *,
        associated_data: bytes,
    ) -> str:
        if (
            protected.ciphertext is None
            or protected.nonce is None
            or protected.key_version != self._key_version
        ):
            raise ConversationContentProtectionError(
                "encrypted content metadata is unavailable or uses an unknown key"
            )
        try:
            plaintext = self._cipher.decrypt(
                protected.nonce,
                protected.ciphertext,
                associated_data,
            )
        except InvalidTag as exc:
            raise ConversationContentProtectionError(
                "conversation content authentication failed"
            ) from exc
        return plaintext.decode("utf-8")


def configured_content_protector() -> ConversationContentProtector:
    """Resolve a protector, failing closed when production has no valid key."""
    encoded_key = os.getenv("SOCIALEASE_CONVERSATION_CONTENT_KEY", "").strip()
    key_version = os.getenv(
        "SOCIALEASE_CONVERSATION_CONTENT_KEY_VERSION",
        "",
    ).strip()
    if encoded_key:
        try:
            key = urlsafe_b64decode(encoded_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ConversationContentProtectionError(
                "conversation content key is not valid URL-safe base64"
            ) from exc
        return AESGCMConversationContentProtector(
            key=key,
            key_version=key_version,
        )
    if auth_mode() == "production":
        raise ConversationContentProtectionError(
            "production conversation persistence requires "
            "SOCIALEASE_CONVERSATION_CONTENT_KEY"
        )
    return LocalPlaintextContentProtector()


def encode_content_key(key: bytes) -> str:
    """Encode a 32-byte key for configuration and test fixtures."""
    return urlsafe_b64encode(key).decode("ascii")
