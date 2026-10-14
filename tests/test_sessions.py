from en2kr.audio.sessions import AudioApp, ProcInfo, playing_elsewhere, resolve_target_pid

PROCS = [ProcInfo(10, 1, "Game.exe"), ProcInfo(11, 10, "Game.exe"), ProcInfo(20, 1, "Game.exe"),
         ProcInfo(30, 1, "other.exe")]


def test_prefers_playing_instance_over_stale_session():
    audio = {20: AudioApp(20, "Game.exe", False, 0.0), 11: AudioApp(11, "Game.exe", True, 0.2)}
    assert resolve_target_pid("game.exe", PROCS, audio) == 11


def test_falls_back_to_root_instance_without_sessions():
    assert resolve_target_pid("Game.exe", PROCS[:2], {}) == 10


def test_missing_process():
    assert resolve_target_pid("nope.exe", PROCS, {}) is None


def test_switches_to_playing_child_spawned_after_capture():
    # loopback capture does not include children created after it started
    audio = {11: AudioApp(11, "Game.exe", True, 0.3)}
    assert playing_elsewhere("Game.exe", 10, PROCS, audio) == 11
    assert playing_elsewhere("Game.exe", 20, PROCS, audio) == 11


def test_no_switch_while_captured_process_plays():
    audio = {10: AudioApp(10, "Game.exe", True, 0.2), 20: AudioApp(20, "Game.exe", True, 0.4)}
    assert playing_elsewhere("Game.exe", 10, PROCS, audio) is None


def test_descendant_with_other_name_is_followed():
    procs = PROCS + [ProcInfo(40, 30, "renderer.exe")]
    audio = {40: AudioApp(40, "renderer.exe", True, 0.3)}
    assert playing_elsewhere("other.exe", 30, procs, audio) == 40
