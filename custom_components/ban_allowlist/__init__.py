"""The Ban Allowlist integration with UI configuration."""

from __future__ import annotations

import dataclasses
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

from .const import CONF_IP_ADDRESSES, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Matches IPv4 and a broad IPv6-like token; validated with ip_address() afterward.
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


def _matching_network(
    ip_obj: IPv4Address | IPv6Address,
    allowlist: list[IPv4Network | IPv6Network],
) -> IPv4Network | IPv6Network | None:
    """Return the first allowlist network containing ip_obj, or None."""
    for network in allowlist:
        if ip_obj in network:
            return network
    return None


@dataclasses.dataclass
class BanAllowlistData:
    """Runtime data stored on the config entry."""

    ban_manager: IpBanManager
    log_handler: logging.Handler
    original_add_ban: Any
    original_process_wrong_login: Any  # the sole module-level function we patch


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Ban Allowlist from YAML configuration (legacy / unit-test path).

    HA calls async_setup for every integration that defines CONFIG_SCHEMA,
    even when the domain key is absent from configuration.yaml.  We only
    act (and only warn about conflicts) when ip_addresses are actually present.
    """
    domain_config = config.get(DOMAIN, {})
    yaml_ips = domain_config.get("ip_addresses", [])

    if not yaml_ips:
        # Nothing in YAML — config entry handles everything, nothing to do.
        return True

    if hass.config_entries.async_entries(DOMAIN):
        _LOGGER.warning(
            "ban_allowlist has ip_addresses in configuration.yaml AND is configured "
            "via the UI. Remove the 'ban_allowlist:' section from configuration.yaml "
            "— the UI config entry will be used and the YAML section is ignored."
        )
        return True

    # YAML-only path (used by unit-test suite via async_setup_component).
    try:
        ban_manager: IpBanManager = hass.http.app[KEY_BAN_MANAGER]
    except KeyError:
        _LOGGER.warning(
            "Can't find ban manager. ban_allowlist requires http.ip_ban_enabled"
            " to be True, so disabling."
        )
        return True

    _LOGGER.debug("Ban manager %s", ban_manager)
    allowlist: list[IPv4Network | IPv6Network] = [ip_network(ip) for ip in yaml_ips]

    _LOGGER.info("Setting allowlist with %s", [str(ip) for ip in allowlist])

    original_async_add_ban = IpBanManager.async_add_ban

    async def allowlist_async_add_ban(
        remote_addr: IPv4Address | IPv6Address,
    ) -> None:
        if _matching_network(remote_addr, allowlist) is not None:
            _LOGGER.info(
                "Not adding %s to ban list, as it's in the allowlist",
                remote_addr,
            )
            return
        _LOGGER.info("Banning IP %s", remote_addr)
        await original_async_add_ban(ban_manager, remote_addr)

    ban_manager.async_add_ban = allowlist_async_add_ban  # type: ignore[method-assign]
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

    if getattr(ban_manager, "_yaml_patched", False):
        _LOGGER.warning(
            "ban_allowlist is configured both in configuration.yaml and via the UI. "
            "Remove the 'ban_allowlist:' section from configuration.yaml — "
            "the UI config entry will not be activated while the YAML entry is present."
        )
        return True

    # Read IPs from options first, falling back to data.
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

    # -------------------------------------------------------------------------
    # Patch 1: process_wrong_login (module-level function in ban.py)
    #
    # This is the SOLE entry point that:
    #   - logs the "Login attempt or request with invalid authentication" WARNING
    #   - creates the persistent "http-login" notification
    #   - increments the failed-login counter
    #   - calls ban_manager.async_add_ban when the threshold is reached
    #     (which creates the "ip-ban" notification)
    #
    # Both ban_middleware and the log_invalid_auth decorator call it as
    #   await process_wrong_login(request)
    # which is a module-global lookup at call time, so replacing
    # ban_module.process_wrong_login redirects both callers.
    #
    # The function receives an aiohttp web.Request; the peer IP is request.remote.
    # -------------------------------------------------------------------------
    original_process_wrong_login: Any = getattr(ban_module, "process_wrong_login", None)

    if original_process_wrong_login is None:
        # Defensive: log all callables so we can identify the correct name if
        # HA renames this function in a future version.
        public_callables = [
            name
            for name in dir(ban_module)
            if not name.startswith("_") and callable(getattr(ban_module, name))
        ]
        _LOGGER.warning(
            "process_wrong_login not found in ban module. "
            "Notification suppression will NOT work. "
            "Available callables in ban module: %s",
            public_callables,
        )
    else:

        async def allowlist_process_wrong_login(request: Any) -> None:
            """Wrap process_wrong_login to skip allowlisted IPs entirely.

            Skipping means: no WARNING log, no http-login notification,
            no failed-login counter increment, no ban — the request is
            treated as if nothing went wrong from the ban system's perspective.
            """
            remote = getattr(request, "remote", None)
            if remote is not None:
                try:
                    remote_addr: IPv4Address | IPv6Address = ip_address(remote)
                    if _matching_network(remote_addr, allowlist) is not None:
                        _LOGGER.debug(
                            "Skipping process_wrong_login for allowlisted IP %s",
                            remote_addr,
                        )
                        return
                except (ValueError, TypeError):
                    pass
            await original_process_wrong_login(request)

        ban_module.process_wrong_login = allowlist_process_wrong_login
        _LOGGER.debug("Patched ban_module.process_wrong_login")

    # -------------------------------------------------------------------------
    # Patch 2: IpBanManager.async_add_ban (instance-level)
    #
    # Belt-and-suspenders: if an IP is in the allowlist but somehow reaches
    # async_add_ban anyway (e.g. via a future code path that bypasses
    # process_wrong_login), we still prevent the ban from being written.
    # -------------------------------------------------------------------------
    original_add_ban: Any = ban_manager.async_add_ban

    async def allowlist_async_add_ban(
        remote_addr: IPv4Address | IPv6Address,
    ) -> None:
        """Wrap async_add_ban to skip allowlisted IPs."""
        if _matching_network(remote_addr, allowlist) is not None:
            _LOGGER.info(
                "Not adding %s to ban list, as it's in the allowlist",
                remote_addr,
            )
            await _remove_from_ban_list(hass, str(remote_addr))
            return
        _LOGGER.info("Banning IP %s", remote_addr)
        await original_add_ban(remote_addr)

    ban_manager.async_add_ban = allowlist_async_add_ban  # type: ignore[method-assign]

    # -------------------------------------------------------------------------
    # Log handler: last-resort fallback
    #
    # If process_wrong_login is ever called via a reference captured before our
    # patch (e.g. a module that did `from ban import process_wrong_login`), the
    # WARNING will still be logged.  This handler catches that and cleans up.
    # -------------------------------------------------------------------------
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
                        "Log handler fallback: dismissing notification for "
                        "allowlisted IP %s",
                        ip_value,
                    )
                    hass.async_create_task(_clear_ban_notification(hass, str(ip_value)))
                    break

    log_handler = _BanLogHandler()
    logging.getLogger("homeassistant.components.http.ban").addHandler(log_handler)

    entry.runtime_data = BanAllowlistData(
        ban_manager=ban_manager,
        log_handler=log_handler,
        original_add_ban=original_add_ban,
        original_process_wrong_login=original_process_wrong_login,
    )

    await _scan_and_remove_whitelisted_bans(hass, allowlist)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Ban Allowlist for entry: %s", entry.title)

    data: BanAllowlistData | None = getattr(entry, "runtime_data", None)
    if data is None:
        return True

    try:
        data.ban_manager.async_add_ban = data.original_add_ban  # type: ignore[method-assign]
        _LOGGER.info("Restored original async_add_ban")

        if data.original_process_wrong_login is not None:
            ban_module.process_wrong_login = data.original_process_wrong_login
            _LOGGER.info("Restored original process_wrong_login")

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
            _LOGGER.error("Error removing IP %s from ip_bans.yaml: %s", ip_addr, err)

    await hass.async_add_executor_job(_do_remove)  # type: ignore[arg-type]


async def _clear_ban_notification(hass: HomeAssistant, ip_addr: str) -> None:
    """Dismiss Home Assistant ban notifications for a whitelisted IP.

    Used only by the log handler fallback path — process_wrong_login interception
    prevents notification creation entirely so this should rarely fire.
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
            _LOGGER.debug("Could not dismiss notification %s: %s", notification_id, err)


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

                network = _matching_network(ip_obj, allowlist)
                if network is not None:
                    del bans[banned_ip]
                    removed.append(banned_ip)
                    _LOGGER.info(
                        "Found allowlisted IP %s in ban list (matches %s), removing",
                        banned_ip,
                        network,
                    )

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

    removed_ips = await hass.async_add_executor_job(_do_scan)  # type: ignore[arg-type]
    for ip in removed_ips:
        await _clear_ban_notification(hass, ip)
