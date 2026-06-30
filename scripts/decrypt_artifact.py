"""decrypt_artifact — decrypt a chat-attachment AST envelope (T-CAT.5/W4).

Usage:
    uv run python scripts/decrypt_artifact.py path/to/envelope.json
    cat envelope.json | uv run python scripts/decrypt_artifact.py -

Reads the envelope produced by `ASTCipher.encrypt_ast()`
(`{"version", "algorithm", "nonce", "ciphertext"}`, as stored at the
artifact's `storage_key` in MinIO) and prints the decrypted plaintext
markdown to stdout.

Requires the same KEK/DEK pair the artifact was encrypted under:
    RAGENT_KEK_BASE64
    RAGENT_ENCRYPTED_DEK_BASE64
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from ragent.security.ast_cipher import ASTCipher, ASTDecryptionError
from ragent.security.key_manager import KeyManager, KeyManagerError


def decrypt_envelope(envelope: dict, *, kek_b64: str, encrypted_dek_b64: str) -> str:
    key_manager = KeyManager(kek_b64=kek_b64, encrypted_dek_b64=encrypted_dek_b64)
    return ASTCipher(key_manager).decrypt_ast(envelope)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "envelope_path", help="Path to the envelope JSON file, or '-' to read stdin."
    )
    args = parser.parse_args(argv)

    if args.envelope_path == "-":
        raw = sys.stdin.read()
    else:
        with open(args.envelope_path) as f:
            raw = f.read()
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON envelope: {exc}", file=sys.stderr)
        return 1

    kek_b64 = os.environ.get("RAGENT_KEK_BASE64", "")
    encrypted_dek_b64 = os.environ.get("RAGENT_ENCRYPTED_DEK_BASE64", "")
    if not kek_b64 or not encrypted_dek_b64:
        print(
            "error: RAGENT_KEK_BASE64 and RAGENT_ENCRYPTED_DEK_BASE64 must be set",
            file=sys.stderr,
        )
        return 1

    try:
        plaintext = decrypt_envelope(envelope, kek_b64=kek_b64, encrypted_dek_b64=encrypted_dek_b64)
    except (KeyManagerError, ASTDecryptionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(plaintext)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
