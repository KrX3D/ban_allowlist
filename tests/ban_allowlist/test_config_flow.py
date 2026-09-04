"""Test the Ban Allowlist config flow."""

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.loader import DATA_CUSTOM_COMPONENTS

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
    assert result["data"] == {
        CONF_IP_ADDRESSES: ["192.168.1.100", "10.0.0.0/24"]
    }
