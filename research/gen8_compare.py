import fan_control
import time

CMD = 0x6C
DATA = 0x68

w = fan_control.TransientWinRing0()

KNOWN = {
    0x06: "FANS fan1 RPM",
    0x20: "FCMO FnQ mode",
    0x39: "FCST",
    0xA9: "THRT",
    0xAE: "TLVL",
    0xAF: "NVHC",
    0xB0: "CPUT",
    0xB1: "CPUS",
    0xB2: "PCHS",
    0xB3: "GPUS",
    0xB4: "GPUT",
    0xB5: "SSDS",
    0xB6: "PCHT",
    0xFE: "FA2S fan2 RPM",
}


def status():
    return w.read_io_port_byte(CMD)


def wait_ibf_clear(timeout=0.5):
    end = time.perf_counter() + timeout

    while time.perf_counter() < end:
        if not (status() & 0x02):
            return

        time.sleep(0.0005)

    raise TimeoutError("IBF Timeout")


def wait_obf_set(timeout=0.5):
    end = time.perf_counter() + timeout

    while time.perf_counter() < end:
        if status() & 0x01:
            return

        time.sleep(0.0005)

    raise TimeoutError("OBF Timeout")


def drain():
    n = 0

    while status() & 0x01:
        w.read_io_port_byte(DATA)

        n += 1

        if n > 16:
            raise RuntimeError(
                "Output Buffer konnte nicht geleert werden"
            )

        time.sleep(0.0005)


def read_ec(offset):
    wait_ibf_clear()
    drain()

    # Lenovo LRAM READ
    w.write_io_port_byte(CMD, 0x7E)

    wait_ibf_clear()
    w.write_io_port_byte(DATA, 0x04)

    wait_ibf_clear()
    w.write_io_port_byte(DATA, offset)

    wait_obf_set()

    return w.read_io_port_byte(DATA)


def snapshot():
    data = {}

    for offset in range(0x100):
        try:
            data[offset] = read_ec(offset)

        except Exception as e:
            print(f"Fehler bei 0x{offset:02X}: {e}")
            data[offset] = None

        time.sleep(0.004)

    return data


def show_status(data, title):
    print()
    print(title)
    print("=" * 65)

    for offset in [
        0x06,
        0xFE,
        0x20,
        0x39,
        0xB0,
        0xB4,
    ]:
        v = data.get(offset)

        if v is None:
            continue

        name = KNOWN.get(offset, "")

        if offset in (0x06, 0xFE):
            print(
                f"0x{offset:02X} {name:<18} "
                f"= {v:3d}   ~{v * 100} RPM"
            )

        elif offset in (0xB0, 0xB4):
            print(
                f"0x{offset:02X} {name:<18} "
                f"= {v:3d} °C"
            )

        else:
            print(
                f"0x{offset:02X} {name:<18} "
                f"= 0x{v:02X} ({v})"
            )


try:
    print("=" * 65)
    print("Yoga Pro 9 Gen8 EC Differential Scan")
    print("=" * 65)
    print()
    print("Es werden ausschließlich LRAM-READ-Befehle verwendet.")
    print("Es werden keine EC-RAM-Werte verändert.")
    print()

    w.open()

    input(
        "1. Laptop normal/idle lassen.\n"
        "Am besten Intelligent Mode.\n"
        "Wenn die Lüfter ruhig sind ENTER drücken..."
    )

    print()
    print("Lese IDLE-Snapshot...")
    idle = snapshot()

    show_status(idle, "IDLE")

    print()
    print("Jetzt Last erzeugen.")
    print()
    print("Zum Beispiel:")
    print("  - Spiel")
    print("  - Cinebench")
    print("  - andere CPU/GPU Last")
    print()
    print("Warte, bis die Lüfter DEUTLICH schneller laufen.")
    print()

    input(
        "Wenn die Lüfter deutlich schneller sind ENTER drücken..."
    )

    print()
    print("Lese HIGH-FAN-Snapshot...")
    high = snapshot()

    show_status(high, "HIGH FAN")

    print()
    print("=" * 65)
    print("GEÄNDERTE EC-BYTES")
    print("=" * 65)

    changes = []

    for offset in range(0x100):
        a = idle.get(offset)
        b = high.get(offset)

        if a is None or b is None:
            continue

        if a != b:
            changes.append((offset, a, b))

    for offset, old, new in changes:
        name = KNOWN.get(offset, "")

        extra = ""

        if offset in (0x06, 0xFE):
            extra = (
                f"   ~{old * 100} -> "
                f"{new * 100} RPM"
            )

        elif offset in (0xB0, 0xB4):
            extra = "   Temperatur"

        print(
            f"0x{offset:02X}: "
            f"0x{old:02X} ({old:3d}) -> "
            f"0x{new:02X} ({new:3d}) "
            f"{name}{extra}"
        )

    print()
    print(f"Insgesamt geändert: {len(changes)} Bytes")

    print()
    print("WICHTIGE KANDIDATEN")
    print("=" * 65)

    for offset, old, new in changes:
        if offset not in (
            0x06,
            0xFE,
            0x20,
            0xB0,
            0xB4,
        ):
            print(
                f"0x{offset:02X}: "
                f"{old} -> {new} "
                f"{KNOWN.get(offset, '')}"
            )

finally:
    try:
        w.close()
    except Exception:
        pass

    print()
    print("WinRing0 geschlossen.")