"""Action (service) tests."""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.printlog.const import DOMAIN
from custom_components.printlog.services import async_register_services

from .test_coordinator import (
    BASE,
    NOW,
    UID,
    VERSION_URL,
    FakeApi,
    api,  # noqa: F401  (fixture)
    make_entry,
    setup_entry,
)
from .test_sensor import entity_id

SPOOL_1 = "11111111-1111-4111-8111-111111111111"
SPOOL_2 = "22222222-2222-4222-8222-222222222222"
SPOOL_3 = "33333333-3333-4333-8333-333333333333"
SPOOL_UNTRACKED = "55555555-5555-4555-8555-555555555555"
ADJ_URL = f"{BASE}/api/Filaments/{SPOOL_1}/adjustments"
ACTIVE_URL = f"{BASE}/api/Filaments/{SPOOL_1}/active"
MAINT_URL = f"{BASE}/api/PrinterMaintenance"


@pytest.fixture(autouse=True)
def _frozen_now(freezer):
    freezer.move_to(NOW)


async def call(hass: HomeAssistant, service: str, data: dict, *, response: bool = True):
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=response
    )


def bodies(api: FakeApi, method: str, url: str) -> list[dict]:  # noqa: F811
    return [c[2] for c in api.mock.mock_calls if c[0] == method and str(c[1]) == url]


def printer_device_id(hass: HomeAssistant, printer_id: int) -> str:
    entry_id = hass.config_entries.async_entries(DOMAIN)[0].entry_id
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{UID}:printer:{printer_id}"), entry_id
    )
    assert device is not None
    return device.id


async def test_adjust_by_slot_entity(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    api.mock.post(ADJ_URL, json={"beforeGrams": 742.5, "afterGrams": 730.0})
    await setup_entry(hass)
    version_calls = len(api.calls(VERSION_URL))
    ent = entity_id(hass, "sensor", "printer:101:slot:1:remaining")
    result = await call(hass, "adjust_remaining", {"entity_id": ent, "amount": -12.5})
    assert result == {"before_grams": 742.5, "after_grams": 730.0}
    assert bodies(api, "POST", ADJ_URL) == [{"source": "weight", "delta": -12.5, "notes": None}]
    await hass.async_block_till_done()
    assert len(api.calls(VERSION_URL)) > version_calls


async def test_adjust_by_filament_entity_and_notes(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    api.mock.post(ADJ_URL, json={"beforeGrams": 1.0, "afterGrams": 2.0})
    await setup_entry(hass)
    ent = entity_id(hass, "sensor", "printer:101:slot:1:filament")
    await call(hass, "adjust_remaining", {"entity_id": ent, "amount": 1, "notes": "reload"})
    assert bodies(api, "POST", ADJ_URL)[0]["notes"] == "reload"


async def test_adjust_by_spool_number(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    api.mock.post(ADJ_URL, json={"beforeGrams": 1.0, "afterGrams": 2.0})
    await setup_entry(hass)
    await call(hass, "adjust_remaining", {"spool_number": 1, "amount": 5})
    assert bodies(api, "POST", ADJ_URL)[0]["delta"] == 5


async def test_adjust_by_spool_id(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    api.mock.post(ADJ_URL, json={"beforeGrams": 1.0, "afterGrams": 2.0})
    await setup_entry(hass)
    await call(hass, "adjust_remaining", {"spool_id": SPOOL_1, "amount": 5}, response=False)
    assert len(bodies(api, "POST", ADJ_URL)) == 1


@pytest.mark.parametrize(
    "extra",
    [{}, {"spool_id": SPOOL_1, "spool_number": 1}],
)
async def test_adjust_requires_exactly_one_target(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    extra: dict,
) -> None:
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "adjust_remaining", {"amount": 5, **extra})
    assert err.value.translation_key == "spool_choice"


@pytest.mark.parametrize("amount", [0, 0.0, float("nan")])
async def test_adjust_zero_amount_rejected(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    amount: float,
) -> None:
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "adjust_remaining", {"spool_number": 1, "amount": amount})
    assert err.value.translation_key == "amount_invalid"


@pytest.mark.parametrize(("unit", "source"), [("g", "weight"), ("mm", "length"), ("ml", "volume")])
async def test_adjust_unit_mapping(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    unit: str,
    source: str,
) -> None:
    api.mock.post(ADJ_URL, json={"beforeGrams": 1.0, "afterGrams": 2.0})
    await setup_entry(hass)
    await call(hass, "adjust_remaining", {"spool_number": 1, "amount": 2, "unit": unit})
    assert bodies(api, "POST", ADJ_URL)[0]["source"] == source


async def test_adjust_unknown_unit_rejected(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    with pytest.raises(vol.Invalid):
        await call(hass, "adjust_remaining", {"spool_number": 1, "amount": 2, "unit": "kg"})


async def test_adjust_empty_slot_error(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    ent = entity_id(hass, "sensor", "printer:101:slot:2:remaining")
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "adjust_remaining", {"entity_id": ent, "amount": 5})
    assert err.value.translation_key == "slot_empty"


async def test_adjust_other_entity_rejected(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    ent = entity_id(hass, "sensor", "printer:101:print_time")
    for bad in (ent, "sensor.nonexistent"):
        with pytest.raises(ServiceValidationError) as err:
            await call(hass, "adjust_remaining", {"entity_id": bad, "amount": 5})
        assert err.value.translation_key == "invalid_slot_entity"


async def test_adjust_unknown_spool_number(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "adjust_remaining", {"spool_number": 99, "amount": 5})
    assert err.value.translation_key == "spool_not_found"


async def test_adjust_unknown_spool_404(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    other = "99999999-9999-4999-8999-999999999999"
    api.mock.post(
        f"{BASE}/api/Filaments/{other}/adjustments",
        status=404,
        json={"code": "material_not_found"},
    )
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "adjust_remaining", {"spool_id": other, "amount": 5})
    assert err.value.translation_key == "spool_not_found"


async def test_adjust_untracked_rejected_locally(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "adjust_remaining", {"spool_id": SPOOL_UNTRACKED, "amount": 5})
    assert err.value.translation_key == "spool_untracked"
    with pytest.raises(ServiceValidationError):
        await call(hass, "adjust_remaining", {"spool_number": 5, "amount": 5})
    assert not [c for c in api.mock.mock_calls if c[0] == "POST"]


async def test_adjust_api_validation_message_surfaces(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    api.mock.post(ADJ_URL, status=400, json={"detail": "Not enough filament left"})
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "adjust_remaining", {"spool_number": 1, "amount": -5000})
    assert err.value.translation_key == "api_validation"
    assert "Not enough filament left" in str(err.value)


async def test_adjust_invalid_spool_id(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "adjust_remaining", {"spool_id": "nope", "amount": 5})
    assert err.value.translation_key == "api_validation"


async def test_adjust_unsupported_api_error(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    api.mock.post(ADJ_URL, status=405)
    await setup_entry(hass)
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "adjust_remaining", {"spool_number": 1, "amount": 5})
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "api_too_old"


@pytest.mark.parametrize("status", [429, 500])
async def test_adjust_unavailable_errors(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    status: int,
) -> None:
    api.mock.post(ADJ_URL, status=status)
    await setup_entry(hass)
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "adjust_remaining", {"spool_number": 1, "amount": 5})
    assert err.value.translation_key == "api_unavailable"


@pytest.mark.parametrize("status", [401, 403])
async def test_adjust_auth_error(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    status: int,
) -> None:
    api.mock.post(ADJ_URL, status=status)
    await setup_entry(hass)
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "adjust_remaining", {"spool_number": 1, "amount": 5})
    assert err.value.translation_key == "api_auth"


@pytest.mark.parametrize(
    ("suffix", "spool_id"),
    [
        ("printer:101:slot:1:remaining", SPOOL_1),
        ("printer:101:slot:3:filament", SPOOL_3),
        ("printer:101:slot:3:remaining", SPOOL_3),
        ("printer:102:slot:1:remaining", SPOOL_2),
    ],
)
async def test_adjust_slot_resolves_to_its_own_spool(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
    suffix: str,
    spool_id: str,
) -> None:
    for sid in (SPOOL_1, SPOOL_2, SPOOL_3):
        api.mock.post(
            f"{BASE}/api/Filaments/{sid}/adjustments",
            json={"beforeGrams": 1.0, "afterGrams": 2.0},
        )
    await setup_entry(hass)
    await call(
        hass, "adjust_remaining", {"entity_id": entity_id(hass, "sensor", suffix), "amount": 1}
    )
    posted = [str(c[1]) for c in api.mock.mock_calls if c[0] == "POST"]
    assert posted == [f"{BASE}/api/Filaments/{spool_id}/adjustments"]


async def test_set_spool_active(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    api.mock.put(ACTIVE_URL, json={})
    await setup_entry(hass)
    version_calls = len(api.calls(VERSION_URL))
    result = await call(hass, "set_spool_active", {"spool_number": 1, "active": False})
    assert result == {}
    assert bodies(api, "PUT", ACTIVE_URL) == [{"isActive": False}]
    await hass.async_block_till_done()
    assert len(api.calls(VERSION_URL)) > version_calls


async def test_set_spool_active_by_entity(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    api.mock.put(ACTIVE_URL, json={})
    await setup_entry(hass)
    ent = entity_id(hass, "sensor", "printer:101:slot:1:filament")
    await call(hass, "set_spool_active", {"entity_id": ent, "active": True}, response=False)
    assert bodies(api, "PUT", ACTIVE_URL) == [{"isActive": True}]


async def test_log_maintenance_defaults_now_and_done_true(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
) -> None:
    api.mock.post(MAINT_URL, json={"id": "abc"})
    await setup_entry(hass)
    result = await call(
        hass,
        "log_maintenance",
        {"device_id": printer_device_id(hass, 101), "description": "Oiled the rods"},
    )
    assert result == {"maintenance_id": "abc"}
    body = bodies(api, "POST", MAINT_URL)[0]
    assert body["printerId"] == 101
    assert body["done"] is True
    assert body["description"] == "Oiled the rods"
    assert body["category"] is None
    assert body["date"] == NOW.isoformat()


async def test_log_maintenance_naive_date_uses_ha_time_zone(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
) -> None:
    await hass.config.async_set_time_zone("America/New_York")
    api.mock.post(MAINT_URL, json={"id": "abc"})
    await setup_entry(hass)
    await call(
        hass,
        "log_maintenance",
        {
            "device_id": printer_device_id(hass, 101),
            "description": "Cleaned",
            "category": "Cleaning",
            "notes": "n",
            "date": "2026-10-10 12:00:00",
        },
    )
    body = bodies(api, "POST", MAINT_URL)[0]
    assert body["date"] == "2026-10-10T16:00:00+00:00"
    assert body["category"] == "Cleaning"
    assert body["notes"] == "n"


async def test_log_maintenance_rejects_maintenance_id(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass,
            "log_maintenance",
            {
                "device_id": printer_device_id(hass, 101),
                "description": "x",
                "maintenance_id": "abc",
            },
        )
    assert err.value.translation_key == "maintenance_id_unsupported"
    assert "not supported yet" in str(err.value)
    assert not [c for c in api.mock.mock_calls if c[0] == "POST"]


async def test_log_maintenance_requires_printlog_device(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    entry = await setup_entry(hass)
    registry = dr.async_get(hass)
    account = registry.async_get_device_by_identifier((DOMAIN, f"{UID}:account"), entry.entry_id)
    foreign = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("other", "x")}
    )
    for device_id in (account.id, foreign.id, "nonexistent"):
        with pytest.raises(ServiceValidationError) as err:
            await call(hass, "log_maintenance", {"device_id": device_id, "description": "x"})
        assert err.value.translation_key == "invalid_printer_device"


async def test_log_maintenance_description_required_and_bounded(
    hass: HomeAssistant,
    api: FakeApi,  # noqa: F811
) -> None:
    await setup_entry(hass)
    device_id = printer_device_id(hass, 101)
    with pytest.raises(vol.Invalid):
        await call(hass, "log_maintenance", {"device_id": device_id})
    with pytest.raises(vol.Invalid):
        await call(hass, "log_maintenance", {"device_id": device_id, "description": "x" * 1001})
    with pytest.raises(vol.Invalid):
        await call(
            hass,
            "log_maintenance",
            {"device_id": device_id, "description": "x", "category": "c" * 257},
        )


async def test_notes_bounded(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    with pytest.raises(vol.Invalid):
        await call(hass, "adjust_remaining", {"spool_number": 1, "amount": 1, "notes": "n" * 1001})


async def test_single_entry_needs_no_config_entry_id(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    api.mock.post(ADJ_URL, json={"beforeGrams": 1.0, "afterGrams": 2.0})
    entry = await setup_entry(hass)
    await call(
        hass,
        "adjust_remaining",
        {"spool_number": 1, "amount": 1, "config_entry_id": entry.entry_id},
    )
    await call(hass, "adjust_remaining", {"spool_number": 1, "amount": 1})
    assert len(bodies(api, "POST", ADJ_URL)) == 2


OTHER_BASE = "https://self.example.com"
OTHER_UID = "self.example.com:43"
OTHER_ADJ_URL = f"{OTHER_BASE}/api/Filaments/{SPOOL_1}/adjustments"


async def setup_second_entry(hass: HomeAssistant, api: FakeApi):  # noqa: F811
    """A second account on its own base URL and key."""
    api.mock.get(f"{OTHER_BASE}/api/sync/version", json={"version": "v1"})
    api.mock.get(f"{OTHER_BASE}/api/sync/snapshot", json=api.snapshot)
    api.mock.get(f"{OTHER_BASE}/api/Notifications/unread-count", json=api.unread_body)
    api.mock.post(OTHER_ADJ_URL, json={"beforeGrams": 10.0, "afterGrams": 20.0})
    second = make_entry(
        unique_id=OTHER_UID,
        data={"base_url": OTHER_BASE, "auth_type": "api_key", "api_key": "other-key"},
    )
    await setup_entry(hass, second)
    assert second.state.value == "loaded"
    return second


def posts(api: FakeApi) -> list[tuple[str, str]]:  # noqa: F811
    """(url, api key) of every POST made."""
    return [(str(c[1]), c[3]["X-Api-Key"]) for c in api.mock.mock_calls if c[0] == "POST"]


async def test_multiple_entries_require_config_entry_id(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    api.mock.post(ADJ_URL, json={"beforeGrams": 1.0, "afterGrams": 2.0})
    first = await setup_entry(hass)
    second = await setup_second_entry(hass, api)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "adjust_remaining", {"spool_number": 1, "amount": 1})
    assert err.value.translation_key == "config_entry_required"
    assert posts(api) == []

    result = await call(
        hass,
        "adjust_remaining",
        {"spool_number": 1, "amount": 1, "config_entry_id": first.entry_id},
    )
    assert result["before_grams"] == 1.0
    assert posts(api) == [(ADJ_URL, "secret-key")]

    result = await call(
        hass,
        "adjust_remaining",
        {"spool_number": 1, "amount": 1, "config_entry_id": second.entry_id},
    )
    assert result["before_grams"] == 10.0
    assert posts(api)[1:] == [(OTHER_ADJ_URL, "other-key")]

    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass,
            "adjust_remaining",
            {"spool_number": 1, "amount": 1, "config_entry_id": "bogus"},
        )
    assert err.value.translation_key == "entry_not_found"


async def test_config_entry_id_not_loaded(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    idle = make_entry(unique_id=OTHER_UID)
    idle.add_to_hass(hass)
    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass,
            "adjust_remaining",
            {"spool_number": 1, "amount": 1, "config_entry_id": idle.entry_id},
        )
    assert err.value.translation_key == "entry_not_loaded"


async def test_log_maintenance_device_entry_mismatch(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    await setup_entry(hass)
    second = await setup_second_entry(hass, api)
    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass,
            "log_maintenance",
            {
                "device_id": printer_device_id(hass, 101),
                "description": "x",
                "config_entry_id": second.entry_id,
            },
        )
    assert err.value.translation_key == "device_entry_mismatch"
    assert posts(api) == []


async def test_entity_entry_overrides_config_entry_id(hass: HomeAssistant, api: FakeApi) -> None:  # noqa: F811
    api.mock.post(ADJ_URL, json={"beforeGrams": 1.0, "afterGrams": 2.0})
    first = await setup_entry(hass)
    other = make_entry(unique_id="api.3dprintlog.com:43")
    other.add_to_hass(hass)
    ent = entity_id(hass, "sensor", "printer:101:slot:1:remaining")
    assert er.async_get(hass).async_get(ent).config_entry_id == first.entry_id
    await call(
        hass,
        "adjust_remaining",
        {"entity_id": ent, "amount": 1, "config_entry_id": other.entry_id},
    )
    assert len(bodies(api, "POST", ADJ_URL)) == 1


async def test_no_loaded_entry(hass: HomeAssistant) -> None:
    async_register_services(hass)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "adjust_remaining", {"spool_number": 1, "amount": 1})
    assert err.value.translation_key == "no_entry"
