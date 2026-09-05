"""Config flow for Ban Allowlist integration."""

import ipaddress
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback

from .const import CONF_IP_ADDRESSES, DOMAIN

_LOGGER = logging.getLogger(__name__)


class BanAllowlistConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ban Allowlist."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}

        if user_input is not None:
            ip_list = user_input.get(CONF_IP_ADDRESSES, "")
            ip_addresses = [ip.strip() for ip in ip_list.split(",") if ip.strip()]
            validated_ips: list[str] = []

            if not ip_addresses:
                errors["base"] = "no_ips"

            for ip in ip_addresses:
                try:
                    ipaddress.ip_network(ip, strict=False)
                    validated_ips.append(ip)
                except ValueError:
                    errors["base"] = "invalid_ip"
                    break

            if not errors:
                return self.async_create_entry(
                    title="Ban Allowlist",
                    data={CONF_IP_ADDRESSES: validated_ips},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_IP_ADDRESSES, default=""): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "example": "192.168.1.100, 10.0.0.0/24, 2001:db8::1, 2001:db8::/32"
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return BanAllowlistOptionsFlow()


class BanAllowlistOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Ban Allowlist."""

    def _get_ips(self) -> list[str]:
        """Return the current working IP list, initialising from the entry if needed."""
        if not hasattr(self, "_ips"):
            entry = self.hass.config_entries.async_get_entry(self.handler)
            assert entry is not None
            opts = entry.options.get(CONF_IP_ADDRESSES)
            data_ips = entry.data.get(CONF_IP_ADDRESSES, [])
            self._ips: list[str] = list(opts if opts is not None else data_ips)
        return self._ips

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options — entry point called once by HA."""
        self._get_ips()
        return await self.async_step_manage_ips()

    async def async_step_manage_ips(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the IP management menu."""
        current_ips = self._get_ips()
        ip_list = "\n".join(current_ips) if current_ips else "No IPs allowlisted"

        return self.async_show_menu(
            step_id="manage_ips",
            menu_options=["add_ip", "remove_ip", "done"],
            description_placeholders={"current_ips": ip_list},
        )

    async def async_step_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Save the working allowlist and close the options flow."""
        return self.async_create_entry(
            title="",
            data={CONF_IP_ADDRESSES: self._get_ips()},
        )

    async def async_step_add_ip(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a new IP to the working allowlist."""
        errors: dict[str, str] = {}

        if user_input is not None:
            new_ip = user_input.get("ip_address", "").strip()
            try:
                ipaddress.ip_network(new_ip, strict=False)
                ips = self._get_ips()
                if new_ip in ips:
                    errors["base"] = "already_exists"
                else:
                    ips.append(new_ip)
                    return await self.async_step_manage_ips()
            except ValueError:
                errors["base"] = "invalid_ip"

        return self.async_show_form(
            step_id="add_ip",
            data_schema=vol.Schema({vol.Required("ip_address"): str}),
            errors=errors,
            description_placeholders={
                "example": "192.168.1.100, 10.0.0.0/24, 2001:db8::1, 2001:db8::/32"
            },
        )

    async def async_step_remove_ip(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove an IP from the working allowlist."""
        ips = self._get_ips()

        if not ips:
            return await self.async_step_manage_ips()

        if user_input is not None:
            ip_to_remove = user_input.get("ip_address")
            if ip_to_remove and ip_to_remove in ips:
                ips.remove(ip_to_remove)
            return await self.async_step_manage_ips()

        return self.async_show_form(
            step_id="remove_ip",
            data_schema=vol.Schema(
                {vol.Required("ip_address"): vol.In({ip: ip for ip in ips})}
            ),
        )
