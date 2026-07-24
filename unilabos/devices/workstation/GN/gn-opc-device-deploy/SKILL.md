---
name: gn-opc-device-deploy
description: >-
  GN 合成工站 OPC UA 1.3.4 子设备驱动改造、图文件、工作流 JSON 与现场部署指南。
  参照 solid_weighing.py、centrifuge_tube_liquid_handling.py、GN_station.json、robotic_arm.py。
  Use when adding/modifying GN workstation devices, OPC drivers, GN_station.json,
  workflow JSON, execute_command, or mentions GN工站/固体加样/离心管/机械手/OPC 1.3.4/部署.
---

# GN OPC 设备改造与部署

本 Skill 汇总 GN 合成工站（2026-07-24 现场调试经验）的**标准模式**。新增或改造子设备时，先读本文件，再对照参考驱动。

## 参考文件（必读）

| 文件 | 用途 |
|------|------|
| `solid_weighing.py` | **单入口** `execute_command` + 测试 yaml 预设 + 位置兜底 |
| `centrifuge_tube_liquid_handling.py` | **语义动作** `@action` + 写死点位 + 简单 CompleteFB 等待 |
| `robotic_arm.py` | 机械手两阶段运动、X 轴相对/绝对语义、工站 Done 边沿 |
| `GN_station.json` | 工站图：容器 + 子设备 + `data.actions` 调试菜单 |
| `gn_station.py` | 工站根容器（仅布局，无 OPC） |
| `OPC_UA协议1.3.4(1).xlsx` | 节点定义（按模块前缀过滤） |
| `X轴位置(1).txt` | 机械手各工站 **绝对 X 坐标** |
| `reference.md` | 模块前缀、机械手板位、工作流 JSON 模板 |

---

## 工作流总览

```text
GN 设备接入进度：
- [ ] 1. 从 xlsx 确认模块前缀与 CmdType 表
- [ ] 2. 选驱动模式（execute_command / 语义 action）
- [ ] 3. 实现 _opc_read/_opc_write + _trigger_and_wait
- [ ] 4. 写 __main__ 调试菜单，单点验证 OPC
- [ ] 5. 更新 GN_station.json（children + config + data.actions）
- [ ] 6. 从测试 yaml 生成云端 workflow JSON
- [ ] 7. 本地 unilab 启动 + 云端 notebook 联调
```

---

## 1. 工站架构

```text
GN_station（WorkstationBase 容器，class=GN_station）
├── gn_solid_weighing          # Solid_ 前缀
├── gn_centrifuge_tube_liquid_handling  # Tube_ 前缀
├── gn_robotic_arm             # Robot_ 前缀
└── …其他子模块
```

**规则**

- 子设备各自 `@device(id="gn_xxx")`，独立 OPC 连接（通常同一 `opc.tcp://192.168.6.6:4840`）。
- `GN_station.json` 的 `children` 顺序 = 2D 布局从左到右；子节点 `parent: "GN_station"`。
- 子设备 `config` 至少含：`url`、`xlsx_path`（相对 GN 目录或绝对路径）、`use_subscription: true`。
- **不要**在 workflow JSON 里引用图里不存在的设备（如已移除的 `gn_system_control`）。

---

## 2. 驱动两种模式（二选一或组合）

### 模式 A：`execute_command`（推荐：参数多、云端 workflow 直调）

参考 `solid_weighing.py`：

```python
@device(id="gn_solid_weighing", category=["workstation"], ...)
class SolidWeighingDevice(OpcUaClientWithSubscription):
    CMD_TYPE_NODE = "Solid_CmdType"
    CMD_TRIG_NODE = "Solid_CmdTrig"
    COMPLETE_NODE = "Solid_CompleteFB"

    @action(description=_EXECUTE_CMD_DOC)
    def execute_command(self, cmd_type: int, x_pos: Optional[int] = None, ..., timeout: float = 180.0) -> dict:
        return self._run(int(cmd_type), label, setpoints=self._build_setpoints(...), timeout=timeout)
```

**要点**

- 对外只注册 **一个** `execute_command`（或再加少量高层语义动作）。
- 测试流程写 `TEST_FLOW_PRESETS` 或独立 yaml，供 `__main__` 菜单使用，**不必**每个 step 都注册 `@action`。
- `_build_setpoints()` 只写非 `None` 参数到 `{Prefix}_*PosSet` / `*Speed`。

### 模式 B：语义 `@action`（推荐：步骤固定、云端用 auto-方法名）

参考 `centrifuge_tube_liquid_handling.py`：

```python
@action(auto_prefix=True, description="8通道装载（指令类型=19）")
def ch8_load(self) -> dict:
    self._set_node_or_raise("Tube_XPosSet", 3095)
    ...
    return self._trigger_and_wait(TubeCommand.CH8_LOAD_TIP, "8通道装载")
```

**要点**

- 点位 **inline 写死**在方法里（来自测试 yaml stepN）。
- 云端 workflow 节点：`"action": "auto-ch8_load"`，`device_name`: `gn_centrifuge_tube_liquid_handling`。
- 适合步骤名稳定、参数几乎不变的模块。

---

## 3. OPC 触发时序（所有 GN 模块通用）

```text
写参（*PosSet / *Speed / 业务参数）
  → 写 {Prefix}_CmdType
  → 写 {Prefix}_CmdTrig = 1
  → 等待完成反馈
  → finally: CmdTrig=0, CmdType=0（必须回读确认）
```

**完成反馈**

| 模块 | 完成节点 | 等待策略 |
|------|----------|----------|
| 固体加样 | `Solid_CompleteFB` | 多数命令等 CompleteFB=1；运动类可位置兜底（见下） |
| 离心管 | `Tube_CompleteFB` | 仅等 CompleteFB=1，**不要求**下发前 idle |
| 机械手 X 点动 | `Robot_XPosFB` | **不要**等 `Robot_FinishFB`（X 点动时常为 0） |
| 机械手工站 | `Robot_*_Done` + `Robot_FinishFB` | 两阶段：先 X 到位再 CmdType=0 |

**固体加样特殊**：`_wait_motion_complete` — 先等 CompleteFB=1，超时后用 `*PosFB` 与 setpoint 比对兜底（`MaterialZPosSet=0` 时跳过 Z 核对）。

**禁止**

- 下发前死等 `CompleteFB=1` 才允许发令（solid 已移除该 gate，会卡 reset）。
- 动作完成后干等 CompleteFB **自然变 0**（应写 CmdTrig/CmdType 清零）。
- 多个脚本/线程并发读同一 OPC 连接（易 Broken pipe）；驱动内用 `_command_lock`。

---

## 4. xlsx 节点加载

从 `OPC_UA协议1.3.4(1).xlsx` 第 0  sheet、第 5 行起解析：

- 列：模块名(0)、node_id(5)、点名(6)、dtype(7)
- 按前缀过滤：`Solid_` / `Tube_` / `Robot_`
- 注册到 `OpcUaClientWithSubscription.register_node_list`
- 代码里统一用**英文名**节点（如 `Solid_XPosSet`），与 xlsx `node_id` 末尾一致

**机械手注意**

- xlsx 名 `Robot_Pick up or place` → PLC 实际 `Robot_Pick_up_or_place`，加载时需 name_mapping。
- 工位动作节点名含尾部空格（如 `Robot_Quick_change_mechanism `），必须与 xlsx **完全一致**。

---

## 5. OPC 健壮性（必须复制）

```python
_command_lock = threading.Lock()
_OPC_WRITE_RETRIES = 2

def _reconnect_opcua(self) -> bool: ...  # disconnect → connect → _setup_subscriptions
def _opc_write(self, name, value, retries=None) -> bool: ...  # 失败重连再试
def _opc_read(self, name, force_read=False, retries=None): ...  # 连续 3 次 None 则 fail-fast
```

`centrifuge_tube_liquid_handling.py` 与 `robotic_arm.py`（经 `GnOpcUaDevice`）均已采用；新驱动同样实现。

---

## 6. 机械手 X 轴（易错，单独记）

**CmdType 1/2（X 点动）**：`Robot_XPosSet` = **相对位移** `|目标绝对坐标 - 当前 XPosFB|`，不是绝对坐标。

```python
delta = abs(target_x - current)
# 当前 < 目标 → CmdType=2（右）；当前 > 目标 → CmdType=1（左）
self._set_node_or_raise("Robot_XPosSet", delta)
# 等待 Robot_XPosFB 到达绝对 target_x
```

**CmdType 0（工站夹放）**：两阶段

1. 阶段1：X 点动到 `X轴位置(1).txt` 绝对坐标，确认 `XPosFB` 到位  
2. 阶段2：写 **绝对** `Robot_XPosSet` → `ModuleNoSet` → 工位动作节点 → `Robot_Pick_up_or_place` → `CmdType=0` → `CmdTrig=1`

**板位坐标**见 `X轴位置(1).txt` 与 `reference.md`，禁止臆造（如 `-1726`）。

---

## 7. 更新 GN_station.json

每新增子设备：

```json
{
  "id": "gn_new_module",
  "name": "显示名",
  "parent": "GN_station",
  "type": "device",
  "class": "gn_new_module",
  "config": {
    "url": "opc.tcp://192.168.6.6:4840",
    "xlsx_path": "OPC_UA协议1.3.4(1).xlsx",
    "use_subscription": true
  },
  "data": {
    "actions": [
      {
        "menu_id": "1",
        "name": "步骤说明",
        "action": "auto-execute_command",
        "params": { "cmd_type": 12, "x_pos": 20, "timeout": 180.0 }
      }
    ]
  }
}
```

- 把设备 id 加入 `GN_station.children`。
- `data.actions`：模式 A 用 `auto-execute_command` + 全参数；模式 B 用 `auto-{method_name}`。
- `position` / `pose.size` 按 2D 布局与 `X轴位置(1).txt` 对齐。

---

## 8. 生成云端 workflow JSON

参考 `固体加样测试流程.json`：

```json
{
  "target_lab_uuid": "<实验室 UUID>",
  "name": "测试流程名",
  "data": {
    "workflow_uuid": "<新 UUID>",
    "workflow_name": "测试流程名",
    "nodes": [
      {
        "uuid": "<新 UUID>",
        "name": "auto-execute_command",
        "type": "ILab",
        "device_name": "gn_solid_weighing",
        "resource_name": "gn_solid_weighing",
        "template_uuid": "c003dbf6-1d2d-4461-87cb-cfd7895ade8d",
        "template_name": "auto-execute_command",
        "param": { "cmd_type": 12, "x_pos": 20, "y_pos": 2100, "timeout": 180 }
      }
    ],
    "edges": [{ "uuid": "...", "source": "<node_uuid>", "target": "<next_uuid>" }]
  }
}
```

**规则**

- 节点仅绑定**图里已有**的 `device_name` / `resource_name`。
- `param` 字段名与 `execute_command` 参数一致（snake_case）。
- 线性流程：`edges` 串联，无需 bookend 节点除非业务需要。
- 语义动作模式：`"name": "auto-ch8_load"`，param 可为 `{}`。
- 每个 node/edge 使用新 UUID（勿复用）。

---

## 9. 本地调试与部署

### 单驱动调试

```bash
cd unilabos/devices/workstation/GN
python solid_weighing.py          # 或 centrifuge_tube_liquid_handling.py / robotic_arm.py
```

菜单顺序建议：**读状态 → 使能/复位 → 单步动作 → 整体测试流程**。

### 整站启动

```bash
pip install -e .
unilab --graph unilabos/devices/workstation/GN/GN_station.json \
  --config <config.py> --backend simple --test_mode   # 无 ROS 时用 simple
```

### 现场检查清单

- [ ] xlsx 节点找到数 = 预期（日志 `找到 N/N`）
- [ ] 单点 OPC：写参 → 触发 → CompleteFB 或 PosFB 正确
- [ ] 机械手：日志含「相对位移=…」而非把绝对坐标写入 X 点动
- [ ] workflow JSON 在云端 notebook 逐步跑通
- [ ] 同一 PLC 避免多客户端并发连接

---

## 10. 常见错误

| 现象 | 原因 | 修复 |
|------|------|------|
| reset/动作卡死 | 下发前等 CompleteFB=1 | 去掉 idle gate，直接触发 |
| X 轴越走越远 | CmdType 1/2 写了绝对坐标 | 改写相对 delta |
| 机械手 X 不动但 FinishFB=1 | 用 FinishFB 判 X 到位 | 改轮询 XPosFB |
| Broken pipe | 多线程/多进程读 OPC | `_command_lock` + 单客户端 |
| 工站动作无响应 | X 未先到位就发 CmdType=0 | 两阶段：先 X 再臂 |
| 云端节点失败 | device 不在 GN_station.json | 补图 + registry id 一致 |
| import gn_system_control | workflow 引用已删设备 | 改绑实际 driver |

---

## 11. 新模块最小模板

```python
"""新模块 — 前缀 New_，协议 OPC_UA协议1.3.4(1).xlsx"""

@device(id="gn_new_module", category=["workstation"], display_name="新模块", ...)
class NewModuleDevice(OpcUaClientWithSubscription):
    CMD_TYPE = "New_CmdType"
    CMD_TRIG = "New_CmdTrig"
    COMPLETE = "New_CompleteFB"

    def __init__(self, url, xlsx_path=DEFAULT_XLSX_PATH, ...):
        super().__init__(...)
        self._command_lock = threading.Lock()
        if xlsx_path:
            self._load_nodes_from_xlsx(xlsx_path, prefix="New_")

    @action(description="按 New_CmdType 执行")
    def execute_command(self, cmd_type: int, timeout: float = 180.0, **kwargs) -> dict:
        with self._command_lock:
            setpoints = self._build_setpoints(**kwargs)
            for n, v in setpoints.items():
                self._set_node_or_raise(n, v)
            self._set_node_or_raise(self.CMD_TYPE, int(cmd_type))
            self._set_node_or_raise(self.CMD_TRIG, 1)
            try:
                if not self._wait_complete_value(1, timeout, f"CmdType={cmd_type}"):
                    raise ValueError("动作未完成")
            finally:
                self._opc_write(self.CMD_TRIG, 0)
                self._opc_write(self.CMD_TYPE, 0)
            return {"success": True, "cmd_type": int(cmd_type)}
```

实现 `_load_nodes_from_xlsx` 时复制 `centrifuge_tube_liquid_handling.py` 并改前缀。

---

## 附加资源

- 模块前缀、机械手板位表、workflow 字段对照：[reference.md](reference.md)
