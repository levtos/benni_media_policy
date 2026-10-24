"""Current day phases and transitions for the legacy subwoofer policy (#34)."""

from dataclasses import replace

import bmp_const as C
import bmp_logic as L
import pytest

# Independent expectations: do not derive the test cases from SUB_ALLOWED_PHASES.
CURRENT_PHASES = (
    "forenoon",
    "midday",
    "afternoon",
    "late_afternoon",
    "evening",
    "late_evening",
)


def streaming(**changes):
    values = dict(
        context=C.CTX_STREAMING,
        subcontext="streaming_default",
        device="appletv",
        day_state="evening",
        local_minute_of_day=19 * 60,
        entertainment_active=True,
        denon_active=True,
        denon_configured=True,
        homepods_configured=True,
    )
    values.update(changes)
    return L.Inputs(**values)


@pytest.mark.parametrize("phase", CURRENT_PHASES + ("late_morning", "early_evening"))
def test_subwoofer_current_and_legacy_phases_allow_streaming(phase):
    decision, _ = L.decide(streaming(day_state=phase), L.OrchestratorState())
    assert decision.subwoofer_allowed is True
    assert decision.subwoofer_block_reason is None
    assert decision.audio_owner == C.AUDIO_OWNER_TV_DENON


@pytest.mark.parametrize("phase", CURRENT_PHASES)
def test_subwoofer_grind_exit_releases_current_context(phase):
    inp = streaming(
        day_state=phase, context=C.CTX_GAMING, subcontext=C.SUB_GAME_GRIND, device="ps5"
    )
    blocked, state = L.decide(inp, L.OrchestratorState())
    assert blocked.subwoofer_allowed is False
    assert blocked.subwoofer_block_reason == "grind_r16"
    assert blocked.volume_target_denon > 0

    # Keep both the phase and the prior orchestrator state when leaving Grind.
    released, _ = L.decide(streaming(day_state=phase), state)
    assert released.is_grind is False
    assert released.subwoofer_allowed is True
    assert released.subwoofer_block_reason is None
    assert released.audio_owner == C.AUDIO_OWNER_TV_DENON


def test_subwoofer_dayphase_transitions_recompute_with_same_state():
    state = L.OrchestratorState()
    for phase in ("early_morning", *CURRENT_PHASES, "early_night", "late_night"):
        decision, state = L.decide(streaming(day_state=phase), state)
        assert decision.subwoofer_allowed is (phase in CURRENT_PHASES), phase
        assert decision.subwoofer_block_reason == (
            None if phase in CURRENT_PHASES else "dayphase_window"
        ), phase


def test_subwoofer_streaming_music_streaming_rechecks_entertainment():
    first, state = L.decide(streaming(), L.OrchestratorState())
    assert first.subwoofer_allowed is True

    music, state = L.decide(
        streaming(
            context="idle",
            subcontext="none",
            device="none",
            homepods_state="playing",
            denon_active=False,
            entertainment_active=False,
        ),
        state,
    )
    assert music.audio_owner == C.AUDIO_OWNER_HOMEPODS
    assert music.audio_scenario == C.AUDIO_SCENARIO_MUSIC
    assert music.subwoofer_allowed is False
    assert music.subwoofer_block_reason == "no_entertainment"

    resumed, _ = L.decide(streaming(), state)
    assert resumed.audio_owner == C.AUDIO_OWNER_TV_DENON
    assert resumed.subwoofer_allowed is True
    assert resumed.subwoofer_block_reason is None


@pytest.mark.parametrize("phase", CURRENT_PHASES + ("late_morning", "early_evening"))
def test_subwoofer_0900_floor_is_unchanged(phase):
    before, state = L.decide(
        streaming(day_state=phase, local_minute_of_day=539), L.OrchestratorState()
    )
    assert before.subwoofer_allowed is False
    assert before.subwoofer_block_reason == "before_0900"
    at, _ = L.decide(streaming(day_state=phase, local_minute_of_day=540), state)
    assert at.subwoofer_allowed is True


@pytest.mark.parametrize("minute", (21 * 60 + 59, 22 * 60, 23 * 60 + 59))
def test_subwoofer_late_evening_has_no_new_2200_cutoff(minute):
    decision, _ = L.decide(
        streaming(day_state="late_evening", local_minute_of_day=minute), L.OrchestratorState()
    )
    assert decision.subwoofer_allowed is True


def test_subwoofer_existing_midnight_floor_is_preserved():
    decision, _ = L.decide(
        streaming(day_state="late_evening", local_minute_of_day=0), L.OrchestratorState()
    )
    assert decision.subwoofer_allowed is False
    assert decision.subwoofer_block_reason == "before_0900"


@pytest.mark.parametrize(
    "phase",
    ("early_morning", "early_night", "late_night", None, "unknown", "unavailable", "unsupported"),
)
def test_subwoofer_night_and_unknown_phases_remain_blocked(phase):
    decision, _ = L.decide(streaming(day_state=phase), L.OrchestratorState())
    assert decision.subwoofer_allowed is False
    assert decision.subwoofer_block_reason == "dayphase_window"


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"away_gate": True}, "away_gate"),
        ({"presence_state": "abwesend"}, "presence_away"),
        ({"quiet_mode": True}, "quiet_mode"),
        ({"entertainment_active": False}, "no_entertainment"),
        ({"headset_active": True}, "headset_active"),
        ({"opening_any_open": True, "denon_active": False}, "window_open_no_denon_path"),
    ),
)
@pytest.mark.parametrize("phase", ("midday", "late_afternoon", "evening"))
def test_subwoofer_protections_still_block_then_release(phase, changes, reason):
    inp = streaming(day_state=phase)
    blocked, state = L.decide(replace(inp, **changes), L.OrchestratorState())
    assert blocked.subwoofer_allowed is False
    assert blocked.subwoofer_block_reason == reason
    released, _ = L.decide(inp, state)
    assert released.subwoofer_allowed is True
    assert released.subwoofer_block_reason is None


def test_subwoofer_blocker_priority_is_unchanged():
    inp = streaming(
        context=C.CTX_GAMING,
        subcontext=C.SUB_GAME_GRIND,
        away_gate=True,
        quiet_mode=True,
        headset_active=True,
        entertainment_active=False,
        day_state="early_night",
    )
    state = L.OrchestratorState()
    for changes, reason in (
        ({}, "away_gate"),
        ({"away_gate": False}, "grind_r16"),
        ({"context": C.CTX_STREAMING}, "quiet_mode"),
        ({"quiet_mode": False}, "no_entertainment"),
        ({"entertainment_active": True}, "headset_active"),
        ({"headset_active": False}, "dayphase_window"),
    ):
        inp = replace(inp, **changes)
        decision, state = L.decide(inp, state)
        assert decision.subwoofer_allowed is False
        assert decision.subwoofer_block_reason == reason
