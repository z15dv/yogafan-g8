import fan_control
import time

CMD = 0x6C
DATA = 0x68

w = fan_control.TransientWinRing0()


def wait_ibf():
    while w.read_io_port_byte(CMD) & 0x02:
        time.sleep(0.0005)


def wait_obf():
    while not (w.read_io_port_byte(CMD) & 0x01):
        time.sleep(0.0005)


def lram(high, low):
    wait_ibf()
    w.write_io_port_byte(CMD, 0x7E)

    wait_ibf()
    w.write_io_port_byte(DATA, high)

    wait_ibf()
    w.write_io_port_byte(DATA, low)

    wait_obf()
    return w.read_io_port_byte(DATA)


try:
    w.open()

    print("FCMO Mode Mapper")
    print()

    while True:
        value = lram(0x04, 0x20)

        print(
            f"\rFCMO = 0x{value:02X} ({value})",
            end="",
            flush=True
        )

        time.sleep(0.25)

finally:
    w.close()