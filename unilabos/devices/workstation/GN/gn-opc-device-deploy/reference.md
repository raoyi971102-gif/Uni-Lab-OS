# GN OPC 设备部署 — 参考表

## OPC 模块前缀（1.3.4 xlsx）

| 模块 | 前缀 | 驱动 class | registry id |
|------|------|------------|-------------|
| 固体加样 | `Solid_` | `SolidWeighingDevice` | `gn_solid_weighing` |
| 离心管液体处理 | `Tube_` | `CentrifugeTubeLiquidHandlingDevice` | `gn_centrifuge_tube_liquid_handling` |
| 机械手 | `Robot_` | `RoboticArmDevice` | `gn_robotic_arm` |
| 堆栈 | `Stack_` | （待接） | `gn_rotary_stack` |
| 锁紧机构 | `Lock_` / 工站节点 | | `gn_locking_mechanism` |

新增模块：在 xlsx 中确认英文前缀，驱动 `_load_nodes_from_xlsx(..., prefix="Xxx_")` 过滤。

---

## 固体加样 CmdType（节选）

| 值 | 含义 |
|----|------|
| 1/2 | X 左/右 |
| 3/4 | Y 里/外 |
| 11 | 加料（仅 CompleteFB，timeout 可 600s） |
| 12/13 | 夹爪夹/放 |
| 14/15 | 天平去皮/称重 |
| 20 | 复位 |
| 23 | xyz 回原点 |

写参节点：`Solid_XPosSet`, `Solid_YPosSet`, `Solid_MaterialZPosSet`, `Solid_GripperZPosSet`, `Solid_DoorPosSet`, `Solid_VoluneWeightSet`, `Solid_*Speed`。

---

## 离心管 CmdType（节选）

| 值 | 语义方法示例 |
|----|--------------|
| 19–22 | ch8_load / ch8_aspirate / ch8_dispense / ch8_unload |
| 27–30 | small_gripper_open_lid / close_lid / pick / place |
| 31–32 | big_gripper_pick / place |
| 36–37 | reset / home_xyz |
| 35 + STOP | ultrasound_mix；`Tube_UltrasoundSTOP` 脉冲停止 |

---

## 机械手 ModuleNoSet → 工位（1.3.4）

| ModuleNo | 工站 | 动作节点 | Done 节点 |
|----------|------|----------|-----------|
| 1 | 锁紧 | `Robot_Locking_mechanism` | `Robot_Locking_mechanism_Done` |
| 2 | 快换 | `Robot_Quick_change_mechanism ` | `Robot_Quick_change_mechanism_Done` |
| 3 | 离心机 | `Robot_Centrifuge` | `Robot_Centrifuge_Done` |
| 4 | 9320 | `Robot_9320` | `Robot_9320_Done` |
| 5 | 离心管液体处理 | `Robot_Centrifuge_tube_liquid_handling` | `Robot_Centrifuge_tube_liquid_handling_Done` |
| 6 | 堆栈 | `Robot_Stack` | `Robot_Stack_Done` |
| 7 | 固体加样 | `Robot_Solid_feed` | `Robot_Solid_feed_Done` |
| 8 | 烘箱 | `Robot_Oven` | `Robot_Oven_Done ` |
| 9 | 真空烘箱 | `Robot_Vacuum_oven` | `Robot_Vacuum_oven_Done` |

工站夹放：`CmdType=0`，`Robot_Pick_up_or_place`（1=夹，0=放）。

---

## X轴位置(1).txt — 机械手绝对坐标

| 工站 | 绝对 X |
|------|--------|
| 锁紧机构 / 烘箱 | -13278 |
| 快换 | -10478 |
| 离心机 | -8582 |
| 真空烘箱 | -6726 |
| 9320 | -3926 |
| 离心管（小瓶大瓶） | 874 |
| 离心管（储液槽） | -1326 |
| 堆栈孔板 | 3274 |
| 堆栈小瓶大瓶 | 2473 |
| 固体加样小瓶 | 5971 |
| 固体加样孔板 | 6318 |

**X 点动（CmdType 1/2）**：`XPosSet = |目标 - 当前 XPosFB|`  
**工站命令（CmdType 0）**：`XPosSet = 上表绝对值`

---

## GN_station.json — config 模板

```json
{
  "url": "opc.tcp://192.168.6.6:4840",
  "xlsx_path": "OPC_UA协议1.3.4(1).xlsx",
  "use_subscription": true,
  "cache_timeout": 5.0,
  "subscription_interval": 500
}
```

---

## workflow JSON — param 与 execute_command 对照（固体加样）

| param 字段 | Solid 节点 |
|------------|------------|
| `cmd_type` | `Solid_CmdType` |
| `x_pos` | `Solid_XPosSet` |
| `y_pos` | `Solid_YPosSet` |
| `material_z_pos` | `Solid_MaterialZPosSet` |
| `gripper_z_pos` | `Solid_GripperZPosSet` |
| `door_pos` | `Solid_DoorPosSet` |
| `volune_weight` | `Solid_VoluneWeightSet` |
| `x_speed` … `door_speed` | `Solid_XSpeed` … `Solid_DoorSpeed` |
| `timeout` | 驱动内等待秒数（非 OPC 节点） |

云端模板 UUID（固体加样 execute_command）：`c003dbf6-1d2d-4461-87cb-cfd7895ade8d`

---

## 测试 yaml → 代码 映射流程

1. 维护 `xxx测试流程.yaml`（step0, step1, …）
2. 驱动：`TEST_FLOW_PRESETS` 或语义方法内 inline 相同数值
3. `GN_station.json` → `data.actions` 菜单
4. 生成 `xxx测试流程.json` → 云端 notebook

保持三处参数一致；改 yaml 后同步改驱动与 JSON。
