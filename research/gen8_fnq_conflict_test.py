import fan_control
import time

CMD = 0x6C
DATA = 0x68

w = fan_control.TransientWinRing0()


def status():
    return w.read_io_port_byte(CMD)


def wait_ibf():
    while status() & 0x02:
        time.sleep(0.0005)


def wait_obf():
    while not (status() & 0x01):
        time.sleep(0.0005)


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

    print("Setze beide Lüfter manuell auf ~3000 RPM...")

    r1 = mbey(0xEF, 0x61, 30)
    r2 = mbey(0xEF, 0x62, 30)

    print(f"Fan1 Antwort: 0x{r1:02X}")
    print(f"Fan2 Antwort: 0x{r2:02X}")
    print()

    print("Jetzt 20 Sekunden lang Fn+Q drücken.")
    print("Wechsle durch:")
    print("Intelligent -> Energiesparen -> Leistung -> Intelligent")
    print()

    start = time.time()

    while time.time() - start < 20:
        f1 = fan1()
        f2 = fan2()

        rpm1 = lram(0x04, 0x06) * 100
        rpm2 = lram(0x04, 0xFE) * 100

        fcmo = lram(0x04, 0x20)
        cpu = lram(0x04, 0xB0)

        print(
            f"FCMO={fcmo} | "
            f"Fan1={f1} ~{rpm1} RPM | "
            f"Fan2={f2} ~{rpm2} RPM | "
            f"CPU={cpu}°C"
        )

        if cpu > 85:
            print("CPU zu warm, breche ab.")
            break

        time.sleep(1)

finally:
    print()
    print("Stelle Lenovo-Automatik wieder her...")

    try:
        r = auto()
        print(f"Auto Antwort: 0x{r:02X}")
    except Exception as e:
        print("Auto-Restore Fehler:", e)

    try:
        w.close()
    except Exception:
        pass