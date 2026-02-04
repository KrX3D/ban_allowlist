"""The Ban Allowlist integration with UI configuration."""

from __future__ import annotations

import asyncio
import inspect
import logging
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)
from pathlib import Path
from typing import List

import voluptuous as vol
from homeassistant.components.http import ban as ban_module
from homeassistant.components.http.ban import KEY_BAN_MANAGER, IpBanManager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, CONF_IP_ADDRESSES

_LOGGER = logging.getLogger(__name__)
MODULE_HOOKS = [
    "async_log_invalid_auth",
    "log_invalid_auth",
    "async_log_invalid_auth_message",
]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("ip_addresses"): vol.All(cv.ensure_list, [cv.string]),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Ban Allowlist from a config entry."""
    try:
        ban_manager: IpBanManager = hass.http.app[KEY_BAN_MANAGER]
    except KeyError:
        _LOGGER.warning(
            "Can't find ban manager. ban_allowlist requires http.ip_ban_enabled to be True, so disabling."
        )
        return True
    _LOGGER.debug("Ban manager %s", ban_manager)
    allowlist: List[IPv4Network | IPv6Network] = [
        ip_network(ip) for ip in config.get(DOMAIN, {}).get("ip_addresses", [])
    ]
    if len(allowlist) == 0:
        _LOGGER.info("Not setting allowlist, as no IPs set")
    else:
        _LOGGER.info("Setting allowlist with %s", [str(ip) for ip in allowlist])

        original_async_add_ban = IpBanManager.async_add_ban

        async def allowlist_async_add_ban(
            remote_addr: IPv4Address | IPv6Address,
        ) -> None:
            for allowed_network in allowlist:
                if remote_addr in allowed_network:
                    _LOGGER.info(
                        "Not adding %s to ban list, as it's in the allowlist",
                        remote_addr,
                    )
                    return

            _LOGGER.info("Banning IP %s", remote_addr)
            await original_async_add_ban(ban_manager, remote_addr)

        ban_manager.async_add_ban = (  # type:ignore[method-assign]
            allowlist_async_add_ban
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    _LOGGER.debug(f"Setting up Ban Allowlist for entry_id: {entry.title}")

    # Get IP addresses from config entry
    ip_addresses = entry.data.get(CONF_IP_ADDRESSES, [])
    allowlist: List[IPv4Network | IPv6Network] = []
    
    for ip in ip_addresses:
        try:
            allowlist.append(ip_network(ip, strict=False))
        except ValueError as err:
            _LOGGER.error("Invalid IP address or network: %s - %s", ip, err)
            continue

    if len(allowlist) == 0:
        _LOGGER.warning("No valid IP networks configured")
        return True

    # Get the ban manager
    try:
        ban_manager: IpBanManager = hass.http.app[KEY_BAN_MANAGER]
        _LOGGER.info("Found IpBanManager via KEY_BAN_MANAGER")
    except KeyError:
        _LOGGER.error("Can't find ban manager. ban_allowlist requires http.ip_ban_enabled to be True")
        return True
    
    _LOGGER.info("Ban Allowlist initialized with %d networks: %s", len(allowlist), [str(n) for n in allowlist])

    # Store original method if not already stored
    if not hasattr(ban_manager, "_original_async_add_ban"):
        ban_manager._original_async_add_ban = IpBanManager.async_add_ban
    if (
        hasattr(IpBanManager, "async_add_login_failed")
        and not hasattr(ban_manager, "_original_async_add_login_failed")
    ):
        ban_manager._original_async_add_login_failed = IpBanManager.async_add_login_failed
    if (
        hasattr(IpBanManager, "async_log_invalid_auth")
        and not hasattr(ban_manager, "_original_async_log_invalid_auth")
    ):
        ban_manager._original_async_log_invalid_auth = IpBanManager.async_log_invalid_auth
    for hook in MODULE_HOOKS:
        if hasattr(ban_module, hook):
            key = f"_original_module_{hook}"
            if key not in hass.data[DOMAIN]:
                hass.data[DOMAIN][key] = getattr(ban_module, hook)

    async def allowlist_async_add_ban(
        remote_addr: IPv4Address | IPv6Address,
    ) -> None:
        """Wrapper for async_add_ban that checks allowlist."""
        ip_str = str(remote_addr)
        
        # Check if IP is in allowlist
        for allowed_network in allowlist:
            if remote_addr in allowed_network:
                _LOGGER.info(
                    "Not adding %s to ban list, as it's in the allowlist",
                    remote_addr,
                )
                
                # Remove from ban list if already banned
                await _remove_from_ban_list(hass, ip_str)
                
                # Clear the notification
                await _clear_ban_notification(hass, ip_str)
                
                return
        
        # If not in allowlist, proceed with original ban
        _LOGGER.info("Banning IP %s", remote_addr)
        await ban_manager._original_async_add_ban(ban_manager, remote_addr)

    # Replace the async_add_ban method
    ban_manager.async_add_ban = allowlist_async_add_ban  # type: ignore[method-assign]
    if hasattr(ban_manager, "_original_async_add_login_failed"):

        async def allowlist_async_add_login_failed(
            self: IpBanManager, remote_addr: IPv4Address | IPv6Address, *args, **kwargs
        ) -> None:
            """Wrapper for async_add_login_failed that checks allowlist."""
            ip_str = str(remote_addr)

            for allowed_network in allowlist:
                if remote_addr in allowed_network:
                    _LOGGER.info(
                        "Skipping login-failed tracking for %s as it's in the allowlist",
                        remote_addr,
                    )
                    await _clear_ban_notification(hass, ip_str)
                    return

            await ban_manager._original_async_add_login_failed(
                self, remote_addr, *args, **kwargs
            )

        IpBanManager.async_add_login_failed = (  # type: ignore[method-assign]
            allowlist_async_add_login_failed
        )
    if hasattr(ban_manager, "_original_async_log_invalid_auth"):

        async def allowlist_async_log_invalid_auth(
            self: IpBanManager, *args, **kwargs
        ) -> None:
            """Wrapper for async_log_invalid_auth that checks allowlist."""
            candidate_values = list(args) + list(kwargs.values())
            ip_value = None

            for value in candidate_values:
                try:
                    ip_value = ip_address(str(value))
                    break
                except (ValueError, TypeError):
                    continue

            if ip_value is not None:
                for allowed_network in allowlist:
                    if ip_value in allowed_network:
                        _LOGGER.info(
                            "Skipping invalid-auth logging for %s as it's in the allowlist",
                            ip_value,
                        )
                        await _clear_ban_notification(hass, str(ip_value))
                        return

            await ban_manager._original_async_log_invalid_auth(self, *args, **kwargs)

        IpBanManager.async_log_invalid_auth = (  # type: ignore[method-assign]
            allowlist_async_log_invalid_auth
        )
    def _make_module_wrapper(hook_name: str):
        original = hass.data[DOMAIN].get(f"_original_module_{hook_name}")
        if original is None:
            return None

        async def _async_wrapper(*args, **kwargs):
            candidate_values = list(args) + list(kwargs.values())
            ip_value = None

            for value in candidate_values:
                try:
                    ip_value = ip_address(str(value))
                    break
                except (ValueError, TypeError):
                    continue

            if ip_value is not None:
                for allowed_network in allowlist:
                    if ip_value in allowed_network:
                        _LOGGER.info(
                            "Skipping module %s for %s as it's in the allowlist",
                            hook_name,
                            ip_value,
                        )
                        await _clear_ban_notification(hass, str(ip_value))
                        return None

            if inspect.iscoroutinefunction(original):
                return await original(*args, **kwargs)
            return original(*args, **kwargs)

        def _sync_wrapper(*args, **kwargs):
            candidate_values = list(args) + list(kwargs.values())
            ip_value = None

            for value in candidate_values:
                try:
                    ip_value = ip_address(str(value))
                    break
                except (ValueError, TypeError):
                    continue

            if ip_value is not None:
                for allowed_network in allowlist:
                    if ip_value in allowed_network:
                        _LOGGER.info(
                            "Skipping module %s for %s as it's in the allowlist",
                            hook_name,
                            ip_value,
                        )
                        hass.async_create_task(
                            _clear_ban_notification(hass, str(ip_value))
                        )
                        return None

            return original(*args, **kwargs)

        return _async_wrapper if inspect.iscoroutinefunction(original) else _sync_wrapper

    for hook in MODULE_HOOKS:
        wrapper = _make_module_wrapper(hook)
        if wrapper is not None:
            setattr(ban_module, hook, wrapper)

    # Store ban manager reference for cleanup
    hass.data[DOMAIN][f"{entry.entry_id}_handler"] = ban_manager

    # Scan existing bans and remove any whitelisted IPs
    await _scan_and_remove_whitelisted_bans(hass, allowlist)
    
    # Set up options update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(entry.entry_id)

    _LOGGER.debug(f"Unloading Ban Allowlist for entry_id: {entry.title}")
    
    # Restore original ban method
    try:
        ban_manager = hass.data[DOMAIN].get(f"{entry.entry_id}_handler")
        
        if ban_manager and hasattr(ban_manager, "_original_async_add_ban"):
            IpBanManager.async_add_ban = ban_manager._original_async_add_ban
            _LOGGER.info("Restored original ban method")
        if ban_manager and hasattr(ban_manager, "_original_async_add_login_failed"):
            IpBanManager.async_add_login_failed = (
                ban_manager._original_async_add_login_failed
            )
            _LOGGER.info("Restored original login-failed method")
        if ban_manager and hasattr(ban_manager, "_original_async_log_invalid_auth"):
            IpBanManager.async_log_invalid_auth = (
                ban_manager._original_async_log_invalid_auth
            )
            _LOGGER.info("Restored original invalid-auth method")
        for hook in MODULE_HOOKS:
            key = f"_original_module_{hook}"
            if key in hass.data[DOMAIN]:
                setattr(ban_module, hook, hass.data[DOMAIN].pop(key))
                _LOGGER.info("Restored original module %s method", hook)
    except Exception as err:
        _LOGGER.warning("Could not restore original ban method: %s", err)
    
    # Clean up data
    hass.data[DOMAIN].pop(f"{entry.entry_id}_handler", None)
    
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove a config entry."""
    if entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(entry.entry_id)

    _LOGGER.debug(f"Removing Ban Allowlist for entry_id: {entry.title}")
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def _remove_from_ban_list(hass: HomeAssistant, ip_address: str) -> None:
    """Remove IP from ip_bans.yaml file."""
    ban_file = Path(hass.config.path("ip_bans.yaml"))
    
    def _do_remove():
        """Perform the actual file operations."""
        if not ban_file.exists():
            _LOGGER.debug("ip_bans.yaml does not exist")
            return
        
        try:
            import yaml
            
            # Read current bans
            with open(ban_file, "r") as f:
                bans = yaml.safe_load(f) or {}
            
            if not bans:
                ban_file.unlink()
                _LOGGER.info("ip_bans.yaml is empty, file deleted")
                return

            # Check if IP is in ban list
            if ip_address in bans:
                del bans[ip_address]
                _LOGGER.info(
                    "Removed whitelisted IP %s from ip_bans.yaml",
                    ip_address
                )
                
                # If ban list is empty, delete the file
                if not bans:
                    ban_file.unlink()
                    _LOGGER.info(
                        "ip_bans.yaml is empty after removing %s, file deleted",
                        ip_address
                    )
                else:
                    # Write updated bans back to file
                    with open(ban_file, "w") as f:
                        yaml.dump(bans, f, default_flow_style=False)
            else:
                _LOGGER.debug("IP %s not found in ip_bans.yaml", ip_address)
                        
        except Exception as err:
            _LOGGER.error(
                "Error removing IP %s from ip_bans.yaml: %s",
                ip_address,
                err
            )
    
    # Run blocking I/O in executor
    await hass.async_add_executor_job(_do_remove)


async def _clear_ban_notification(hass: HomeAssistant, ip_address: str) -> None:
    """Clear the Home Assistant notification for the banned IP."""
    # These are the notification IDs used by Home Assistant's ban system
    # See: https://github.com/home-assistant/core/blob/dev/homeassistant/components/http/ban.py
    notification_ids = ["ip-ban", "http-login"]
    
    # Wait and dismiss multiple times to catch all notifications
    # Sometimes HA creates multiple notifications or creates them slightly delayed
    for attempt in range(5):  # Try 5 times
        if attempt > 0:
            await asyncio.sleep(0.5)  # Wait 500ms between attempts
        
        for notification_id in notification_ids:
            try:
                await hass.services.async_call(
                    "persistent_notification",
                    "dismiss",
                    {"notification_id": notification_id},
                    blocking=True,
                )
                if attempt == 0:  # Only log on first attempt to avoid spam
                    _LOGGER.info(
                        "Dismissed notification '%s' for whitelisted IP %s",
                        notification_id,
                        ip_address
                    )
                    
            except Exception as err:
                if attempt == 0:  # Only log errors on first attempt
                    _LOGGER.debug(
                        "Could not dismiss notification %s: %s",
                        notification_id,
                        err
                    )
    
    # Final cleanup pass after 3 seconds to catch any late notifications
    _LOGGER.debug("Scheduling final notification cleanup in 3 seconds for IP %s", ip_address)
    
    async def final_cleanup():
        """Final cleanup of notifications after delay."""
        await asyncio.sleep(3.0)
        _LOGGER.debug("Running final notification cleanup for IP %s", ip_address)
        for notification_id in notification_ids:
            try:
                await hass.services.async_call(
                    "persistent_notification",
                    "dismiss",
                    {"notification_id": notification_id},
                    blocking=True,
                )
                _LOGGER.debug("Final cleanup dismissed '%s' for IP %s", notification_id, ip_address)
            except Exception as err:
                _LOGGER.debug("Final cleanup: Could not dismiss %s: %s", notification_id, err)
        _LOGGER.info("Final notification cleanup completed for IP %s", ip_address)
    
    # Schedule final cleanup without waiting
    hass.async_create_task(final_cleanup())
    
    _LOGGER.info("Completed initial notification cleanup for whitelisted IP %s", ip_address)


async def _scan_and_remove_whitelisted_bans(
    hass: HomeAssistant, allowlist: List[IPv4Network | IPv6Network]
) -> None:
    """Scan existing bans and remove any that match the whitelist."""
    _LOGGER.info("Scanning existing bans for whitelisted IPs...")
    
    ban_file = Path(hass.config.path("ip_bans.yaml"))
    
    def _do_scan():
        """Perform the actual file operations."""
        if not ban_file.exists():
            _LOGGER.debug("No ip_bans.yaml file found")
            return []
        
        try:
            import yaml
            from ipaddress import ip_address
            
            # Read current bans
            with open(ban_file, "r") as f:
                bans = yaml.safe_load(f) or {}

            if not bans:
                ban_file.unlink()
                _LOGGER.info("ip_bans.yaml is empty, file deleted")
                return []
            
            removed_ips = []
            
            # Check each banned IP
            for banned_ip in list(bans.keys()):
                try:
                    ip_obj = ip_address(banned_ip)
                    
                    # Check if it's in any whitelist network
                    for network in allowlist:
                        if ip_obj in network:
                            removed_ips.append(banned_ip)
                            del bans[banned_ip]
                            _LOGGER.info(
                                "Found whitelisted IP %s in ban list (matches %s), removing",
                                banned_ip,
                                network
                            )
                            break
                            
                except ValueError:
                    _LOGGER.debug("Invalid IP in ban list: %s", banned_ip)
            
            if removed_ips:
                # If ban list is empty, delete the file
                if not bans:
                    ban_file.unlink()
                    _LOGGER.info("ip_bans.yaml is empty after removing whitelisted IPs, file deleted")
                else:
                    # Write updated bans back to file
                    with open(ban_file, "w") as f:
                        yaml.dump(bans, f, default_flow_style=False)
                
                _LOGGER.info("Removed %d whitelisted IPs from ban list: %s", len(removed_ips), removed_ips)
            else:
                _LOGGER.info("No whitelisted IPs found in ban list")
            
            return removed_ips
                        
        except Exception as err:
            _LOGGER.error("Error scanning existing bans: %s", err)
            return []
    
    # Run blocking I/O in executor
    removed_ips = await hass.async_add_executor_job(_do_scan)
    
    # Clear notifications for removed IPs
    for ip in removed_ips:
        await _clear_ban_notification(hass, ip)
