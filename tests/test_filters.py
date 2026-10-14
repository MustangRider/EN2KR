from en2kr.filters import AUTO_BAN_COUNT, HallucinationFilter


def test_blocked_phrase_ignores_case_and_punctuation():
    f = HallucinationFilter(["Thank you."])
    assert f.check("thank you") == "차단 목록"
    assert f.check("THANK YOU!!") == "차단 목록"
    assert f.check("Thank you for the shield, mate") is None


def test_confident_repeated_callout_is_never_auto_banned():
    f = HallucinationFilter()
    for i in range(10):
        assert f.check("Behind you!", avg_logprob=-0.2, no_speech_prob=0.05, now=i) is None
    assert f.session_bans == []


def test_low_confidence_repeat_is_banned_for_session_only():
    banned = []
    f = HallucinationFilter(on_auto_ban=banned.append)
    results = [f.check("Oh.", avg_logprob=-0.9, no_speech_prob=0.5, now=i) for i in range(AUTO_BAN_COUNT)]
    assert results[-1] == "자동 차단(이번 실행)" and results[:-1] == [None] * (AUTO_BAN_COUNT - 1)
    assert banned == ["Oh."]
    assert f.check("oh", now=10) == "자동 차단(이번 실행)"
    f.unban("Oh.")
    assert f.check("oh", avg_logprob=-0.1, now=11) is None


def test_subtitle_artefacts():
    assert HallucinationFilter().check("Thanks for watching and subscribe!") == "자막형 환청"
