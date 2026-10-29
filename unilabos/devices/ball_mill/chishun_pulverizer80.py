"""南京驰顺 PULVERIZER 80 自动化高能行星式球磨机驱动。

依据《【说明书】自动化高能球磨机-20260408.pdf》的上位机通讯协议：
- 球磨机开机后需处于"自动模式"，上位机才能通过 Modbus 控制。
- Modbus RTU，19200 bps，8E1（8 数据位、偶校验、1 停止位），从站地址 0x02。
- 0x0000 设备控制字（上位机写），0x0010~0x0017 设备状态（上位机读），
  0x0100 起为 72 组工艺参数，每组 0x80 个寄存器。
- 说明书要求严禁越界访问，本驱动只读写说明书列出的地址。

说明书没有写明的地方（按常规做法处理，真机联调时需确认）：
- 工艺步骤"工作时间"的单位：说明书只写了工艺总时长不超过 1440 min，这里按分钟处理。
- 工艺名称字符在寄存器内的字节顺序：按高字节在前处理。
- 罐定位指令执行完后，驱动会把 bit4~6 清零（0 属于"无效"值），避免重复触发。
- 协议里没有远程暂停位，暂停只能在手动模式下在触摸屏上操作。
"""

from __future__ import annotations

import struct
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from unilabos.registry.decorators import action, device, not_action, topic_config
from unilabos.utils.log import logger

if TYPE_CHECKING:
    from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode


# ---------------------------------------------------------------------------
# Modbus RTU 帧
# ---------------------------------------------------------------------------

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(payload: bytes) -> bytes:
    c = crc16(payload)
    return payload + bytes((c & 0xFF, c >> 8))


def check_crc(frame: bytes) -> bool:
    return len(frame) >= 4 and crc16(frame[:-2]) == (frame[-2] | (frame[-1] << 8))


def to_u16(v: int) -> int:
    return v & 0xFFFF


def to_s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class ModbusError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 寄存器定义
# ---------------------------------------------------------------------------

REG_CONTROL = 0x0000
REG_STATUS = 0x0010           # 0x0010~0x0017 连续 8 个寄存器
STATUS_COUNT = 8
RECIPE_BASE = 0x0100
RECIPE_STRIDE = 0x0080
RECIPE_COUNT = 0x18           # 每组工艺用到 0x00~0x17
RECIPE_MAX = 72
STEP_MAX = 6
NAME_CHARS = 18

ARM_STATES = {0: "已退出", 1: "请求介入", 2: "放罐中", 3: "取罐中"}
JAR_STATES = {0: "未装罐", 1: "压弹罐中", 2: "已压好", 3: "已弹出"}


def parse_status(regs: List[int]) -> Dict[str, Any]:
    """解析 0x0010~0x0017 的 8 个寄存器。"""
    w = regs[0]
    jars = {i + 1: JAR_STATES[(w >> (8 + 2 * i)) & 0x3] for i in range(4)}
    positioned = (w >> 3) & 0x7
    return {
        "remote": bool(w & 0x01),                      # bit0 控制源：1=远程
        "arm_allowed": bool(w & 0x02),                 # bit1 允许机械臂介入
        "door_open": bool(w & 0x04),                   # bit2 舱门已全开
        "jar_positioned": positioned if 1 <= positioned <= 4 else 0,  # 0=定位中
        "running": bool(w & 0x40),                     # bit6 运行
        "paused": bool(w & 0x80),                      # bit7 暂停
        "jars": jars,
        "fault_code": regs[1],
        "recipe": regs[2],
        "step": regs[3],
        "progress": regs[4],                           # 0~100 %
        "disk_rpm": to_s16(regs[5]),
        "jar_rpm": to_s16(regs[6]),
        "angle": regs[7] / 100.0,                      # 缩放系数 100
        "status_word": w,
    }


@device(
    id="chishun_pulverizer80",
    category=["ball_mill"],
    description="南京驰顺 PULVERIZER 80 自动化高能行星式球磨机，Modbus RTU（19200 8E1，站号 2）",
    display_name="驰顺 PULVERIZER 80 球磨机",
)
class ChishunPulverizer80:
    """PULVERIZER 80 球磨机。设备需开机后选择"自动模式"（5 秒内不选也会自动进入）。"""

    _ros_node: "BaseROS2DeviceNode"

    def __init__(
        self,
        device_id: Optional[str] = None,
        port: str = "COM1",
        address: int = 2,
        baudrate: int = 19200,
        timeout: float = 0.5,
        simulate: bool = False,
        **kwargs,
    ):
        """
        Args:
            device_id[设备ID]: 球磨机实例 ID。
            port[串口]: RS485 串口，例如 COM3 或 /dev/ttyUSB0。
            address[Modbus站号]: 默认 2（说明书固定值）。
            baudrate[波特率]: 默认 19200。
            timeout[超时]: 单次应答超时（秒）。
            simulate[模拟模式]: 为 True 时不打开串口，用内存模拟设备。
        """
        self.device_id = device_id or "ball_mill"
        self.port = port
        self.address = int(address)
        self.baudrate = int(baudrate)
        self.timeout = float(timeout)
        self.simulate = bool(simulate)
        self._lock = threading.RLock()
        self._ser = None
        self._control = 0          # 控制字影子值，所有写操作在它上面改位后整字写入
        self._sim_regs: Dict[int, int] = {}
        self.data: Dict[str, Any] = {"status": "Idle", "fault_code": 0, "progress": 0, "running": False}

        if self.simulate:
            self._sim_regs = {a: 0 for a in range(REG_STATUS, REG_STATUS + STATUS_COUNT)}
            self._sim_regs[REG_STATUS] = 0x0001  # 远程
            self._sim_regs[REG_STATUS + 2] = 1
        else:
            self._open()
            try:
                self._control = self._read(REG_CONTROL, 1)[0]
            except Exception as exc:  # 读不到控制字就从 0 开始
                logger.warning(f"球磨机 {self.device_id}: 读取控制字失败，按 0 处理 - {exc}")

    # ------------------------------------------------------------------ 通信
    def _open(self) -> None:
        import serial  # pyserial

        self._ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
        )

    @not_action
    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    def _transact(self, frame: bytes, expect: int) -> bytes:
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(frame)
            resp = self._ser.read(expect)
            if len(resp) >= 5 and resp[1] & 0x80:  # 异常应答只有 5 字节
                raise ModbusError(f"Modbus 异常码 0x{resp[2]:02X}")
            if len(resp) < expect:
                resp += self._ser.read(expect - len(resp))
            if len(resp) != expect or not check_crc(resp) or resp[0] != self.address:
                raise ModbusError(f"应答无效: 发送 {frame.hex()} 收到 {resp.hex()}")
            return resp

    def _read(self, reg: int, count: int) -> List[int]:
        if self.simulate:
            if reg == REG_STATUS and self._sim_regs[REG_STATUS] & 0x40:
                self._sim_ticks = getattr(self, "_sim_ticks", 0) + 1
                if self._sim_ticks > 3:  # 模拟：运行几次轮询后结束
                    self._sim_regs[REG_STATUS] &= ~0x40
                    self._sim_ticks = 0
            return [self._sim_regs.get(reg + i, 0) for i in range(count)]
        req = with_crc(struct.pack(">BBHH", self.address, 0x03, reg, count))
        resp = self._transact(req, 5 + 2 * count)
        return list(struct.unpack(">" + "H" * count, resp[3:3 + 2 * count]))

    def _write(self, reg: int, values: List[int]) -> None:
        values = [to_u16(v) for v in values]
        if self.simulate:
            for i, v in enumerate(values):
                self._sim_regs[reg + i] = v
            if reg == REG_CONTROL:
                self._sim_apply_control(values[0])
            return
        if len(values) == 1:
            req = with_crc(struct.pack(">BBHH", self.address, 0x06, reg, values[0]))
        else:
            req = with_crc(
                struct.pack(">BBHHB", self.address, 0x10, reg, len(values), 2 * len(values))
                + struct.pack(">" + "H" * len(values), *values)
            )
        self._transact(req, 8)

    def _sim_apply_control(self, c: int) -> None:
        """模拟模式：按控制字更新状态字。"""
        s = self._sim_regs[REG_STATUS] & ~(0x04 | 0x38 | 0x40)
        if c & 0x08:
            s |= 0x04
        jar = (c >> 4) & 0x7
        if 1 <= jar <= 4:
            s |= jar << 3
        if c & 0x80:
            s |= 0x40
            self._sim_regs[REG_STATUS + 4] = 100
        if (c >> 1) & 0x3 == 1 and not c & 0x80:
            s |= 0x02
        self._sim_regs[REG_STATUS] = s

    def _set_control(self, mask: int, value: int) -> None:
        with self._lock:
            new = (self._control & ~mask) | (value & mask)
            self._write(REG_CONTROL, [new])
            self._control = new

    @not_action
    def post_init(self, ros_node: "BaseROS2DeviceNode") -> None:
        self._ros_node = ros_node
        self.device_id = getattr(ros_node, "device_id", self.device_id)

    def _fail(self, msg: str, exc: Exception) -> Dict[str, Any]:
        self.data["status"] = "Error"
        logger.error(f"球磨机 {self.device_id}: {msg} - {exc}")
        return {"success": False, "error": str(exc), "message": msg}

    # ------------------------------------------------------------------ 状态
    @action(description="读取设备状态（控制源、舱门、罐位、运行、故障码、进度、转速等）")
    def read_status(self) -> Dict[str, Any]:
        try:
            st = parse_status(self._read(REG_STATUS, STATUS_COUNT))
        except Exception as exc:
            return self._fail("读取状态失败", exc)
        self.data.update(st)
        if st["fault_code"]:
            self.data["status"] = "Error"
        elif st["paused"]:
            self.data["status"] = "Paused"
        elif st["running"]:
            self.data["status"] = "Running"
        else:
            self.data["status"] = "Idle"
        return {"success": True, **st}

    def _require_remote(self) -> Optional[Dict[str, Any]]:
        r = self.read_status()
        if not r.get("success"):
            return r
        if not r["remote"]:
            return {"success": False, "error": "not_remote", "message": "球磨机不在自动模式（远程控制），请在触摸屏选择自动模式"}
        if r["fault_code"]:
            return {"success": False, "error": "fault", "message": f"设备故障码 {r['fault_code']}"}
        return None

    def _wait(self, cond, timeout: float, interval: float = 0.5) -> Optional[Dict[str, Any]]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r = self.read_status()
            if r.get("success") and cond(r):
                return r
            time.sleep(interval)
        return None

    # ------------------------------------------------------------------ 舱门
    @action(description="打开舱门（仅停机状态有效），等待舱门全开")
    def open_door(self, timeout: float = 30.0) -> Dict[str, Any]:
        """
        Args:
            timeout[超时]: 等待舱门全开的时间（秒）。
        """
        err = self._require_remote()
        if err:
            return err
        if self.data.get("running"):
            return {"success": False, "error": "running", "message": "运行中不能开舱门"}
        try:
            self._set_control(0x08, 0x08)
        except Exception as exc:
            return self._fail("写开门指令失败", exc)
        r = self._wait(lambda s: s["door_open"], timeout)
        if r is None:
            return {"success": False, "error": "timeout", "message": "等待舱门全开超时"}
        return {"success": True, "message": "舱门已全开"}

    @action(description="关闭舱门（仅停机状态有效）")
    def close_door(self, timeout: float = 30.0) -> Dict[str, Any]:
        """
        Args:
            timeout[超时]: 等待舱门离开全开状态的时间（秒）。说明书只提供"已全开"信号，没有"已关好"信号。
        """
        err = self._require_remote()
        if err:
            return err
        try:
            self._set_control(0x08, 0x00)
        except Exception as exc:
            return self._fail("写关门指令失败", exc)
        r = self._wait(lambda s: not s["door_open"], timeout)
        if r is None:
            return {"success": False, "error": "timeout", "message": "舱门仍为全开状态"}
        return {"success": True, "message": "已下发关门指令，舱门不再处于全开状态"}

    # ------------------------------------------------------------------ 罐位
    @action(description="把指定罐位转到参考点（零点），供机械臂取放罐")
    def position_jar(self, jar: int = 1, timeout: float = 60.0) -> Dict[str, Any]:
        """
        Args:
            jar[罐位]: 1~4。
            timeout[超时]: 等待定位完成的时间（秒）。
        """
        jar = int(jar)
        if not 1 <= jar <= 4:
            return {"success": False, "error": "bad_jar", "message": "罐位只能是 1~4"}
        err = self._require_remote()
        if err:
            return err
        if self.data.get("running"):
            return {"success": False, "error": "running", "message": "运行中不能转罐位"}
        try:
            self._set_control(0x70, jar << 4)
        except Exception as exc:
            return self._fail("写罐定位指令失败", exc)
        r = self._wait(lambda s: s["jar_positioned"] == jar, timeout)
        try:
            self._set_control(0x70, 0)  # 清除定位指令
        except Exception as exc:
            logger.warning(f"球磨机 {self.device_id}: 清除罐定位指令失败 - {exc}")
        if r is None:
            return {"success": False, "error": "timeout", "message": f"罐 {jar} 定位超时"}
        return {"success": True, "jar": jar, "jar_state": r["jars"][jar], "message": f"罐 {jar} 已转到参考点"}

    # ------------------------------------------------------------------ 机械臂握手
    @action(description="向球磨机报告机械臂状态：0=已退出，1=请求介入，2=放罐中，3=取罐中")
    def set_arm_state(self, state: int = 0, fault: bool = False) -> Dict[str, Any]:
        """
        Args:
            state[机械臂状态]: 0=已退出，1=请求介入，2=放罐中，3=取罐中。
            fault[机械臂故障]: 机械臂是否故障。
        """
        state = int(state)
        if state not in ARM_STATES:
            return {"success": False, "error": "bad_state", "message": "机械臂状态只能是 0~3"}
        try:
            self._set_control(0x07, (state << 1) | (1 if fault else 0))
        except Exception as exc:
            return self._fail("写机械臂状态失败", exc)
        return {"success": True, "arm_state": ARM_STATES[state], "fault": bool(fault)}

    @action(description="请求机械臂介入，等待球磨机允许")
    def request_arm_access(self, timeout: float = 60.0) -> Dict[str, Any]:
        """
        Args:
            timeout[超时]: 等待"允许机械臂介入"的时间（秒）。
        """
        r = self.set_arm_state(1)
        if not r["success"]:
            return r
        s = self._wait(lambda s: s["arm_allowed"], timeout)
        if s is None:
            return {"success": False, "error": "timeout", "message": "球磨机未允许机械臂介入"}
        return {"success": True, "message": "球磨机已允许机械臂介入"}

    # ------------------------------------------------------------------ 启停
    @action(description="按工艺编号启动研磨（0=当前工艺，1~72=预存工艺）")
    def start(self, recipe: int = 0) -> Dict[str, Any]:
        """
        Args:
            recipe[工艺编号]: 0=当前工艺，1~72=预存工艺。
        """
        recipe = int(recipe)
        if not 0 <= recipe <= RECIPE_MAX:
            return {"success": False, "error": "bad_recipe", "message": "工艺编号只能是 0~72"}
        err = self._require_remote()
        if err:
            return err
        if self.data.get("door_open"):
            return {"success": False, "error": "door_open", "message": "舱门未关，不能启动"}
        try:
            self._set_control(0xFF80, (recipe << 8) | 0x80)
        except Exception as exc:
            return self._fail("写启动指令失败", exc)
        r = self._wait(lambda s: s["running"], 10.0)
        if r is None:
            return {"success": False, "error": "not_started", "message": "已下发启动指令，但设备未进入运行状态"}
        return {"success": True, "recipe": r["recipe"], "message": f"已启动工艺 {r['recipe']}"}

    @action(description="停止研磨")
    def stop(self) -> Dict[str, Any]:
        try:
            self._set_control(0x80, 0)
        except Exception as exc:
            return self._fail("写停机指令失败", exc)
        self.data["status"] = "Idle"
        return {"success": True, "message": "已下发停机指令"}

    @action(description="等待当前工艺运行结束")
    def wait_until_finished(self, timeout: float = 86400.0, interval: float = 5.0) -> Dict[str, Any]:
        """
        Args:
            timeout[超时]: 最长等待时间（秒），工艺最长 24 小时。
            interval[轮询间隔]: 读状态的间隔（秒）。
        """
        r = self._wait(lambda s: not s["running"] or s["fault_code"], timeout, interval)
        if r is None:
            return {"success": False, "error": "timeout", "message": "等待研磨结束超时"}
        if r["fault_code"]:
            return {"success": False, "error": "fault", "message": f"设备故障码 {r['fault_code']}"}
        try:
            self._set_control(0x80, 0)  # 设备停机后把启停命令复位，避免下次误启动
        except Exception as exc:
            logger.warning(f"球磨机 {self.device_id}: 复位启停命令失败 - {exc}")
        return {"success": True, "message": "研磨已结束"}

    # ------------------------------------------------------------------ 工艺参数
    @action(description="读取一组预存工艺（名称、6 个步骤的大盘转速和时间、循环次数）")
    def read_recipe(self, recipe: int = 1) -> Dict[str, Any]:
        """
        Args:
            recipe[工艺编号]: 1~72。
        """
        recipe = int(recipe)
        if not 1 <= recipe <= RECIPE_MAX:
            return {"success": False, "error": "bad_recipe", "message": "工艺编号只能是 1~72"}
        try:
            regs = self._read(RECIPE_BASE + (recipe - 1) * RECIPE_STRIDE, RECIPE_COUNT)
        except Exception as exc:
            return self._fail("读取工艺失败", exc)
        name = b"".join(struct.pack(">H", r) for r in regs[1:10]).decode("ascii", errors="replace").rstrip(" \x00")
        steps = [{"rpm": to_s16(regs[10 + 2 * i]), "time": regs[11 + 2 * i]} for i in range(STEP_MAX)]
        return {
            "success": True, "recipe": recipe, "number": regs[0], "name": name,
            "steps": steps, "cycles": regs[22], "elapsed": regs[23],
        }

    @action(description="写入一组预存工艺。大盘转速只能是 0（停转）或 100~600 RPM，负值表示反转")
    def write_recipe(
        self,
        recipe: int = 1,
        name: str = "",
        steps: Optional[List[Dict[str, int]]] = None,
        cycles: int = 1,
    ) -> Dict[str, Any]:
        """
        Args:
            recipe[工艺编号]: 1~72。
            name[工艺名称]: 最多 18 个 ASCII 字符。
            steps[步骤]: 最多 6 步，每步 {"rpm": 大盘转速, "time": 工作时间（按分钟处理，见文件头说明）}，不足 6 步补 0。
            cycles[循环次数]: 工艺循环次数。
        """
        recipe = int(recipe)
        if not 1 <= recipe <= RECIPE_MAX:
            return {"success": False, "error": "bad_recipe", "message": "工艺编号只能是 1~72"}
        steps = list(steps or [])
        if len(steps) > STEP_MAX:
            return {"success": False, "error": "too_many_steps", "message": "最多 6 个步骤"}
        try:
            raw_name = name.encode("ascii")
        except UnicodeEncodeError:
            return {"success": False, "error": "bad_name", "message": "工艺名称只能用 ASCII 字符"}
        if len(raw_name) > NAME_CHARS:
            return {"success": False, "error": "bad_name", "message": "工艺名称最多 18 个字符"}
        total = 0
        regs_steps: List[int] = []
        for st in steps + [{"rpm": 0, "time": 0}] * (STEP_MAX - len(steps)):
            rpm, t = int(st.get("rpm", 0)), int(st.get("time", 0))
            if rpm != 0 and not 100 <= abs(rpm) <= 600:
                return {"success": False, "error": "bad_rpm", "message": f"大盘转速 {rpm} 不在 0 或 100~600 RPM 范围内"}
            if t < 0:
                return {"success": False, "error": "bad_time", "message": "工作时间不能为负"}
            total += t
            regs_steps += [rpm, t]
        if total * max(int(cycles), 1) > 1440:
            return {"success": False, "error": "too_long", "message": "工艺总时长超过 1440 min"}
        padded = raw_name.ljust(NAME_CHARS, b" ")
        name_regs = list(struct.unpack(">" + "H" * 9, padded))
        base = RECIPE_BASE + (recipe - 1) * RECIPE_STRIDE
        try:
            self._write(base, [recipe] + name_regs + regs_steps + [int(cycles)])
        except Exception as exc:
            return self._fail("写入工艺失败", exc)
        return {"success": True, "recipe": recipe, "message": f"工艺 {recipe} 已写入"}

    # ------------------------------------------------------------------ 话题
    @property
    @topic_config()
    def status(self) -> str:
        return self.data["status"]

    @property
    @topic_config(period=2.0)
    def progress(self) -> int:
        return int(self.data.get("progress", 0))

    @property
    @topic_config()
    def fault_code(self) -> int:
        return int(self.data.get("fault_code", 0))
