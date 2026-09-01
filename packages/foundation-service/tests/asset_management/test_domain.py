from __future__ import annotations

import pytest
from a13n_service.asset_management.domain import normalize_asset_filename, normalize_media_type


def test_filename_is_nfc_normalized_and_bounded_display_metadata() -> None:
    assert normalize_asset_filename("re\u0301sume\u0301.pdf") == "résumé.pdf"

    for invalid in ("", " report.pdf", "report.pdf ", "a/b", "a\\b", "a\x00b", "a\nb", "x" * 257):
        with pytest.raises(ValueError):
            normalize_asset_filename(invalid)


def test_media_type_is_an_extensible_lowercase_essence() -> None:
    assert normalize_media_type(None) == "application/octet-stream"
    assert normalize_media_type("Application/Vnd.Example+JSON") == "application/vnd.example+json"

    for invalid in ("text", "text/plain; charset=utf-8", "text/*", "*/json", "text/ plain"):
        with pytest.raises(ValueError):
            normalize_media_type(invalid)
