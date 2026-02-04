"""Config flow for Ban Allowlist integration."""
import ipaddress
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, CONF_IP_ADDRESSES

_LOGGER = logging.getLogger(__name__)


class BanAllowlistConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ban Allowlist."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            ip_list = user_input.get(CONF_IP_ADDRESSES, "")
            
            # Parse and validate IP addresses
            ip_addresses = [ip.strip() for ip in ip_list.split(",") if ip.strip()]
            validated_ips = []
            
            for ip in ip_addresses:
                try:
                    # Validate IP address or network
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
        return BanAllowlistOptionsFlow(config_entry)


class BanAllowlistOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Ban Allowlist."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        return await self.async_step_manage_ips()

    async def async_step_manage_ips(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage whitelisted IPs."""
        errors = {}

        if user_input is not None:
            action = user_input.get("action")
            
            if action == "add":
                return await self.async_step_add_ip()
            elif action == "remove":
                return await self.async_step_remove_ip()
            elif action == "done":
                return self.async_create_entry(title="", data={})

        # Get current IPs
        current_ips = self.config_entry.data.get(CONF_IP_ADDRESSES, [])
        ip_list = "\n".join(current_ips) if current_ips else "No IPs whitelisted"

        return self.async_show_form(
            step_id="manage_ips",
            data_schema=vol.Schema(
                {
                    vol.Required("action"): vol.In(
                        {
                            "add": "Add new IP address",
                            "remove": "Remove IP address",
                            "done": "Done",
                        }
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "current_ips": ip_list,
            },
        )

    async def async_step_add_ip(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a new IP to the allowlist."""
        errors = {}

        if user_input is not None:
            new_ip = user_input.get("ip_address", "").strip()
            
            try:
                # Validate IP address or network
                ipaddress.ip_network(new_ip, strict=False)
                
                # Get current IPs and add new one
                current_ips = list(self.config_entry.data.get(CONF_IP_ADDRESSES, []))
                
                if new_ip in current_ips:
                    errors["base"] = "already_exists"
                else:
                    current_ips.append(new_ip)
                    
                    # Update config entry
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={CONF_IP_ADDRESSES: current_ips},
                    )
                    
                    return await self.async_step_manage_ips()
                    
            except ValueError:
                errors["base"] = "invalid_ip"

        return self.async_show_form(
            step_id="add_ip",
            data_schema=vol.Schema(
                {
                    vol.Required("ip_address"): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "example": "192.168.1.100 or 10.0.0.0/24"
            },
        )

    async def async_step_remove_ip(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Remove an IP from the allowlist."""
        current_ips = self.config_entry.data.get(CONF_IP_ADDRESSES, [])
        
        if not current_ips:
            return await self.async_step_manage_ips()

        if user_input is not None:
            ip_to_remove = user_input.get("ip_address")
            
            if ip_to_remove:
                current_ips = [ip for ip in current_ips if ip != ip_to_remove]
                
                # Update config entry
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={CONF_IP_ADDRESSES: current_ips},
                )
            
            return await self.async_step_manage_ips()

        # Create selection schema
        ip_options = {ip: ip for ip in current_ips}

        return self.async_show_form(
            step_id="remove_ip",
            data_schema=vol.Schema(
                {
                    vol.Required("ip_address"): vol.In(ip_options),
                }
            ),
        )