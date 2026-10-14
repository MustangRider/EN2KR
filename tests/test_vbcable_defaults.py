import pytest

from en2kr.audio import vbcable
from en2kr.audio.endpoints import CAPTURE, RENDER, Endpoint

CABLE_IN = Endpoint("r-cable", "CABLE Input(VB-Audio Virtual Cable)", False)
HEADSET = Endpoint("r-head", "Headset", False)
CABLE_OUT = Endpoint("c-cable", "CABLE Output(VB-Audio Virtual Cable)", False)
MIC = Endpoint("c-mic", "Blue Snowball", False)
WEBCAM = Endpoint("c-cam", "Webcam Mic", False)


@pytest.fixture
def fake_audio(monkeypatch):
    state = {
        "eps": {RENDER: [CABLE_IN, HEADSET], CAPTURE: [CABLE_OUT, MIC]},
        "defaults": {RENDER: {0: "r-head", 1: "r-head", 2: "r-head"},
                     CAPTURE: {0: "c-cable", 1: "c-cable", 2: "c-cable"}},
        "fail": False,
    }

    def set_default(dev, flow, roles):
        if state["fail"]:
            return False
        for r in roles:
            state["defaults"][flow][r] = dev
        return True

    monkeypatch.setattr(vbcable, "list_endpoints", lambda flow: state["eps"][flow])
    monkeypatch.setattr(vbcable, "default_ids", lambda flow: dict(state["defaults"][flow]))
    monkeypatch.setattr(vbcable, "set_default", set_default)
    return state


def test_restores_only_mic_when_single_candidate(fake_audio):
    assert vbcable.default_input_is_cable() and not vbcable.default_output_is_cable()
    r = vbcable.fix_cable_defaults()
    assert r.changed == ["마이크 → Blue Snowball"] and not r.failed and not r.need_choice
    assert set(fake_audio["defaults"][CAPTURE].values()) == {"c-mic"}
    assert not vbcable.default_input_is_cable()


def test_snapshot_restores_per_role(fake_audio):
    fake_audio["eps"][CAPTURE].append(WEBCAM)
    snap = {str(CAPTURE): {"0": "c-mic", "1": "c-mic", "2": "c-cam"}}
    r = vbcable.fix_cable_defaults(snapshot=snap)
    assert fake_audio["defaults"][CAPTURE] == {0: "c-mic", 1: "c-mic", 2: "c-cam"}
    assert not r.need_choice


def test_ambiguous_without_snapshot_asks_user(fake_audio):
    fake_audio["eps"][CAPTURE].append(WEBCAM)
    r = vbcable.fix_cable_defaults()
    assert [e.name for e in r.need_choice[CAPTURE]] == ["Blue Snowball", "Webcam Mic"]
    r = vbcable.fix_cable_defaults(chosen={CAPTURE: "c-cam"})
    assert set(fake_audio["defaults"][CAPTURE].values()) == {"c-cam"}


def test_failure_is_reported_for_manual_fix(fake_audio):
    fake_audio["fail"] = True
    r = vbcable.fix_cable_defaults()
    assert r.failed == ["마이크"] and not r.changed


def test_snapshot_skips_cable_defaults(fake_audio):
    snap = vbcable.snapshot_defaults()
    assert snap[str(RENDER)] == {"0": "r-head", "1": "r-head", "2": "r-head"}
    assert snap[str(CAPTURE)] == {}
