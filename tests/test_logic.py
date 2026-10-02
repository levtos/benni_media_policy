"""Pure-logic-Tests für den Policy-Lift (Phase 3, FLEET-34).

Deckt ab: Owner aus Context, GRIND-Pflicht-Delta (R15/R16/§6), Action-
Zustandsmaschine (pause/auto-pause/resume/radio/manual-stop), Volume-Routing +
Offsets + Ducking + Muted + Blocked, Subwoofer-Policy.
"""
from __future__ import annotations

import bmp_const as C
import bmp_logic as L


def _inp(**kw):
    base = dict(homepods_configured=True, denon_configured=True)
    base.update(kw)
    return L.Inputs(**base)


def _decide(inp, state=None, settings=None):
    return L.decide(inp, state or L.OrchestratorState(), settings)


# ------------------------------------------------------------------- Owner
def test_owner_idle():
    d, _ = _decide(_inp())
    assert d.audio_owner == C.AUDIO_OWNER_NONE


def test_owner_homepods_when_playing():
    d, _ = _decide(_inp(homepods_state="playing"))
    assert d.audio_owner == C.AUDIO_OWNER_HOMEPODS


def test_owner_tv_denon_for_streaming_and_tv():
    assert _decide(_inp(context=C.CTX_STREAMING))[0].audio_owner == C.AUDIO_OWNER_TV_DENON
    assert _decide(_inp(context=C.CTX_TV))[0].audio_owner == C.AUDIO_OWNER_TV_DENON


def test_owner_gaming():
    d, _ = _decide(_inp(context=C.CTX_GAMING, device="ps5"))
    assert d.audio_owner == C.AUDIO_OWNER_GAMING


def test_owner_private_via_context_sleep_separate_not_quiet():
    # control#45: bio_sleep ist kein Owner-Gate; quiet bleibt Volume-Overlay.
    assert _decide(_inp(context=C.CTX_PRIVATE))[0].audio_owner == C.AUDIO_OWNER_PRIVATE
    assert _decide(_inp(bio_sleep=True))[0].audio_owner == C.AUDIO_OWNER_NONE
    # Quiet bei spielenden HomePods → Owner bleibt HOMEPODS (kein PRIVATE/pause).
    assert _decide(_inp(quiet_mode=True, homepods_state="playing"))[0].audio_owner == C.AUDIO_OWNER_HOMEPODS


def test_sleep_pauses_homepods_without_hiding_real_owner_or_scenario():
    d, _ = _decide(_inp(bio_sleep=True, homepods_state="playing"))
    assert d.audio_owner == C.AUDIO_OWNER_HOMEPODS
    assert d.audio_scenario == C.AUDIO_SCENARIO_MUSIC
    assert d.action == C.ACTION_PAUSE
    assert d.homepods_should_pause is True
    assert d.volume_target_homepods == 0.0
    assert d.volume_apply_allowed is True


def test_issue59_provisional_sleep_is_a_consumer_sleep_value():
    assert "provisional_sleep" in C.BIO_SLEEP_VALUES
    # The coordinator maps every value in BIO_SLEEP_VALUES to this existing
    # modifier; the policy keeps the active TV owner/scenario unchanged.
    d, _ = _decide(
        _inp(bio_sleep=True, context=C.CTX_TV, homepods_state="playing")
    )
    assert d.audio_owner == C.AUDIO_OWNER_TV_DENON
    assert d.audio_scenario == C.AUDIO_SCENARIO_TV
    assert d.homepods_should_pause is True


def test_quiet_ducks_homepods_not_pause_fleet81():
    # FLEET-81: Tür auf (quiet) bei spielenden HomePods → ducken, NICHT stoppen.
    d, _ = _decide(_inp(quiet_mode=True, homepods_state="playing"))
    assert d.action != C.ACTION_PAUSE
    assert d.homepods_should_pause is False
    assert d.volume_policy == C.VOL_POLICY_DUCKED
    assert d.volume_target_homepods is not None and d.volume_target_homepods > 0.0


# -------------------------------------------------------------- Volume base
def test_volume_homepods_owner():
    d, _ = _decide(_inp(homepods_state="playing"))
    assert d.volume_policy == C.VOL_POLICY_MEDIA
    assert d.volume_target_homepods == 0.35
    assert d.volume_target_denon == 0.0


def test_volume_tv_denon_owner():
    d, _ = _decide(_inp(context=C.CTX_TV))
    assert d.volume_target_denon == 0.40
    assert d.volume_target_homepods == 0.0


def test_volume_dayphase_baseline_denon():
    # Denon-Baseline (Lastenheft §6) ersetzt das alte flache night_offset.
    assert _decide(_inp(context=C.CTX_TV, day_state="late_night"))[0].volume_target_denon == 0.20
    assert _decide(_inp(context=C.CTX_TV, day_state="forenoon"))[0].volume_target_denon == 0.30


def test_volume_dayphase_baseline_homepods():
    # HomePods-Baseline pro Tagesphase.
    assert _decide(_inp(homepods_state="playing", day_state="afternoon"))[0].volume_target_homepods == 0.45
    assert _decide(_inp(homepods_state="playing", day_state="early_morning"))[0].volume_target_homepods == 0.25


def test_volume_unknown_dayphase_falls_back_to_base():
    # day_state=None → Fallback-Base (homepods_base 0.35 / denon_base 0.40).
    assert _decide(_inp(homepods_state="playing"))[0].volume_target_homepods == 0.35
    assert _decide(_inp(context=C.CTX_TV))[0].volume_target_denon == 0.40


def test_volume_opening_offset():
    # fenster_offset (R17) auf die Dayphase-Baseline: afternoon Denon 0.30 − 0.05.
    d, _ = _decide(_inp(context=C.CTX_TV, day_state="afternoon", opening_any_open=True))
    assert d.volume_target_denon == 0.25


def test_volume_blocked_no_speakers():
    d, _ = _decide(_inp(homepods_configured=False, denon_configured=False, context=C.CTX_TV))
    assert d.volume_policy == C.VOL_POLICY_BLOCKED
    assert d.volume_apply_allowed is False


def test_volume_sleep_blocks_only_homepods_leg():
    d, _ = _decide(_inp(bio_sleep=True, context=C.CTX_TV, day_state="late_night"))
    assert d.volume_policy == C.VOL_POLICY_MEDIA
    assert d.volume_target_homepods == 0.0
    assert d.volume_target_denon == 0.20
    assert d.volume_apply_allowed is True


def test_volume_ducked_quiet():
    d, _ = _decide(_inp(quiet_mode=True, context=C.CTX_TV))
    assert d.volume_policy == C.VOL_POLICY_DUCKED
    assert d.volume_target_denon == 0.15  # ducked target
    assert d.volume_target_homepods == 0.0
    assert d.volume_apply_allowed is True


def test_volume_idle_zeros():
    d, s = _decide(_inp())
    assert d.volume_policy == C.VOL_POLICY_IDLE
    assert d.volume_target_homepods == 0.0
    assert d.volume_target_denon == 0.0
    # Frisches Idle ohne Vorlauf → kein Stick.
    assert s.last_hp_media_target is None


# ------------------------------------------------ FLEET-153: HomePods-Idle-Sticky
def test_volume_idle_sticky_holds_last_homepods_media():
    # Tick 1: HomePods spielen → MEDIA-Target 0.35, Stick gesetzt.
    d1, s1 = _decide(_inp(homepods_state="playing"))
    assert d1.volume_policy == C.VOL_POLICY_MEDIA
    assert d1.volume_target_homepods == 0.35
    assert s1.last_hp_media_target == 0.35
    # Tick 2: Playback-Lücke (owner none) → Target bleibt sticky statt 0.0 zu
    # kollabieren → Apply rampt nicht runter/hoch (FLEET-153).
    d2, s2 = _decide(_inp(), state=s1)
    assert d2.volume_policy == C.VOL_POLICY_IDLE
    assert d2.volume_target_homepods == 0.35
    assert d2.volume_reason == "idle_sticky_homepods"
    assert s2.last_hp_media_target == 0.35


def test_volume_idle_sticky_cleared_on_denon_path():
    # HomePods spielen → Stick 0.35.
    _d1, s1 = _decide(_inp(homepods_state="playing"))
    assert s1.last_hp_media_target == 0.35
    # Echter Pfadwechsel auf Denon/TV → Stick gelöscht (HomePods verlassen den Pfad).
    _d2, s2 = _decide(_inp(context=C.CTX_TV), state=s1)
    assert s2.last_hp_media_target is None
    # Folgendes Idle → HomePods 0.0 (nicht ihr Pfad), kein Schein-Stick.
    d3, _s3 = _decide(_inp(), state=s2)
    assert d3.volume_policy == C.VOL_POLICY_IDLE
    assert d3.volume_target_homepods == 0.0


# --------------------------------------------------- FLEET-102 Volume-Matrix (A)
def test_matrix_default_base_tables_match_consts():
    # Default-Matrix = die bisherigen Konstanten (verhaltensgleich).
    s = L.VolumeSettings()
    assert s.base_homepods == C.HOMEPODS_BASELINES
    assert s.base_denon == C.DENON_BASELINES
    assert s.scenario_off_homepods == {} and s.activity_off_homepods == {}


def test_matrix_custom_base_changes_target():
    # Per-Tagesphase-Base aus der Matrix steuert das Target (das eigentliche 102).
    s = L.VolumeSettings(base_homepods={**C.HOMEPODS_BASELINES, "afternoon": 0.50})
    d, _ = _decide(_inp(homepods_state="playing", day_state="afternoon"), settings=s)
    assert d.volume_target_homepods == 0.50  # Default wäre 0.45


def test_matrix_scenario_offset_applies():
    # Szenario-Offset (heute tot, 0.0) wird aus der Matrix gezogen und wirkt.
    s = L.VolumeSettings(scenario_off_homepods={C.AUDIO_SCENARIO_MUSIC: 0.10})
    d, _ = _decide(_inp(homepods_state="playing"), settings=s)  # Fallback-Base 0.35
    assert d.volume_target_homepods == 0.45
    # Nicht-passendes Szenario → kein Offset (.get-Default 0.0).
    s2 = L.VolumeSettings(scenario_off_homepods={C.AUDIO_SCENARIO_TV: 0.10})
    d2, _ = _decide(_inp(homepods_state="playing"), settings=s2)
    assert d2.volume_target_homepods == 0.35


def test_matrix_activity_offset_applies():
    # Aktivitäts-Offset (heute tot, 0.0) wird aus der Matrix gezogen und wirkt.
    s = L.VolumeSettings(activity_off_homepods={"relax": -0.05})
    d, _ = _decide(_inp(homepods_state="playing", activity_context="relax"), settings=s)
    assert d.volume_target_homepods == 0.30  # 0.35 - 0.05


def test_matrix_patch_sets_and_clamps():
    ov = L.apply_matrix_patch({}, {"base": {"homepods": {"afternoon": 0.50}}})
    assert ov == {"base": {"homepods": {"afternoon": 0.50}}}
    # base clamp [0,1], offsets clamp [-1,1].
    ov = L.apply_matrix_patch(ov, {
        "base": {"homepods": {"forenoon": 2.0}},
        "scenario_off": {"denon": {"music": -5.0}},
    })
    assert ov["base"]["homepods"]["forenoon"] == 1.0
    assert ov["scenario_off"]["denon"]["music"] == -1.0
    # Bestehender Wert bleibt (Merge, kein Replace).
    assert ov["base"]["homepods"]["afternoon"] == 0.50


def test_matrix_patch_none_deletes_cell_and_prunes():
    ov = L.apply_matrix_patch({}, {"base": {"homepods": {"afternoon": 0.50}}})
    ov = L.apply_matrix_patch(ov, {"base": {"homepods": {"afternoon": None}}})
    assert ov == {}  # leere Maps werden geprunt → kein Müll im Store


def test_matrix_patch_ignores_garbage():
    ov = L.apply_matrix_patch(
        {}, {"base": {"homepods": {"afternoon": "laut", "forenoon": 0.4}},
             "bogus_dim": {"x": 1}, "scenario_off": "nope"}
    )
    assert ov == {"base": {"homepods": {"forenoon": 0.4}}}  # nur valide Zelle bleibt


def test_matrix_patch_does_not_mutate_input():
    src = {"base": {"homepods": {"afternoon": 0.50}}}
    L.apply_matrix_patch(src, {"base": {"homepods": {"afternoon": 0.20}}})
    assert src["base"]["homepods"]["afternoon"] == 0.50  # Eingabe unverändert


def test_volume_idle_sticky_resume_no_change():
    # Stick auf 0.35, dann Resume auf identischem Pfad → gleiches Target, kein Ramp.
    _d1, s1 = _decide(_inp(homepods_state="playing"))
    _d2, s2 = _decide(_inp(), state=s1)                       # sticky idle 0.35
    d3, _s3 = _decide(_inp(homepods_state="playing"), state=s2)
    assert d3.volume_policy == C.VOL_POLICY_MEDIA
    assert d3.volume_target_homepods == 0.35  # unverändert → Apply-No-op


# ------------------------------------------------------ GRIND Pflicht-Delta
def test_grind_flag():
    d, _ = _decide(_inp(context=C.CTX_GAMING, subcontext=C.SUB_GAME_GRIND))
    assert d.is_grind is True


def test_grind_homepods_play_denon_kulisse():
    d, _ = _decide(_inp(context=C.CTX_GAMING, subcontext=C.SUB_GAME_GRIND))
    # HomePods auf Normal-Niveau, Denon Basis + negativer Offset.
    assert d.volume_target_homepods == 0.35
    assert d.volume_target_denon == 0.30  # 0.40 - 0.10 (control#3: Grind-Denon -0.10)
    assert d.volume_policy == C.VOL_POLICY_MEDIA


def test_grind_does_not_pause_homepods():
    # Grind hat HomePods-Anteil (R15) → kein Pause, auch wenn HomePods spielen.
    d, _ = _decide(_inp(context=C.CTX_GAMING, subcontext=C.SUB_GAME_GRIND,
                        homepods_state="playing"))
    assert d.homepods_should_pause is False
    assert d.action == C.ACTION_NONE


def test_grind_subwoofer_always_off():
    d, _ = _decide(_inp(context=C.CTX_GAMING, subcontext=C.SUB_GAME_GRIND,
                        entertainment_active=True, denon_active=True))
    assert d.subwoofer_allowed is False
    assert d.subwoofer_block_reason == "grind_r16"


def test_non_grind_gaming_pauses_homepods():
    # gaming_default (kein Grind, kein PC) verdrängt HomePods.
    d, _ = _decide(_inp(context=C.CTX_GAMING, subcontext="gaming_default",
                        homepods_state="playing"))
    assert d.homepods_should_pause is True
    assert d.action == C.ACTION_PAUSE


# --------------------------------------------------- PC-Gaming (FLEET-101)
def test_pc_gaming_flag():
    d, _ = _decide(_inp(context=C.CTX_GAMING, device=C.DEV_PC))
    assert d.is_pc_gaming is True


def test_pc_gaming_does_not_pause_homepods():
    # FLEET-101: PC-Gaming → Game-Audio im Headset, Raum-Musik läuft weiter.
    d, _ = _decide(_inp(context=C.CTX_GAMING, device=C.DEV_PC,
                        subcontext="gaming_default", homepods_state="playing"))
    assert d.homepods_should_pause is False
    assert d.action != C.ACTION_PAUSE


def test_pc_gaming_homepods_normal_denon_off():
    d, _ = _decide(_inp(context=C.CTX_GAMING, device=C.DEV_PC,
                        subcontext="gaming_default"))
    assert d.volume_target_homepods == 0.35   # normale Musik-Baseline
    assert d.volume_target_denon == 0.0        # Denon aus
    assert d.volume_policy == C.VOL_POLICY_MEDIA
    assert d.volume_reason == "pc_gaming_homepods_denon_off"


def test_pc_gaming_headset_still_plays_music():
    # Robust gegen Title-Classifier-Headset-Bugs (FLEET-97): am Device gebunden,
    # nicht am headset-Enum → auch im gaming_headset-Subcontext läuft Musik weiter.
    d, _ = _decide(_inp(context=C.CTX_GAMING, device=C.DEV_PC,
                        subcontext="gaming_headset", headset_active=True,
                        homepods_state="playing"))
    assert d.is_pc_gaming is True
    assert d.homepods_should_pause is False
    assert d.volume_target_homepods == 0.35
    assert d.volume_target_denon == 0.0


def test_pc_grind_pc_routing_wins_denon_off():
    # PC + grind: PC-Gaming-Routing übersteuert die Grind-Denon-Kulisse.
    d, _ = _decide(_inp(context=C.CTX_GAMING, device=C.DEV_PC,
                        subcontext=C.SUB_GAME_GRIND))
    assert d.is_pc_gaming is True
    assert d.is_grind is True
    assert d.volume_target_homepods == 0.35
    assert d.volume_target_denon == 0.0   # NICHT Grind-Kulisse (0.28)


def test_ps5_gaming_still_pauses_regression():
    # Regression-Guard: PS5 bleibt unverändert (verdrängt HomePods).
    d, _ = _decide(_inp(context=C.CTX_GAMING, device="ps5",
                        subcontext="gaming_default", homepods_state="playing"))
    assert d.is_pc_gaming is False
    assert d.homepods_should_pause is True
    assert d.action == C.ACTION_PAUSE


# ----------------------------------------------------- Action-Zustandsmaschine
def test_pause_when_competing_stack_and_playing():
    d, _ = _decide(_inp(context=C.CTX_TV, homepods_state="playing"))
    assert d.action == C.ACTION_PAUSE
    assert d.homepods_should_pause is True


def test_radio_resume_after_entertainment():
    # tick1: HomePods spielen + Radio geplant, TV (competing).
    d1, s1 = _decide(_inp(context=C.CTX_TV, homepods_state="playing",
                          planned_radio_active=True))
    # tick2: HomePods gestoppt (vom TV-Stack gezogen).
    d2, s2 = _decide(_inp(context=C.CTX_TV, homepods_state="paused"), s1)
    assert s2.auto_paused is True
    assert s2.pre_pause_mode == C.RESUME_MODE_RADIO
    # tick3: TV aus → kein competing stack → Radio-Resume.
    d3, s3 = _decide(_inp(homepods_state="paused"), s2)
    assert d3.action == C.ACTION_START_RADIO
    assert d3.homepods_resume_allowed is True


def test_manual_stop_blocks_resume():
    # manual_stop kommt jetzt NUR vom expliziten User-Latch (nicht aus
    # playing→idle abgeleitet) → blockiert Resume/Baseline.
    d, s = _decide(_inp(homepods_state="idle", media_stop_latch=True))
    assert s.manual_stop is True
    assert d.action == C.ACTION_NONE
    assert d.homepods_resume_allowed is False


def test_media_stop_latch_forces_manual_stop():
    d, s = _decide(_inp(homepods_state="idle", media_stop_latch=True))
    assert s.manual_stop is True


def test_stream_drop_does_not_set_manual_stop():
    # DER Fix: playing→idle OHNE konkurrierenden Stack (Stream-Abriss/Dropout/
    # HA-Neustart) darf KEIN manual_stop setzen — sonst blockiert es die
    # Baseline-Recovery (der Stream käme nie zurück).
    _d1, s1 = _decide(_inp(homepods_state="playing"))
    d2, s2 = _decide(_inp(homepods_state="idle"), state=s1)
    assert s2.manual_stop is False
    assert d2.manual_stop is False


# ------------------------------------------------ Wake-Reset (wake_planner)
def test_wake_needed_clears_manual_stop():
    # Stopp via Latch → manual_stop. Wach-Flanke räumt ihn ab (gewinnt auch über
    # einen noch gehaltenen Latch).
    _d1, s1 = _decide(_inp(homepods_state="idle", media_stop_latch=True))
    assert s1.manual_stop is True
    d2, s2 = _decide(
        _inp(homepods_state="idle", media_stop_latch=True, wake_needed=True), state=s1
    )
    assert s2.manual_stop is False and d2.manual_stop is False


def test_wake_needed_only_clears_on_rising_edge():
    # wake_needed dauerhaft on (kein Edge): ein gehaltener Latch bleibt manual_stop.
    _d0, s0 = _decide(_inp(homepods_state="idle", media_stop_latch=True, wake_needed=True))
    d1, _s1 = _decide(
        _inp(homepods_state="idle", media_stop_latch=True, wake_needed=True), state=s0
    )
    assert d1.manual_stop is True  # kein Edge → kein Clear, Latch hält


def test_manual_stop_exposed_in_decision_dict():
    d, _ = _decide(_inp(homepods_state="idle", media_stop_latch=True))
    assert d.as_dict()["manual_stop"] is True


def test_homepods_missing_blocks_action():
    d, _ = _decide(_inp(homepods_configured=False, denon_configured=True,
                        context=C.CTX_TV))
    assert d.action == C.ACTION_NONE
    assert d.action_reason == "homepods_entity_missing"


# ------------------------------------------------------------- Subwoofer
def test_subwoofer_on_with_entertainment_and_denon_path():
    d, _ = _decide(_inp(context=C.CTX_TV, entertainment_active=True, denon_active=True,
                        day_state="afternoon"))
    assert d.subwoofer_allowed is True


def test_subwoofer_off_no_entertainment():
    d, _ = _decide(_inp(entertainment_active=False))
    assert d.subwoofer_allowed is False
    assert d.subwoofer_block_reason == "no_entertainment"


def test_subwoofer_off_headset():
    d, _ = _decide(_inp(context=C.CTX_GAMING, subcontext="gaming_headset",
                        entertainment_active=True, headset_active=True, denon_active=True))
    assert d.subwoofer_allowed is False
    assert d.subwoofer_block_reason == "headset_active"


def test_subwoofer_off_quiet():
    d, _ = _decide(_inp(quiet_mode=True, entertainment_active=True, denon_active=True))
    assert d.subwoofer_allowed is False
    assert d.subwoofer_block_reason == "quiet_mode"


def test_subwoofer_off_window_no_denon_path():
    d, _ = _decide(_inp(context=C.CTX_STREAMING, entertainment_active=True,
                        opening_any_open=True, denon_active=False, device="appletv"))
    assert d.subwoofer_allowed is False
    assert d.subwoofer_block_reason == "window_open_no_denon_path"


def test_denon_audio_path_via_device():
    d, _ = _decide(_inp(context=C.CTX_TV, entertainment_active=True, device=C.DEV_DENON,
                        opening_any_open=True, day_state="afternoon"))
    # device==denon → denon_path True → window allein blockt nicht mehr.
    assert d.denon_audio_path is True
    assert d.subwoofer_allowed is True


# ----------------------------------------- R-§6 Subwoofer-Dayphase-Fenster (FLEET-39)
def _sub_inp(**kw):
    base = dict(context=C.CTX_TV, entertainment_active=True, denon_active=True)
    base.update(kw)
    return _inp(**base)


def test_subwoofer_off_outside_dayphase_window():
    # late_night ∉ erlaubte Phasen → aus, trotz Entertainment + Denon-Path.
    d, _ = _decide(_sub_inp(day_state="late_night"))
    assert d.subwoofer_allowed is False
    assert d.subwoofer_block_reason == "dayphase_window"


def test_subwoofer_off_early_morning():
    d, _ = _decide(_sub_inp(day_state="early_morning"))
    assert d.subwoofer_allowed is False
    assert d.subwoofer_block_reason == "dayphase_window"


def test_subwoofer_off_late_morning_before_0900():
    # Erlaubte Phase, aber Wanduhr < 09:00 (Sommer-Fall) → Floor blockt.
    d, _ = _decide(_sub_inp(day_state="late_morning", local_minute_of_day=8 * 60 + 30))
    assert d.subwoofer_allowed is False
    assert d.subwoofer_block_reason == "before_0900"


def test_subwoofer_on_late_morning_at_0900():
    d, _ = _decide(_sub_inp(day_state="late_morning", local_minute_of_day=9 * 60))
    assert d.subwoofer_allowed is True


def test_subwoofer_unknown_time_allows_in_phase():
    # Lokalzeit unbekannt → Phase reicht (kein Floor-Block).
    d, _ = _decide(_sub_inp(day_state="afternoon", local_minute_of_day=None))
    assert d.subwoofer_allowed is True


def test_subwoofer_all_allowed_phases_on():
    for ph in ("late_morning", "forenoon", "afternoon", "early_evening", "late_evening"):
        d, _ = _decide(_sub_inp(day_state=ph, local_minute_of_day=12 * 60))
        assert d.subwoofer_allowed is True, ph


# ------------------------------------------------- R18 Track-Boost / R19 Mute
def test_track_boost_adds_offset():
    # Musik-Enum 1 (boost) → HomePods-Ziel + boost_offset (0.45 + 0.15).
    d, _ = _decide(_inp(homepods_state="playing", day_state="afternoon",
                        homepods_music_enum=C.MUSIC_ENUM_BOOST))
    assert d.track_boost_applied is True
    assert d.volume_target_homepods == 0.60


def test_track_boost_blocked_in_work_home():
    d, _ = _decide(_inp(homepods_state="playing", day_state="afternoon",
                        homepods_music_enum=C.MUSIC_ENUM_BOOST,
                        activity_context="work_home"))
    assert d.track_boost_applied is False
    assert d.volume_target_homepods == 0.45  # ohne Boost


def test_track_boost_blocked_in_quiet():
    # Quiet → Ducked-Zweig, Boost greift strukturell nicht.
    d, _ = _decide(_inp(homepods_state="playing", day_state="afternoon",
                        quiet_mode=True, homepods_music_enum=C.MUSIC_ENUM_BOOST))
    assert d.track_boost_applied is False
    assert d.volume_policy == C.VOL_POLICY_DUCKED


def test_music_mute_forces_zero():
    # Musik-Enum 2 (mute) → HomePods hart auf 0, übersteuert die Formel.
    d, _ = _decide(_inp(homepods_state="playing", day_state="afternoon",
                        homepods_music_enum=C.MUSIC_ENUM_MUTE))
    assert d.music_muted is True
    assert d.volume_target_homepods == 0.0


def test_music_mute_does_not_touch_denon_in_grind():
    # Grind: HomePods gemutet (0), Denon-Kulisse unberührt (Baseline + Grind-Offset).
    d, _ = _decide(_inp(context=C.CTX_GAMING, subcontext=C.SUB_GAME_GRIND,
                        day_state="afternoon", homepods_music_enum=C.MUSIC_ENUM_MUTE))
    assert d.volume_target_homepods == 0.0
    assert d.volume_target_denon == 0.20  # 0.30 - 0.10 (control#3: Grind-Denon -0.10)


# ----------------------------------------------------- R21/R22 Nudge + Boost-Reset
def test_manual_nudge_raises_homepods():
    base = _decide(_inp(homepods_state="playing", day_state="afternoon"))[0]
    d, _ = _decide(_inp(homepods_state="playing", day_state="afternoon",
                        manual_nudge=0.10))
    assert d.volume_target_homepods == round(base.volume_target_homepods + 0.10, 3)


def test_manual_nudge_negative_lowers_homepods():
    d, _ = _decide(_inp(homepods_state="playing", day_state="afternoon",
                        manual_nudge=-0.30))
    assert d.volume_target_homepods == 0.15  # 0.45 - 0.30


def test_manual_nudge_raises_denon():
    base = _decide(_inp(context=C.CTX_TV, day_state="afternoon"))[0]
    d, _ = _decide(_inp(context=C.CTX_TV, day_state="afternoon", manual_nudge=0.10))
    assert d.volume_target_denon == round(base.volume_target_denon + 0.10, 3)


def test_boost_suppressed_disables_track_boost():
    # R22: gleicher Boost-Track, aber Boost-Reset aktiv → kein Offset.
    d, _ = _decide(_inp(homepods_state="playing", day_state="afternoon",
                        homepods_music_enum=C.MUSIC_ENUM_BOOST,
                        boost_suppressed=True))
    assert d.track_boost_applied is False
    assert d.volume_target_homepods == 0.45  # ohne Boost


def test_volume_breakdown_reports_nudge():
    bd = L.volume_breakdown(
        _inp(homepods_state="playing", day_state="afternoon", manual_nudge=0.10),
        C.AUDIO_OWNER_HOMEPODS, False, L.VolumeSettings(),
    )
    assert bd["homepods"]["manual_nudge"] == 0.10
    assert bd["denon"]["manual_nudge"] == 0.0  # spielt nicht → kein Nudge


def test_volume_breakdown_matches_homepods_sleep_target():
    inp = _inp(
        bio_sleep=True,
        homepods_state="playing",
        day_state="afternoon",
        homepods_music_enum=C.MUSIC_ENUM_BOOST,
    )
    d, _ = _decide(inp)
    bd = L.volume_breakdown(inp, d.audio_owner, d.is_grind, L.VolumeSettings(),
                            d.audio_scenario, d.music_baseline_active)
    assert d.volume_target_homepods == 0.0
    assert d.track_boost_applied is False
    assert bd["homepods"]["plays"] is False
    assert bd["homepods"]["result"] == 0.0
    assert bd["homepods"]["track_boost"] == 0.0


# ----------------------------------------- audio_scenario / Desired-Audio (FLEET-85)
def test_scenario_idle_constellation_is_music_baseline():
    # Kein Owner, HomePods spielen nicht → trotzdem MUSIC (nicht idle). Der Fix.
    d, _ = _decide(_inp())
    assert d.audio_owner == C.AUDIO_OWNER_NONE
    assert d.audio_scenario == C.AUDIO_SCENARIO_MUSIC
    assert d.audio_scenario_label == "Musik"


def test_scenario_music_when_homepods_playing_with_station():
    d, _ = _decide(_inp(homepods_state="playing", radio_station="GAYFM"))
    assert d.audio_scenario == C.AUDIO_SCENARIO_MUSIC
    assert d.audio_scenario_detail == "GAYFM"


def test_scenario_station_detail_in_idle_baseline():
    # Auch in der Baseline (owner NONE) trägt der gewählte Sender ins Detail.
    d, _ = _decide(_inp(radio_station="  byte.fm  "))
    assert d.audio_scenario == C.AUDIO_SCENARIO_MUSIC
    assert d.audio_scenario_detail == "byte.fm"   # getrimmt


def test_scenario_tv_with_context_detail():
    d, _ = _decide(_inp(context=C.CTX_TV))
    assert d.audio_scenario == C.AUDIO_SCENARIO_TV
    assert d.audio_scenario_detail == C.CTX_TV
    d2, _ = _decide(_inp(context=C.CTX_STREAMING))
    assert d2.audio_scenario == C.AUDIO_SCENARIO_TV
    assert d2.audio_scenario_detail == C.CTX_STREAMING


def test_scenario_gaming_with_platform_detail():
    d, _ = _decide(_inp(context=C.CTX_GAMING, device="ps5"))
    assert d.audio_scenario == C.AUDIO_SCENARIO_GAMING
    assert d.audio_scenario_detail == "ps5"


def test_scenario_grind_is_gaming():
    d, _ = _decide(_inp(context=C.CTX_GAMING, subcontext=C.SUB_GAME_GRIND, device="ps5"))
    assert d.audio_scenario == C.AUDIO_SCENARIO_GAMING


def test_scenario_private_via_context():
    d, _ = _decide(_inp(context=C.CTX_PRIVATE))
    assert d.audio_scenario == C.AUDIO_SCENARIO_PRIVATE


def test_scenario_reflects_context_when_bio_sleep():
    d, _ = _decide(_inp(bio_sleep=True, context=C.CTX_TV))
    assert d.audio_scenario == C.AUDIO_SCENARIO_TV
    assert d.audio_scenario_label == "TV"


def test_scenario_quiet_is_overlay_not_scenario():
    # Quiet ist ein Volume-Overlay → Szenario bleibt die Baseline (music), nicht „quiet".
    d, _ = _decide(_inp(quiet_mode=True, homepods_state="playing"))
    assert d.volume_policy == C.VOL_POLICY_DUCKED
    assert d.audio_scenario == C.AUDIO_SCENARIO_MUSIC


def test_scenario_exposed_in_as_dict():
    d, _ = _decide(_inp(homepods_state="playing", radio_station="GAYFM"))
    data = d.as_dict()
    assert data["audio_scenario"] == C.AUDIO_SCENARIO_MUSIC
    assert data["audio_scenario_label"] == "Musik"
    assert data["audio_scenario_detail"] == "GAYFM"


# ----------------------------------------------- Presence / Away-Gate (FLEET-212)
def test_away_gate_blocks_music_baseline():
    d, _ = _decide(_inp(away_gate=True, radio_station="gayfm"))
    assert d.audio_owner == C.AUDIO_OWNER_NONE
    assert d.audio_scenario == C.AUDIO_SCENARIO_OFF
    assert d.volume_policy == C.VOL_POLICY_IDLE
    assert d.volume_apply_allowed is False
    assert d.volume_target_homepods == 0.0
    assert d.volume_target_denon == 0.0


def test_away_gate_pauses_active_homepods():
    d, _ = _decide(_inp(away_gate=True, homepods_state="playing"))
    assert d.action == C.ACTION_PAUSE
    assert d.homepods_should_pause is True
    assert d.homepods_resume_allowed is False


def test_presence_away_preserves_resume_memory_and_resumes_home():
    # Presence-away ist ein konkurrierender Stack wie der TV: es blockiert das
    # Resume, während weg — ABER erhält die Resume-Erinnerung, damit Heimkehr die
    # Musik über die normale Maschinerie zurückholt (eine Flanke, kein Dauer-Level).
    d1, s1 = _decide(_inp(context=C.CTX_TV, homepods_state="playing",
                          planned_radio_active=True))
    d2, s2 = _decide(_inp(context=C.CTX_TV, homepods_state="paused",
                          planned_radio_active=True), s1)
    assert s2.auto_paused is True
    assert s2.pre_pause_mode == C.RESUME_MODE_RADIO
    # Weg: Resume blockiert, aber Erinnerung ERHALTEN (anders als der alte
    # Hard-Block, der sie löschte — genau das erzwang den Dauer-Baseline-Hack).
    d3, s3 = _decide(_inp(presence_state="abwesend", homepods_state="paused",
                          planned_radio_active=True), s2)
    assert d3.action == C.ACTION_NONE
    assert d3.homepods_resume_allowed is False
    assert s3.auto_paused is True
    assert s3.pre_pause_mode == C.RESUME_MODE_RADIO
    # Heim: Resume-Flanke → start_radio (wie TV-aus).
    d4, s4 = _decide(_inp(presence_state="zuhause", homepods_state="paused",
                          planned_radio_active=True), s3)
    assert d4.action == C.ACTION_START_RADIO
    assert d4.homepods_resume_allowed is True


# --- Musik-Baseline als Idle-Resume-Netz (FLEET-246, zurück mit Wake-Gate) ---
def test_baseline_starts_when_stably_idle_at_home():
    # HomePods stabil idle (Debounce erfüllt) + zuhause + Sender + kein Stack →
    # start_radio. Das Sicherheitsnetz, wenn die Resume-Erinnerung verloren ging.
    d, _ = _decide(_inp(
        presence_state="zuhause",
        radio_station="gayfm",
        homepods_state="idle",
        homepods_stably_idle=True,
        day_state="afternoon",
    ))
    assert d.action == C.ACTION_START_RADIO
    assert d.music_baseline_active is True
    assert d.volume_policy == C.VOL_POLICY_MEDIA
    assert d.volume_target_homepods and d.volume_target_homepods > 0


def test_baseline_not_without_debounce():
    # Ohne stabiles Idle-Fenster (Restore-Flap/Track-Gap) → KEIN Start (kein Churn).
    d, _ = _decide(_inp(
        presence_state="zuhause", radio_station="gayfm",
        homepods_state="idle", homepods_stably_idle=False,
    ))
    assert d.action == C.ACTION_NONE
    assert d.music_baseline_active is False


def test_baseline_suppressed_during_wake_needed():
    # Wake-Gate (FLEET-246): wake_needed → die Wake-Sequenz besitzt den Start,
    # Baseline schweigt (sonst MA-Lock-Doppelzündung).
    d, _ = _decide(_inp(
        presence_state="zuhause", radio_station="gayfm",
        homepods_state="idle", homepods_stably_idle=True, wake_needed=True,
    ))
    assert d.action == C.ACTION_NONE
    assert d.music_baseline_active is False


def test_baseline_suppressed_while_bio_waking():
    # Wake-Gate: bio_state=waking ist ebenfalls das Wake-Fenster → Baseline still.
    d, _ = _decide(_inp(
        presence_state="zuhause", radio_station="gayfm",
        homepods_state="idle", homepods_stably_idle=True, bio_state="waking",
    ))
    assert d.action == C.ACTION_NONE
    assert d.music_baseline_active is False


def test_baseline_not_when_tv():
    # „Nicht bei TV": TV-Stack → owner tv_denon → keine Baseline, auch stabil idle.
    d, _ = _decide(_inp(
        context=C.CTX_TV, presence_state="zuhause", radio_station="gayfm",
        homepods_state="idle", homepods_stably_idle=True,
    ))
    assert d.action != C.ACTION_START_RADIO
    assert d.music_baseline_active is False


def test_manual_stop_blocks_baseline():
    # Manuell gestoppt → auch bei stabil idle KEIN Auto-Start (bis zum Wecken).
    d, _ = _decide(_inp(
        presence_state="zuhause",
        radio_station="gayfm",
        homepods_state="idle",
        homepods_stably_idle=True,
        media_stop_latch=True,
    ))
    assert d.audio_scenario == C.AUDIO_SCENARIO_MUSIC
    assert d.action == C.ACTION_NONE
    assert d.music_baseline_active is False
    assert d.volume_policy == C.VOL_POLICY_IDLE
    assert d.volume_target_homepods == 0.0


def test_unknown_presence_holds_does_not_start_when_idle():
    # unknown (z.B. kurzer Reload-Flap) darf idle NICHT zwangs-starten — auch
    # nicht mit erfülltem Idle-Debounce (presence_holds_resume blockt).
    d, _ = _decide(_inp(presence_state="unknown", radio_station="gayfm",
                        homepods_state="idle", homepods_stably_idle=True))
    assert d.action == C.ACTION_NONE
    assert d.music_baseline_active is False


def test_unknown_presence_does_not_pause_playing_music():
    # DER Reload-Bug: unknown ist KEIN Block mehr → laufende Musik läuft weiter,
    # wird nicht pausiert und nicht auf Volume 0 gezwungen.
    d, _ = _decide(_inp(presence_state="unknown", homepods_state="playing",
                        radio_station="gayfm"))
    assert d.action != C.ACTION_PAUSE
    assert d.homepods_should_pause is False
    assert d.audio_owner == C.AUDIO_OWNER_HOMEPODS
    assert d.volume_policy == C.VOL_POLICY_MEDIA


def test_unknown_presence_holds_resume_when_paused():
    # War auto-paused (TV), Presence wird unknown → nicht resumen, bis wieder
    # klar (zuhause). Kein blindes Wiederanwerfen.
    d1, s1 = _decide(_inp(context=C.CTX_TV, homepods_state="playing",
                          planned_radio_active=True))
    d2, s2 = _decide(_inp(context=C.CTX_TV, homepods_state="paused",
                          planned_radio_active=True), s1)
    assert s2.auto_paused is True
    d3, s3 = _decide(_inp(presence_state="unknown", homepods_state="paused",
                          planned_radio_active=True), s2)
    assert d3.action == C.ACTION_NONE
    assert s3.auto_paused is True   # Erinnerung bleibt für spätere Heimkehr


def test_entertainment_false_with_away_does_not_keep_music_running():
    d, _ = _decide(_inp(away_gate=True, entertainment_active=False,
                        homepods_state="playing"))
    assert d.audio_scenario == C.AUDIO_SCENARIO_OFF
    assert d.action == C.ACTION_PAUSE


# ------------------------------- control#3: per-Gerät Grind/Fenster + Private-Cap
def _settings(**kw):
    return L.VolumeSettings(**kw)


def test_grind_homepods_offset_default_zero():
    # HomePods-Grind-Offset Default 0 → HomePods bleiben auf Normal-Baseline.
    d, _ = _decide(_inp(context=C.CTX_GAMING, subcontext=C.SUB_GAME_GRIND,
                        day_state="afternoon"))
    assert d.volume_target_homepods == 0.45  # afternoon baseline, +0


def test_grind_homepods_offset_configurable():
    s = _settings(grind_homepods_offset=-0.05)
    d, _ = _decide(_inp(context=C.CTX_GAMING, subcontext=C.SUB_GAME_GRIND,
                        day_state="afternoon"), settings=s)
    assert d.volume_target_homepods == 0.40  # 0.45 - 0.05


def test_grind_denon_offset_configurable():
    s = _settings(grind_denon_offset=-0.15)
    d, _ = _decide(_inp(context=C.CTX_GAMING, subcontext=C.SUB_GAME_GRIND,
                        day_state="afternoon"), settings=s)
    assert d.volume_target_denon == 0.15  # 0.30 - 0.15


def test_window_per_device_separate():
    # Fenster offen: HP und Denon eigene Offsets.
    s = _settings(opening_offset_homepods=-0.10, opening_offset_denon=-0.05)
    d, _ = _decide(_inp(homepods_state="playing", day_state="afternoon",
                        opening_any_open=True), settings=s)
    assert d.volume_target_homepods == 0.35  # 0.45 - 0.10
    d2, _ = _decide(_inp(context=C.CTX_TV, day_state="afternoon",
                         opening_any_open=True), settings=s)
    assert d2.volume_target_denon == 0.25  # 0.30 - 0.05


def test_window_closed_no_offset():
    s = _settings(opening_offset_homepods=-0.10, opening_offset_denon=-0.05)
    d, _ = _decide(_inp(homepods_state="playing", day_state="afternoon",
                        opening_any_open=False), settings=s)
    assert d.volume_target_homepods == 0.45  # kein Abzug


def test_private_denon_cap_limits():
    # Private routet auf Denon; Cap begrenzt den berechneten Wert.
    s = _settings(private_denon_cap=0.15)
    d, _ = _decide(_inp(context=C.CTX_PRIVATE, day_state="afternoon"), settings=s)
    # normal wäre 0.30 (afternoon), Cap 0.15 → effektiv 0.15
    assert d.volume_target_denon == 0.15


def test_private_denon_cap_does_not_raise_lower_value():
    # Ist der normale Wert kleiner als der Cap, bleibt er (min, kein Anheben).
    s = _settings(private_denon_cap=0.50)
    d, _ = _decide(_inp(context=C.CTX_PRIVATE, day_state="afternoon"), settings=s)
    assert d.volume_target_denon == 0.30  # < Cap → unverändert


def test_private_denon_cap_nudge_proof():
    # Ein positiver Nudge darf den Cap NICHT überfahren.
    s = _settings(private_denon_cap=0.15)
    d, _ = _decide(_inp(context=C.CTX_PRIVATE, day_state="afternoon",
                        manual_nudge=0.30), settings=s)
    assert d.volume_target_denon == 0.15


def test_private_cap_only_affects_denon_path():
    # Cap gilt nur im Private-Owner (Denon-Pfad), nicht für andere Owner.
    s = _settings(private_denon_cap=0.15)
    d, _ = _decide(_inp(context=C.CTX_TV, day_state="afternoon"), settings=s)
    assert d.volume_target_denon == 0.30  # TV-Owner, kein Private-Cap


# ------------------------------- control#3: sanitize_scalar_patch (Panel→Options)
_RANGES = {
    "grind_denon_offset": (-1.0, 1.0),
    "grind_homepods_offset": (-1.0, 1.0),
    "volume_opening_offset_denon": (-1.0, 1.0),
    "private_denon_cap": (0.0, 1.0),
    "volume_homepods_max": (0.0, 1.0),
}


def test_sanitize_scalar_keeps_known_clamps_rounds():
    out = L.sanitize_scalar_patch(
        {"grind_denon_offset": -0.123456, "private_denon_cap": 0.2}, _RANGES)
    assert out == {"grind_denon_offset": -0.123, "private_denon_cap": 0.2}


def test_sanitize_scalar_drops_unknown_keys():
    out = L.sanitize_scalar_patch({"totally_unknown": 0.5, "grind_denon_offset": 0.0}, _RANGES)
    assert out == {"grind_denon_offset": 0.0}


def test_sanitize_scalar_clamps_offset_and_level_ranges():
    out = L.sanitize_scalar_patch(
        {"grind_homepods_offset": -5.0, "private_denon_cap": 9.0, "volume_homepods_max": -3.0},
        _RANGES)
    assert out["grind_homepods_offset"] == -1.0   # offset floor
    assert out["private_denon_cap"] == 1.0         # level ceiling
    assert out["volume_homepods_max"] == 0.0       # level floor


def test_sanitize_scalar_ignores_non_numeric_and_non_dict():
    assert L.sanitize_scalar_patch({"grind_denon_offset": "abc"}, _RANGES) == {}
    assert L.sanitize_scalar_patch(None, _RANGES) == {}


def test_sanitize_scalar_empty_patch_is_empty():
    assert L.sanitize_scalar_patch({}, _RANGES) == {}


# ------------------------------------------------ control#45: 24/7 + R25
def test_sleep_keeps_denon_matrix_and_safety_caps_for_all_contexts():
    tv, _ = _decide(_inp(
        bio_sleep=True, context=C.CTX_TV, day_state="afternoon",
        opening_any_open=True,
    ))
    streaming, _ = _decide(_inp(
        bio_sleep=True, context=C.CTX_STREAMING, day_state="late_night",
    ))
    gaming, _ = _decide(_inp(
        bio_sleep=True, context=C.CTX_GAMING, device="ps5",
        day_state="late_night",
    ))
    private, _ = _decide(_inp(
        bio_sleep=True, context=C.CTX_PRIVATE, day_state="afternoon",
    ))

    assert (tv.audio_owner, tv.audio_scenario, tv.volume_target_denon) == (
        C.AUDIO_OWNER_TV_DENON, C.AUDIO_SCENARIO_TV, 0.25)
    assert streaming.volume_target_denon == 0.20
    assert (gaming.audio_owner, gaming.audio_scenario,
            gaming.volume_target_denon) == (
        C.AUDIO_OWNER_GAMING, C.AUDIO_SCENARIO_GAMING, 0.20)
    assert (private.audio_owner, private.audio_scenario,
            private.volume_target_denon) == (
        C.AUDIO_OWNER_PRIVATE, C.AUDIO_SCENARIO_PRIVATE, 0.15)
    for decision in (tv, streaming, gaming, private):
        assert decision.volume_target_homepods == 0.0
        assert decision.volume_apply_allowed is True


def test_sleep_repauses_homepods_preserves_sticky_and_resumes_when_waking():
    _awake, s_awake = _decide(_inp(
        homepods_state="playing", planned_radio_active=True,
        day_state="afternoon",
    ))
    sleep_playing, s_sleep_playing = _decide(_inp(
        bio_sleep=True, homepods_state="playing", planned_radio_active=True,
        day_state="afternoon",
    ), s_awake)
    sleep_paused, s_sleep_paused = _decide(_inp(
        bio_sleep=True, homepods_state="paused", planned_radio_active=True,
        day_state="afternoon",
    ), s_sleep_playing)
    restarted, _ = _decide(_inp(
        bio_sleep=True, homepods_state="playing", planned_radio_active=True,
        day_state="afternoon",
    ), s_sleep_paused)
    waking, _ = _decide(_inp(
        bio_state="waking", homepods_state="paused", planned_radio_active=True,
        day_state="afternoon",
    ), s_sleep_paused)

    assert sleep_playing.action == C.ACTION_PAUSE
    assert sleep_playing.volume_target_homepods == 0.0
    assert sleep_paused.action == C.ACTION_NONE
    assert sleep_paused.homepods_resume_allowed is False
    assert s_sleep_paused.auto_paused is True
    assert s_sleep_paused.last_hp_media_target == 0.45
    assert restarted.action == C.ACTION_PAUSE
    assert waking.action == C.ACTION_START_RADIO
    assert waking.homepods_resume_allowed is True
    assert waking.volume_target_homepods == 0.45


def test_post_entertainment_resume_has_audible_homepods_target():
    s = L.OrchestratorState(auto_paused=True, pre_pause_mode=C.RESUME_MODE_MANUAL,
                            last_homepods_playing=False)
    d, s2 = _decide(_inp(homepods_state="paused"), state=s)
    assert d.action == C.ACTION_RESUME
    assert d.homepods_resume_allowed is True
    assert d.volume_policy == C.VOL_POLICY_MEDIA
    assert d.volume_target_homepods > 0.0
    assert d.volume_target_denon == 0.0
    assert d.volume_reason == "resume_homepods_start"
    assert d.music_baseline_active is False
    assert s2.last_hp_media_target == d.volume_target_homepods


def test_resume_start_unchanged_under_quiet_and_sleep():
    s = L.OrchestratorState(auto_paused=True, pre_pause_mode=C.RESUME_MODE_MANUAL)
    dq, _ = _decide(_inp(homepods_state="paused", quiet_mode=True), state=s)
    assert dq.volume_policy == C.VOL_POLICY_DUCKED
    assert dq.volume_target_homepods == 0.0
    ds, _ = _decide(_inp(homepods_state="paused", bio_sleep=True), state=s)
    assert ds.action != C.ACTION_RESUME
