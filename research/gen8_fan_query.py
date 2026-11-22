import fan_control
import time

CMD = 0x6C
DATA = 0x68

w = fan_control.TransientWinRing0()


def status():
    return w.read_io_port_byte(CMD)


def wait_ibf_clear(timeout=0.5):
    end = time.perf_counter() + timeout

    while time.perf_counter() < end:
        if (status() & 0x02) == 0:
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
    count = 0

    while status() & 0x01:
        w.read_io_port_byte(DATA)
        count += 1

        if count > 16:
            raise RuntimeError("Output Buffer Problem")

        time.sleep(0.0005)


def mbey(cmd, subcmd, arg):
    wait_ibf_clear()
    drain()

    w.write_io_port_byte(CMD, cmd)

    wait_ibf_clear()
    w.write_io_port_byte(DATA, subcmd)

    wait_ibf_clear()
    w.write_io_port_byte(DATA, arg)

    wait_ibf_clear()
    wait_obf_set()

    return w.read_io_port_byte(DATA)


def lram(high, low):
    return mbey(0x7E, high, low)


try:
    print("=" * 68)
    print("Yoga Pro 9 Gen8 - Fan Mailbox Query Test")
    print("=" * 68)
    print()
    print("Nur Fan-Abfragen.")
    print("Keine Lüftergeschwindigkeit wird gesetzt.")
    print("Auto-Steuerung wird NICHT verändert.")
    print()

    w.open()

    for i in range(15):

        # bekannte RPM-Telemetrie
        rpm1_raw = lram(0x04, 0x06)
        rpm2_raw = lram(0x04, 0xFE)

        # Gen9 Fan-Query-Protokoll testen
        q1 = mbey(0xEF, 0x63, 0x01)
        q2 = mbey(0xEF, 0x63, 0x02)

        print(
            f"{i+1:02d}: "
            f"Fan1 RPM ~{rpm1_raw * 100:4d} | "
            f"Query=0x{q1:02X} ({q1:3d})%    "
            f"Fan2 RPM ~{rpm2_raw * 100:4d} | "
            f"Query=0x{q2:02X} ({q2:3d})%"
        )

        time.sleep(0.7)

finally:
    try:
        w.close()
    except Exception:
        pass

    print()
    print("WinRing0 geschlossen.")