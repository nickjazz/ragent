"""Tests for scripts/decrypt_artifact.py — AST envelope decrypt CLI."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import types
from pathlib import Path

import pytest

from ragent.security.ast_cipher import ASTCipher
from ragent.security.key_manager import KeyManager

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "decrypt_artifact.py"


def _load_script() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("decrypt_artifact", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    return _load_script()


@pytest.fixture
def kek_dek_pair() -> tuple[str, str]:
    kek = os.urandom(32)
    dek = os.urandom(32)
    kek_b64 = base64.b64encode(kek).decode()
    encrypted_dek_b64 = KeyManager.wrap(kek_b64, dek)
    return kek_b64, encrypted_dek_b64


def _encrypt(kek_b64: str, encrypted_dek_b64: str, plaintext: str) -> dict:
    key_manager = KeyManager(kek_b64=kek_b64, encrypted_dek_b64=encrypted_dek_b64)
    return ASTCipher(key_manager).encrypt_ast(plaintext)


def test_decrypt_envelope_round_trips(script, kek_dek_pair):
    kek_b64, encrypted_dek_b64 = kek_dek_pair
    envelope = _encrypt(kek_b64, encrypted_dek_b64, "# hello\n\nworld")

    plaintext = script.decrypt_envelope(
        envelope, kek_b64=kek_b64, encrypted_dek_b64=encrypted_dek_b64
    )

    assert plaintext == "# hello\n\nworld"


def test_main_reads_envelope_from_file(script, kek_dek_pair, tmp_path, monkeypatch, capsys):
    kek_b64, encrypted_dek_b64 = kek_dek_pair
    envelope = _encrypt(kek_b64, encrypted_dek_b64, "file content")
    envelope_path = tmp_path / "envelope.json"
    envelope_path.write_text(json.dumps(envelope))

    monkeypatch.setenv("RAGENT_KEK_BASE64", kek_b64)
    monkeypatch.setenv("RAGENT_ENCRYPTED_DEK_BASE64", encrypted_dek_b64)

    exit_code = script.main([str(envelope_path)])

    assert exit_code == 0
    assert capsys.readouterr().out == "file content\n"


def test_main_reads_envelope_from_stdin(script, kek_dek_pair, monkeypatch, capsys):
    kek_b64, encrypted_dek_b64 = kek_dek_pair
    envelope = _encrypt(kek_b64, encrypted_dek_b64, "stdin content")

    monkeypatch.setenv("RAGENT_KEK_BASE64", kek_b64)
    monkeypatch.setenv("RAGENT_ENCRYPTED_DEK_BASE64", encrypted_dek_b64)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(envelope)))

    exit_code = script.main(["-"])

    assert exit_code == 0
    assert capsys.readouterr().out == "stdin content\n"


def test_main_missing_env_vars_exits_nonzero(script, kek_dek_pair, tmp_path, monkeypatch, capsys):
    kek_b64, encrypted_dek_b64 = kek_dek_pair
    envelope = _encrypt(kek_b64, encrypted_dek_b64, "content")
    envelope_path = tmp_path / "envelope.json"
    envelope_path.write_text(json.dumps(envelope))

    monkeypatch.delenv("RAGENT_KEK_BASE64", raising=False)
    monkeypatch.delenv("RAGENT_ENCRYPTED_DEK_BASE64", raising=False)

    exit_code = script.main([str(envelope_path)])

    assert exit_code == 1
    assert "error:" in capsys.readouterr().err


def test_main_invalid_json_exits_nonzero(script, kek_dek_pair, tmp_path, monkeypatch, capsys):
    kek_b64, encrypted_dek_b64 = kek_dek_pair
    envelope_path = tmp_path / "envelope.json"
    envelope_path.write_text("not json")

    monkeypatch.setenv("RAGENT_KEK_BASE64", kek_b64)
    monkeypatch.setenv("RAGENT_ENCRYPTED_DEK_BASE64", encrypted_dek_b64)

    exit_code = script.main([str(envelope_path)])

    assert exit_code == 1
    assert "invalid JSON envelope" in capsys.readouterr().err


def test_main_wrong_key_exits_nonzero(script, kek_dek_pair, tmp_path, monkeypatch, capsys):
    kek_b64, encrypted_dek_b64 = kek_dek_pair
    envelope = _encrypt(kek_b64, encrypted_dek_b64, "content")
    envelope_path = tmp_path / "envelope.json"
    envelope_path.write_text(json.dumps(envelope))

    wrong_kek_b64 = base64.b64encode(os.urandom(32)).decode()
    wrong_encrypted_dek_b64 = KeyManager.wrap(wrong_kek_b64, os.urandom(32))
    monkeypatch.setenv("RAGENT_KEK_BASE64", wrong_kek_b64)
    monkeypatch.setenv("RAGENT_ENCRYPTED_DEK_BASE64", wrong_encrypted_dek_b64)

    exit_code = script.main([str(envelope_path)])

    assert exit_code == 1
    assert "error:" in capsys.readouterr().err


def test_main_tampered_ciphertext_exits_nonzero(
    script, kek_dek_pair, tmp_path, monkeypatch, capsys
):
    kek_b64, encrypted_dek_b64 = kek_dek_pair
    envelope = _encrypt(kek_b64, encrypted_dek_b64, "content")
    envelope["ciphertext"] = "00" * (len(envelope["ciphertext"]) // 2)
    envelope_path = tmp_path / "envelope.json"
    envelope_path.write_text(json.dumps(envelope))

    monkeypatch.setenv("RAGENT_KEK_BASE64", kek_b64)
    monkeypatch.setenv("RAGENT_ENCRYPTED_DEK_BASE64", encrypted_dek_b64)

    exit_code = script.main([str(envelope_path)])

    assert exit_code == 1
    assert "error:" in capsys.readouterr().err
