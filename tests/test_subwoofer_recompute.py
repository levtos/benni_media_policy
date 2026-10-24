"""Exercise the real coordinator callbacks with an in-memory HA boundary."""

import asyncio
import importlib.util
import sys
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
    module("bmp_pure_pkg.storage", make_matrix_store=lambda *_args: object())

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
        coord=coord, change=change, clock=clock, subscriptions=subscriptions, new=new_coordinator
    )


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
