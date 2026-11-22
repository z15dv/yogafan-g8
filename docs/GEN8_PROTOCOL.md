# Confirmed Gen 8 EC protocol

This document records only behavior already confirmed on a **Lenovo Yoga Pro 9
14IRP8 Gen 8, machine type 83BU**. It is not a compatibility claim for any
other laptop.

## Ports and status bits

| Purpose | Value |
|---|---:|
| Command/status port | `0x6C` |
| Data port | `0x68` |
| Input buffer full (IBF) | `status & 0x02` |
| Output buffer full (OBF) | `status & 0x01` |

The Gen 9 ports `0x5C0` and `0x5C4` are not used. All waits are bounded by a
finite timeout and access is serialized by one lock.

## LRAM read

1. Wait for IBF clear.
2. Drain stale OBF bytes (bounded).
3. Write `0x7E` to `0x6C`.
4. Wait for IBF clear; write the high address byte to `0x68`.
5. Wait for IBF clear; write the low address byte to `0x68`.
6. Wait for IBF clear, then wait for OBF set.
7. Read the response byte from `0x68`.

| Address | Meaning | Conversion |
|---|---|---|
| `0x0406` | Fan 1 tachometer | raw × 100 ≈ RPM |
| `0x04FE` | Fan 2 tachometer | raw × 100 ≈ RPM |
| `0x04B0` | CPU temperature | °C |
| `0x04B4` | GPU temperature | °C |
| `0x0420` | Lenovo FCMO/Fn+Q mode | read-only mapping below |

FCMO values: `0` Intelligent, `1` Performance, `2` Battery Saving, `3` APF
(Auto Performance), and `4` AQT (Auto Quiet). Any other value is displayed as
`Unknown (X)`. FCMO is informational and read-only: YogaFan G8 never writes it
or replaces Lenovo's Fn+Q behavior.

## Fan commands

Every command writes `0xEF` to `0x6C`, then the subcommand and argument to
`0x68`, using the same IBF/OBF sequencing as the confirmed experiments.

| Operation | Bytes |
|---|---|
| Set Fan 1 | `EF 61 value` |
| Set Fan 2 | `EF 62 value` |
| Read Fan 1 target | `EF 63 01` |
| Read Fan 2 target | `EF 63 02` |
| Restore Lenovo Auto | `EF 63 03` |

Set and restore operations require response `0xAC`. Manual commands accept
only raw values `18..53`, corresponding to approximately 1800–5300 RPM in
100-RPM steps. Manual 0 RPM is invalid, while an actual tachometer reading of
0 RPM remains valid in Lenovo Auto. There is no Fan Off command.

The 5300-RPM software ceiling is conservative, not a Lenovo-certified absolute
limit. Experimental requests at 5700 RPM worked briefly but the measured fans
slowed despite an unchanged request; 5800 RPM also did not hold consistently.
YogaFan G8 therefore does not expose those experimental speeds.

The `EF 63 01/02` query observed during research appears to mirror actual fan
speed. It is not presented as a confirmed independent target-RPM sensor.
