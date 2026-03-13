"""Tests for TData converter utilities."""

import json
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from utils.tdata_converter import (
    ConvertedAccount,
    TDataConversionResult,
    generate_tdata_metadata_json,
    is_tdata_directory,
    is_tdata_zip,
)


def test_generate_tdata_metadata_json():
    meta = generate_tdata_metadata_json("+79001234567", first_name="Test")
    assert meta["app_id"] == 2040
    assert meta["app_hash"] == "b18441a1ff607e10a989891a5462e627"
    assert meta["device"] == "Telegram Desktop"
    assert meta["sdk"] == "Windows 10"
    assert meta["first_name"] == "Test"
    assert meta["phone"] == "+79001234567"
    assert meta["session_file"] == "79001234567.session"
    assert meta["source"] == "tdata"


def test_is_tdata_directory_with_key_data(tmp_path: Path):
    tdata_dir = tmp_path / "tdata"
    tdata_dir.mkdir()
    (tdata_dir / "key_data").write_bytes(b"\x00" * 16)
    assert is_tdata_directory(tdata_dir)


def test_is_tdata_directory_nested(tmp_path: Path):
    parent = tmp_path / "account1"
    parent.mkdir()
    tdata_dir = parent / "tdata"
    tdata_dir.mkdir()
    (tdata_dir / "key_data").write_bytes(b"\x00" * 16)
    assert is_tdata_directory(parent)


def test_is_tdata_directory_empty(tmp_path: Path):
    assert not is_tdata_directory(tmp_path)


def test_is_tdata_zip_positive():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("tdata/key_data", b"\x00" * 16)
    assert is_tdata_zip(buf.getvalue())


def test_is_tdata_zip_negative():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("79001234567.session", b"session_data")
        zf.writestr("79001234567.json", json.dumps({"app_id": 123}))
    assert not is_tdata_zip(buf.getvalue())


def test_is_tdata_zip_bad_data():
    assert not is_tdata_zip(b"not a zip at all")


def test_converted_account_dataclass():
    acct = ConvertedAccount(
        phone="+79001234567",
        session_bytes=b"fake_session",
        metadata={"app_id": 2040},
        source="tdata",
    )
    assert acct.phone == "+79001234567"
    assert acct.source == "tdata"


def test_tdata_conversion_result_dataclass():
    result = TDataConversionResult(
        accounts=[],
        errors=["test error"],
    )
    assert len(result.errors) == 1
    assert result.errors[0] == "test error"


def test_metadata_passes_validation():
    """Verify TData-generated metadata passes the existing validation pipeline."""
    from utils.account_uploads import validate_and_normalize_account_metadata

    meta = generate_tdata_metadata_json("+79001234567")
    normalized = validate_and_normalize_account_metadata(
        meta,
        expected_phone="+79001234567",
        expected_session_file="79001234567.session",
    )
    assert normalized["app_id"] == 2040
    assert normalized["device"] == "Telegram Desktop"
    assert normalized["phone"] == "+79001234567"
