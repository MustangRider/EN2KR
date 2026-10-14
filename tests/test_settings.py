import base64
import json
import os

from en2kr import secret_store
from en2kr.settings import DEFAULTS, Settings


def test_defaults_when_missing(tmp_path):
    s = Settings(str(tmp_path / "s.json")).load()
    assert s.get("vad.threshold") == DEFAULTS["vad"]["threshold"]
    assert s.load_error is None


def test_bom_file_is_read(tmp_path):
    p = tmp_path / "s.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"vad": {"threshold": 0.42}}).encode("utf-8"))
    s = Settings(str(p)).load()
    assert s.get("vad.threshold") == 0.42
    assert s.load_error is None


def test_corrupt_file_is_preserved_not_overwritten(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{ not json", encoding="utf-8")
    s = Settings(str(p)).load()
    assert s.load_error
    backups = [f for f in os.listdir(tmp_path) if ".corrupt-" in f]
    assert len(backups) == 1
    assert (tmp_path / backups[0]).read_text(encoding="utf-8") == "{ not json"


def test_save_keeps_all_keys_and_unknown_keys(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"ui": {"cpu_warning_dismissed": True}, "future_key": 1}), encoding="utf-8")
    s = Settings(str(p)).load()
    s.set("vad.silence_sec", 0.9)
    s.save()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["ui"]["cpu_warning_dismissed"] is True   # v1 lost this on every save
    assert data["future_key"] == 1
    assert data["overlay"]["w"] == DEFAULTS["overlay"]["w"]
    assert data["vad"]["silence_sec"] == 0.9


def test_user_collections_replace_defaults(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"glossary": {"Foo": "푸"}}), encoding="utf-8")
    s = Settings(str(p)).load()
    assert s.get("glossary") == {"Foo": "푸"}


def test_api_key_encrypted_on_disk(tmp_path):
    p = tmp_path / "s.json"
    s = Settings(str(p)).load()
    s.set("translate.providers.deepl.key", "secret-key-123:fx")
    s.save()
    raw = p.read_text(encoding="utf-8")
    assert "secret-key-123" not in raw
    stored = json.loads(raw)["translate"]["providers"]["deepl"]["key"]
    assert stored.startswith("dpapi:")
    assert Settings(str(p)).load().get("translate.providers.deepl.key") == "secret-key-123:fx"


def test_secret_roundtrip():
    assert secret_store.decrypt(secret_store.encrypt("한글 key")) == "한글 key"
    assert secret_store.decrypt("") == ""


def test_v1_migration(tmp_path, isolated_home):
    legacy = isolated_home / "legacy.json"
    v1 = {
        "hallucinations": ["Oh"], "glossary": {"raider": "레이더"},
        "vad_threshold": 0.2, "silence_duration": 0.2, "selected_api": "DeepL",
        "api_key": base64.b64encode(b"abc:fx").decode(), "azure_region": "",
        "x": 10, "y": 20, "w": 300, "h": 100, "font_size": 12, "line_spacing": 6,
        "max_display_lines": 3, "ignore_cuda_warning": True,
    }
    legacy.write_bytes(b"\xef\xbb\xbf" + json.dumps(v1, ensure_ascii=False).encode("utf-8"))
    p = tmp_path / "new.json"
    s = Settings(str(p)).load()
    assert s.migrated_from == str(legacy)
    assert s.get("translate.order") == ["deepl"]
    assert s.get("translate.providers.deepl.key") == "abc:fx"
    assert s.get("overlay.max_lines") == 3
    assert s.get("vad.silence_sec") == 0.2
    assert s.get("ui.cpu_warning_dismissed") is True
    assert s.get("hallucinations") == ["Oh"]
    assert p.exists()


def test_old_text_chat_slang_defaults_are_replaced(tmp_path):
    from en2kr.settings import _OLD_DEFAULT_SLANG, DEFAULT_SLANG
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"slang": _OLD_DEFAULT_SLANG}), encoding="utf-8")
    assert Settings(str(p)).load().get("slang") == DEFAULT_SLANG
    p.write_text(json.dumps({"slang": {"gg": "good game", "flank": "go around"}}), encoding="utf-8")
    assert Settings(str(p)).load().get("slang") == {"gg": "good game", "flank": "go around"}  # user-edited: kept
