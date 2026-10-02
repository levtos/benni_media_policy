"""HA-freie Policy-Engine für benni_media_policy (L2 / Media-Gehirn).

Phase 3 (FLEET-34): Lift + Adaption von orchestrator.py (Audio-Owner,
pause/resume/radio) + volume_orchestrator.py (Volume-Policy, Ducking, Offsets) +
evaluate_subwoofer/_denon_audio_path aus bennis_toolbox/benni_media_context.

Volume-Modell v3.1 (FLEET-38): `decide_volume` ersetzt die flachen night/edge-
Offsets durch per-Dayphase-Baselines (Lastenheft §6) und die R17-Formel
(baseline + szenario/fenster/kontext/manual-Offset + track_boost). R18 Track-
Boost (Musik-Enum 1, geblockt in work_*/Quiet) und R19 Mute (Enum 2 → hart 0).
Nudges (R21/R22, ±0.10 + Boost-Reset) sind als Folge-Karte ausgegliedert
(manual_off-Hook liegt schon vor).

Adaption ggü. dem Toolbox-Original (verbindlich per Board):
- **Owner aus media_state-Context**, nicht Roh-Re-Derive: der Context kodiert
  Geräte-Priorität + B2-Gaming-Gate bereits (media_state). owner = f(context,
  quiet, bio_sleep, homepods_playing).
- **Pflicht-Delta GRIND** (v3.1 KH-5/R15/R16/§6): bei subcontext == gaming_grind
  1. HomePods werden NICHT pausiert (Grind hat HomePods-Anteil → spielen weiter),
  2. Volume: HomePods auf Normal-Niveau, Denon mit negativem Offset (Kulisse),
  3. Subwoofer IMMER aus (R16 übersteuert die Sub-Policy).

Gating klar getrennt: `volume_apply_allowed` = pro Entscheidung (hier). Der
globale `apply_enabled`-Shadow-Switch lebt als Option im Coordinator; der
Apply-Layer (benni_media_apply) prüft BEIDE.

Keine HA-Imports. Der Coordinator macht das Entity-State-Plumbing + hält den
persistenten OrchestratorState über die Ticks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .const import (
    ACTION_NONE,
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_START_RADIO,
    AUDIO_OWNER_GAMING,
    AUDIO_OWNER_HOMEPODS,
    AUDIO_OWNER_NONE,
    AUDIO_OWNER_PRIVATE,
    AUDIO_OWNER_TV_DENON,
    AUDIO_SCENARIO_GAMING,
    AUDIO_SCENARIO_LABELS,
    AUDIO_SCENARIO_MUSIC,
    AUDIO_SCENARIO_OFF,
    AUDIO_SCENARIO_PRIVATE,
    AUDIO_SCENARIO_TV,
    BOOST_BLOCK_ACTIVITIES,
    CTX_GAMING,
    CTX_PRIVATE,
    CTX_STREAMING,
    CTX_TV,
    DEFAULT_GRIND_DENON_OFFSET,
    DEFAULT_GRIND_HOMEPODS_OFFSET,
    DEFAULT_PRIVATE_DENON_CAP,
    DEFAULT_VOL_ACTIVE_MIN,
    DEFAULT_VOL_BOOST_OFFSET,
    DEFAULT_VOL_DENON_BASE,
    DEFAULT_VOL_DENON_MAX,
    DEFAULT_VOL_DUCKED_TARGET,
    DEFAULT_VOL_HOMEPODS_BASE,
    DEFAULT_VOL_HOMEPODS_MAX,
    DEFAULT_VOL_OPENING_OFFSET,
    DEFAULT_VOL_OPENING_OFFSET_DENON,
    DEFAULT_VOL_OPENING_OFFSET_HOMEPODS,
    DENON_BASELINES,
    DEV_DENON,
    DEV_PC,
    HOMEPODS_BASELINES,
    MUSIC_ENUM_BOOST,
    MUSIC_ENUM_MUTE,
    RESUME_MODE_MANUAL,
    RESUME_MODE_RADIO,
    SUB_ALLOWED_PHASES,
    SUB_EARLIEST_MINUTE,
    SUB_GAME_GRIND,
    VOL_POLICY_BLOCKED,
    VOL_POLICY_DUCKED,
    VOL_POLICY_IDLE,
    VOL_POLICY_MEDIA,
)


# --------------------------------------------------------------------------- #
# Inputs / persistenter Zustand / Settings
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Inputs:
    """Snapshot der Policy-Eingänge. None = unknown/nicht gebunden."""

    # aus media_state (Entity-State):
    context: Optional[str] = None
    subcontext: Optional[str] = None
    device: Optional[str] = None
    entertainment_active: bool = False
    headset_active: bool = False
    quiet_mode: bool = False
    presence_state: Optional[str] = None
    presence_degraded: bool = False
    away_gate: Optional[bool] = None
    # eigene Inputs:
    homepods_state: Optional[str] = None      # "playing"/"paused"/"idle"/…
    homepods_configured: bool = False
    # HomePods stabil ≥N s nicht am Spielen (Coordinator-Debounce): Gate für die
    # Musik-Baseline, damit Restore-Flap/Track-Gap sie nicht auslösen.
    homepods_stably_idle: bool = False
    denon_configured: bool = False
    denon_active: bool = False                # Denon Master / player-active fallback
    denon_source: Optional[str] = None        # Denon-Player source-Attribut
    bio_state: Optional[str] = None
    bio_sleep: bool = False
    day_state: Optional[str] = None
    activity_context: Optional[str] = None    # core_state activity_state (R18-Block)
    homepods_music_enum: Optional[int] = None  # title_classifier musikkatalog (R18/R19)
    opening_any_open: bool = False
    local_minute_of_day: Optional[int] = None  # Wanduhr-Floor Subwoofer (09:00)
    manual_playback_active: bool = False
    planned_radio_active: bool = False
    radio_station: Optional[str] = None        # gewählter Sender (audio_scenario-Detail)
    media_stop_latch: Optional[bool] = None   # None = nicht konfiguriert
    wake_needed: Optional[bool] = None         # wake_planner Wach-Flanke → manual_stop-Reset
    manual_nudge: float = 0.0                  # R21 ±0.10-Nudge (Laufzeit, Cockpit)
    boost_suppressed: bool = False             # R22 Boost-Reset (Track-Boost aus)


@dataclass
class OrchestratorState:
    """Persistenter Zustand zwischen Coordinator-Ticks (RAM, wie Toolbox)."""

    auto_paused: bool = False
    pre_pause_mode: Optional[str] = None
    manual_stop: bool = False
    last_homepods_playing: bool = False
    last_competes: bool = False
    last_planned_radio_active: bool = False
    last_manual_playback_active: bool = False
    last_wake_needed: bool = False   # Wach-Flanken-Erkennung (wake_planner)
    # FLEET-153: letztes HomePods-MEDIA-Target, gehalten über Idle-Gaps (sticky),
    # solange HomePods der zuletzt spielende Pfad war. None = kein Stick (noch nie
    # gespielt ODER Pfad ist von HomePods weg, z.B. Denon/TV).
    last_hp_media_target: Optional[float] = None


@dataclass(frozen=True)
class VolumeSettings:
    # homepods_base/denon_base = Fallback-Baseline bei unbekannter Tagesphase;
    # die regulären Baselines kommen aus HOMEPODS_BASELINES/DENON_BASELINES.
    homepods_base: float = DEFAULT_VOL_HOMEPODS_BASE
    denon_base: float = DEFAULT_VOL_DENON_BASE
    ducked_target: float = DEFAULT_VOL_DUCKED_TARGET
    homepods_max: float = DEFAULT_VOL_HOMEPODS_MAX
    denon_max: float = DEFAULT_VOL_DENON_MAX
    active_min: float = DEFAULT_VOL_ACTIVE_MIN
    opening_offset: float = DEFAULT_VOL_OPENING_OFFSET   # Legacy (Migrationsquelle)
    # control#3: Fenster-Offset je Gerät (gekippt == offen; geschlossen = 0).
    opening_offset_homepods: float = DEFAULT_VOL_OPENING_OFFSET_HOMEPODS
    opening_offset_denon: float = DEFAULT_VOL_OPENING_OFFSET_DENON
    boost_offset: float = DEFAULT_VOL_BOOST_OFFSET       # R18 Track-Boost
    # control#3: Grind-Offset je Gerät (HomePods 0, Denon −0.10).
    grind_homepods_offset: float = DEFAULT_GRIND_HOMEPODS_OFFSET
    grind_denon_offset: float = DEFAULT_GRIND_DENON_OFFSET  # R17 szenario_offset (Grind)
    # control#3: Private-Time-Denon-Cap (Ceiling; min(normal, cap), Nudge-fest).
    private_denon_cap: float = DEFAULT_PRIVATE_DENON_CAP
    # FLEET-102 Volume-Matrix (Stage A): tabellarische Summanden, data-driven.
    # Base = per-Tagesphase (Lastenheft §6). scenario_off/activity_off seeded leer
    # → Lookup .get(key, 0.0) = 0.0 ⇒ verhaltensgleich, bis Stage C sie füllt.
    # Pro Gerät getrennt (HomePods/Denon), wie die Baselines.
    base_homepods: dict = field(default_factory=lambda: dict(HOMEPODS_BASELINES))
    base_denon: dict = field(default_factory=lambda: dict(DENON_BASELINES))
    scenario_off_homepods: dict = field(default_factory=dict)
    scenario_off_denon: dict = field(default_factory=dict)
    activity_off_homepods: dict = field(default_factory=dict)
    activity_off_denon: dict = field(default_factory=dict)


@dataclass
class PolicyDecision:
    """Entscheidung. Spiegelt das Entity-Roster + Debug-Felder."""

    # Roster (Output-Entities):
    volume_target_homepods: Optional[float] = None
    volume_target_denon: Optional[float] = None
    audio_owner: str = AUDIO_OWNER_NONE
    audio_scenario: str = AUDIO_SCENARIO_MUSIC
    audio_scenario_label: str = AUDIO_SCENARIO_LABELS[AUDIO_SCENARIO_MUSIC]
    audio_scenario_detail: Optional[str] = None
    action: str = ACTION_NONE
    volume_policy: str = VOL_POLICY_IDLE
    subwoofer_allowed: bool = False
    homepods_should_pause: bool = False
    homepods_resume_allowed: bool = False
    volume_apply_allowed: bool = False
    # Debug (nur im WS-Status / nicht als eigene Entity):
    owner_reason: str = "idle"
    action_reason: str = "idle"
    volume_reason: str = "idle"
    subwoofer_block_reason: Optional[str] = None
    denon_audio_path: bool = False
    is_grind: bool = False
    is_pc_gaming: bool = False
    music_baseline_active: bool = False
    track_boost_applied: bool = False
    music_muted: bool = False
    manual_stop: bool = False   # Stop hält bis zum nächsten Wecken (wake_planner)
    active_reasons: list = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "volume_target_homepods": self.volume_target_homepods,
            "volume_target_denon": self.volume_target_denon,
            "audio_owner": self.audio_owner,
            "audio_scenario": self.audio_scenario,
            "audio_scenario_label": self.audio_scenario_label,
            "audio_scenario_detail": self.audio_scenario_detail,
            "action": self.action,
            "volume_policy": self.volume_policy,
            "subwoofer_allowed": self.subwoofer_allowed,
            "homepods_should_pause": self.homepods_should_pause,
            "homepods_resume_allowed": self.homepods_resume_allowed,
            "volume_apply_allowed": self.volume_apply_allowed,
            "manual_stop": self.manual_stop,
        }

    def debug(self) -> dict[str, Any]:
        return {
            **self.as_dict(),
            "owner_reason": self.owner_reason,
            "action_reason": self.action_reason,
            "volume_reason": self.volume_reason,
            "subwoofer_block_reason": self.subwoofer_block_reason,
            "denon_audio_path": self.denon_audio_path,
            "is_grind": self.is_grind,
            "is_pc_gaming": self.is_pc_gaming,
            "music_baseline_active": self.music_baseline_active,
            "track_boost_applied": self.track_boost_applied,
            "music_muted": self.music_muted,
            "active_reasons": list(self.active_reasons),
        }


# --------------------------------------------------------------------------- #
# Owner (aus media_state-Context, kein Roh-Re-Derive)
# --------------------------------------------------------------------------- #
def decide_owner(inp: Inputs) -> tuple[str, str]:
    """audio_owner + Label aus dem Context. Priorität wie Lastenheft:
    private_time > gaming > streaming/tv > homepods > none.

    control#45: `bio_sleep` ist ein HomePods-Modifier, kein globales Owner-Gate.

    WICHTIG (FLEET-81 / FLEET-31): `quiet_mode` gehört hier NICHT rein. Quiet (Tür/
    Anruf, R20) ist ein reines Volume-Overlay (decide_volume duckt auf ducked_target),
    KEIN Owner/Szenario. Früher koppelte quiet→PRIVATE → competes → pause_homepods
    UND hp_plays=False → Ducked-HomePods=0.0 statt 0.10 (= „Wiedergabe komplett
    gestoppt"). CTX_PRIVATE ist das echte Privat-Szenario."""
    if inp.context == CTX_PRIVATE:
        return AUDIO_OWNER_PRIVATE, "private_time"
    if inp.context == CTX_GAMING:
        return AUDIO_OWNER_GAMING, f"gaming_{inp.device or 'unknown'}"
    if inp.context in (CTX_STREAMING, CTX_TV):
        return AUDIO_OWNER_TV_DENON, ("streaming" if inp.context == CTX_STREAMING else "tv")
    if inp.homepods_state == "playing":
        return AUDIO_OWNER_HOMEPODS, "homepods_only"
    return AUDIO_OWNER_NONE, "idle"


def decide_audio_scenario(
    inp: Inputs, owner: str, grind: bool
) -> tuple[str, str, Optional[str]]:
    """Desired-Audio-Wahrheit (FLEET-85): (scenario, label, detail).

    Immer aus der Konstellation abgeleitet, UNABHÄNGIG vom beobachtbaren Player-
    Zustand (idle/unavailable/playing). Kernregel (Benni): kein Screen-/Sleep-
    Szenario aktiv → Musik ist die Baseline. Genau das fixt den „idle"-Bug:
    owner == NONE (HomePods spielen gerade nicht) ergibt NICHT idle, sondern
    music. Quiet bleibt ein Volume-Overlay (decide_volume → ducked), KEIN
    Szenario — die Umbrella komponiert das Leise-Badge aus quiet_mode."""
    if owner == AUDIO_OWNER_PRIVATE:
        scenario, detail = AUDIO_SCENARIO_PRIVATE, None
    elif owner == AUDIO_OWNER_GAMING or grind:
        scenario, detail = AUDIO_SCENARIO_GAMING, (inp.device or None)
    elif owner == AUDIO_OWNER_TV_DENON:
        scenario, detail = AUDIO_SCENARIO_TV, (inp.context or None)
    else:
        # owner HOMEPODS oder NONE → Musik-Baseline (der Fix gegen „idle").
        scenario, detail = AUDIO_SCENARIO_MUSIC, ((inp.radio_station or "").strip() or None)
    return scenario, AUDIO_SCENARIO_LABELS[scenario], detail


def media_block_reason(inp: Inputs) -> Optional[str]:
    """Höchstpriorer Abwesenheits-Block. NUR echte Abwesenheit (away) blockt
    Media hart: scenario off, Pause laufender HomePods, Volume 0.

    `unknown`/degraded ist BEWUSST kein Block: bei einem Reload/Restart flappt
    presence_state kurz auf `unknown` — das darf laufende Musik NICHT anfassen
    (defensiv: wir wissen nicht, ob weg). Unknown hält nur Auto-Start/Resume
    zurück, siehe `presence_holds_resume`.
    """
    if inp.away_gate is True:
        return "away_gate"
    presence = (inp.presence_state or "").strip().lower()
    if presence == "abwesend":
        return "presence_away"
    return None


def presence_holds_resume(inp: Inputs) -> bool:
    """True bei `unknown`/degraded Presence: kein Auto-Start/Resume (wir wissen
    nicht, ob zuhause), ABER laufende Musik wird NICHT pausiert (kein Block)."""
    presence = (inp.presence_state or "").strip().lower()
    return presence == "unknown" or inp.presence_degraded


def is_grind(inp: Inputs) -> bool:
    """GRIND-Delta gilt nur im Gaming-Context mit subcontext == gaming_grind."""
    return inp.context == CTX_GAMING and inp.subcontext == SUB_GAME_GRIND


def is_pc_gaming(inp: Inputs) -> bool:
    """FLEET-101: PC-Gaming (device == pc im Gaming-Context). Game-Audio läuft am
    Schreibtisch/Headset, NICHT über die Raum-Speaker → HomePods-Musik spielt
    weiter, Denon bleibt aus. Bewusst am Device gebunden (nicht am headset-Enum),
    damit die Title-Classifier-Headset-Bugs (FLEET-97) das nicht aushebeln.
    Gilt für jeden Gaming-Subcontext (default/headset/grind) auf dem PC."""
    return inp.context == CTX_GAMING and inp.device == DEV_PC


def competes_with_homepods(owner: str, grind: bool, pc_gaming: bool = False) -> bool:
    """Stack, der die HomePods verdrängt. GRIND hat HomePods-Anteil (R15) →
    konkurriert NICHT, HomePods spielen weiter. PC-Gaming (FLEET-101) ebenso:
    Game-Audio ist im Headset, Raum-Musik läuft weiter. Schlaf wird unabhängig
    vom Owner am Aufrufort als HomePods-Block ergänzt (control#45/R25)."""
    if grind or pc_gaming:
        return False
    return owner in (
        AUDIO_OWNER_PRIVATE,
        AUDIO_OWNER_GAMING,
        AUDIO_OWNER_TV_DENON,
    )


def _wake_sequence_active(inp: Inputs) -> bool:
    """Wake-Fenster: die Wake-Sequenz (media_apply R23) besitzt hier den Musik-
    Start. FLEET-246: die Baseline MUSS in diesem Fenster schweigen, sonst feuern
    beide start_radio → Music-Assistant-Playback-Lock-Contention (genau der Grund,
    warum die Baseline in v0.15.0 ganz rausflog). Fenster = wake_needed ODER
    bio_state == waking."""
    return inp.wake_needed is True or (inp.bio_state or "").strip().lower() == "waking"


def music_baseline_candidate(inp: Inputs, owner: str, grind: bool) -> bool:
    """„Musik darf spielen" + Stream ist stabil weg → HomePods-Baseline starten.

    Zuhause ist Musik der Default, sobald die HomePods stabil nicht spielen
    (Dropout / Kalt-Idle / verlorene Resume-Erinnerung, z.B. nach einem Screen-
    Stack der sie pausiert hat). NUR wenn Musik überhaupt das Ziel ist:
    `owner == none` schließt TV/Gaming/Denon aus (Benni: „nicht bei TV"), weg/
    sleep/quiet/manual sind separat geblockt. Der Debounce (`homepods_stably_idle`)
    hält Restore-Flap/Track-Gap raus, `presence_holds_resume` das unknown-Fenster,
    und das Wake-Gate (`_wake_sequence_active`) überlässt den Morgen-Start allein
    der Wake-Sequenz (FLEET-246 — behebt die Weckmusik-Doppelzündung, die der
    einzige echte Grund fürs frühere Entfernen war)."""
    return (
        owner == AUDIO_OWNER_NONE
        and not grind
        and inp.homepods_configured
        and inp.homepods_stably_idle
        and not inp.bio_sleep
        and not inp.quiet_mode
        and not inp.manual_playback_active
        and not presence_holds_resume(inp)
        and not _wake_sequence_active(inp)
        and bool((inp.radio_station or "").strip())
    )


# --------------------------------------------------------------------------- #
# Audio-Action-Zustandsmaschine (Orchestrator-Lift; competes statt other_stack)
# --------------------------------------------------------------------------- #
def decide_action(
    inp: Inputs, owner: str, competes: bool, state: OrchestratorState,
    music_baseline: bool = False,
) -> tuple[str, bool, bool, str, OrchestratorState]:
    """Return (action, should_pause, resume_allowed, reason, new_state)."""
    new_state = OrchestratorState(
        auto_paused=state.auto_paused,
        pre_pause_mode=state.pre_pause_mode,
        manual_stop=state.manual_stop,
        last_homepods_playing=state.last_homepods_playing,
        last_competes=state.last_competes,
        last_planned_radio_active=state.last_planned_radio_active,
        last_manual_playback_active=state.last_manual_playback_active,
        last_wake_needed=state.last_wake_needed,
        last_hp_media_target=state.last_hp_media_target,
    )
    homepods_playing = inp.homepods_state == "playing"
    homepods_missing = not inp.homepods_configured

    block_reason = media_block_reason(inp)
    if block_reason:
        # Presence-away ist ein konkurrierender Stack wie der TV: HomePods
        # auto-pausieren, aber die Resume-Erinnerung ERHALTEN (auto_paused +
        # pre_pause_mode), damit die Heimkehr über die normale Resume-Maschinerie
        # start_radio/resume auslöst — genau EINE Flanke, kein Dauer-Level.
        # `unknown`/degraded HÄLT dagegen ohne Resume-Kandidat (wir wissen nicht,
        # ob heim/weg → nichts fabrizieren).
        is_away = block_reason in ("away_gate", "presence_away")
        if is_away and (homepods_playing or state.auto_paused):
            new_state.auto_paused = True
            new_state.manual_stop = False
            if state.pre_pause_mode is not None:
                new_state.pre_pause_mode = state.pre_pause_mode
            elif state.last_planned_radio_active or inp.planned_radio_active:
                new_state.pre_pause_mode = RESUME_MODE_RADIO
            elif state.last_manual_playback_active or inp.manual_playback_active:
                new_state.pre_pause_mode = RESUME_MODE_MANUAL
            else:
                new_state.pre_pause_mode = None
        else:
            # Nichts lief beim Weggehen, oder unknown/degraded → kein Resume.
            new_state.auto_paused = False
            new_state.pre_pause_mode = None
            new_state.manual_stop = False
        new_state.last_homepods_playing = homepods_playing
        new_state.last_competes = False
        new_state.last_planned_radio_active = inp.planned_radio_active
        new_state.last_manual_playback_active = inp.manual_playback_active
        new_state.last_wake_needed = inp.wake_needed is True
        if homepods_missing:
            return ACTION_NONE, False, False, "homepods_entity_missing", new_state
        if homepods_playing:
            return ACTION_PAUSE, True, False, f"{block_reason}_pause_homepods", new_state
        return ACTION_NONE, False, False, f"{block_reason}_blocks_media", new_state

    # ---- Persistente Übergänge (auf Basis des vorigen Ticks) ----
    if state.last_homepods_playing and not homepods_playing:
        if state.last_competes or competes:
            # Auto-Pause: ein höher-priorer Stack hat das Audio gezogen.
            new_state.auto_paused = True
            new_state.manual_stop = False
            if state.last_planned_radio_active:
                new_state.pre_pause_mode = RESUME_MODE_RADIO
            elif state.last_manual_playback_active:
                new_state.pre_pause_mode = RESUME_MODE_MANUAL
            else:
                new_state.pre_pause_mode = None
        else:
            # Kein konkurrierender Stack → playing→idle. Das ist NICHT
            # unterscheidbar zwischen User-Pause und Stream-Abriss/Dropout
            # (HA-Neustart, MA-Reconnect, Netz-Blip) — beide gehen auf `idle`.
            # Es als `manual_stop` zu werten hat die Baseline-Recovery blockiert
            # (ein Abriss konnte nie zurückkommen). Daher: hier NICHTS latchen.
            # Ein echter Stopp kommt ausschließlich explizit über den
            # media_stop_latch (User-Control) unten.
            new_state.auto_paused = False
            new_state.pre_pause_mode = None
    elif homepods_playing and not state.last_homepods_playing:
        new_state.auto_paused = False
        new_state.manual_stop = False
        new_state.pre_pause_mode = None

    # Externer media_stop_latch übersteuert die interne manual_stop-Buchführung.
    if inp.media_stop_latch is True:
        new_state.manual_stop = True
    elif inp.media_stop_latch is False and not state.last_homepods_playing:
        new_state.manual_stop = False

    # Wach-Flanke (wake_planner): das geplante Wecken räumt den Manual-Stop ab →
    # Morgen-Radio/Resume wieder frei. Nativer Ersatz der YAML-Automation
    # „clear stop latch on wake" — gewinnt auch über einen noch stehenden Latch.
    if inp.wake_needed is True and not state.last_wake_needed:
        new_state.manual_stop = False

    # ---- Action / reason ----
    reason = "idle"
    action = ACTION_NONE
    should_pause = False
    resume_allowed = False

    if homepods_missing:
        reason = "homepods_entity_missing"
    elif competes and homepods_playing:
        action = ACTION_PAUSE
        should_pause = True
        reason = "competing_stack_pause_homepods"
    elif competes:
        reason = "competing_stack_active"
    else:
        # Kein konkurrierender Stack (oder GRIND) → Resume/Radio prüfen.
        if inp.bio_sleep:
            reason = "bio_sleep_blocks_resume"
        elif new_state.manual_stop:
            reason = "manual_stop_blocks_resume"
        elif presence_holds_resume(inp):
            # unknown/degraded: nicht resumen (wissen nicht, ob zuhause) — aber
            # auch nicht pausieren; laufende Musik bleibt unberührt.
            reason = "presence_unknown_holds_resume"
        elif not new_state.auto_paused:
            # Keine Pause-Erinnerung (Kalt-Idle / verlorene Resume-Erinnerung, z.B.
            # nach einem Screen-Stack der die HomePods pausiert hat): debouncte
            # Musik-Baseline als Sicherheitsnetz. Der pre_pause_mode-Resume (away/
            # TV) hat Vorrang (else-Zweig) und bleibt sofort; hier fangen wir den
            # Fall OHNE Erinnerung ab (FLEET-246).
            if music_baseline and not homepods_playing:
                action = ACTION_START_RADIO
                resume_allowed = True
                reason = "music_baseline_start_radio"
            else:
                reason = "no_auto_pause"
        else:
            mode = new_state.pre_pause_mode
            if mode == RESUME_MODE_RADIO:
                action = ACTION_START_RADIO
                resume_allowed = True
                reason = "post_entertainment_start_radio"
            elif mode == RESUME_MODE_MANUAL:
                action = ACTION_RESUME
                resume_allowed = True
                reason = "post_entertainment_resume_music"
            else:
                reason = "no_resume_candidate"

    # ---- Buchführung für den nächsten Tick ----
    new_state.last_homepods_playing = homepods_playing
    new_state.last_competes = competes
    new_state.last_planned_radio_active = inp.planned_radio_active
    new_state.last_manual_playback_active = inp.manual_playback_active
    new_state.last_wake_needed = inp.wake_needed is True

    return action, should_pause, resume_allowed, reason, new_state


# --------------------------------------------------------------------------- #
# Volume (volume_orchestrator-Lift + GRIND-Delta)
# --------------------------------------------------------------------------- #
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _baseline(day_state: Optional[str], table: dict[str, float], fallback: float) -> float:
    """Per-Dayphase-Baseline (Lastenheft §6). Unbekannte Phase → Fallback-Base."""
    if day_state and day_state in table:
        return table[day_state]
    return fallback


def _assemble(
    base: float, *, szenario_off: float, fenster_off: float, kontext_off: float,
    manual_off: float, boost: float, active_min: float, hard_max: float,
) -> float:
    """R17-Formel: Σ der Komponenten, active_min-Boden, hard-Clamp, rund auf 3."""
    target = base + szenario_off + fenster_off + kontext_off + manual_off + boost
    target = max(target, active_min)
    return round(_clamp(target, 0.0, hard_max), 3)


def boost_active(inp: Inputs) -> bool:
    """R18: HomePods-Track-Boost (Musik-Enum 1). Geblockt in work_home/work_away,
    bei Quiet (strukturell schon im Ducked-Zweig) und bei R22 Boost-Reset."""
    if inp.boost_suppressed:
        return False
    if inp.homepods_music_enum != MUSIC_ENUM_BOOST:
        return False
    if inp.quiet_mode:
        return False
    if inp.activity_context in BOOST_BLOCK_ACTIVITIES:
        return False
    return True


def music_muted(inp: Inputs) -> bool:
    """R19: HomePods-Musik-Enum 2 → Mute (hart auf 0, übersteuert die Formel)."""
    return inp.homepods_music_enum == MUSIC_ENUM_MUTE


def decide_volume(
    inp: Inputs, owner: str, grind: bool, s: VolumeSettings,
    scenario: Optional[str] = None, music_baseline: bool = False,
) -> tuple[str, Optional[float], Optional[float], bool, str, bool, bool]:
    """R17-Volume-Modell v3.1. Return
    (policy, homepods_target, denon_target, apply_allowed, reason,
     boost_applied, muted)."""
    # 1. Blocked: keine adressierbaren Speaker.
    if not inp.homepods_configured and not inp.denon_configured:
        return VOL_POLICY_BLOCKED, None, None, False, "no_speakers_configured", False, False

    # ---- Routing: welche Seite spielt? PC-Gaming (FLEET-101) → HomePods normal,
    # Denon aus (Game-Audio im Headset). GRIND → beide (HomePods dominant). ----
    pc_gaming = is_pc_gaming(inp)
    if music_baseline:
        # Baseline startet HomePods-Radio → hörbares Ziel schon bevor der Player
        # `playing` meldet (sonst erster Tick auf 0, dann Ramp).
        hp_plays, dn_plays = True, False
    elif pc_gaming:
        hp_plays, dn_plays = True, False
    elif grind:
        hp_plays, dn_plays = True, True
    elif owner == AUDIO_OWNER_HOMEPODS:
        hp_plays, dn_plays = True, False
    elif owner in (AUDIO_OWNER_TV_DENON, AUDIO_OWNER_GAMING, AUDIO_OWNER_PRIVATE):
        hp_plays, dn_plays = False, True
    else:  # NONE
        hp_plays, dn_plays = False, False

    # R25/control#45: Schlaf schaltet nur das HomePods-Bein stumm. Denon läuft
    # unverändert durch Tagesphase, Matrix und Caps.
    if inp.bio_sleep:
        hp_plays = False

    # control#3: Fenster-Offset je Gerät (gekippt == offen; geschlossen = 0).
    # opening_any_open kommt aus dem dreistufigen core_devices-Master (any_not_
    # closed = gekippt ODER offen) — kein neuer binärer Sensor.
    win_hp = s.opening_offset_homepods if inp.opening_any_open else 0.0
    win_dn = s.opening_offset_denon if inp.opening_any_open else 0.0

    # 3. Ducked: quiet mode (R20 — hart auf ducked_target für aktive Seite[n]).
    if inp.quiet_mode:
        hp = dn = None
        if inp.homepods_configured:
            hp = round(_clamp(s.ducked_target, 0.0, s.homepods_max), 3) if hp_plays else 0.0
        if inp.denon_configured:
            dn = round(_clamp(s.ducked_target, 0.0, s.denon_max), 3) if dn_plays else 0.0
        return VOL_POLICY_DUCKED, hp, dn, True, "quiet_mode_ducked", False, False

    # 4. Idle: kein Owner und kein Grind (aber NICHT wenn die Baseline gerade
    # startet — die soll hörbar, nicht auf 0).
    if owner == AUDIO_OWNER_NONE and not grind and not music_baseline:
        hp = 0.0 if inp.homepods_configured else None
        dn = 0.0 if inp.denon_configured else None
        return VOL_POLICY_IDLE, hp, dn, True, "idle_no_owner", False, False

    # 5. Media: R17-Formel mit Dayphase-Baselines + Boost/Mute.
    boost_flag = hp_plays and boost_active(inp)
    muted_flag = hp_plays and music_muted(inp)

    hp = dn = None
    if inp.homepods_configured:
        if not hp_plays:
            hp = 0.0
        elif muted_flag:
            hp = 0.0   # R19: Mute übersteuert die berechnete Ziel-Lautstärke.
        else:
            hp = _assemble(
                _baseline(inp.day_state, s.base_homepods, s.homepods_base),
                # GRIND: eigener HomePods-Grind-Offset (Default 0 → normal).
                szenario_off=(s.grind_homepods_offset if grind else 0.0)
                + s.scenario_off_homepods.get(scenario, 0.0),
                fenster_off=win_hp,
                kontext_off=s.activity_off_homepods.get(inp.activity_context, 0.0),
                manual_off=inp.manual_nudge,
                boost=s.boost_offset if boost_flag else 0.0,
                active_min=s.active_min, hard_max=s.homepods_max,
            )
    if inp.denon_configured:
        if not dn_plays:
            dn = 0.0
        else:
            # GRIND: Denon als Hintergrund-Kulisse via szenario_offset (R16/§6).
            dn = _assemble(
                _baseline(inp.day_state, s.base_denon, s.denon_base),
                szenario_off=(s.grind_denon_offset if grind else 0.0)
                + s.scenario_off_denon.get(scenario, 0.0),
                fenster_off=win_dn,
                kontext_off=s.activity_off_denon.get(inp.activity_context, 0.0),
                manual_off=inp.manual_nudge,
                boost=0.0,
                active_min=s.active_min, hard_max=s.denon_max,
            )
            # control#3: Private-Time-Denon-Cap. Ceiling NACH der additiven Formel
            # (inkl. Nudge) → ein positiver Nudge kann den Cap nicht überfahren.
            # min() hebt einen bereits niedrigeren Wert nie an.
            if owner == AUDIO_OWNER_PRIVATE:
                dn = round(min(dn, _clamp(s.private_denon_cap, 0.0, s.denon_max)), 3)
    if music_baseline:
        reason = "music_baseline_homepods"
    elif pc_gaming:
        reason = "pc_gaming_homepods_denon_off"
    elif grind:
        reason = "grind_homepods_denon_kulisse"
    else:
        reason = f"owner_{owner}"
    return VOL_POLICY_MEDIA, hp, dn, True, reason, boost_flag, muted_flag


# --------------------------------------------------------------------------- #
# Observability: Volume-Formel-Breakdown + strukturierte Reasons (für das Cockpit;
# ändern KEINE Entscheidung). volume_breakdown spiegelt den MEDIA-Zweig von
# decide_volume über dieselben Helfer (_baseline/_assemble/boost_active) → kein Drift.
# --------------------------------------------------------------------------- #
def volume_breakdown(
    inp: Inputs, owner: str, grind: bool, s: VolumeSettings,
    scenario: Optional[str] = None, music_baseline: bool = False,
) -> dict[str, Any]:
    """R17-Komponenten je Gerät (base · scenario · window · activity · nudge ·
    boost → result). Nur im MEDIA-Zweig voll aussagekräftig; sonst result=0/—."""
    if music_baseline or is_pc_gaming(inp):
        hp_plays, dn_plays = True, False
    elif grind:
        hp_plays, dn_plays = True, True
    elif owner == AUDIO_OWNER_HOMEPODS:
        hp_plays, dn_plays = True, False
    elif owner in (AUDIO_OWNER_TV_DENON, AUDIO_OWNER_GAMING, AUDIO_OWNER_PRIVATE):
        hp_plays, dn_plays = False, True
    else:
        hp_plays, dn_plays = False, False
    if inp.bio_sleep:
        hp_plays = False
    boost_flag = hp_plays and boost_active(inp)
    is_private = owner == AUDIO_OWNER_PRIVATE

    nudge = inp.manual_nudge
    def comp(
        base: float, szen: float, act: float, plays: bool, hard_max: float,
        boost: float, window: float, cap: Optional[float] = None,
    ) -> dict[str, Any]:
        win = window if plays else 0.0
        man = nudge if plays else 0.0
        kontext = act if plays else 0.0
        result = _assemble(
            base, szenario_off=szen, fenster_off=win, kontext_off=kontext,
            manual_off=man, boost=boost, active_min=s.active_min, hard_max=hard_max,
        ) if plays else 0.0
        out = {
            "base": round(base, 3), "scenario_offset": round(szen, 3),
            "window_offset": round(win, 3), "activity_offset": round(kontext, 3),
            "manual_nudge": round(man, 3), "track_boost": round(boost, 3),
            "result": result, "plays": plays,
        }
        # control#3: Private-Time-Cap sichtbar machen (normal / cap / effektiv).
        if cap is not None and plays:
            capped = round(min(result, _clamp(cap, 0.0, hard_max)), 3)
            out["cap"] = round(cap, 3)
            out["cap_active"] = capped < result
            out["effective"] = capped
        return out

    return {
        "homepods": comp(
            _baseline(inp.day_state, s.base_homepods, s.homepods_base),
            (s.grind_homepods_offset if grind else 0.0)
            + s.scenario_off_homepods.get(scenario, 0.0),
            s.activity_off_homepods.get(inp.activity_context, 0.0),
            hp_plays, s.homepods_max, s.boost_offset if boost_flag else 0.0,
            s.opening_offset_homepods,
        ),
        "denon": comp(
            _baseline(inp.day_state, s.base_denon, s.denon_base),
            (s.grind_denon_offset if grind else 0.0)
            + s.scenario_off_denon.get(scenario, 0.0),
            s.activity_off_denon.get(inp.activity_context, 0.0),
            dn_plays, s.denon_max, 0.0, s.opening_offset_denon,
            s.private_denon_cap if is_private else None,
        ),
    }


# --------------------------------------------------------------------------- #
# FLEET-102 Stage B — Volume-Matrix-Override-Merge (HA-frei, testbar)
# --------------------------------------------------------------------------- #
MATRIX_DIMS: tuple[str, ...] = ("base", "scenario_off", "activity_off")
MATRIX_DEVICES: tuple[str, ...] = ("homepods", "denon")


def sanitize_scalar_patch(
    patch: dict[str, Any], ranges: dict[str, tuple[float, float]]
) -> dict[str, Any]:
    """control#3: Panel-Skalar-Patch säubern (pure). Nur Keys aus ``ranges``
    werden übernommen, jeweils auf ihr [lo, hi] geclamped und auf 3 gerundet.
    Unbekannte/ungültige Werte werden still verworfen (fail-safe). Das Ergebnis
    schreibt der Coordinator in die ConfigEntry-Options (gleiche Quelle wie der
    Options-Flow)."""
    out: dict[str, Any] = {}
    if not isinstance(patch, dict):
        return out
    for key, (lo, hi) in ranges.items():
        if key not in patch:
            continue
        try:
            out[key] = round(max(lo, min(hi, float(patch[key]))), 3)
        except (TypeError, ValueError):
            continue
    return out


def apply_matrix_patch(
    override: dict[str, Any], patch: dict[str, Any]
) -> dict[str, Any]:
    """Partiellen Patch in den persistenten Matrix-Override mergen (pure).

    - Dimensionen: base/scenario_off/activity_off × {homepods,denon}.
    - Werte: float, geclamped (base ∈ [0,1], Offsets ∈ [-1,1]), rund auf 3.
    - ``None`` als Zellwert löscht den Override dieser Zelle (zurück auf Default).
    - Leere Geräte-/Dimensions-Maps werden entfernt (kein Müll im Store).
    - Unbekannte/ungültige Einträge werden still ignoriert (fail-safe).
    Gibt einen NEUEN Override zurück (Eingabe unverändert)."""
    ov: dict[str, Any] = {
        dim: {dev: dict(cells) for dev, cells in (override.get(dim) or {}).items()}
        for dim in MATRIX_DIMS
        if override.get(dim)
    }
    for dim in MATRIX_DIMS:
        pd = patch.get(dim)
        if not isinstance(pd, dict):
            continue
        lo, hi = (0.0, 1.0) if dim == "base" else (-1.0, 1.0)
        dim_ov = ov.get(dim, {})
        for device in MATRIX_DEVICES:
            cells = pd.get(device)
            if not isinstance(cells, dict):
                continue
            dev_ov = dict(dim_ov.get(device) or {})
            for key, val in cells.items():
                if val is None:
                    dev_ov.pop(str(key), None)
                    continue
                try:
                    dev_ov[str(key)] = round(max(lo, min(hi, float(val))), 3)
                except (TypeError, ValueError):
                    continue
            if dev_ov:
                dim_ov[device] = dev_ov
            else:
                dim_ov.pop(device, None)
        if dim_ov:
            ov[dim] = dim_ov
        else:
            ov.pop(dim, None)
    return ov


_REASON_BLOCKED_KW = (
    "block", "mute", "no_", "stop", "sleep", "window", "dayphase",
    "before_0900", "competing", "missing",
)
_REASON_WARN_KW = ("ducked", "quiet", "grind", "headset", "private")


def reason_severity(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in _REASON_BLOCKED_KW):
        return "blocked"
    if any(k in t for k in _REASON_WARN_KW):
        return "warn"
    return "ok"


def structured_reasons(dbg: dict[str, Any]) -> list[dict[str, Any]]:
    """Why-Stack: aktive Reasons → [{id, severity, text}] für das Cockpit."""
    out: list[dict[str, Any]] = []
    for r in dbg.get("active_reasons", []) or []:
        out.append({"id": str(r), "severity": reason_severity(str(r)), "text": str(r)})
    sub = dbg.get("subwoofer_block_reason")
    if sub:
        out.append({"id": f"subwoofer_{sub}", "severity": "blocked", "text": f"subwoofer_blocked:{sub}"})
    return out


# --------------------------------------------------------------------------- #
# Subwoofer (evaluate_subwoofer-Lift + R16 GRIND-Override)
# --------------------------------------------------------------------------- #
def denon_audio_path(inp: Inputs) -> bool:
    """Audio läuft gerade über den Denon (Plug-Active ODER primäres Gerät Denon
    ODER Denon-Player-source nicht leer)."""
    if inp.denon_active:
        return True
    if inp.device == DEV_DENON:
        return True
    src = (inp.denon_source or "").strip().lower()
    return bool(src and src not in ("", "off", "standby"))


def subwoofer_dayphase_ok(inp: Inputs) -> bool:
    """Dayphase-Fenster v3.1 (§6): erlaubte Phase UND frühestens 09:00 lokal.
    Der 09:00-Floor greift real nur in late_morning (solar/saisonal → kann im
    Sommer vor 09:00 beginnen); bei unbekannter Lokalzeit reicht die Phase."""
    if inp.day_state not in SUB_ALLOWED_PHASES:
        return False
    if inp.local_minute_of_day is not None and inp.local_minute_of_day < SUB_EARLIEST_MINUTE:
        return False
    return True


def evaluate_subwoofer(inp: Inputs, grind: bool, denon_path: bool) -> tuple[bool, Optional[str]]:
    # R16: bei gaming_grind IMMER aus (übersteuert die Sub-Policy).
    if grind:
        return False, "grind_r16"
    if inp.quiet_mode:
        return False, "quiet_mode"
    if not inp.entertainment_active:
        return False, "no_entertainment"
    if inp.headset_active:
        return False, "headset_active"
    if inp.opening_any_open and not denon_path:
        return False, "window_open_no_denon_path"
    # Dayphase-Fenster v3.1 (§6): nur late_morning..late_evening, frühestens 09:00.
    if not subwoofer_dayphase_ok(inp):
        if inp.day_state not in SUB_ALLOWED_PHASES:
            return False, "dayphase_window"
        return False, "before_0900"
    return True, None


# --------------------------------------------------------------------------- #
# Master-Entscheidung
# --------------------------------------------------------------------------- #
def decide(
    inp: Inputs, state: OrchestratorState, settings: Optional[VolumeSettings] = None
) -> tuple[PolicyDecision, OrchestratorState]:
    """Berechnet die Policy-Entscheidung + den nächsten persistenten Zustand."""
    if settings is None:
        settings = VolumeSettings()
    d = PolicyDecision()
    reasons: list[str] = []

    grind = is_grind(inp)
    d.is_grind = grind

    pc_gaming = is_pc_gaming(inp)
    d.is_pc_gaming = pc_gaming

    block_reason = media_block_reason(inp)
    if block_reason:
        d.audio_owner = AUDIO_OWNER_NONE
        d.owner_reason = block_reason
        d.audio_scenario = AUDIO_SCENARIO_OFF
        d.audio_scenario_label = AUDIO_SCENARIO_LABELS[AUDIO_SCENARIO_OFF]
        d.audio_scenario_detail = None
        reasons.append(f"owner:{block_reason}")
        reasons.append("scenario:off")

        action, should_pause, resume_allowed, action_reason, new_state = decide_action(
            inp, AUDIO_OWNER_NONE, False, state
        )
        d.action = action
        d.homepods_should_pause = should_pause
        d.homepods_resume_allowed = resume_allowed
        d.action_reason = action_reason
        d.manual_stop = new_state.manual_stop
        reasons.append(f"action:{action_reason}")

        d.volume_policy = VOL_POLICY_IDLE
        d.volume_target_homepods = 0.0 if inp.homepods_configured else None
        d.volume_target_denon = 0.0 if inp.denon_configured else None
        d.volume_apply_allowed = False
        d.volume_reason = block_reason
        reasons.append(f"volume:{block_reason}")
        d.subwoofer_allowed = False
        d.subwoofer_block_reason = block_reason
        reasons.append(f"sub_off:{block_reason}")
        d.active_reasons = reasons
        return d, new_state

    owner, owner_label = decide_owner(inp)
    d.audio_owner = owner
    d.owner_reason = owner_label
    reasons.append(f"owner:{owner_label}")

    scenario, scenario_label, scenario_detail = decide_audio_scenario(inp, owner, grind)
    d.audio_scenario = scenario
    d.audio_scenario_label = scenario_label
    d.audio_scenario_detail = scenario_detail
    reasons.append(f"scenario:{scenario}")

    baseline_candidate = music_baseline_candidate(inp, owner, grind)
    competes = competes_with_homepods(owner, grind, pc_gaming) or inp.bio_sleep
    action, should_pause, resume_allowed, action_reason, new_state = decide_action(
        inp, owner, competes, state, baseline_candidate
    )
    d.action = action
    d.homepods_should_pause = should_pause
    d.homepods_resume_allowed = resume_allowed
    d.action_reason = action_reason
    d.manual_stop = new_state.manual_stop
    reasons.append(f"action:{action_reason}")

    # Baseline gilt nur, wenn sie auch als Action durchkam (nicht durch
    # manual_stop/auto_pause geblockt) → steuert den hörbaren Volume-Zweig.
    baseline_active = baseline_candidate and action == ACTION_START_RADIO
    d.music_baseline_active = baseline_active

    # Post-Entertainment-Resume: Resume-Soll ist „HomePods spielen" — ohne hörbares
    # Ziel kollabiert owner=none auf idle_no_owner/0.0 und Apply verwirft den Start.
    resume_start = (
        action == ACTION_RESUME
        and resume_allowed
        and owner == AUDIO_OWNER_NONE
        and not grind
        and not inp.quiet_mode
        and not inp.bio_sleep
    )
    hp_start_active = baseline_active or resume_start

    policy, hp, dn, apply_allowed, vol_reason, boost_applied, muted = decide_volume(
        inp, owner, grind, settings, scenario, hp_start_active
    )
    if resume_start and vol_reason == "music_baseline_homepods":
        vol_reason = "resume_homepods_start"
    # ---- FLEET-153: HomePods-Pfad sticky über Idle-Gaps ----
    # Ein transienter owner=none (Playback-Lücke, Track-/Sender-Wechsel) darf das
    # HomePods-Target NICHT auf 0.0 kollabieren — sonst rampt der Apply-Layer ohne
    # Nudge-Änderung runter und wieder hoch (Fade-Down/Ramp-Up, FLEET-153). Wir
    # halten das letzte MEDIA-Target, solange HomePods der zuletzt spielende Pfad
    # war; echter Pfadwechsel (→Denon/TV/Gaming) löscht den Stick. Quiet/Ducking
    # bleibt eigener Zweig (hart 0.10, unberührt). Idle ist Geräte-Sache
    # (pause/resume via action), nicht Volume → kein Ramp-Down (OQ-1).
    hp_on_path = hp_start_active or pc_gaming or grind or owner == AUDIO_OWNER_HOMEPODS
    if (
        policy == VOL_POLICY_MEDIA
        and hp_on_path
        and hp is not None
        and not inp.bio_sleep
    ):
        new_state.last_hp_media_target = hp
    elif policy == VOL_POLICY_MEDIA and not hp_on_path:
        new_state.last_hp_media_target = None
    if (
        policy == VOL_POLICY_IDLE
        and inp.homepods_configured
        and new_state.last_hp_media_target is not None
        and not inp.bio_sleep
    ):
        hp = new_state.last_hp_media_target
        vol_reason = "idle_sticky_homepods"
    d.volume_policy = policy
    d.volume_target_homepods = hp
    d.volume_target_denon = dn
    d.volume_apply_allowed = apply_allowed
    d.volume_reason = vol_reason
    d.track_boost_applied = boost_applied
    d.music_muted = muted
    reasons.append(f"volume:{vol_reason}")
    if boost_applied:
        reasons.append("boost:track_boost_r18")
    if muted:
        reasons.append("mute:music_enum_r19")

    d.denon_audio_path = denon_audio_path(inp)
    allowed, block = evaluate_subwoofer(inp, grind, d.denon_audio_path)
    d.subwoofer_allowed = allowed
    d.subwoofer_block_reason = block
    if block:
        reasons.append(f"sub_off:{block}")

    d.active_reasons = reasons
    return d, new_state
