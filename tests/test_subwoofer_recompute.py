"""Exercise the real coordinator callbacks with an in-memory HA boundary."""

import asyncio
import importlib.util
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import bmp_const as C
import pytest


@pytest.fixture
def runtime(monkeypatch):
    # Only HA's event bus, clock, storage and coordinator publication are doubles.
    # Input reads, watched bindings, callbacks and policy computation are real.
    clock = [datetime.fromisoformat("2026-10-24T19:00:00+02:00")]
    subscriptions = {}

    class CoordinatorBase:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, hass, _logger, **_kwargs):
            self.hass = hass
            self.data = None

        def async_set_updated_data(self, data):
            self.data = data

    def watch(_hass, entity_ids, callback):
        subscriptions["state"] = (set(entity_ids), callback)
        return lambda: None

    def watch_time(_hass, callback, **when):
        subscriptions["time"] = (when, callback)
        return lambda: None

    def module(name, **values):
        mod = ModuleType(name)
        mod.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, mod)

    module("homeassistant")
    module("homeassistant.config_entries", ConfigEntry=object)
    module(
        "homeassistant.core",
        CALLBACK_TYPE=object,
        Event=object,
        HomeAssistant=object,
        callback=lambda fn: fn,
    )
    module("homeassistant.helpers")
    module(
        "homeassistant.helpers.event",
        async_track_state_change_event=watch,
        async_track_time_change=watch_time,
        async_call_later=lambda *_args: lambda: None,
    )
    module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=CoordinatorBase)
    module("homeassistant.util", dt=SimpleNamespace(now=lambda: clock[0]))

    class MatrixStore:
        data = None
        fail_save = False

        async def async_load(self):
            return deepcopy(self.data)

        async def async_save(self, data):
            if self.fail_save:
                raise OSError("simulated write failure")
            self.data = deepcopy(data)

    store = MatrixStore()
    module("bmp_pure_pkg.storage", make_matrix_store=lambda *_args: store)

    path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "benni_media_policy"
        / "coordinator.py"
    )
    spec = importlib.util.spec_from_file_location("bmp_pure_pkg.coordinator", path)
    coordinator = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, coordinator)
    spec.loader.exec_module(coordinator)

    bindings = {key: f"sensor.test_{key}" for key in C.WATCH_KEYS}
    states = {}

    def set_state(key, value):
        states[bindings[key]] = SimpleNamespace(state=value, attributes={})

    for key, value in (
        (C.CONF_MEDIA_CONTEXT, "streaming"),
        (C.CONF_MEDIA_SUBCONTEXT, "streaming_default"),
        (C.CONF_MEDIA_DEVICE, "appletv"),
        (C.CONF_DAY_STATE, "evening"),
        (C.CONF_ENTERTAINMENT_ACTIVE, "on"),
        (C.CONF_DENON, "on"),
        (C.CONF_DENON_ACTIVE, "on"),
        (C.CONF_HOMEPODS, "playing"),
        (C.CONF_PRESENCE_STATE, "zuhause"),
    ):
        set_state(key, value)

    hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
    entry = SimpleNamespace(
        data=bindings, options={}, entry_id="test", async_on_unload=lambda _callback: None
    )

    def new_coordinator():
        return coordinator.MediaPolicyCoordinator(hass, entry)

    coord = new_coordinator()
    coord.async_start()

    def change(key, value):
        set_state(key, value)
        watched, callback = subscriptions["state"]
        assert bindings[key] in watched, key
        callback(SimpleNamespace(data={"entity_id": bindings[key]}))

    return SimpleNamespace(
        coord=coord,
        change=change,
        clock=clock,
        subscriptions=subscriptions,
        new=new_coordinator,
        store=store,
        entry=entry,
    )


def test_phase_matrix_round_trip_and_event_recompute(runtime):
    legacy = {
        "base": {
            "homepods": {"late_morning": 0.31, "forenoon": 0.37, "early_evening": 0.42},
            "denon": {"late_morning": 0.23, "early_evening": 0.34, "evening": 0.38},
        },
        "scenario_off": {"homepods": {"music": 0.03}},
        "activity_off": {"denon": {"household": -0.06}},
    }
    runtime.store.data = deepcopy(legacy)
    runtime.entry.options = {C.CONF_VOL_HOMEPODS_MAX: 0.66, C.CONF_VOL_BOOST_OFFSET: 0.12}
    coord = runtime.coord
    asyncio.run(coord.async_load_matrix())
    matrix = coord.matrix()
    assert matrix["override"] == legacy
    assert matrix["base"]["homepods"]["forenoon"] == 0.37
    assert matrix["base"]["homepods"]["late_afternoon"] == 0.42
    assert matrix["base"]["homepods"]["evening"] == 0.42
    assert matrix["base"]["denon"]["forenoon"] == 0.23
    assert matrix["base"]["denon"]["evening"] == 0.38
    assert matrix["phase_max"] == {"homepods": {}, "denon": {}}
    assert runtime.store.data == legacy

    patch = {
        "phase_max": {
            "homepods": {"evening": 0.49, "early_night": 0.2},
            "denon": {"evening": 0.29},
        },
        "boost_allowed": {"homepods": {"late_afternoon": False}},
    }
    saved = asyncio.run(coord.async_set_matrix(patch))
    assert saved["override"] == legacy | patch
    restarted = runtime.new()
    asyncio.run(restarted.async_load_matrix())
    assert restarted.matrix() == saved
    assert restarted.settings().boost_offset == 0.12
    assert restarted.settings().homepods_max == 0.66
    assert restarted.settings().activity_off_denon["household"] == -0.06

    runtime.change(C.CONF_MEDIA_CONTEXT, "idle")
    runtime.change(C.CONF_HOMEPODS_MUSIC_ENUM, "1")
    for phase, target, cap, boost in (
        ("late_afternoon", 0.45, 0.66, False),
        ("evening", 0.49, 0.49, True),
        ("early_night", 0.20, 0.20, False),
        ("unavailable", 0.38, 0.66, False),
        ("midday", 0.60, 0.66, True),
    ):
        runtime.change(C.CONF_DAY_STATE, phase)
        assert coord.data["volume_target_homepods"] == target
        assert coord.debug()["track_boost_applied"] is boost
        assert coord.status()["volume_formula"]["homepods"]["effective_max"] == cap

    cleared = asyncio.run(
        restarted.async_set_matrix(
            {
                "phase_max": {"homepods": {"evening": None}},
                "boost_allowed": {"homepods": {"late_afternoon": None}},
            }
        )
    )
    assert "evening" not in cleared["phase_max"]["homepods"]
    assert cleared["boost_allowed"]["homepods"]["late_afternoon"] is True
    assert cleared["override"]["base"] == legacy["base"]
    assert cleared["phase_max"]["denon"]["evening"] == 0.29


def test_failed_matrix_save_leaves_previous_settings_active(runtime):
    coord = runtime.coord
    previous = coord.matrix()
    runtime.store.fail_save = True
    with pytest.raises(OSError, match="simulated write failure"):
        asyncio.run(coord.async_set_matrix({"phase_max": {"homepods": {"evening": 0.3}}}))
    assert coord.matrix() == previous


def assert_subwoofer(coord, allowed, reason=None):
    assert coord.data["subwoofer_allowed"] is allowed
    assert coord.debug()["subwoofer_block_reason"] == reason


def test_subwoofer_bound_dayphase_events_recompute(runtime):
    for phase in ("afternoon", "late_afternoon", "evening", "late_evening"):
        runtime.change(C.CONF_DAY_STATE, phase)
        assert_subwoofer(runtime.coord, True)
    runtime.change(C.CONF_DAY_STATE, "early_night")
    assert_subwoofer(runtime.coord, False, "dayphase_window")
    runtime.change(C.CONF_DAY_STATE, "midday")
    assert_subwoofer(runtime.coord, True)


def test_subwoofer_grind_exit_and_media_events_recompute(runtime):
    runtime.change(C.CONF_MEDIA_CONTEXT, "gaming")
    runtime.change(C.CONF_MEDIA_SUBCONTEXT, C.SUB_GAME_GRIND)
    assert_subwoofer(runtime.coord, False, "grind_r16")

    # Context changes first: a still-old Grind subcontext must not latch a block.
    runtime.change(C.CONF_MEDIA_CONTEXT, "streaming")
    assert_subwoofer(runtime.coord, True)
    runtime.change(C.CONF_MEDIA_SUBCONTEXT, "streaming_default")
    assert_subwoofer(runtime.coord, True)

    # Subcontext changes first on the second exit: both source event orders work.
    runtime.change(C.CONF_MEDIA_CONTEXT, "gaming")
    runtime.change(C.CONF_MEDIA_SUBCONTEXT, C.SUB_GAME_GRIND)
    assert_subwoofer(runtime.coord, False, "grind_r16")
    runtime.change(C.CONF_MEDIA_SUBCONTEXT, "gaming_default")
    assert_subwoofer(runtime.coord, True)
    runtime.change(C.CONF_MEDIA_CONTEXT, "streaming")
    assert_subwoofer(runtime.coord, True)

    runtime.change(C.CONF_MEDIA_CONTEXT, "idle")
    runtime.change(C.CONF_ENTERTAINMENT_ACTIVE, "off")
    assert runtime.coord.data["audio_owner"] == C.AUDIO_OWNER_HOMEPODS
    assert_subwoofer(runtime.coord, False, "no_entertainment")
    runtime.change(C.CONF_MEDIA_CONTEXT, "streaming")
    runtime.change(C.CONF_ENTERTAINMENT_ACTIVE, "on")
    assert runtime.coord.data["audio_owner"] == C.AUDIO_OWNER_TV_DENON
    assert_subwoofer(runtime.coord, True)


def test_subwoofer_0900_callback_recomputes_without_phase_event(runtime):
    runtime.clock[0] = datetime.fromisoformat("2026-10-24T08:59:00+02:00")
    runtime.change(C.CONF_DAY_STATE, "forenoon")
    assert_subwoofer(runtime.coord, False, "before_0900")
    when, callback = runtime.subscriptions["time"]
    assert when == {"hour": 9, "minute": 0, "second": 0}
    runtime.clock[0] = datetime.fromisoformat("2026-10-24T09:00:00+02:00")
    callback(runtime.clock[0])
    assert_subwoofer(runtime.coord, True)


def test_subwoofer_fresh_coordinator_reads_current_and_later_sources(runtime):
    runtime.change(C.CONF_MEDIA_CONTEXT, "gaming")
    runtime.change(C.CONF_MEDIA_SUBCONTEXT, C.SUB_GAME_GRIND)
    assert_subwoofer(runtime.coord, False, "grind_r16")
    runtime.change(C.CONF_MEDIA_CONTEXT, "streaming")

    fresh = runtime.new()
    initial = asyncio.run(fresh._async_update_data())
    assert initial["subwoofer_allowed"] is True
    assert fresh.debug()["is_grind"] is False

    runtime.change(C.CONF_DAY_STATE, "unavailable")
    fresh = runtime.new()
    initial = asyncio.run(fresh._async_update_data())
    assert initial["subwoofer_allowed"] is False
    assert fresh.debug()["subwoofer_block_reason"] == "dayphase_window"
    fresh.async_start()
    runtime.change(C.CONF_DAY_STATE, "evening")
    assert_subwoofer(fresh, True)
