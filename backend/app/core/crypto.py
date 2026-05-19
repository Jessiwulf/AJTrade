from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os
from typing import Tuple

# AES-256 GCM helpers for application-layer encryption of API keys.
# Keys are encrypted with a random 96-bit nonce and stored with nonce+ciphertext.

def generate_data_key() -> bytes:
    # In production, derive this from a KMS or environment variable securely.
    # AES-256 requires exactly 32 bytes (256 bits).
    key = os.environ.get('AJTRADE_DATA_KEY')
    if key:
        key_bytes = key.encode('utf-8')
    else:
        # WARNING: fallback insecure key for local dev only (exactly 32 bytes)
        key_bytes = b'dev-localkey-please-replace-00000'
    # Ensure exactly 32 bytes: truncate if longer, pad with null if shorter
    return (key_bytes[:32]).ljust(32, b'\x00')


def encrypt_api_key(plaintext: bytes, aad: bytes = b"") -> bytes:
    aesgcm = AESGCM(generate_data_key())
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext, aad)
    return nonce + ct


def decrypt_api_key(blob: bytes, aad: bytes = b"") -> bytes:
    nonce = blob[:12]
    ct = blob[12:]
    aesgcm = AESGCM(generate_data_key())
    return aesgcm.decrypt(nonce, ct, aad)
