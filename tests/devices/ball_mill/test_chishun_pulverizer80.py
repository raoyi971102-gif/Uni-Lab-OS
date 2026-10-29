"""驰顺 PULVERIZER 80 球磨机驱动的离线测试：用假串口核对 Modbus 帧，不需要真机。"""

import struct

import pytest

from unilabos.devices.ball_mill import chishun_pulverizer80 as bm


def test_crc_known_frame():
    # 站号 2 读 0x0010 起 8 个寄存器；CRC 和已验证的偶氮工站 modbus_rtu.append_crc 结果一致
    assert bm.with_crc(bytes.fromhex("020300100008")).hex() == "02030010000845fa"


def test_parse_status_bits():
    w = 0x0001 | 0x0004 | (3 << 3) | 0x0040 | (2 << 8) | (3 << 14)
    st = bm.parse_status([w, 0, 5, 2, 40, (-100) & 0xFFFF, 300, 18000])
    assert st["remote"] and st["door_open"] and st["running"]
    assert st["jar_positioned"] == 3
    assert st["jars"][1] == "已压好" and st["jars"][4] == "已弹出"
    assert st["disk_rpm"] == -100 and st["angle"] == 180.0


class FakeSerial:
    """记录写入的帧，并按请求生成合法应答。"""

    def __init__(self, status_word=0x0001):
        self.sent, self._out = [], b""
        self.regs = {bm.REG_CONTROL: 0, bm.REG_STATUS: status_word}

    def reset_input_buffer(self):
        pass

    def write(self, frame):
        assert bm.check_crc(frame)
        self.sent.append(frame)
        addr, fc, reg = frame[0], frame[1], struct.unpack(">H", frame[2:4])[0]
        if fc == 0x03:
            n = struct.unpack(">H", frame[4:6])[0]
            data = b"".join(struct.pack(">H", self.regs.get(reg + i, 0)) for i in range(n))
            self._out = bm.with_crc(bytes((addr, 3, 2 * n)) + data)
        elif fc == 0x06:
            self.regs[reg] = struct.unpack(">H", frame[4:6])[0]
            if reg == bm.REG_CONTROL and self.regs[reg] & 0x80:
                self.regs[bm.REG_STATUS] |= 0x40
            self._out = frame
        elif fc == 0x10:
            self._out = bm.with_crc(frame[:6])

    def read(self, n):
        out, self._out = self._out[:n], self._out[n:]
        return out


def make(fake):
    dev = bm.ChishunPulverizer80(simulate=True)
    dev.simulate = False
    dev._ser = fake
    return dev


def test_start_writes_recipe_and_start_bit():
    fake = FakeSerial()
    dev = make(fake)
    r = dev.start(recipe=5)
    assert r["success"], r
    writes = [f for f in fake.sent if f[1] == 0x06]
    assert writes[0][:6].hex() == "020600000580"  # 控制字 = 工艺 5 << 8 | bit7


def test_not_remote_refused():
    dev = make(FakeSerial(status_word=0))
    assert dev.start(1)["error"] == "not_remote"


def test_write_recipe_frame_and_limits():
    fake = FakeSerial()
    dev = make(fake)
    r = dev.write_recipe(recipe=2, name="LPSCl", steps=[{"rpm": 500, "time": 60}, {"rpm": -500, "time": 60}], cycles=2)
    assert r["success"], r
    f = fake.sent[-1]
    assert f[1] == 0x10 and struct.unpack(">H", f[2:4])[0] == 0x0180 and f[5] == 23
    assert dev.write_recipe(recipe=1, steps=[{"rpm": 50, "time": 1}])["error"] == "bad_rpm"
    assert dev.write_recipe(recipe=1, steps=[{"rpm": 300, "time": 800}], cycles=2)["error"] == "too_long"


def test_simulate_full_cycle():
    dev = bm.ChishunPulverizer80(simulate=True)
    assert dev.open_door(timeout=2)["success"]
    assert dev.position_jar(2, timeout=2)["success"]
    assert dev.close_door(timeout=2)["success"]
    assert dev.start(1)["success"]
    assert dev.wait_until_finished(timeout=10, interval=0.01)["success"]
