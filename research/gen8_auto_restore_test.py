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


def show():
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


try:
    w.open()

    print("Bitte zuerst INTELLIGENT MODE einstellen.")
    input("Dann ENTER drücken...")

    print()
    print("Vor manueller Steuerung:")
    show()

    print()
    print("Setze beide Lüfter auf ~3000 RPM...")

    r1 = mbey(0xEF, 0x61, 30)
    r2 = mbey(0xEF, 0x62, 30)

    print(f"Fan1 Set: 0x{r1:02X}")
    print(f"Fan2 Set: 0x{r2:02X}")
    print()

    for i in range(6):
        print(f"MANUAL {i+1}: ", end="")
        show()
        time.sleep(1)

    print()
    input("ENTER drücken für Lenovo Auto-Restore...")

    r = auto()

    print()
    print(f"Auto Antwort: 0x{r:02X}")
    print()

    for i in range(15):
        print(f"AUTO {i+1:02d}: ", end="")
        show()
        time.sleep(1)

finally:
    try:
        auto()
    except Exception:
        pass

    try:
        w.close()
    except Exception:
        pass

    print()
    print("WinRing0 geschlossen.")