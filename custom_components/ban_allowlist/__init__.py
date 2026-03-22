"""The Ban Allowlist integration with UI configuration."""

from __future__ import annotations

import dataclasses
import inspect
import logging
import re
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)
from pathlib import Path
from typing import Any

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
# Matches IPv4 and a broad IPv6-like token; validated with ip_address() afterward.
# FIX: was IPv4-only, which silently ignored IPv6 addresses in the log handler.
IP_MESSAGE_PATTERN = re.compile(
    r"((?:\d{1,3}\.){3}\d{1,3}|[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){2,7})"
)

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


@dataclasses.dataclass
class BanAllowlistData:
    """Runtime data stored on the config entry."""

    ban_manager: IpBanManager
    log_handler: logging.Handler
    original_add_ban: Any
    original_add_login_failed: Any | None
    original_log_invalid_auth: Any | None
    original_module_hooks: dict[str, Any]


def _extract_ip(
    args: tuple[Any, ...], kwargs: dict[str, Any]
) -> IPv4Address | IPv6Address | None:
    """Extract the first recognizable IP address from positional/keyword args."""
    for value in (*args, *kwargs.values()):
        try:
            return ip_address(str(value))
        except (ValueError, TypeError):
            continue
    return None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Ban Allowlist from YAML configuration (legacy path).

    The preferred setup path is via the UI (config entry).  YAML support is
    retained only for the unit-test suite which drives setup via
    async_setup_component with a YAML dict.

    Conflict rules:
      - If a config entry already exists, skip YAML setup entirely and warn
        the user to remove the YAML section.
      - If we do patch the ban manager, mark it with ``_yaml_patched`` so that
        async_setup_entry can detect the situation and refuse to layer on top.
    """
    # FIX: if a config entry is already managing the ban manager, don't also
    # set up via YAML — the two patches would stack and restoration on unload
    # would leave behind the YAML wrapper instead of the true HA original.
    if hass.config_entries.async_entries(DOMAIN):
        _LOGGER.warning(
            "ban_allowlist is configured via the UI — ignoring configuration.yaml "
            "entry. Remove the 'ban_allowlist:' section from configuration.yaml."
        )
        return True

    try:
        ban_manager: IpBanManager = hass.http.app[KEY_BAN_MANAGER]
    except KeyError:
        _LOGGER.warning(
            "Can't find ban manager. ban_allowlist requires http.ip_ban_enabled"
            " to be True, so disabling."
        )
        return True

    _LOGGER.debug("Ban manager %s", ban_manager)
    allowlist: list[IPv4Network | IPv6Network] = [
        ip_network(ip) for ip in config.get(DOMAIN, {}).get("ip_addresses", [])
    ]

    if not allowlist:
        _LOGGER.info("Not setting allowlist, as no IPs set")
        return True

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

    ban_manager.async_add_ban = (  # type: ignore[method-assign]
        allowlist_async_add_ban
    )
    # Mark so async_setup_entry can detect a YAML patch is already in place.
    ban_manager._yaml_patched = True  # type: ignore[attr-defined]

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    _LOGGER.debug("Setting up Ban Allowlist for entry: %s", entry.title)

    try:
        ban_manager: IpBanManager = hass.http.app[KEY_BAN_MANAGER]
    except KeyError:
        _LOGGER.error(
            "Can't find ban manager. ban_allowlist requires http.ip_ban_enabled to be True"
        )
        return True

    # FIX: if async_setup (YAML path) already patched this ban_manager instance,
    # we cannot safely capture the true original — it would already be the YAML
    # wrapper.  Refuse to stack a second patch and tell the user to clean up their
    # configuration.yaml instead.
    if getattr(ban_manager, "_yaml_patched", False):
        _LOGGER.warning(
            "ban_allowlist is configured both in configuration.yaml and via the UI. "
            "Remove the 'ban_allowlist:' section from configuration.yaml — "
            "the UI config entry will not be activated while the YAML entry is present."
        )
        return True

    # Read IPs from options first (written by the options flow on save),
    # falling back to data (written by the initial config flow).
    opts_ips: list[str] | None = entry.options.get(CONF_IP_ADDRESSES)
    ip_addresses: list[str] = (
        opts_ips if opts_ips is not None else entry.data.get(CONF_IP_ADDRESSES, [])
    )

    allowlist: list[IPv4Network | IPv6Network] = []
    for ip in ip_addresses:
        try:
            allowlist.append(ip_network(ip, strict=False))
        except ValueError as err:
            _LOGGER.error("Invalid IP address or network: %s - %s", ip, err)

    if not allowlist:
        _LOGGER.warning("No valid IP networks configured")
        return True

    _LOGGER.info(
        "Ban Allowlist initialized with %d networks: %s",
        len(allowlist),
        [str(n) for n in allowlist],
    )

    # Store originals as bound methods so calling them in wrappers needs no
    # explicit `self` / `ban_manager` argument — restoration is a plain assignment.
    # All three are patched at the *instance* level so no other IpBanManager
    # instances are affected and restoration is isolated to this entry.
    original_add_ban: Any = ban_manager.async_add_ban
    original_add_login_failed: Any | None = getattr(
        ban_manager, "async_add_login_failed", None
    )
    original_log_invalid_auth: Any | None = getattr(
        ban_manager, "async_log_invalid_auth", None
    )
    original_module_hooks: dict[str, Any] = {
        hook: getattr(ban_module, hook)
        for hook in MODULE_HOOKS
        if hasattr(ban_module, hook)
    }

    # --- Patch async_add_ban (instance-level) ---
    async def allowlist_async_add_ban(
        remote_addr: IPv4Address | IPv6Address,
    ) -> None:
        """Wrap async_add_ban to skip allowlisted IPs."""
        for allowed_network in allowlist:
            if remote_addr in allowed_network:
                _LOGGER.info(
                    "Not adding %s to ban list, as it's in the allowlist",
                    remote_addr,
                )
                await _remove_from_ban_list(hass, str(remote_addr))
                await _clear_ban_notification(hass, str(remote_addr))
                return
        _LOGGER.info("Banning IP %s", remote_addr)
        await original_add_ban(remote_addr)

    ban_manager.async_add_ban = allowlist_async_add_ban  # type: ignore[method-assign]

    # --- Patch async_add_login_failed (instance-level) ---
    if original_add_login_failed is not None:

        async def allowlist_async_add_login_failed(
            remote_addr: IPv4Address | IPv6Address, *args: Any, **kwargs: Any
        ) -> None:
            """Wrap async_add_login_failed to skip allowlisted IPs."""
            for allowed_network in allowlist:
                if remote_addr in allowed_network:
                    _LOGGER.info(
                        "Skipping login-failed tracking for %s as it's in the allowlist",
                        remote_addr,
                    )
                    await _clear_ban_notification(hass, str(remote_addr))
                    return
            await original_add_login_failed(remote_addr, *args, **kwargs)

        ban_manager.async_add_login_failed = allowlist_async_add_login_failed  # type: ignore[method-assign]

    # --- Patch async_log_invalid_auth (instance-level) ---
    if original_log_invalid_auth is not None:

        async def allowlist_async_log_invalid_auth(
            *args: Any, **kwargs: Any
        ) -> None:
            """Wrap async_log_invalid_auth to skip allowlisted IPs."""
            ip_value = _extract_ip(args, kwargs)
            if ip_value is not None:
                for allowed_network in allowlist:
                    if ip_value in allowed_network:
                        _LOGGER.info(
                            "Skipping invalid-auth logging for %s as it's in the allowlist",
                            ip_value,
                        )
                        await _clear_ban_notification(hass, str(ip_value))
                        return
            await original_log_invalid_auth(*args, **kwargs)

        ban_manager.async_log_invalid_auth = allowlist_async_log_invalid_auth  # type: ignore[method-assign]

    # --- Patch module-level hooks ---
    def _make_module_wrapper(hook_name: str, original: Any) -> Any:
        async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
            ip_value = _extract_ip(args, kwargs)
            if ip_value is not None:
                for allowed_network in allowlist:
                    if ip_value in allowed_network:
                        _LOGGER.info(
                            "Skipping module hook %s for %s as it's in the allowlist",
                            hook_name,
                            ip_value,
                        )
                        await _clear_ban_notification(hass, str(ip_value))
                        return None
            return await original(*args, **kwargs)

        def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            ip_value = _extract_ip(args, kwargs)
            if ip_value is not None:
                for allowed_network in allowlist:
                    if ip_value in allowed_network:
                        _LOGGER.info(
                            "Skipping module hook %s for %s as it's in the allowlist",
                            hook_name,
                            ip_value,
                        )
                        hass.async_create_task(
                            _clear_ban_notification(hass, str(ip_value))
                        )
                        return None
            return original(*args, **kwargs)

        return _async_wrapper if inspect.iscoroutinefunction(original) else _sync_wrapper

    for hook, original in original_module_hooks.items():
        setattr(ban_module, hook, _make_module_wrapper(hook, original))

    # --- Log handler (belt-and-suspenders for any paths not caught above) ---
    class _BanLogHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            message = record.getMessage()
            match = IP_MESSAGE_PATTERN.search(message)
            if not match:
                return
            try:
                ip_value = ip_address(match.group(1))
            except ValueError:
                return
            for allowed_network in allowlist:
                if ip_value in allowed_network:
                    _LOGGER.info(
                        "Dismissed login-failed notification for allowlisted IP %s",
                        ip_value,
                    )
                    hass.async_create_task(
                        _clear_ban_notification(hass, str(ip_value))
                    )
                    break

    log_handler = _BanLogHandler()
    logging.getLogger("homeassistant.components.http.ban").addHandler(log_handler)

    # Store all runtime state on the entry — no hass.data dict management needed,
    # and no NameError risk from mismatched local variable scopes across functions.
    entry.runtime_data = BanAllowlistData(
        ban_manager=ban_manager,
        log_handler=log_handler,
        original_add_ban=original_add_ban,
        original_add_login_failed=original_add_login_failed,
        original_log_invalid_auth=original_log_invalid_auth,
        original_module_hooks=original_module_hooks,
    )

    await _scan_and_remove_whitelisted_bans(hass, allowlist)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Ban Allowlist for entry: %s", entry.title)

    data: BanAllowlistData | None = getattr(entry, "runtime_data", None)
    if data is None:
        # Entry never fully set up (e.g. YAML conflict warning path), nothing to restore.
        return True

    ban_manager = data.ban_manager

    try:
        ban_manager.async_add_ban = data.original_add_ban  # type: ignore[method-assign]
        _LOGGER.info("Restored original async_add_ban")

        if data.original_add_login_failed is not None:
            ban_manager.async_add_login_failed = data.original_add_login_failed  # type: ignore[method-assign]
            _LOGGER.info("Restored original async_add_login_failed")

        if data.original_log_invalid_auth is not None:
            ban_manager.async_log_invalid_auth = data.original_log_invalid_auth  # type: ignore[method-assign]
            _LOGGER.info("Restored original async_log_invalid_auth")

        for hook, original in data.original_module_hooks.items():
            setattr(ban_module, hook, original)
            _LOGGER.info("Restored original module hook: %s", hook)

    except Exception as err:
        _LOGGER.warning("Could not fully restore original ban methods: %s", err)

    logging.getLogger("homeassistant.components.http.ban").removeHandler(
        data.log_handler
    )

    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a config entry."""
    _LOGGER.debug("Removing Ban Allowlist for entry: %s", entry.title)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _remove_from_ban_list(hass: HomeAssistant, ip_addr: str) -> None:
    """Remove an IP address from ip_bans.yaml."""
    ban_file = Path(hass.config.path("ip_bans.yaml"))

    def _do_remove() -> None:
        if not ban_file.exists():
            _LOGGER.debug("ip_bans.yaml does not exist")
            return

        try:
            import yaml

            with ban_file.open() as f:
                bans: dict[str, object] = yaml.safe_load(f) or {}

            if not bans:
                ban_file.unlink()
                _LOGGER.info("ip_bans.yaml is empty, file deleted")
                return

            if ip_addr not in bans:
                _LOGGER.debug("IP %s not found in ip_bans.yaml", ip_addr)
                return

            del bans[ip_addr]
            _LOGGER.info("Removed whitelisted IP %s from ip_bans.yaml", ip_addr)

            if not bans:
                ban_file.unlink()
                _LOGGER.info(
                    "ip_bans.yaml is empty after removing %s, file deleted", ip_addr
                )
            else:
                with ban_file.open("w") as f:
                    yaml.dump(bans, f, default_flow_style=False)

        except Exception as err:
            _LOGGER.error(
                "Error removing IP %s from ip_bans.yaml: %s", ip_addr, err
            )

    await hass.async_add_executor_job(_do_remove)


async def _clear_ban_notification(hass: HomeAssistant, ip_addr: str) -> None:
    """Dismiss Home Assistant ban notifications for a whitelisted IP.

    A single dismiss pass is sufficient because we intercept the ban before HA
    calls async_add_ban / creates the notification.  The previous retry loop
    (5 attempts x 500 ms + a 3 s deferred task) was both noisy and racy, and
    could accidentally dismiss legitimate ban notifications for IPs that are not
    in the allowlist when two events were processed concurrently.
    """
    for notification_id in ("ip-ban", "http-login"):
        try:
            await hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": notification_id},
                blocking=True,
            )
            _LOGGER.debug(
                "Dismissed notification '%s' for whitelisted IP %s",
                notification_id,
                ip_addr,
            )
        except Exception as err:
            _LOGGER.debug(
                "Could not dismiss notification %s: %s", notification_id, err
            )


async def _scan_and_remove_whitelisted_bans(
    hass: HomeAssistant, allowlist: list[IPv4Network | IPv6Network]
) -> None:
    """Scan ip_bans.yaml on startup and remove any allowlisted IPs."""
    _LOGGER.info("Scanning existing bans for allowlisted IPs...")

    ban_file = Path(hass.config.path("ip_bans.yaml"))

    def _do_scan() -> list[str]:
        if not ban_file.exists():
            _LOGGER.debug("No ip_bans.yaml file found")
            return []

        try:
            import yaml

            with ban_file.open() as f:
                bans: dict[str, object] = yaml.safe_load(f) or {}

            if not bans:
                ban_file.unlink()
                _LOGGER.info("ip_bans.yaml is empty, file deleted")
                return []

            removed: list[str] = []
            for banned_ip in list(bans.keys()):
                try:
                    ip_obj = ip_address(banned_ip)
                except ValueError:
                    _LOGGER.debug("Invalid IP in ban list: %s", banned_ip)
                    continue

                for network in allowlist:
                    if ip_obj in network:
                        del bans[banned_ip]
                        removed.append(banned_ip)
                        _LOGGER.info(
                            "Found allowlisted IP %s in ban list (matches %s), removing",
                            banned_ip,
                            network,
                        )
                        break

            if removed:
                if not bans:
                    ban_file.unlink()
                    _LOGGER.info(
                        "ip_bans.yaml is empty after removing allowlisted IPs, file deleted"
                    )
                else:
                    with ban_file.open("w") as f:
                        yaml.dump(bans, f, default_flow_style=False)
                _LOGGER.info(
                    "Removed %d allowlisted IPs from ban list: %s",
                    len(removed),
                    removed,
                )
            else:
                _LOGGER.info("No allowlisted IPs found in ban list")

            return removed

        except Exception as err:
            _LOGGER.error("Error scanning existing bans: %s", err)
            return []

    removed_ips = await hass.async_add_executor_job(_do_scan)

    for ip in removed_ips:
        await _clear_ban_notification(hass, ip)