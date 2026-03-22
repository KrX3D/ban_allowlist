"""Config flow for Ban Allowlist integration."""
import ipaddress
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN, CONF_IP_ADDRESSES

_LOGGER = logging.getLogger(__name__)


class BanAllowlistConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ban Allowlist."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        # FIX: enforce a single instance.  Previously the config flow had no unique
        # ID, so the integration could be added multiple times.  Each instance would
        # monkey-patch on top of the previous one, making method restoration on
        # unload unreliable.
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}

        if user_input is not None:
            ip_list = user_input.get(CONF_IP_ADDRESSES, "")
            ip_addresses = [ip.strip() for ip in ip_list.split(",") if ip.strip()]
            validated_ips: list[str] = []

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
                "example": "192.168.1.100, 10.0.0.0/24, 172.16.0.1"
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        # FIX: modern HA (2024+) sets self.config_entry automatically on the
        # OptionsFlow instance; no need to pass it via __init__.
        return BanAllowlistOptionsFlow()


class BanAllowlistOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Ban Allowlist.

    FIX: the previous implementation called async_update_entry(data=...) mid-flow
    to immediately persist each add/remove.  This had two problems:
      1. It wrote to entry.data instead of entry.options (wrong storage slot).
      2. It bypassed the normal options-flow commit lifecycle.

    The new approach tracks a working copy (_ips) in memory during the flow and
    only commits to entry.options when the user explicitly selects "Done".  If the
    dialog is closed early, no changes are saved — which is the expected HA pattern.

    async_setup_entry reads from entry.options first, falling back to entry.data
    for entries created before this change (migration path).
    """

    def _get_ips(self) -> list[str]:
        """Return the current working IP list, initialising from the entry if needed.

        Using a lazy-init helper means _ips is available regardless of which step
        is entered first, without requiring __init__ to be overridden.
        """
        if not hasattr(self, "_ips"):
            # Prefer options (set by a previous options flow), fall back to data
            # (set by the initial config flow).  Use an explicit None check so an
            # intentionally empty options list is respected rather than overridden.
            opts = self.config_entry.options.get(CONF_IP_ADDRESSES)
            data_ips = self.config_entry.data.get(CONF_IP_ADDRESSES, [])
            self._ips: list[str] = list(opts if opts is not None else data_ips)
        return self._ips

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options — entry point called once by HA."""
        self._get_ips()  # eagerly initialise so all subsequent steps see it
        return await self.async_step_manage_ips()

    async def async_step_manage_ips(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show IP management menu."""
        errors: dict[str, str] = {}

        if user_input is not None:
            action = user_input.get("action")
            if action == "add":
                return await self.async_step_add_ip()
            if action == "remove":
                return await self.async_step_remove_ip()
            if action == "done":
                # Commit the working copy to entry.options and trigger reload.
                return self.async_create_entry(
                    title="",
                    data={CONF_IP_ADDRESSES: self._get_ips()},
                )

        translations = await async_get_translations(
            self.hass, self.hass.config.language, "options", [DOMAIN]
        )

        def _t(key: str, default: str) -> str:
            return translations.get(f"component.{DOMAIN}.{key}", default)

        current_ips = self._get_ips()
        ip_list = (
            "\n".join(current_ips)
            if current_ips
            else _t("options.step.manage_ips.no_ips", "No IPs whitelisted")
        )

        action_labels = {
            "add": _t(
                "options.step.manage_ips.action_options.add", "Add new IP address"
            ),
            "remove": _t(
                "options.step.manage_ips.action_options.remove", "Remove IP address"
            ),
            "done": _t("options.step.manage_ips.action_options.done", "Done"),
        }

        return self.async_show_form(
            step_id="manage_ips",
            data_schema=vol.Schema(
                {vol.Required("action"): vol.In(action_labels)}
            ),
            errors=errors,
            description_placeholders={"current_ips": ip_list},
        )

    async def async_step_add_ip(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
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
            description_placeholders={"example": "192.168.1.100 or 10.0.0.0/24"},
        )

    async def async_step_remove_ip(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
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