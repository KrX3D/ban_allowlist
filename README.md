# Ban Allowlist - Enhanced Version with UI Configuration

A Home Assistant custom integration that prevents specific IP addresses from being banned, with a user-friendly configuration interface.

## Features

✅ **UI-Based Configuration** - Manage whitelisted IPs through Home Assistant's interface  
✅ **Add/Remove IPs Easily** - No need to edit YAML files  
✅ **View Current Whitelist** - See all whitelisted IPs at a glance  
✅ **Automatic Notification Clearing** - Removes ban and failed-login notifications for whitelisted IPs  
✅ **Automatic Ban Removal** - Removes whitelisted IPs from ip_bans.yaml  
✅ **Auto-cleanup** - Deletes ip_bans.yaml when empty  
✅ **Comprehensive Logging** - Detailed logs for all actions  
✅ **CIDR Network Support** - Whitelist entire networks (e.g., 192.168.1.0/24)

## Installation

### Method 1: Manual Installation

1. Copy the entire `ban_allowlist` folder to your Home Assistant `custom_components` directory:
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

4. Search for "Ban Allowlist" and click to add it

### Method 2: HACS (If published)

1. Open HACS
2. Go to "Integrations"
3. Click the three dots in the top right → Custom repositories
4. Add this repository URL
5. Install "Ban Allowlist"
6. Restart Home Assistant

## File Structure

```
custom_components/ban_allowlist/
├── __init__.py           # Main integration logic
├── config_flow.py        # UI configuration flow
├── const.py              # Constants
├── manifest.json         # Integration metadata
├── strings.json          # UI strings
└── translations/
    ├── de.json           # German translations
    └── en.json           # English translations
```

## Configuration

### Initial Setup

1. Go to **Settings → Devices & Services**
2. Click **Add Integration**
3. Search for "Ban Allowlist"
4. Enter your IP addresses (comma-separated)
   - Example: `192.168.1.100, 10.0.0.0/24, 172.16.0.1`

### Managing Whitelisted IPs

1. Go to **Settings → Devices & Services**
2. Find "Ban Allowlist" in your integrations
3. Click **Configure**
4. Choose an action:
   - **Add new IP address** - Add a single IP or network
   - **Remove IP address** - Select from a list of current IPs to remove
   - **Done** - Return to Home Assistant

### Supported Formats

- **Single IP**: `192.168.1.100`
- **CIDR Network**: `192.168.1.0/24`
- **IPv6**: `2001:db8::1`
- **IPv6 Network**: `2001:db8::/32`

## How It Works

1. **Prevention**: When an IP in the allowlist tries to connect with invalid credentials, the integration prevents the ban
2. **Login Failed Tracking**: Failed-login tracking is skipped for allowlisted IPs
3. **Cleanup**: If the IP was previously banned, it's removed from `ip_bans.yaml`
4. **Notification**: Ban and failed-login Home Assistant notifications for that IP are automatically dismissed
5. **Log-based Cleanup**: A log handler watches HTTP ban warnings and dismisses allowlisted login-failed notifications
6. **File Management**: If `ip_bans.yaml` becomes empty, it's automatically deleted

## Logging

The integration provides detailed logging at the INFO level:

```
INFO: IP 192.168.10.17 is in allowlist (matches 192.168.10.0/24), preventing ban
INFO: Removed whitelisted IP 192.168.10.17 from ip_bans.yaml
INFO: Dismissed notification 'ip-ban' for whitelisted IP 192.168.10.17
INFO: Dismissed notification 'http-login' for whitelisted IP 192.168.10.17
INFO: ip_bans.yaml is empty after removing 192.168.10.17, file deleted
```

To see these logs:
1. Go to **Settings → System → Logs**
2. Search for "ban_allowlist"

Or view the full log file at `config/home-assistant.log`

## Example Use Cases

### Home Network Protection
Whitelist your entire home network to prevent accidental bans:
```
192.168.1.0/24
```

### Multiple Locations
Whitelist multiple static IPs:
```
192.168.1.100
203.0.113.50
198.51.100.75
```

### Internal Services
Whitelist internal services that authenticate frequently:
```
10.0.0.50
10.0.0.51
```

## Troubleshooting

### Integration Not Appearing
- Ensure all files are in the correct directory
- Restart Home Assistant completely
- Check logs for any errors during startup

### IPs Still Getting Banned
- Verify the IP format is correct
- Check that the IP is actually in your whitelist (Configure → view current IPs)
- Review logs to see if the integration is catching the ban attempt

### Notifications Not Clearing
- The integration dismisses the `ip-ban` and `http-login` persistent notifications
- Check logs for any errors when attempting to clear notifications

## Migration from YAML Configuration

If you were using the old YAML-based configuration:

1. Note your current whitelisted IPs from `configuration.yaml`
2. Remove the old `ban_allowlist:` section from `configuration.yaml`
3. Install this new version
4. Add your IPs through the UI configuration

## Important Notes

⚠️ **GIANT HACK WARNING**: This integration modifies Home Assistant's internal HTTP ban middleware. While it has been tested, use at your own risk.

⚠️ **Security**: Only whitelist IPs you trust. Whitelisted IPs can make unlimited login attempts without being banned.

⚠️ **Requirement**: Home Assistant's HTTP ban system must be enabled (`http.ip_ban_enabled: true`).

## Support

For issues, questions, or contributions, please visit the GitHub repository:
https://github.com/KrX3D/ban_allowlist

## License

Same license as the original ban_allowlist project.
