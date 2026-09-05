# Ban Allowlist

A Home Assistant custom integration that prevents specific IP addresses and networks from being banned, with a user-friendly UI configuration interface.

## Features

- **UI-Based Configuration** — Manage allowlisted IPs through Home Assistant's Settings interface, no YAML editing required
- **Add / Remove IPs Easily** — Add or remove individual IPs and CIDR networks at any time via the Configure button
- **View Current Allowlist** — See all allowlisted entries at a glance in the options dialog
- **Prevents Bans at the Source** — Intercepts Home Assistant's `process_wrong_login` function before the ban, notification, and failed-login counter are created
- **Automatic Ban Removal** — Scans and removes any allowlisted IPs from `ip_bans.yaml` on startup
- **Auto-cleanup** — Deletes `ip_bans.yaml` automatically when it becomes empty
- **IPv4 and IPv6 Support** — Single addresses, CIDR networks, and IPv6 addresses/networks are all supported
- **Comprehensive Logging** — Detailed debug and info logs for all actions

## Requirements

- Home Assistant 2025.12.0 or newer
- `http.ip_ban_enabled: true` in your `configuration.yaml`

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations**
3. Click the three dots in the top right → **Custom repositories**
4. Add `https://github.com/KrX3D/ban_allowlist` with category **Integration**
5. Install **IP Ban Allowlist**
6. Restart Home Assistant

### Manual

1. Copy the `ban_allowlist` folder into your `config/custom_components/` directory:
   ```
   config/
   └── custom_components/
       └── ban_allowlist/
           ├── __init__.py
           ├── config_flow.py
           ├── const.py
           ├── manifest.json
           ├── strings.json
           └── translations/
               ├── de.json
               └── en.json
   ```
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration**
4. Search for **Ban Allowlist** and follow the setup dialog

## Configuration

### Initial Setup

1. Go to **Settings → Devices & Services**
2. Click **Add Integration** and search for **Ban Allowlist**
3. Enter your IP addresses or networks, comma-separated
   - Example: `192.168.1.100, 10.0.0.0/24, 2001:db8::1, 2001:db8::/32`
4. Click **Submit**

### Managing the Allowlist

1. Go to **Settings → Devices & Services**
2. Find **IP Ban Allowlist** and click **Configure**
3. Choose an action:
   - **Add new IP address** — Enter a single IP or CIDR network
   - **Remove IP address** — Select an entry from the current list to remove
   - **Done** — Save changes and close (changes are only committed when you select Done)

### Supported Formats

| Format | Example |
|--------|---------|
| Single IPv4 | `192.168.1.100` |
| IPv4 CIDR network | `192.168.1.0/24` |
| Single IPv6 | `2001:db8::1` |
| IPv6 CIDR network | `2001:db8::/32` |

## How It Works

The integration patches Home Assistant's internal `process_wrong_login` function in `homeassistant/components/http/ban.py`. This is the single function responsible for:

- Logging the "Login attempt or request with invalid authentication" warning
- Creating the `http-login` persistent notification
- Incrementing the failed-login counter
- Calling `async_add_ban` (and creating the `ip-ban` notification) when the threshold is reached

By wrapping this function, allowlisted IPs are silently skipped **before** any of those side effects occur — no notification is created, no counter is incremented, and no ban is written.

A second patch on `IpBanManager.async_add_ban` acts as a belt-and-suspenders guard in case any future HA code path bypasses `process_wrong_login`.

### Startup Window

The integration cannot patch `process_wrong_login` until HA loads it during startup. If an allowlisted device sends a bad authentication request in the few seconds between the HTTP server starting and the integration loading, that single event will go through unpatched. This is a fundamental limitation of the approach and is not harmful in practice — no ban will be written until the login threshold is reached, and the integration will be fully active within seconds.

### All Changes are Temporary

All patches are applied in memory at runtime and fully restored when the integration is unloaded or restarted. No HA core files are modified on disk.

## Logging

Enable debug logging to see detailed output:

```yaml
logger:
  logs:
    custom_components.ban_allowlist: debug
```

Example log output when working correctly:

```
DEBUG  Setting up Ban Allowlist for entry: Ban Allowlist
INFO   Ban Allowlist initialized with 2 networks: ['192.168.1.0/24', '10.0.0.50/32']
DEBUG  Patched ban_module.process_wrong_login
INFO   Scanning existing bans for allowlisted IPs...
DEBUG  Skipping process_wrong_login for allowlisted IP 192.168.1.55
DEBUG  Skipping process_wrong_login for allowlisted IP 192.168.1.55
```

Logs are visible at **Settings → System → Logs** — search for `ban_allowlist`.

## Troubleshooting

### Integration not appearing in the UI
- Ensure all files are in the correct `custom_components/ban_allowlist/` directory
- Restart Home Assistant completely
- Check the logs for errors during startup

### IPs still getting banned
- Verify `http.ip_ban_enabled: true` is set — the integration requires the ban system to be active
- Confirm the IP is in your allowlist (Settings → Devices & Services → IP Ban Allowlist → Configure)
- Check the logs for `Patched ban_module.process_wrong_login` — if this line is missing, the patch did not apply
- A single ban notification at startup is normal (see Startup Window above)

### Seeing a YAML conflict warning
If you see:
```
ban_allowlist has ip_addresses in configuration.yaml AND is configured via the UI
```
Remove the `ban_allowlist:` section from `configuration.yaml`. The UI config entry manages everything — the YAML section is ignored when a UI entry exists.

## Migration from YAML Configuration

If you previously used the YAML-based configuration:

1. Note your current allowlisted IPs from `configuration.yaml`
2. Add the integration via **Settings → Devices & Services → Add Integration**
3. Enter your IPs in the setup dialog
4. Remove the `ban_allowlist:` section from `configuration.yaml`
5. Restart Home Assistant

## Important Notes

> ⚠️ **Hack Warning**: This integration modifies Home Assistant's internal HTTP ban middleware at runtime. It has been tested against HA 2025.12.0+ but may break if HA significantly refactors `homeassistant/components/http/ban.py`. Use at your own risk.

> ⚠️ **Security**: Only allowlist IPs you trust. Allowlisted IPs can make unlimited failed authentication attempts without being banned or notified.

> ⚠️ **Requirement**: `http.ip_ban_enabled: true` must be set in `configuration.yaml`.

## Support

For issues, questions, or contributions: https://github.com/KrX3D/ban_allowlist

## License

GNU Affero General Public License v3 — see [LICENSE](LICENSE) for details.
