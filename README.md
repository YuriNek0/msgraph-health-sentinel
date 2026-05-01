# MSGraph Health Sentinel

This utility polls a curated set of Microsoft Graph endpoints on a schedule and
sends alert emails through Microsoft Graph when in-scope endpoints fail, or when out-of-scope endpoints succeed (while including all in-scope and out-of-scope details).

## Highlights

- Credentials are no longer hard-coded; all runtime settings are loaded from a JSON config file.
- Polling and reporting are both performed via Microsoft Graph.
- Error handling is centralized and includes explicit request failures, auth failures,
  and connectivity checks.
- Endpoint list is structured and easy to extend.

## Setup

1. Copy the sample configuration and provide tenant, app, and user credentials.

   ```bash
   cp config.example.json config.json
   ```

2. Install dependencies:

    ```bash
    pip install -r requirements.txt
    ```

3. Run one pass first, then keep it running in loop mode.

   ```bash
   python fetch.py --once
   python fetch.py
   ```

You can also point to an alternate config file:

```bash
MSGRAPH_HEALTH_SENTINEL_CONFIG_PATH=/path/to/custom-config.json python fetch.py
```

### Required app registration permissions

Use `User.Read`, `Mail.Send`, and the permissions listed in the endpoint table below.
`Teamwork`, `Security`, and Intune endpoints in the list often need higher admin
consent.

## Run options

- `--config`: path to an alternate JSON config file.
- `--once`: perform one polling cycle and exit.
- `--oneshot`: perform one polling cycle and skip the `/tmp/msgraph-health-sentinel.lock` file.

## Configuration keys

| Key | Purpose |
| --- | --- |
| `tenant_id` | Microsoft Entra tenant ID |
| `client_id` | App registration client ID |
| `client_secret` | App client secret |
| `username` | Polling user principal name |
| `password` | Polling user password |
| `redirect_uri` | OAuth redirect URI registered in the app |
| `alert_recipient` | Email address or list of addresses for error alerts |
| `scopes` | OAuth delegated scopes requested during token acquisition |
| `required_success_count` | Minimum successful Graph calls per cycle |
| `error_threshold` | Kept for compatibility; alerting now sends on in-scope failures and also when out-of-scope endpoints succeed (subject to cooldown) |
| `poll_delay_seconds.min` / `.max` | Randomized sleep interval in seconds |
| `request_timeout_seconds` | Graph request timeout (seconds) |
| `connectivity_host`, `connectivity_port`, `connectivity_timeout_seconds` | Network reachability check |
| `email_cooldown_minutes` | Minimum minutes between two alert emails (minimum enforced: 720) |
| `token_refresh_window_seconds` | Buffer before token expiry for re-auth |

## Polling endpoints (latest Microsoft Graph paths used)

| Endpoint | Method | Required permissions |
| --- | --- | --- |
| `https://graph.microsoft.com/v1.0/me` | GET | `User.Read` |
| `https://graph.microsoft.com/v1.0/me/messages?$top=5&$select=id,subject,receivedDateTime` | GET | `Mail.Read` |
| `https://graph.microsoft.com/v1.0/me/mailFolders` | GET | `Mail.Read` |
| `https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messageRules` | GET | `Mail.Read` |
| `https://graph.microsoft.com/v1.0/me/outlook/masterCategories` | GET | `Mail.Read` |
| `https://graph.microsoft.com/v1.0/me/drive/root` | GET | `Files.Read` |
| `https://graph.microsoft.com/v1.0/me/drive/root/children?$top=5` | GET | `Files.Read` |
| `https://graph.microsoft.com/v1.0/me/drive/recent?$top=5` | GET | `Files.Read` |
| `https://graph.microsoft.com/v1.0/me/insights/trending` | GET | `People.Read.All` |
| `https://graph.microsoft.com/v1.0/me/insights/shared?$top=5` | GET | `People.Read.All` |
| `https://graph.microsoft.com/v1.0/me/insights/used?$top=5` | GET | `People.Read.All` |
| `https://graph.microsoft.com/v1.0/me/calendars` | GET | `Calendars.Read` |
| `https://graph.microsoft.com/v1.0/me/events?$top=5&$select=id,subject,start,end` | GET | `Calendars.Read` |
| `https://graph.microsoft.com/v1.0/me/contacts?$top=5` | GET | `Contacts.Read` |
| `https://graph.microsoft.com/v1.0/me/mailboxSettings` | GET | `MailboxSettings.Read` |
| `https://graph.microsoft.com/v1.0/me/presence` | GET | `Presence.Read` |
| `https://graph.microsoft.com/v1.0/me/joinedTeams` | GET | `Team.ReadBasic.All` |
| `https://graph.microsoft.com/v1.0/me/onenote/notebooks` | GET | `Notes.Read` |
| `https://graph.microsoft.com/v1.0/me/onenote/pages?$top=5` | GET | `Notes.Read` |
| `https://graph.microsoft.com/v1.0/me/onenote/sections?$top=5` | GET | `Notes.Read` |
| `https://graph.microsoft.com/v1.0/me/todo/lists` | GET | `Tasks.Read` |
| `https://graph.microsoft.com/v1.0/sites/root/lists?$top=5` | GET | `Sites.Read.All` |
| `https://graph.microsoft.com/v1.0/me/teamwork/installedApps` | GET | `TeamworkAppInstallation.ReadForUser` |
| `https://graph.microsoft.com/v1.0/me/activities/recent?$top=5` | GET | `UserActivity.ReadWrite.CreatedByApp` |
| `https://graph.microsoft.com/v1.0/users?$top=5&$select=id,displayName` | GET | `User.Read.All` |
| `https://graph.microsoft.com/v1.0/devices?$top=5&$select=id,displayName` | GET | `Device.Read.All` |
| `https://graph.microsoft.com/v1.0/identityProtection/riskyUsers?$top=5&$filter=riskLevel eq 'high'` | GET | `IdentityRiskyUser.Read.All` |
| `https://graph.microsoft.com/v1.0/security/alerts?$top=5&$select=id,createdDateTime,status` | GET | `SecurityEvents.Read.All` |
| `https://graph.microsoft.com/v1.0/deviceManagement/managedDevices?$top=5&$select=id,deviceName` | GET | `DeviceManagementManagedDevices.Read.All` |
| `https://graph.microsoft.com/v1.0/applications?$top=5&$select=id,displayName` | GET | `Application.Read.All` |
| `https://graph.microsoft.com/v1.0/servicePrincipals?$top=5&$select=id,displayName` | GET | `Application.Read.All` |
| `https://graph.microsoft.com/v1.0/groups?$top=5&$select=id,displayName,description` | GET | `Group.Read.All` |
| `https://graph.microsoft.com/v1.0/directoryRoles` | GET | `Directory.Read.All` |
| `https://graph.microsoft.com/v1.0/me/people?$top=5` | GET | `People.Read` |
| `https://graph.microsoft.com/v1.0/sites/root` | GET | `Sites.Read.All` |
| `https://graph.microsoft.com/v1.0/drives?$top=5&$select=id,name` | GET | `Files.Read.All` |
| `https://graph.microsoft.com/v1.0/teams?$top=5&$select=id,displayName` | GET | `Group.Read.All` |
| `https://graph.microsoft.com/v1.0/me/chats?$top=5&$select=id,topic` | GET | `Chat.Read` |
| `https://graph.microsoft.com/v1.0/me/planner/plans` | GET | `Tasks.Read.All` |
| `https://graph.microsoft.com/v1.0/me/planner/tasks` | GET | `Tasks.Read.All` |
| `https://graph.microsoft.com/v1.0/auditLogs/signIns?$top=5&$select=id,userPrincipalName,createdDateTime` | GET | `AuditLog.Read.All` |
| `https://graph.microsoft.com/v1.0/auditLogs/directoryAudits?$top=5&$select=id,activityDisplayName,activityDateTime` | GET | `AuditLog.Read.All` |
| `https://graph.microsoft.com/v1.0/deviceManagement/deviceConfigurations?$top=5&$select=id,displayName` | GET | `DeviceManagementConfiguration.Read.All` |
| `https://graph.microsoft.com/v1.0/reports/getMailboxUsageMailboxCounts(period='D30')` | GET | `Reports.Read.All` |
| `https://graph.microsoft.com/v1.0/reports/getOffice365ActiveUserCounts(period='D30')` | GET | `Reports.Read.All` |

## Error alert (Graph mail)

Failures are reported using:

- `POST https://graph.microsoft.com/v1.0/me/sendMail`

Required permission for this endpoint:

- `Mail.Send`

## Nix + Home Manager

This repo now ships a flake package and a Home Manager module that can run the
service as a user unit with automatic restart on failure.

1. Add the flake as an input in your Home Manager flake:

   ```nix
   inputs.msgraph-health-sentinel.url = "github:<you>/msgraph-health-sentinel";
   ```

2. Import the module and configure the service:

   ```nix
   {
     imports = [
       inputs.msgraph-health-sentinel.homeManagerModules.default
     ];

     services.msgraph-health-sentinel = {
       enable = true;
       configFile = "/home/<user>/.config/msgraph-health-sentinel/config.json";
       # optional:
       # extraArgs = [ "--once" ];
     };
   }
   ```

3. Apply your Home Manager config:

   ```bash
   home-manager switch
   ```

Service behavior:

- Unit name: `msgraph-health-sentinel.service` (user service)
- Restart policy: `Restart=always`
- Restart delay: `RestartSec=10s`
- Restart limit: max 3 restarts in 10 minutes (`StartLimitBurst=3`, `StartLimitIntervalSec=10min`)

Important:

- `services.msgraph-health-sentinel.configFile` is required when the service is enabled.
- Keep the config file out of the Nix store because it contains credentials.

## Notes

- Keep credentials out of source control. Place only `config.example.json` in version
  control and create `config.json` locally.
- Some endpoints in this list require administrative consent and may return 401/403
  when used with a low-privilege account.
