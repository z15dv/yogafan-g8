import fan_control
import time

CMD = 0x6C
DATA = 0x68

w = fan_control.TransientWinRing0()


def status():
    return w.read_io_port_byte(CMD)


def wait_ibf(timeout=0.5):
    end = time.perf_counter() + timeout

    while time.perf_counter() < end:
        if (status() & 0x02) == 0:
            return

        time.sleep(0.0005)

    raise TimeoutError("IBF timeout")


def wait_obf(timeout=0.5):
    end = time.perf_counter() + timeout

    while time.perf_counter() < end:
        if status() & 0x01:
            return

        time.sleep(0.0005)

    raise TimeoutError("OBF timeout")


def drain():
    while status() & 0x01:
        w.read_io_port_byte(DATA)
        time.sleep(0.0005)


def mbey(cmd, subcmd, arg):
    wait_ibf()
    drain()

    w.write_io_port_byte(CMD, cmd)

    wait_ibf()
    w.write_io_port_byte(DATA, subcmd)

    wait_ibf()
    w.write_io_port_byte(DATA, arg)

    wait_ibf()
    wait_obf()

    return w.read_io_port_byte(DATA)


def lram(high, low):
    return mbey(0x7E, high, low)


def fan1():
    return mbey(0xEF, 0x63, 0x01)


def fan2():
    return mbey(0xEF, 0x63, 0x02)


def auto():
    return mbey(0xEF, 0x63, 0x03)


try:
    w.open()

    print("Vorher:")
    print(f"Fan 1: {fan1()}  (~{fan1() * 100} RPM)")
    print(f"Fan 2: {fan2()}  (~{fan2() * 100} RPM)")
    print()

    cpu = lram(0x04, 0xB0)
    print(f"CPU: {cpu} °C")

    if cpu > 75:
        print("CPU zu warm. Test abgebrochen.")
        raise SystemExit

    print()
    input("ENTER = beide Lüfter für 8 Sekunden auf Wert 30 setzen...")

    r1 = mbey(0xEF, 0x61, 30)
    r2 = mbey(0xEF, 0x62, 30)

    print()
    print(f"Set Fan1 Antwort: 0x{r1:02X}")
    print(f"Set Fan2 Antwort: 0x{r2:02X}")
    print()

    for i in range(8):
        f1 = fan1()
        f2 = fan2()

        rpm1 = lram(0x04, 0x06) * 100
        rpm2 = lram(0x04, 0xFE) * 100
        cpu = lram(0x04, 0xB0)

        print(
            f"{i+1}: "
            f"Query {f1}/{f2} | "
            f"RPM ~{rpm1}/{rpm2} | "
            f"CPU {cpu} °C"
        )

        time.sleep(1)

finally:
    print()
    print("Stelle Lenovo-Automatik wieder her...")

    try:
        result = auto()
        print(f"Auto Antwort: 0x{result:02X}")
    except Exception as e:
        print("Auto-Restore Fehler:", e)

    try:
        w.close()
    except Exception:
        pass

    print("Fertig.")