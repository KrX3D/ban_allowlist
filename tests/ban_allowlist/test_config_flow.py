"""Test the Ban Allowlist config flow."""

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.loader import DATA_CUSTOM_COMPONENTS
from pytest_homeassistant_custom_component.common import (  # type: ignore[import-untyped]
    MockConfigEntry,
)

from custom_components.ban_allowlist.const import CONF_IP_ADDRESSES, DOMAIN


@pytest.mark.anyio
async def test_user_flow_requires_at_least_one_ip(hass: HomeAssistant) -> None:
    """Submitting an empty IP list should re-show the form with an error."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IP_ADDRESSES: ""}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_ips"}


@pytest.mark.anyio
async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """A valid IP list creates a config entry."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IP_ADDRESSES: "192.168.1.100, 10.0.0.0/24"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_IP_ADDRESSES: ["192.168.1.100", "10.0.0.0/24"]}


@pytest.mark.anyio
async def test_options_flow_add_remove_via_menu(hass: HomeAssistant) -> None:
    """The options flow's menu should let you add, then remove, an IP."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_IP_ADDRESSES: ["192.168.1.100"]})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "manage_ips"
    assert result["description_placeholders"] == {"current_ips": "192.168.1.100"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_ip"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_ip"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"ip_address": "10.0.0.0/24"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "manage_ips"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remove_ip"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "remove_ip"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"ip_address": "192.168.1.100"}
    )
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_IP_ADDRESSES: ["10.0.0.0/24"]}
