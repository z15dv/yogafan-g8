import fan_control
import time

CMD_PORT  = 0x6C
DATA_PORT = 0x68

REGS = {
    "FANS": (0x04, 0x06),
    "FCMO": (0x04, 0x20),
    "FCST": (0x04, 0x39),
    "CPUT": (0x04, 0xB0),
    "GPUT": (0x04, 0xB4),
    "FA2S": (0x04, 0xFE),
}

w = fan_control.TransientWinRing0()


def status():
    return w.read_io_port_byte(CMD_PORT)


def wait_ibf_clear(timeout=0.5):
    end = time.perf_counter() + timeout

    while time.perf_counter() < end:
        if (status() & 0x02) == 0:
            return True

        time.sleep(0.0005)

    return False


def wait_obf_set(timeout=0.5):
    end = time.perf_counter() + timeout

    while time.perf_counter() < end:
        if status() & 0x01:
            return True

        time.sleep(0.0005)

    return False


def drain_output():
    count = 0

    while status() & 0x01:
        w.read_io_port_byte(DATA_PORT)

        count += 1

        if count > 32:
            raise RuntimeError("Zu viele alte EC-Daten im Output Buffer")

        time.sleep(0.0005)


def lram_read(high, low):
    # Entspricht der LRAM-Methode aus deiner DSDT.
    #
    # Das schreibt KEINEN Registerwert.
    # 0x7E + high + low bilden nur den READ-Befehl.

    if not wait_ibf_clear():
        raise TimeoutError("EC IBF vor Start nicht frei")

    drain_output()

    # READ-LRAM Command
    w.write_io_port_byte(CMD_PORT, 0x7E)

    if not wait_ibf_clear():
        raise TimeoutError("Timeout nach Command 0x7E")

    # Adressbyte 1
    w.write_io_port_byte(DATA_PORT, high)

    if not wait_ibf_clear():
        raise TimeoutError("Timeout nach High-Byte")

    # Adressbyte 2
    w.write_io_port_byte(DATA_PORT, low)

    if not wait_ibf_clear():
        raise TimeoutError("Timeout nach Low-Byte")

    if not wait_obf_set():
        raise TimeoutError(
            f"Keine Antwort für Adresse {high:02X}{low:02X}"
        )

    return w.read_io_port_byte(DATA_PORT)


def read_all():
    result = {}

    for name, address in REGS.items():
        result[name] = lram_read(*address)

        # EC nicht unnötig schnell belasten
        time.sleep(0.01)

    return result


def show(values):
    print(
        f"FANS={values['FANS']:3d} "
        f"(~{values['FANS'] * 100:4d} RPM)   "
        f"FA2S={values['FA2S']:3d} "
        f"(~{values['FA2S'] * 100:4d} RPM)"
    )

    print(
        f"FCMO=0x{values['FCMO']:02X} ({values['FCMO']})   "
        f"FCST=0x{values['FCST']:02X} ({values['FCST']})"
    )

    print(
        f"CPUT={values['CPUT']:3d} °C   "
        f"GPUT={values['GPUT']:3d} °C"
    )


try:
    print("=" * 72)
    print(" Lenovo Yoga Pro 9 14IRP8 - EC LRAM READ TEST")
    print("=" * 72)
    print()
    print("Firmware-Methode: LRAM")
    print("Ports: 0x6C / 0x68")
    print()
    print("WICHTIG:")
    print("Es werden KEINE Fan-, Mode- oder Steuerwerte geschrieben.")
    print("0x7E + Adresse ist ausschließlich der EC-READ-Befehl.")
    print()

    print("Öffne WinRing0...")
    w.open()
    print("OK")
    print()

    print("Status 0x6C vor Test:")
    s = status()

    print(
        f"0x{s:02X}   "
        f"IBF={(s >> 1) & 1} "
        f"OBF={s & 1}"
    )

    print()
    print("TEST 1 - Drei Einzelmessungen")
    print("-" * 72)

    for i in range(3):
        values = read_all()

        print()
        print(f"Messung {i + 1}:")
        show(values)

        time.sleep(1)

    print()
    print("=" * 72)
    print("TEST 2 - Fn+Q")
    print("=" * 72)
    print()
    print("Nach ENTER werden 15 Sekunden lang die Register gelesen.")
    print("Drücke Fn+Q ungefähr alle 2 bis 3 Sekunden.")
    print()

    input("ENTER drücken... ")

    previous = read_all()

    start = time.perf_counter()
    end = start + 15

    print()
    print("Startwerte:")
    show(previous)
    print()
    print("Änderungen:")
    print("-" * 72)

    changes = 0

    while time.perf_counter() < end:

        try:
            current = read_all()
        except TimeoutError as e:
            print(f"EC Timeout: {e}")
            time.sleep(0.5)
            continue

        now = time.perf_counter() - start

        for name in REGS:
            old = previous[name]
            new = current[name]

            if old != new:

                extra = ""

                if name in ("FANS", "FA2S"):
                    extra = (
                        f"   ~{old * 100} -> "
                        f"{new * 100} RPM"
                    )

                elif name in ("CPUT", "GPUT"):
                    extra = " °C"

                print(
                    f"{now:7.2f}s   "
                    f"{name:<4}   "
                    f"0x{old:02X} ({old:3d})"
                    f" -> "
                    f"0x{new:02X} ({new:3d})"
                    f"{extra}"
                )

                changes += 1

        previous = current

        # ca. 4 Messungen pro Sekunde
        time.sleep(0.25)

    print()
    print("=" * 72)
    print("ERGEBNIS")
    print("=" * 72)

    print(f"Registeränderungen erkannt: {changes}")

    print()
    print("Letzte Werte:")
    show(previous)

    print()
    print("Status 0x6C nach Test:")

    s = status()

    print(
        f"0x{s:02X}   "
        f"IBF={(s >> 1) & 1} "
        f"OBF={s & 1}"
    )

    print()
    print("Fertig.")
    print("Es wurden keine Fan- oder Moduswerte verändert.")

except Exception as e:
    print()
    print("FEHLER:")
    print(repr(e))

finally:
    print()
    print("Schließe WinRing0...")

    try:
        w.close()
    except Exception:
        pass