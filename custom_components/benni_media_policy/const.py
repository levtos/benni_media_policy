"""Konstanten von benni_media_policy (L2 Policy / Media-Gehirn).

Eigenständige HA-Integration. Audio-Owner (pause/resume/radio) + Volume/Ducking/
Subwoofer. Konsumiert benni_media_state AUSSCHLIESSLICH über HA-Entity-State —
kein Cross-Modul-Python-Import (const wird KOPIERT, nicht importiert).

Phase 3 (FLEET-34): Lift von orchestrator.py + volume_orchestrator.py +
evaluate_subwoofer/_denon_audio_path aus bennis_toolbox/benni_media_context.
Owner-Bestimmung aus media_state-Context (kein Roh-Re-Derive). Pflicht-Delta
GRIND (Lastenheft v3.1 KH-5/R15/R16/§6): bei gaming_grind spielen HomePods
weiter (kein Pause), HomePods auf Normal-Niveau, Denon mit negativem Offset
(Kulisse), Subwoofer immer aus.

Apply-Gate-Doku: `apply_enabled` = globaler Shadow-Kill-Switch (Option);
`volume_apply_allowed` = pro Entscheidung (aus der Volume-Logik). Klar getrennt —
der Apply-Layer (benni_media_apply, FLEET-40) prüft BEIDE.

Lastenheft: einhornzentrale/docs/lastenhefte/reviewed/media/ (v3.1)
"""
from __future__ import annotations

from typing import Any, Final

DOMAIN: Final[str] = "benni_media_policy"
MODULE_ID: Final[str] = "media_policy"
NAME: Final[str] = "Benni Media Policy"

DATA_COORDINATOR: Final[str] = "coordinator"

STORAGE_VERSION: Final[int] = 1


def unique_id(entry_id: str, suffix: str) -> str:
    """Domain- + entry-scoped unique_id (core_state-Blaupause, kollisionsfrei)."""
    return f"{DOMAIN}_{entry_id}_{suffix}"


# --------------------------------------------------------------------------- #
# Profil-Hub (benni / eltern) + Auto-Bind: options ▶ data ▶ Profil-Map ▶ leer.
# --------------------------------------------------------------------------- #
CONF_PROFILE: Final[str] = "profile"
PROFILE_BENNI: Final[str] = "benni"
PROFILE_ELTERN: Final[str] = "eltern"
PROFILES: Final[list[str]] = [PROFILE_BENNI, PROFILE_ELTERN]
DEFAULT_PROFILE: Final[str] = PROFILE_BENNI
PROFILE_LABELS: Final[dict[str, str]] = {PROFILE_BENNI: "Benni", PROFILE_ELTERN: "Eltern"}

# --------------------------------------------------------------------------- #
# Context / Subcontext / Device (KOPIE aus media_context-Contract, kein Import).
# --------------------------------------------------------------------------- #
CTX_IDLE: Final = "idle"
CTX_TV: Final = "tv"
CTX_STREAMING: Final = "streaming"
CTX_GAMING: Final = "gaming"
CTX_PRIVATE: Final = "private_time"

SUB_GAME_GRIND: Final = "gaming_grind"

DEV_DENON: Final = "denon"
DEV_PC: Final = "pc"

# --------------------------------------------------------------------------- #
# Audio-Owner / Action (Orchestrator-Lift).
# --------------------------------------------------------------------------- #
AUDIO_OWNER_NONE: Final = "none"
AUDIO_OWNER_HOMEPODS: Final = "homepods"
AUDIO_OWNER_TV_DENON: Final = "tv_denon"
AUDIO_OWNER_GAMING: Final = "gaming_stack"
AUDIO_OWNER_PRIVATE: Final = "private_stack"
# FLEET-221: Schlaf ist ein eigener Owner, NICHT „private_stack" (Privat-Session
# via context/stash). Beide verdrängen die HomePods (competes) + Szenario off,
# aber getrennt benannt fürs Cockpit (bio_sleep vs. echtem private_time).
AUDIO_OWNER_SLEEP: Final = "sleep"

ACTION_NONE: Final = "none"
ACTION_PAUSE: Final = "pause_homepods"
ACTION_RESUME: Final = "resume_homepods"
ACTION_START_RADIO: Final = "start_radio"

RESUME_MODE_MANUAL: Final = "manual"
RESUME_MODE_RADIO: Final = "radio"

# Issue #59: provisional_sleep is the same consumer-side sleep context as S.
# It remains a distinct Core-State lifecycle value; policy only consumes it as
# the HomePod/baseline modifier and never turns it into Wake evidence.
BIO_SLEEP_VALUES: Final = ("provisional_sleep", "sleep", "sleeping", "asleep")

# --------------------------------------------------------------------------- #
# Audio-Scenario — Desired-Audio-Wahrheit (FLEET-85). Immer aus der Konstellation
# abgeleitet, unabhängig vom beobachtbaren Player-Zustand (idle/unavailable/
# playing). Kernregel (Benni): kein Screen-/Sleep-Szenario aktiv → Musik ist die
# Baseline (nie „idle"). Quiet bleibt ein Volume-Overlay (binary_sensor.*_quiet_
# mode), KEIN Szenario. Konsumenten: Umbrella-Status (FLEET-86), Apply-Converge
# (FLEET-84).
# --------------------------------------------------------------------------- #
AUDIO_SCENARIO_OFF: Final = "off"
AUDIO_SCENARIO_PRIVATE: Final = "private"
AUDIO_SCENARIO_GAMING: Final = "gaming"
AUDIO_SCENARIO_TV: Final = "tv"
AUDIO_SCENARIO_MUSIC: Final = "music"

AUDIO_SCENARIO_LABELS: Final[dict[str, str]] = {
    AUDIO_SCENARIO_OFF: "Aus",
    AUDIO_SCENARIO_PRIVATE: "Privat",
    AUDIO_SCENARIO_GAMING: "Gaming",
    AUDIO_SCENARIO_TV: "TV",
    AUDIO_SCENARIO_MUSIC: "Musik",
}

# --------------------------------------------------------------------------- #
# Volume-Policy-Zustände (volume_orchestrator-Lift).
# --------------------------------------------------------------------------- #
VOL_POLICY_IDLE: Final = "idle"
VOL_POLICY_MEDIA: Final = "media"
VOL_POLICY_DUCKED: Final = "ducked"
VOL_POLICY_MUTED: Final = "muted"
VOL_POLICY_BLOCKED: Final = "blocked"

# --------------------------------------------------------------------------- #
# Volume-Modell v3.1 (FLEET-38) — Dayphase-Baselines + Boost/Mute.
# Die per-Phase-Baseline (Lastenheft §6) ERSETZT die alten flachen night/edge-
# Offsets: die Tagesphase steckt jetzt in der Baseline selbst. Werte slug-gleich
# mit benni_core_day_state. Unbekannte/fehlende Phase → flacher Fallback-Base
# (VolumeSettings.homepods_base / .denon_base).
# --------------------------------------------------------------------------- #
DAY_PHASES: Final[tuple[str, ...]] = (
    "early_morning", "late_morning", "forenoon", "afternoon",
    "early_evening", "late_evening", "early_night", "late_night",
)
HOMEPODS_BASELINES: Final[dict[str, float]] = {
    "early_morning": 0.25, "late_morning": 0.30, "forenoon": 0.40, "afternoon": 0.45,
    "early_evening": 0.40, "late_evening": 0.35, "early_night": 0.30, "late_night": 0.25,
}
DENON_BASELINES: Final[dict[str, float]] = {
    "early_morning": 0.20, "late_morning": 0.25, "forenoon": 0.30, "afternoon": 0.30,
    "early_evening": 0.30, "late_evening": 0.30, "early_night": 0.25, "late_night": 0.20,
}

# Musik-Title-Classifier-Enum (HomePods) — R18 Boost / R19 Mute.
MUSIC_ENUM_NORMAL: Final[int] = 0
MUSIC_ENUM_BOOST: Final[int] = 1
MUSIC_ENUM_MUTE: Final[int] = 2

# R18: Track-Boost geblockt in diesen Activity-Kontexten (+ bei Quiet, strukturell).
ACT_WORK_HOME: Final = "work_home"
ACT_WORK_AWAY: Final = "work_away"
BOOST_BLOCK_ACTIVITIES: Final = (ACT_WORK_HOME, ACT_WORK_AWAY)

# --------------------------------------------------------------------------- #
# Subwoofer-Policy v3.1 (FLEET-39) — Dayphase-Fenster (Lastenheft §6).
# Erlaubt: forenoon bis einschließlich late_evening; alte Phasenwerte bleiben
# kompatibel. Vor 09:00 sowie in early_morning/early_night/late_night gesperrt.
# Sonderfall gaming_grind → immer aus (R16, schon im Lift). Die saisonalen
# Phasengrenzen ersetzen nicht den zusätzlichen 09:00-Wanduhr-Floor.
# --------------------------------------------------------------------------- #
SUB_ALLOWED_PHASES: Final[tuple[str, ...]] = (
    "late_morning", "forenoon", "midday", "afternoon",
    "late_afternoon", "early_evening", "evening", "late_evening",
)
SUB_EARLIEST_MINUTE: Final[int] = 9 * 60   # 09:00 lokal, Minuten seit Mitternacht

# --------------------------------------------------------------------------- #
# Config-Keys — Eingänge aus benni_media_state (via Entity-State).
# --------------------------------------------------------------------------- #
CONF_MEDIA_CONTEXT: Final[str] = "media_context_entity"
CONF_MEDIA_SUBCONTEXT: Final[str] = "media_subcontext_entity"
CONF_MEDIA_DEVICE: Final[str] = "media_device_entity"
CONF_ENTERTAINMENT_ACTIVE: Final[str] = "entertainment_active_entity"
CONF_HEADSET_ACTIVE: Final[str] = "headset_active_entity"
CONF_QUIET_MODE: Final[str] = "quiet_mode_entity"
CONF_PRESENCE_STATE: Final[str] = "presence_state_entity"
CONF_AWAY_GATE: Final[str] = "away_gate_entity"

# Eigene Policy-Inputs (Roh-/Template-Entitäten — core_devices-Atomics später).
CONF_HOMEPODS: Final[str] = "homepods_player_entity"   # MA-Gruppe (1 Player)
CONF_DENON: Final[str] = "denon_player_entity"
CONF_DENON_ACTIVE: Final[str] = "denon_active_entity"  # Core-Devices Denon Master
CONF_SUBWOOFER: Final[str] = "subwoofer_entity"        # nur Target (Apply-Layer)
CONF_BIO_STATE: Final[str] = "bio_state_entity"
CONF_DAY_STATE: Final[str] = "day_state_entity"
CONF_ACTIVITY_STATE: Final[str] = "activity_state_entity"   # R18 Boost-Block (work_*)
CONF_OPENING: Final[str] = "opening_any_open_entity"   # Fenster/Tür offen → Offset
CORE_OPENINGS_MASTER_ENTITY: Final[str] = "sensor.benni_combined_openings"
CORE_OPENINGS_MEDIA_ATTRIBUTE: Final[str] = "any_not_closed"
CONF_HOMEPODS_MUSIC_ENUM: Final[str] = "homepods_music_enum_entity"  # R18/R19 Boost/Mute
CONF_MANUAL_PLAYBACK: Final[str] = "manual_playback_entity"
CONF_PLANNED_RADIO: Final[str] = "planned_radio_entity"
CONF_MEDIA_STOP_LATCH: Final[str] = "media_stop_latch_entity"
CONF_RADIO_STATION: Final[str] = "radio_station_entity"  # input_select Sender → audio_scenario-Detail (FLEET-85)
CONF_WAKE_NEEDED: Final[str] = "wake_needed_entity"  # wake_planner Wach-Flanke → manual_stop-Reset

# Keys, deren gebundene Entities der Coordinator beobachtet (event-driven).
WATCH_KEYS: Final[tuple[str, ...]] = (
    CONF_MEDIA_CONTEXT, CONF_MEDIA_SUBCONTEXT, CONF_MEDIA_DEVICE,
    CONF_ENTERTAINMENT_ACTIVE, CONF_HEADSET_ACTIVE, CONF_QUIET_MODE,
    CONF_PRESENCE_STATE, CONF_AWAY_GATE,
    CONF_HOMEPODS, CONF_DENON, CONF_DENON_ACTIVE,
    CONF_BIO_STATE, CONF_DAY_STATE, CONF_ACTIVITY_STATE, CONF_OPENING,
    CONF_HOMEPODS_MUSIC_ENUM,
    CONF_MANUAL_PLAYBACK, CONF_PLANNED_RADIO, CONF_MEDIA_STOP_LATCH,
    CONF_RADIO_STATION, CONF_WAKE_NEEDED,
)

# Subwoofer ist nur ein Apply-Target (kein beobachteter Input).
ENTITY_SLOT_KEYS: Final[tuple[str, ...]] = WATCH_KEYS + (CONF_SUBWOOFER,)

# --------------------------------------------------------------------------- #
# Profil-Map (Auto-Bind). benni: media_state-Entities (Konsum-Vertrag) + Live-IDs
# der Einhornzentrale. Direkte Player bleiben für Player-Attribute/Targets;
# aktive Geräte-Wahrheiten kommen aus Core-Devices-Mastern.
# eltern leer (Anlage existiert noch nicht). Existenz-Filter regelt Fehlendes.
# --------------------------------------------------------------------------- #
PROFILE_PREFILL: Final[dict[str, dict[str, Any]]] = {
    PROFILE_BENNI: {
        # aus media_state (Feeder):
        CONF_MEDIA_CONTEXT: "sensor.benni_media_state_media_context",
        CONF_MEDIA_SUBCONTEXT: "sensor.benni_media_state_media_subcontext",
        CONF_MEDIA_DEVICE: "sensor.benni_media_state_media_device",
        CONF_ENTERTAINMENT_ACTIVE: "binary_sensor.benni_media_state_entertainment_active",
        CONF_HEADSET_ACTIVE: "binary_sensor.benni_media_state_headset_active",
        CONF_QUIET_MODE: "binary_sensor.benni_media_state_quiet_mode",
        # Live existiert (renamed-device, system_-Präfix) NUR der system_-Slug —
        # der clean slug existierte nie → presence_degraded / toter Away-Block.
        CONF_PRESENCE_STATE: "sensor.system_benni_media_state_presence_state",
        CONF_AWAY_GATE: "binary_sensor.system_benni_media_state_away_gate",
        # eigene Geräte/Inputs:
        CONF_HOMEPODS: "media_player.living_homepods_ma_group",
        CONF_DENON: "media_player.living_denon",
        CONF_DENON_ACTIVE: "sensor.benni_master_denon",
        CONF_BIO_STATE: "sensor.benni_core_state_bio_state",
        CONF_DAY_STATE: "sensor.benni_combined_context_day_state",
        CONF_ACTIVITY_STATE: "sensor.benni_core_state_activity_state",
        CONF_OPENING: CORE_OPENINGS_MASTER_ENTITY,
        CONF_HOMEPODS_MUSIC_ENUM: "sensor.title_classifier_musikkatalog_enum",
        CONF_MANUAL_PLAYBACK: "binary_sensor.media_manual_playback_active",
        CONF_PLANNED_RADIO: "binary_sensor.media_radio_playing_planned_station",
        # Expliziter User-Stopp (wie apply). NICHT auf die eigene manual_stop-
        # Ausgabe binden — das wäre ein Selbst-Latch, der bis zum Wecken klebt.
        CONF_MEDIA_STOP_LATCH: "input_boolean.media_stop_latch",
        CONF_RADIO_STATION: "input_select.media_radio_station",
        CONF_WAKE_NEEDED: "binary_sensor.wake_planner_benni_wake_needed",
    },
    PROFILE_ELTERN: {},
}

LEGACY_ENTITY_MAP: Final[dict[str, str]] = {
    "sensor.benni_core_day_state": "sensor.benni_combined_context_day_state",
    # FLEET-94: Fenster-Offset liest Kipp/offen aus dem Openings-Master.
    "sensor.benni_combined_opening_any_open": CORE_OPENINGS_MASTER_ENTITY,
    "sensor.benni_combined_opening_any_open_or_tilted": CORE_OPENINGS_MASTER_ENTITY,
    # FLEET-64-retirete Atomics/Combineds → core_devices (Diagnostics-Fund).
    "binary_sensor.living_denon_plug_power_active_atomic": "sensor.benni_master_denon",
    "sensor.benni_device_living_avr": "sensor.benni_master_denon",
    "binary_sensor.opening_any_open_combined": CORE_OPENINGS_MASTER_ENTITY,
    # Renamed-device (system_) Entity-ID-Fix: media_states presence_state/away_gate
    # existieren live nur als system_-Slug; alte gebackene clean-Slugs repointen.
    "sensor.benni_media_state_presence_state": "sensor.system_benni_media_state_presence_state",
    "binary_sensor.benni_media_state_away_gate": "binary_sensor.system_benni_media_state_away_gate",
}

# --------------------------------------------------------------------------- #
# Options — Apply-Gate + Volume-Settings (volume_orchestrator-Lift + GRIND).
# --------------------------------------------------------------------------- #
CONF_APPLY_ENABLED: Final[str] = "apply_enabled"
DEFAULT_APPLY_ENABLED: Final[bool] = False   # Shadow-safe out of the box.

# homepods_base / denon_base = Fallback-Baseline bei unbekannter Tagesphase
# (die regulären Werte kommen aus HOMEPODS_BASELINES / DENON_BASELINES).
CONF_VOL_HOMEPODS_BASE: Final[str] = "volume_homepods_base"
CONF_VOL_DENON_BASE: Final[str] = "volume_denon_base"
CONF_VOL_DUCKED_TARGET: Final[str] = "volume_ducked_target"
CONF_VOL_HOMEPODS_MAX: Final[str] = "volume_homepods_max"
CONF_VOL_DENON_MAX: Final[str] = "volume_denon_max"
CONF_VOL_ACTIVE_MIN: Final[str] = "volume_active_min"
CONF_VOL_OPENING_OFFSET: Final[str] = "volume_opening_offset"  # Legacy (Migration → per-Gerät)
# control#3: Fenster-Offset je Gerät getrennt (gekippt == offen; geschlossen = 0).
CONF_VOL_OPENING_OFFSET_HOMEPODS: Final[str] = "volume_opening_offset_homepods"
CONF_VOL_OPENING_OFFSET_DENON: Final[str] = "volume_opening_offset_denon"
CONF_VOL_BOOST_OFFSET: Final[str] = "volume_boost_offset"      # R18 Track-Boost
# control#3: Grind-Offset je Gerät (HomePods 0, Denon −0.10). Ersetzt den einen
# versteckten Skalar; persistent + editierbar.
CONF_GRIND_HOMEPODS_OFFSET: Final[str] = "grind_homepods_offset"
CONF_GRIND_DENON_OFFSET: Final[str] = "grind_denon_offset"     # R17 szenario_offset (Grind)
# control#3: Private-Time-Denon-Cap (Ceiling; min(normal, cap), Nudge-fest).
CONF_PRIVATE_DENON_CAP: Final[str] = "private_denon_cap"

DEFAULT_VOL_HOMEPODS_BASE: Final = 0.35
DEFAULT_VOL_DENON_BASE: Final = 0.40
DEFAULT_VOL_DUCKED_TARGET: Final = 0.15
DEFAULT_VOL_HOMEPODS_MAX: Final = 0.65
DEFAULT_VOL_DENON_MAX: Final = 0.70
DEFAULT_VOL_ACTIVE_MIN: Final = 0.05
DEFAULT_VOL_OPENING_OFFSET: Final = -0.05          # Legacy-Default (Migrationsquelle)
DEFAULT_VOL_OPENING_OFFSET_HOMEPODS: Final = -0.05
DEFAULT_VOL_OPENING_OFFSET_DENON: Final = -0.05
DEFAULT_VOL_BOOST_OFFSET: Final = 0.15
# GRIND-Delta (control#3): HomePods normal (0), Denon leise Kulisse (−0.10).
DEFAULT_GRIND_HOMEPODS_OFFSET: Final = 0.0
DEFAULT_GRIND_DENON_OFFSET: Final = -0.10
# Private-Time-Denon-Cap-Default (kein Migrationswert ableitbar → 0.15).
DEFAULT_PRIVATE_DENON_CAP: Final = 0.15

# control#3: Über das Matrix-Panel editierbare Skalare. Schreiben in dieselben
# ConfigEntry-Options wie der Options-Flow → EINE persistente Quelle. Der
# Legacy-Skalar volume_opening_offset ist bewusst NICHT dabei (per-Gerät ersetzt).
PANEL_SCALAR_KEYS: Final[tuple[str, ...]] = (
    "volume_homepods_base", "volume_denon_base",
    "volume_ducked_target", "volume_homepods_max", "volume_denon_max",
    "volume_active_min", "volume_boost_offset",
    "volume_opening_offset_homepods", "volume_opening_offset_denon",
    "grind_homepods_offset", "grind_denon_offset",
    "private_denon_cap",
)

VOL_SETTING_DEFAULTS: Final[dict[str, float]] = {
    CONF_VOL_HOMEPODS_BASE: DEFAULT_VOL_HOMEPODS_BASE,
    CONF_VOL_DENON_BASE: DEFAULT_VOL_DENON_BASE,
    CONF_VOL_DUCKED_TARGET: DEFAULT_VOL_DUCKED_TARGET,
    CONF_VOL_HOMEPODS_MAX: DEFAULT_VOL_HOMEPODS_MAX,
    CONF_VOL_DENON_MAX: DEFAULT_VOL_DENON_MAX,
    CONF_VOL_ACTIVE_MIN: DEFAULT_VOL_ACTIVE_MIN,
    CONF_VOL_OPENING_OFFSET: DEFAULT_VOL_OPENING_OFFSET,
    CONF_VOL_OPENING_OFFSET_HOMEPODS: DEFAULT_VOL_OPENING_OFFSET_HOMEPODS,
    CONF_VOL_OPENING_OFFSET_DENON: DEFAULT_VOL_OPENING_OFFSET_DENON,
    CONF_VOL_BOOST_OFFSET: DEFAULT_VOL_BOOST_OFFSET,
    CONF_GRIND_HOMEPODS_OFFSET: DEFAULT_GRIND_HOMEPODS_OFFSET,
    CONF_GRIND_DENON_OFFSET: DEFAULT_GRIND_DENON_OFFSET,
    CONF_PRIVATE_DENON_CAP: DEFAULT_PRIVATE_DENON_CAP,
}

# --------------------------------------------------------------------------- #
# Default-data. Spiegelt das Entity-Roster (= PolicyDecision.as_dict()).
# quiet_mode lebt in media_state (L1, FLEET-31) — hier nur konsumiert.
# --------------------------------------------------------------------------- #
DEFAULT_DATA: Final[dict[str, Any]] = {
    "volume_target_homepods": None,
    "volume_target_denon": None,
    "audio_owner": AUDIO_OWNER_NONE,
    "audio_scenario": AUDIO_SCENARIO_MUSIC,
    "audio_scenario_label": AUDIO_SCENARIO_LABELS[AUDIO_SCENARIO_MUSIC],
    "audio_scenario_detail": None,
    "action": ACTION_NONE,
    "volume_policy": VOL_POLICY_IDLE,
    "subwoofer_allowed": False,
    "homepods_should_pause": False,
    "homepods_resume_allowed": False,
    "volume_apply_allowed": False,
    "manual_stop": False,
}

# --------------------------------------------------------------------------- #
# Output-Entity-Roster. uid = unique_id-Suffix = object_id-Basis, key = data-Feld.
# --------------------------------------------------------------------------- #
UID_VOLUME_TARGET_HOMEPODS: Final[str] = "volume_target_homepods"
UID_VOLUME_TARGET_DENON: Final[str] = "volume_target_denon"
UID_AUDIO_OWNER: Final[str] = "audio_owner"
UID_AUDIO_SCENARIO: Final[str] = "audio_scenario"
UID_ACTION: Final[str] = "action"
UID_VOLUME_POLICY: Final[str] = "volume_policy"
UID_SUBWOOFER_ALLOWED: Final[str] = "subwoofer_allowed"
UID_HOMEPODS_SHOULD_PAUSE: Final[str] = "homepods_should_pause"
UID_HOMEPODS_RESUME_ALLOWED: Final[str] = "homepods_resume_allowed"
UID_VOLUME_APPLY_ALLOWED: Final[str] = "volume_apply_allowed"
UID_MANUAL_STOP: Final[str] = "manual_stop"

# --------------------------------------------------------------------------- #
# Panel / WebSocket-API (Vanilla, kein Build-Step).
# --------------------------------------------------------------------------- #
PANEL_URL_PATH: Final[str] = "benni_media_policy"
PANEL_TITLE: Final[str] = "Media Policy"
PANEL_ICON: Final[str] = "mdi:volume-high"
FRONTEND_DIR_URL: Final[str] = "/benni_media_policy_app"
FRONTEND_ENTRY: Final[str] = f"{FRONTEND_DIR_URL}/main.js"
PANEL_ELEMENT: Final[str] = "bmp-app"

WS_GET_STATUS: Final[str] = f"{DOMAIN}/get_status"
# FLEET-102 Volume-Matrix: Snapshot lesen (A) + persistent setzen/zurücksetzen (B).
WS_GET_MATRIX: Final[str] = f"{DOMAIN}/get_matrix"
WS_SET_MATRIX: Final[str] = f"{DOMAIN}/set_matrix"
WS_RESET_MATRIX: Final[str] = f"{DOMAIN}/reset_matrix"
# control#3: Skalare (Grind/Fenster/Cap/…) aus dem Panel setzen — schreibt in
# DIESELBEN ConfigEntry-Options wie der Options-Flow (eine Quelle, keine Dopplung).
WS_SET_SCALARS: Final[str] = f"{DOMAIN}/set_scalars"
# R21/R22 Nudge-Steuerung (Laufzeit, Cockpit-Quick-Actions).
WS_NUDGE_VOLUME: Final[str] = f"{DOMAIN}/nudge_volume"
WS_RESET_NUDGE: Final[str] = f"{DOMAIN}/reset_nudge"
WS_RESET_BOOST: Final[str] = f"{DOMAIN}/reset_boost"

# R21: ±-Schritt + Klammer für den manuellen Nudge-Offset.
NUDGE_STEP: Final[float] = 0.05
NUDGE_MIN: Final[float] = -0.30
NUDGE_MAX: Final[float] = 0.30
