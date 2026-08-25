# PRCXI 复用枪头动作：`one_channel_reuse_tip` / `eight_channels_reuse_tips`

两个动作都由 `transfer_liquid` 改造而来，共同点是**全程只取一次枪头**：首轮取、末轮丢，
中间反复「从同一个源孔吸液 → 放到下一个目标」。实现位于
[`prcxi.py`](./prcxi.py) 的 `PRCXI9300Handler`，注册表条目在
[`unilabos/registry/devices/liquid_handler.yaml`](../../../registry/devices/liquid_handler.yaml)
的 `liquid_handler.prcxi.class.action_value_mappings` 下（动作名即注册名，不带 `auto-` 前缀）。

## 两者的差异（不可互换）

| | `one_channel_reuse_tip` | `eight_channels_reuse_tips` |
|---|---|---|
| 并行度 | 1 通道 | 8 通道（整列） |
| 轴 | 单通道轴（9320 实机为左轴，`use_channels=[0]`） | 8 通道轴（9320 实机为右轴，`use_channels=[8..15]`） |
| 枪头 | 1 个 | 一整列 8 个 |
| 每轮 | 1 次吸 → 1 个目标孔 | 1 次 8 通道吸 → 1 整列目标孔 |
| `targets` | 任意个数的孔，按传入顺序 | 长度必须是 8 的倍数，每 8 个是同板同列的 A~H |
| `vols` | 长度 1（所有目标孔共用）或 == 目标孔数量 | 长度 1（所有列共用）或 == 列数 |

## 参数

两个动作的签名一致：

```python
await handler.one_channel_reuse_tip(
    sources,          # 只接受 1 个孔：每轮都从这里吸液
    targets,
    tip_racks,
    vols=50.0,        # 或 [50.0, 60.0, ...]
    blow_out_air_volume=None,
)
```

- `sources` **只接受 1 个孔**（多于 1 个直接报错）。8 通道版同样只给 1 个孔（储液槽/trough），
  内部广播成 8 份，8 个通道同时从这一个孔吸液。
- `vols` 是唯一的体积入参。`asp_vols` / `dis_vols` / `asp_flow_rates` / `dis_flow_rates`
  **不对外暴露**：吸放体积相等取自 `vols`，速率固定 30。
- 也不暴露 `use_channels`（由轴选择推导）、`mix_*`、`liquid_height`、`offsets`、`delays`、
  `touch_tip`。需要这些能力时用 `transfer_liquid`。
- `blow_out_air_volume` 可选，逐通道透传给 PLR（PRCXI 端映射为「反向吸液 / 吹样」辅助功能）。

## 轴选择

按 `pip_setting`（构造参数，形如 `{"left": {"vol": 300, "channels": 1}, "right": {"vol": 1000, "channels": 8}}`）
动态选：

1. 先筛出「并行度 >= 需求」且「量程容得下最大 `vols`」的轴；
2. 并行度**正好等于**需求的轴优先（避免单通道动作占用 8 通道轴），同档按量程从小到大取（精度优先）；
3. 单通道动作在单通道轴装不下体积时，会回退到 8 通道轴只用 1 个通道（例如 500µL 落到右轴 `[8]`）；
4. 8 通道动作找不到并行度 >= 8 的轴时直接报错，不会退化成单通道串行；
5. 未配置 `pip_setting` 时回退硬编码：单通道 → 左轴 `[0]`，8 通道 → 右轴 `[8..15]`。

通道编号约定（左 `[0..7]` / 右 `[8..15]`）与 `transfer_liquid` 一致，详见
[`flatten_utils.py`](./flatten_utils.py)。

## 边界与限制

- **体积超量程直接报错，不做拆分。** 单次体积超过所选轴量程或枪头量程时抛
  `ValueError`，不会自动拆成多轮吸放，也不会静默截断。
- **8 通道逐通道不同体积做不到。** `PRCXI9300Backend.aspirate` / `dispense` 要求同一次操作
  内所有 op 体积相同（设备端 `Imbibing` / `Tapping` 一步只带一个 `dosage`），所以 8 通道版的
  `vols` 是「每列一个体积」，而不是「每通道一个体积」。
- **整列校验是硬性的。** 8 通道整列放液在设备端是一条带 `HoleCol` + 整列 `HoleNumbers` 的指令，
  因此每 8 个目标孔必须同板、同列、按 A→H 顺序，且目标板 `num_items_y == 8`；否则报错。
- **速率 30 只写进 PLR op。** PRCXI 的 step payload 没有速率字段，实际吸放速率由设备侧决定；
  这里保留 30 是为了语义可追溯与 simulator 行为一致。
- **8 通道吸液用 `spread="custom"`。** 8 个通道插进同一个孔时，PLR 的 `wide` / `tight` 会按孔
  几何算通道间距并可能抛 `ChannelsDoNotFitError`；而 PRCXI 下发的是槽位 + 整列孔号、完全不用
  offsets，所以用 `custom`（零偏移）跳过与设备无关的几何校验。
- **液量追踪。** 8 通道每轮会从源孔扣 8 × `vols` 的体积，源孔（储液槽）液量不足时 PLR 的
  volume tracker 会报错。

## 与 `transfer_liquid` 的关系

- 复用了 `transfer_liquid` 的前置流程：首次建 `WorkTabletMatrix`、`step_mode` 建协议、
  资源解析（支持 uuid dict 入参）、自动挂 deck、`update_pipetting_position` 位置同步
  （抽成了 `_sync_pipetting_positions`，两条路径共用）。
- 不复用抽象层 `LiquidHandlerAbstract.transfer_liquid` 的主循环：后者的 `pick_up` / `drop` 由
  「相邻轮源孔身份或残液同名」推断，跨多个目标时不保证全程只用一副枪头。这两个动作直接驱动
  `_transfer_base_method`，`pick_up` / `drop` 是显式的（首轮 / 末轮）。
- 中途失败同样走 `_cleanup_after_failed_transfer`：把残留 tip 丢到 trash 并清 head 软件状态，
  下次动作无需重启 edge。

## 测试

```bash
pytest tests/devices/liquid_handling/test_prcxi_reuse_tip_actions.py
```

27 项：轮次编排（首轮取 / 末轮丢）、固定速率与轴选择、`vols` 长度规则、量程报错、
8 通道整列校验、失败清理，以及注册表暴露校验（动作名、`placeholder_keys`、`handles`、
未暴露的速率/体积参数）。
