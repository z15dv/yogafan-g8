# Windows and physical-laptop validation checklist

Automated tests use only `MockEcBackend`. Perform this checklist later, with
administrator consent, only on a Lenovo Yoga Pro 9 14IRP8 Gen 8 whose SMBIOS
machine type is exactly **83BU**. Save important work first. Review
`SAFETY_SERVICE.md` and the source before allowing EC access.

Never deliberately overheat the laptop, force a BSOD, disable Defender, create
an antivirus exclusion, or run the scripts in `research/` merely to complete
this checklist.

## Prerequisites

- Confirm Windows reports machine type 83BU and the app accepts it.
- Run the complete mock suite and packaged `--mock-self-test` first.
- Start the separately elevated safety-server development process and verify
  that the GUI reports **Safety Service: Active**.
- Keep Windows Task Manager and Event Viewer available. Know how to stop a
  workload immediately.

## Checklist

1. **Initial Auto Restore** — Start the safety server and GUI. Confirm the first
   hardware action is acknowledged Lenovo Auto and no saved Manual/Curve mode is
   reactivated.
2. **Manual endpoints** — In a cool, idle state, request 1800 RPM and later 5300
   RPM. Confirm each fan target and tachometer response independently. Do not
   test higher values.
3. **Manual pickup** — Set a high Manual target, return to Auto, let actual RPM
   fall, and enter Manual again. Confirm sliders initialize from rounded fresh
   actual RPM rather than the old high target.
4. **Curve transitions** — Exercise Quiet, Balanced, and Performance at ordinary
   temperatures. Confirm increases are immediate and reductions wait about ten
   seconds without command chatter.
5. **Separate CPU/GPU demand** — First validate with injected/mock readings.
   During ordinary workloads, confirm a warmer component can raise the shared
   target independently. Do not manufacture a hot condition.
6. **Measured animation** — While changing a Manual target, confirm rotor speed
   does not jump. It should accelerate only as each fan's tachometer increases;
   Fan 1 and Fan 2 may visibly differ.
7. **Auto 0 RPM** — When Lenovo firmware naturally stops a fan, confirm Actual
   reads 0, the rotor stops, blur disappears, and the cyan actual ring is empty.
8. **Fn+Q / FCMO** — Cycle Lenovo modes. Confirm Intelligent, Performance,
   Battery Saving, APF, or AQT updates are informational and never change the
   YogaFan G8 ownership state by themselves.
9. **GPU fallback** — Observe EC-source operation first. If the EC sensor becomes
   unavailable naturally, verify throttled NVML fallback/source tooltip, sleep
   behavior, bounded grace, and conservative Auto Restore after an unexplained
   loss. Check that idle battery behavior is not degraded.
10. **Tray monitoring** — With **Run in background** enabled, click X. Confirm
    the window hides, telemetry/watchdog remain active, fan animations stop
    consuming frames while hidden, and tray Open restores the window. Disable
    **Run in background**, click X again, and confirm the app exits instead of
    displaying the still-running tray notification.
11. **Normal Exit** — From Manual and again from Fan Curves, choose tray Exit.
    Confirm the server logs an acknowledged Auto Restore before the GUI lease is
    released.
12. **Forced GUI termination** — With a moderate, safe Manual target, end only
    the GUI process in Task Manager. Confirm the independent server remains and
    requests acknowledged Lenovo Auto within the configured timeout.
13. **Safety-server interruption/recovery** — Test only after reviewing risks.
    Confirm the GUI disables custom control when IPC disappears and a restarted
    server begins with Auto. This does not prove hardware failsafe behavior.
14. **Windows shutdown/restart** — Validate bounded Auto Restore logging during
    sign-out, restart, and shutdown once native SCM hosting exists. This item is
    currently blocked by incomplete ServiceMain/preshutdown integration.
15. **Sleep/hibernate/resume** — Confirm planned suspend restores Auto and resume
    starts in Auto without reactivating saved custom control. Repeat for both
    sleep and hibernate only when the service power-handler work is complete.
16. **Thermal protection** — Defaults are CPU 90/95 °C warning/critical and GPU
    83/85 °C. Confirm the settings state that the action is Lenovo Auto Restore,
    not a Windows shutdown. Use simulated readings to verify custom bounded
    limits, the one-time risk confirmation, the persistent disabled warning,
    critical Auto Restore, latch, and rejected queued commands. During real use
    only observe naturally occurring thresholds; do not deliberately heat the
    laptop.
17. **CSV logging** — Start/stop logging, export a CSV, and confirm timestamps,
    actual/target separation, GPU source/validity, mode/profile, events, and
    empty (not zero) unavailable GPU values.
18. **Compatibility rejection** — On a mocked/controlled identity test, confirm
    a non-83BU device shows an unsupported state and makes no EC write attempt.
19. **Ring/marker alignment** — With matching actual and target values at 1800,
    2800, 3700, 4700, and 5300 RPM, confirm the cyan ring endpoint and white
    target marker align. At 0 RPM the ring must remain empty.

Record the app version/commit, BIOS version, Windows build, result, timings, and
any EC acknowledgement failure for every test. Do not call the Safety Service
reliable until the blocked Windows items and real-hardware results are reviewed.

## Research scripts

Files under `research/` and the user-owned root-level probe scripts are manual
hardware experiments. Automated tests and `build.ps1` do not invoke them. They
must never be treated as routine validation commands.
