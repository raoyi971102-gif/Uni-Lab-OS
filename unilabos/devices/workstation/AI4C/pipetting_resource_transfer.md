# AI4C 移液站物料转移

## 问题

AI4C 机械臂取放「移液站」时，硬件动作能完成，但前端物料不跟着走。

根因：`_pick_resource_from_warehouse` / `_place_held_resource_to_warehouse` 在仓库名不在 `AI4C_deck.warehouses` 时直接 `return`。移液站为避免和 `PRCXI_Deck` 重复，本来就不挂在 AI4C 台面上，于是取放都会跳过解绑、挂载和前端同步。

典型日志：

```text
移液站 不在 AI4C_deck 上（由独立设备管理），跳过资源树放料
移液站 不在 AI4C_deck 上，且已关闭缺失资源占位创建；仅执行硬件取料，不生成前端资源
```

本 deck 内的料架、称量、磁搅、HPLC 不受影响。

## 改动

不把「移液站」仓挂回 `AI4C_deck`。取放改为把**同一块带 UUID 的物料** reparent 到独立设备 `PRCXI` 的 `PRCXI_Deck`。

| 项目 | 约定 |
|------|------|
| 外部仓名 | `移液站`（动作里写死的仓库名，未改） |
| 目标设备 | `pipetting_device_id`，默认 `PRCXI` |
| 目标台面 | `pipetting_deck_id`，默认 `PRCXI_Deck` |
| 槽位 | 机械臂板位 `N` = PRCXI `TN` = `sites[N-1]` |

流程：

1. 本 deck 有该仓：仍走原来的 `unassign` / `assign` + 同步 AI4C_deck。
2. 本 deck 没有、但登记为外部仓（目前只有移液站）：查 HostNode 上的 PRCXI，对 `PRCXI_Deck` 取放。
3. 未登记的仓名：行为与从前一致（取料看占位开关，放料跳过）。

放到 PRCXI 时写入 `unilabos_extra.update_resource_site = "TN"`，后续 `create_protocol` 记账模式可以按 Tn 取板。槽位上若已有 plate adapter / module，板挂到 adapter 下，不替换 adapter。

同步：改 PRCXI 树后调用 PRCXI 节点的 `update_resource([PRCXI_Deck])`，并再同步一次 AI4C_deck。

## JSON

`AI4C_station.json` 的 `AI4C_station.config` 增加：

```json
"pipetting_device_id": "PRCXI",
"pipetting_deck_id": "PRCXI_Deck"
```

与图里 PRCXI 节点 `id`、deck 节点 `id` 保持一致。改完需重启 Edge。

## 入口代码

- 路由：`unilabos/devices/workstation/AI4C/AI4C.py` 中 `_get_external_warehouse_spec`
- 取：`_pick_resource_from_external_deck`
- 放：`_place_held_resource_to_external_deck`
- 设备查找：`HostNode.devices_instances` + `_lookup_deck_for_slot`

## 验收

上料架有板的槽 → 移液站 5 → 再取回上料架，日志应出现：

- `✓ 已从 孔板上料架[N] 解绑 …`
- `✓ 已绑定资源 … 到 PRCXI_Deck[T5]`
- 不再出现 `跳过资源树放料`

前端：板离开料架，出现在 PRCXI T5；取回后回到料架。

对照：上料架 → 固态称量 只动 AI4C_deck，行为不变。

## 测试

```bash
pytest tests/devices/workstation/test_ai4c_pipetting_resource_transfer.py tests/devices/workstation/test_ai4c_missing_resource_mode.py
```
