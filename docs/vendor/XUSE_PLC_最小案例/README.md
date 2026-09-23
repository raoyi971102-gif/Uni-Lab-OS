# XUSE 与 PLC 交互：供应商最小案例

这是从 XMU 分支的 XUSE 工站驱动中提取的、可以单独交给 PLC 供应商阅读的最小通信案例。

## 1. 先看结论

- **通信角色**：XUSE Python 程序是 OPC UA Client；PLC 是 OPC UA Server。
- **连接方式**：供应商只需要提供 OPC UA Endpoint，并在服务器上发布约定的变量节点。
- **节点寻址**：当前案例使用 `ns=4;s=uniab|中文节点名` 形式的 NodeId。NodeId 必须和 CSV 中完全一致；如果供应商使用自己的 NodeId，只需要同步修改 CSV/映射表。
- **动作模式**：Python 写入参数和触发位，等待 PLC 返回请求/完成/故障状态；完成后由 Python 复位触发位，PLC 再复位完成位。
- **最小依赖**：供应商自己的程序只需要实现 OPC UA Server 和下面的节点状态机，不需要安装 Uni-Lab-OS。

本目录内容：

- `xuse_plc_minimal_client.py`：不依赖 Uni-Lab-OS 的 Python OPC UA 客户端示例。
- `xuse_nodes_minimal.csv`：最小节点清单，含中文名、英文别名、数据类型和 NodeId。
- `requirements.txt`：运行客户端所需的 Python 依赖。
- 本说明：PLC 侧协议、时序、数据类型和验收要点。

## 2. PLC 需要提供的最小 OPC UA 节点

CSV 是通信契约的准确信息源。节点至少应具备以下属性：

| 节点 | 方向（以 Python 为准） | 类型 | 作用 |
|---|---|---:|---|
| `Robotic_Arm_Idle_1` | PLC → Python | Boolean | 机械臂 1 可接收动作 |
| `Robotic_Arm_Fault_1` | PLC → Python | Boolean | 机械臂 1 故障；为 `true` 时拒绝动作 |
| `Robotic_Arm_Target_Position_Code_1` | Python → PLC | Int16 | 目标位置代码 |
| `Robotic_Arm_Target_Pick_Place_Code_1` | Python → PLC | Int16 | 取放动作代码 |
| `Robotic_Arm_Action_Trigger_1` | Python → PLC | Boolean | 动作触发位，使用 `false → true` 上升沿 |
| `Robotic_Arm_Action_Complete_1` | PLC → Python | Boolean | 动作完成位 |
| `Add_Sample_Occupied` | PLC → Python | Boolean | 加样位有工件 |
| `Add_Sample_Request_Process` | PLC → Python | Boolean | 加样单元已准备好、请求开始 |
| `Add_Sample_Start_Process` | Python → PLC | Boolean | Python 确认开始加样 |
| `Add_Sample_Process_Complete` | PLC → Python | Boolean | 加样完成 |
| `Powder_Name` | PLC → Python | String | 本次实际粉末名称 |
| `Add_Sample_Weight` | PLC → Python | Float | 目标加粉重量 |
| `Powder_Weight` | PLC → Python | Float | 实际加粉重量 |

`Ball_Mill_*` 节点也采用完全相同的 `Request → Start → Complete` 模式，清单中已附上球磨的三个握手节点作为第二个工艺示例。

## 3. 通用工艺握手

以加样为例，Python 侧的动作顺序是：

```text
1. （可选）等待 Add_Sample_Occupied == true
2. 等待 Add_Sample_Request_Process == true
3. 写 Add_Sample_Start_Process = true
4. 等待 Add_Sample_Process_Complete == true
5. 读取 Powder_Name / Add_Sample_Weight / Powder_Weight
6. 写 Add_Sample_Start_Process = false
7. 等待 Add_Sample_Process_Complete == false
8. 本次动作结束
```

PLC 侧建议采用以下状态机：

```text
READY:
    Request_Process = true
    Start_Process = false
    Process_Complete = false

RUNNING:
    检测到 Start_Process 的上升沿后开始设备动作
    Request_Process 可以继续保持 true
    Process_Complete = false

DONE:
    动作成功后置 Process_Complete = true
    保持 Complete，直到检测到 Start_Process = false

ACKNOWLEDGED:
    检测到 Start_Process = false 后清 Process_Complete = false
    回到 READY
```

动作失败时应清晰地提供故障位或错误码；不要在没有完成的情况下把 `Process_Complete` 置为 `true`。所有等待都应有超时，客户端示例默认 120 秒。

## 4. 机械臂动作握手

以“机械臂 1 从罐架第 1 位取罐”为例：

```text
1. 等待 Robotic_Arm_Idle_1 == true
2. 检查 Robotic_Arm_Fault_1 == false
3. 写 Robotic_Arm_Action_Complete_1 = false（清理上一次完成状态）
4. 写 Robotic_Arm_Target_Position_Code_1 = 1       # 罐架区
5. 写 Robotic_Arm_Target_Pick_Place_Code_1 = 1     # 取罐架第 1 位
6. 写 Robotic_Arm_Action_Trigger_1 = false
7. 短暂等待后写 Robotic_Arm_Action_Trigger_1 = true
8. 等待 Robotic_Arm_Action_Complete_1 == true
9. 写 Robotic_Arm_Action_Trigger_1 = false
10. 等待 Robotic_Arm_Action_Complete_1 == false
```

位置代码和取放代码由 PLC 与上位机共同维护。当前 XUSE 示例中，机械臂 1 的常用位置代码为：

| 代码 | 位置 |
|---:|---|
| 1 | 罐架区 |
| 2 | 加珠区 |
| 3 | 开罐区 |
| 4 | 刮粉区 |
| 5 | 过筛区 |
| 6 | 加粉区 |
| 7 | 球磨区 |

罐架取罐代码为 `1 + rack_position - 1`，因此第 1～32 位对应 `1～32`。

## 5. 参数下发握手（通用模板）

球磨和马弗炉参数下发也遵循同一上升沿模式：

```text
写 Parameter_Send = false
等待约 0.5 秒
写 Parameter_Send = true
等待 Parameter_Send_Complete = true
写 Parameter_Send = false
等待 Parameter_Send_Complete = false
```

例如球磨使用：

- `Ball_Mill_Parameter_Send`
- `Ball_Mill_Parameter_Send_Complete`

马弗炉使用带编号的节点，例如 `Muffle_Furnace_Parameter_Send_1` 和 `Muffle_Furnace_Parameter_Send_Complete_1`。

参数的工程单位和缩放规则必须和双方确认的变量表一致；本案例不把 Excel 参数模板当作 PLC 通信协议的一部分。

## 6. 运行最小 Python 客户端

```powershell
python -m pip install -r requirements.txt
python xuse_plc_minimal_client.py --endpoint opc.tcp://192.168.1.10:4840 --action add-sample
```

机械臂示例：

```powershell
python xuse_plc_minimal_client.py --endpoint opc.tcp://192.168.1.10:4840 --action arm-pick --rack-position 1
```

如启用了 OPC UA 用户认证，再加 `--username` 和 `--password`。示例不会保存密码，也不会连接云端。

## 7. 验收时序

供应商可以用 PLC 仿真或真实 PLC 按下面顺序验收：

1. OPC UA 客户端能读取 `Robotic_Arm_Idle_1` 和 `Add_Sample_Request_Process`。
2. 客户端写入一个 Int16 目标代码后，PLC 读到相同值。
3. 客户端把 `Robotic_Arm_Action_Trigger_1` 从 `false` 置为 `true`，PLC 返回 `Robotic_Arm_Action_Complete_1 = true`。
4. 客户端复位 Trigger 后，PLC 清除 Complete。
5. 加样流程中，PLC 先置 Request，客户端置 Start，PLC 完成动作后置 Complete；客户端复位 Start 后 PLC 清除 Complete。
6. 故障位为 `true` 时，客户端拒绝发起动作；PLC 也应拒绝执行并保持完成位为 `false`。
7. 断开 OPC UA 连接后，PLC 不应继续执行未确认的旧触发；重新连接后应从安全空闲状态开始。

## 8. 不包含在交付案例中的内容

为了让供应商只看到通信必需部分，本案例没有包含 Uni-Lab-OS 的完整运行环境、ROS、资源树、云端 AK/SK、实验流程、Excel 模板和业务日志；完整 XUSE 驱动文件已单独放在 `reference_source/` 供阅读。原始仓库中的连接凭据也不应对外转发。

如需扩展动作，只需按相同模式增加节点并在双方确认的变量表中固定 NodeId、数据类型、读写方向、触发条件、完成条件、错误条件和复位条件。

## 9. 完整参考源文件

`reference_source/` 保留了本案例对应的完整源文件内容，没有截断或改写：

- `XUSE.py`：XUSE 工站动作实现，包含节点读写、等待、故障检查和动作时序。
- `base_opcua_client.py`：OPC UA 连接、节点注册、读写、订阅、缓存和重连。
- `XUSE_CONSTS.py`：机械臂位置代码、取放代码和故障节点常量。
- `xuse_variables.csv`：完整节点表（原始版本）。
- `XUSE.json`：工站配置示例，展示 `url` 和 `csv_path` 如何传给设备驱动。

这些文件依赖 Uni-Lab-OS 的资源和注册系统，供应商无需完整运行它们；阅读 PLC 交互时优先看 `base_opcua_client.py`、`XUSE.py` 中的 `_wait_until_*`/`set_node_value` 调用和 CSV 节点定义。

