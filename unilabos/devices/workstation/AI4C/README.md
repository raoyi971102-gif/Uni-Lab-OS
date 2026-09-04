# AI4C 工作站 JSON 开关说明

本目录的 `AI4C_station.json` 同时配置 AI4C 搬运机器人和 PRCXI 移液站。
修改开关后需要重启 Edge 才会生效。JSON 布尔值请使用 `true` / `false`，不要加引号。

机械臂与移液站之间的前端物料转移见 [`pipetting_resource_transfer.md`](./pipetting_resource_transfer.md)。

## 当前建议配置

```json
{
  "AI4C_station": {
    "simulator": false,
    "use_subscription": true,
    "create_placeholder_resource_when_missing": false,
    "pipetting_device_id": "PRCXI",
    "pipetting_deck_id": "PRCXI_Deck"
  },
  "PRCXI": {
    "debug": false,
    "setup": true,
    "is_9320": true,
    "simulator": false,
    "validate_material_volume": false,
    "step_mode": false
  }
}
```

上面是配置项摘要，实际修改位置是 `AI4C_station.json` 中对应节点的 `config`。

## AI4C 搬运机器人

### `simulator`

- `false`：连接 `url` 指定的真实 OPC UA 服务。
- `true`：连接 `url_sim` 指定的模拟 OPC UA 服务。
- 当前值：`false`。

### `use_subscription`

- `true`：启用 OPC UA 节点订阅和缓存，用于持续读取状态。
- `false`：不启用订阅，状态通过直接读取获取。
- 当前值：`true`。

### `create_placeholder_resource_when_missing`

- `true`：前端指定的取料位没有物料时，允许创建临时占位物料，搬运后会在目标位出现该物料。
- `false`：取料位没有前端物料时，不因此报错退出，也不创建新物料；只执行机器人搬运指令。
- 当前值：`false`。
- 注意：该开关只处理“前端资源缺失”，PLC 报告的实际仓位占用或硬件错误仍会正常报错。

### `pipetting_device_id` / `pipetting_deck_id`

- 机械臂动作里的仓库名仍是「移液站」，但物料挂在独立 PRCXI 设备的 deck 上。
- 当前值：`PRCXI` / `PRCXI_Deck`。
- 详见 [`pipetting_resource_transfer.md`](./pipetting_resource_transfer.md)。

## PRCXI 移液站

### `simulator`

- `false`：将步骤下发给真实 PRCXI。
- `true`：使用移液模拟后端，不下发真实硬件步骤。
- 当前值：`false`。

### `validate_material_volume`

- `true`：校验源孔和目标孔的当前物料体积，并在吸液/排液后更新孔内体积记账。
- `false`：不校验、不累计孔内当前体积，按工作流请求体积下发。
- 当前值：`false`。
- 注意：关闭的只是物料体积跟踪；枪头库存、移液轴量程和单次吸排体积等硬件安全校验仍然生效。

## `create_protocol` 动作开关

### `track_move_plate_resource_position`

该开关已从 PRCXI 启动 JSON 移到 `create_protocol` 动作，可以在每个工作流开始时单独选择。

- `true`（记账模式）：搬板取料位优先使用 `unilabos_extra.update_resource_site`；搬运后将板重新挂到目标槽位，写入新的 `Tn` 记账位置，前端位置会跟随更新。
- `false`（前端同步模式）：每次 `create_protocol` 会先按 `PRCXI_Deck` UUID 从云端主动拉取最新前端资源树，再以它为起点；同一 protocol 内搬板后更新 PRCXI 内存槽位，后续搬板和移液动作都按内存位置执行，但不把这些变化回写到前端。
- 动作默认值：`false`。
- 适用情况：工作流前由操作员在前端摆好板位，不希望上一次 `T15 → T4 → T2` 的记账结果影响本次从 T15 取板。
- 注意：关闭后，前端不会自动反映真实机器人搬板后的物理位置；新 protocol 会再次以前端为准，因此下一个工作流前应手动确认前端板位与现场一致。
- 安全策略：真实 Edge 上云端资源拉取失败时，`create_protocol` 会报错停止，不会退回使用 Edge 启动时的旧位置继续抓板。

## PRCXI 其他布尔配置

### `step_mode`

- `false`：工作流先用 `create_protocol` 创建方案并累积步骤，再用 `run_protocol` 整体执行。
- `true`：每个动作单独创建并执行方案；仅 9320 模式支持。
- 当前值：`false`。
- 注意：整体方案模式下，新工作流必须先调用 `create_protocol`，用于清空上一次未执行的步骤。

### `debug`

- `false`：调用真实 PRCXI API。
- `true`：PRCXI API 调用返回调试模拟结果，主要用于开发排查。
- 当前值：`false`。

### `setup`

- `true`：Edge 启动设备时执行 PRCXI 后端初始化/复位。
- `false`：跳过这一启动初始化步骤，主要用于调试。
- 当前值：`true`。

### `is_9320`

- `true`：使用 9320 型号行为，包括 9320 工作台矩阵 API，并允许 `step_mode`。
- `false`：使用 9300 型号行为。
- 当前值：`true`。
