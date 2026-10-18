"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load integrations from custom_components."""
    yield


@pytest.fixture
def snapshot(snapshot):
    """Snapshot assertion with Home Assistant's serializer (stable ids and times)."""
    from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension

    return snapshot.use_extension(HomeAssistantSnapshotExtension)
