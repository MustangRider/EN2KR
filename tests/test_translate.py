import threading
import time

import pytest

from en2kr.translate import providers as P
from en2kr.translate.service import Job, TranslationService
from en2kr.translate.text import Glossary, Slang


# ---------- glossary / slang ----------
def test_glossary_longest_term_wins():
    g = Glossary({"raider": "레이더", "Raider Hatch Key": "레이더 해치 키"})
    out, used = g.protect("Use the raider hatch key, raider!")
    assert out == "Use the 【레이더 해치 키】, 【레이더】!"
    assert used == ["레이더 해치 키", "레이더"]


def test_glossary_plural_and_case():
    g = Glossary({"Raider": "레이더", "Bandage": "붕대"})
    out, _ = g.protect("RAIDERS have bandages")
    assert out == "【레이더】 have 【붕대】"


def test_glossary_whole_words_only():
    g = Glossary({"Pop": "팝", "Tick": "틱"})
    assert g.protect("popular pop-up ticket")[0] == "popular pop-up ticket"


def test_glossary_restore_tolerates_rewritten_brackets():
    used = ["레이더", "블루 게이트"]
    assert Glossary.restore("【레이더】가 [블루 게이트] 뒤에", used) == "레이더가 블루 게이트 뒤에"
    assert Glossary.restore("「 레이더 」 조심", used) == "레이더 조심"


def test_slang_expansion():
    s = Slang({"gg": "good game", "nade": "grenade"})
    assert s.expand("GG, nice nade") == "good game, nice grenade"
    assert s.expand("eggs") == "eggs"


# ---------- service ----------
class FakeProvider(P.Provider):
    def __init__(self, label, behaviour):
        super().__init__({"key": "x"})
        self.id = label.lower()
        self.label = label
        self.behaviour = behaviour
        self.calls = []

    def translate(self, text, source_lang=None):
        self.calls.append(text)
        b = self.behaviour
        if isinstance(b, Exception):
            raise b
        return b(text)


def make_service(*provs, results=None):
    svc = TranslationService(on_result=(results.append if results is not None else lambda r: None))
    svc.configure({"translate": {"order": []}, "glossary": {"raider": "레이더"}, "slang": {"gg": "good game"}})
    svc._providers = list(provs)
    return svc


def test_fallback_to_next_provider_on_quota():
    a = FakeProvider("A", P.QuotaError("quota"))
    b = FakeProvider("B", lambda t: "번역:" + t)
    svc = make_service(a, b)
    r = svc.translate_now("hello")
    assert r.text == "번역:hello" and r.provider == "B"
    svc.translate_now("again")
    assert len(a.calls) == 1, "provider on quota cooldown must be skipped"


def test_all_providers_fail_reports_error():
    svc = make_service(FakeProvider("A", P.AuthError("bad key")), FakeProvider("B", P.TransientError("down")))
    r = svc.translate_now("hello")
    assert r.error and "bad key" in r.error and "down" in r.error


def test_cache_avoids_second_call():
    b = FakeProvider("B", lambda t: t.upper())
    svc = make_service(b)
    assert svc.translate_now("Behind you").text == "BEHIND YOU"
    r = svc.translate_now("behind you")
    assert r.cached and len(b.calls) == 1


def test_glossary_and_slang_applied_around_provider():
    b = FakeProvider("B", lambda t: t.replace("good game", "잘했어").replace(" the ", " "))
    svc = make_service(b)
    r = svc.translate_now("gg the raider")
    assert b.calls == ["good game the 【레이더】"]
    assert r.text == "잘했어 레이더"


def test_no_providers():
    svc = make_service()
    assert "설정" in svc.translate_now("x").error


def test_submit_never_blocks_and_drops_oldest():
    gate = threading.Event()
    slow = FakeProvider("S", lambda t: (gate.wait(5), t)[1])
    results = []
    svc = make_service(slow, results=results)
    svc.MAX_PENDING = 3
    svc.start()
    try:
        t0 = time.perf_counter()
        for i in range(10):
            svc.submit(Job(i, f"line {i}"))
        assert time.perf_counter() - t0 < 0.5
        gate.set()
        deadline = time.time() + 5
        while time.time() < deadline and (not results or results[-1].line_id != 9):
            time.sleep(0.05)
    finally:
        svc.stop()
    ids = [r.line_id for r in results]
    assert ids[-1] == 9 and len(ids) <= 4


def test_superseded_line_translated_once():
    gate = threading.Event()
    prov = FakeProvider("S", lambda t: (gate.wait(5), t)[1])
    results = []
    svc = make_service(prov, results=results)
    svc.start()
    try:
        svc.submit(Job(1, "first"))
        time.sleep(0.2)  # worker is now blocked on line 1
        svc.submit(Job(2, "part one"))
        svc.submit(Job(2, "part one part two", replace=True))
        gate.set()
        deadline = time.time() + 5
        while time.time() < deadline and len(results) < 2:
            time.sleep(0.05)
        time.sleep(0.2)
    finally:
        svc.stop()
    assert prov.calls == ["first", "part one part two"]


@pytest.mark.parametrize("pid", list(P.PROVIDERS))
def test_providers_report_unconfigured(pid):
    assert P.create(pid, {}).configured is False
