"""Volume matrix regressions for the Core State day phases."""

from dataclasses import replace

import bmp_const as C
import bmp_logic as L
import pytest

PHASES = (
    ("early_night", 0.30, 0.25, False),
    ("late_night", 0.25, 0.20, False),
    ("early_morning", 0.25, 0.20, True),
    ("forenoon", 0.40, 0.30, True),
    ("midday", 0.45, 0.30, True),
    ("afternoon", 0.45, 0.30, True),
    ("late_afternoon", 0.40, 0.30, True),
    ("evening", 0.40, 0.30, True),
    ("late_evening", 0.35, 0.30, True),
)
PLAYING = L.Inputs(
    homepods_configured=True,
    denon_configured=True,
    homepods_state="playing",
    day_state="afternoon",
    homepods_music_enum=C.MUSIC_ENUM_BOOST,
)


def test_phase_catalog_and_binding_migration():
    assert C.DAY_PHASES == tuple(phase[0] for phase in PHASES)
    assert C.PROFILE_PREFILL[C.PROFILE_BENNI][C.CONF_DAY_STATE] == C.CORE_DAY_STATE_ENTITY
    for old in ("sensor.benni_core_day_state", "sensor.benni_combined_context_day_state"):
        assert C.LEGACY_ENTITY_MAP[old] == C.CORE_DAY_STATE_ENTITY
    assert "sensor.custom_day_state" not in C.LEGACY_ENTITY_MAP


@pytest.mark.parametrize(("phase", "homepods", "denon", "boost"), PHASES)
def test_all_current_phases_use_their_baselines_and_default_boost(phase, homepods, denon, boost):
    inp = replace(PLAYING, day_state=phase)
    plain, _ = L.decide(replace(inp, homepods_music_enum=0), L.OrchestratorState())
    boosted, _ = L.decide(inp, L.OrchestratorState())
    tv, _ = L.decide(replace(inp, context=C.CTX_TV), L.OrchestratorState())
    assert plain.volume_target_homepods == homepods
    assert tv.volume_target_denon == denon
    assert boosted.track_boost_applied is boost
    assert boosted.volume_target_homepods == pytest.approx(homepods + (0.15 if boost else 0))


@pytest.mark.parametrize("phase", [None, "unknown", "unavailable", "late_morning", "early_evening"])
def test_unrecognized_phases_use_fallback_and_never_boost(phase):
    settings = L.VolumeSettings(homepods_base=0.32, boost_phases={phase: True})
    decision, _ = L.decide(replace(PLAYING, day_state=phase), L.OrchestratorState(), settings)
    assert decision.volume_target_homepods == 0.32
    assert not decision.track_boost_applied


@pytest.mark.parametrize(
    ("phase_max", "target", "delta"),
    [
        (None, 0.60, 0.15),
        (0.65, 0.60, 0.15),
        (0.55, 0.55, 0.10),
        (0.45, 0.45, 0),
        (0.02, 0.02, 0),
        (0, 0, 0),
    ],
)
def test_final_cap_limits_the_applied_boost(phase_max, target, delta):
    settings = L.VolumeSettings(
        homepods_max=0.60,
        phase_max_homepods={} if phase_max is None else {"afternoon": phase_max},
    )
    decision, _ = L.decide(PLAYING, L.OrchestratorState(), settings)
    breakdown = L.volume_breakdown(PLAYING, C.AUDIO_OWNER_HOMEPODS, False, settings)["homepods"]
    assert decision.volume_target_homepods == breakdown["result"] == target
    assert decision.track_boost_applied is (delta > 0)
    assert breakdown["track_boost"] == 0.15
    assert breakdown["track_boost_effective"] == delta
    assert breakdown["effective_max"] == min(0.60, phase_max if phase_max is not None else 0.60)


def test_device_caps_follow_all_offsets_and_nudges():
    inp = replace(
        PLAYING,
        context=C.CTX_GAMING,
        subcontext=C.SUB_GAME_GRIND,
        activity_context="free_time",
        opening_any_open=True,
        manual_nudge=0.10,
    )
    settings = L.VolumeSettings(
        phase_max_homepods={"afternoon": 0.51},
        phase_max_denon={"afternoon": 0.42},
        denon_max=0.39,
        grind_homepods_offset=0.02,
        grind_denon_offset=-0.06,
        scenario_off_homepods={"gaming": 0.01},
        scenario_off_denon={"gaming": 0.10},
        activity_off_homepods={"free_time": -0.10},
        activity_off_denon={"free_time": 0.02},
    )
    decision, _ = L.decide(inp, L.OrchestratorState(), settings)
    assert decision.volume_target_homepods == 0.51
    assert decision.volume_target_denon == 0.39
    breakdown = L.volume_breakdown(
        inp, decision.audio_owner, True, settings, decision.audio_scenario
    )
    assert breakdown["homepods"]["track_boost_effective"] == 0.08
    assert breakdown["denon"]["result"] == breakdown["denon"]["effective_max"] == 0.39


def test_ducking_mute_reset_and_private_cap_keep_their_precedence():
    settings = L.VolumeSettings(
        phase_max_homepods={"afternoon": 0.08},
        phase_max_denon={"afternoon": 0.12},
        private_denon_cap=0.09,
    )
    quiet, _ = L.decide(replace(PLAYING, quiet_mode=True), L.OrchestratorState(), settings)
    assert quiet.volume_policy == C.VOL_POLICY_DUCKED
    assert quiet.volume_target_homepods == 0.08
    assert not quiet.track_boost_applied
    muted, _ = L.decide(replace(PLAYING, homepods_music_enum=2), L.OrchestratorState(), settings)
    assert muted.music_muted and muted.volume_target_homepods == 0
    private, _ = L.decide(replace(PLAYING, context=C.CTX_PRIVATE), L.OrchestratorState(), settings)
    assert private.volume_target_denon == 0.09
    reset, _ = L.decide(replace(PLAYING, boost_suppressed=True), L.OrchestratorState())
    assert reset.volume_target_homepods == 0.45
    assert not reset.track_boost_applied


def test_idle_gap_obeys_new_phase_cap_and_drops_boost_in_restricted_phases():
    settings = L.VolumeSettings(phase_max_homepods={"late_evening": 0.55, "early_night": 0.50})
    playing, state = L.decide(PLAYING, L.OrchestratorState(), settings)
    assert playing.volume_target_homepods == 0.60
    gap = replace(PLAYING, homepods_state="idle")
    for phase, target in (("late_evening", 0.55), ("early_night", 0.45), (None, 0.45)):
        decision, state = L.decide(replace(gap, day_state=phase), state, settings)
        assert decision.volume_reason == "idle_sticky_homepods"
        assert decision.volume_target_homepods == target


def test_phase_patch_accepts_zero_and_false_and_rejects_unknown_or_invalid_cells():
    updated = L.apply_matrix_patch(
        {},
        {
            "phase_max": {
                "homepods": {
                    "early_night": 0,
                    "afternoon": True,
                    "midday": float("nan"),
                    "other": 0.4,
                },
                "denon": {"midday": 1.5, "late_night": -1},
            },
            "boost_allowed": {
                "homepods": {"early_night": True, "afternoon": False, "midday": 1, "other": True},
                "denon": {"midday": True},
            },
        },
    )
    assert updated == {
        "phase_max": {"homepods": {"early_night": 0}, "denon": {"midday": 1, "late_night": 0}},
        "boost_allowed": {"homepods": {"early_night": True, "afternoon": False}},
    }
