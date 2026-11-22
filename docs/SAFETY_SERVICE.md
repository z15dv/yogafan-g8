# Safety Service architecture and current boundary

## What is implemented

`services/safety_service.py` contains a process-independent safety core and a
Windows named-pipe transport. The server is the sole owner of `FanController`
and therefore the only process that can open the EC backend. It provides only a
small validated command allow-list; arbitrary port or LRAM writes are not
exposed.

An authenticated GUI receives one exclusive lease. The GUI sends heartbeats at
approximately two-second intervals. If the IPC connection is lost or no valid
heartbeat arrives for more than six seconds, the server requests Lenovo Auto,
checks for the required `0xAC` acknowledgement, records success or failure, and
invalidates the lease. A new client session also begins with a confirmed Auto
Restore.

Mock tests cover valid heartbeats, timeout recovery, simulated GUI termination,
duplicate-client rejection, reconnect startup state, failed acknowledgement,
and bounded shutdown recovery. They never load WinRing0.

## Packaged first-run setup

Double-click `YogaFanG8.exe`. If the independent helper is not ready, the app
shows a **Set up and start** action. Only after that button is clicked does
Windows show UAC. Once approved, an elevated copy of the same packaged binary:

1. checks for the exact supported 83BU machine type;
2. creates the random key only when it does not already exist;
3. restricts the key and every pipe instance to SYSTEM, Administrators, and the
   launching user, with an explicit medium-integrity boundary for the normal GUI;
4. starts the named-pipe server in a separate hidden process;
5. begins from a confirmed Lenovo Auto request before accepting control.

The GUI waits for both valid key material and the named pipe. UAC cancellation,
unsupported hardware, an invalid existing key, startup failure, or a timeout
all leave the GUI fail-closed. The normal GUI never takes direct EC ownership.

The helper is not registered for persistence. It normally remains available
after the GUI exits, but a Windows restart requires starting it again. This is
intentional until native SCM hosting and its lifecycle handling are validated.

## Manual development setup

No service, key, driver, or persistence is installed automatically. On the
confirmed 83BU laptop only, review the source and then use an elevated
PowerShell:

```powershell
python src\main.py --provision-service-key
python src\main.py --safety-service
```

Leave that second process running and launch the GUI from a separate, ordinary
PowerShell:

```powershell
python src\main.py
```

The key is stored at `%PROGRAMDATA%\YogaFan G8\service.auth`. Provisioning
refuses to overwrite an existing key. Authentication uses a random 48-byte
secret; there is no default or hard-coded credential.

## Portable helper design

YogaFan G8 starts the elevated helper on demand for the current Windows session.
It does not install a permanent Windows service, configure automatic startup, or
register recovery tasks. Do not register the executable manually with `sc.exe`.
When the helper or authentication key is unavailable, the normal GUI remains
fail-closed and does not fall back to direct hardware access.

## Unavoidable limits

A user-mode GUI and Windows service cannot run during a BSOD, sudden power loss,
EC/firmware failure, or some forced kernel shutdowns. No evidence currently
shows that Lenovo's EC releases this manual mode automatically when software
communication stops. The design reduces common process-failure risk; it is not
an unconditional hardware failsafe.
