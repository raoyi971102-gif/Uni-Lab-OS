# SZLab Robot PLC Contract

## Temporary Completion Signal

- Temporary variable name: `机器人Busy信号`
- Direction: PLC->PC
- Semantics: `True` means robot task is running or not complete; `False` means task is complete or idle.
- Replacement note: update `busy_variable_name` in the `szlab_mixer_robot` graph/runtime config after the PLC engineer provides the final variable name.

## PC->PLC Reset Rule

PC->PLC variables are reset by Uni-LabOS after task completion or after a failed/timeout task submission. PLC->PC variables are read-only from Uni-LabOS and must be reset by PLC logic.

## Status-Maintenance Precheck

Every robot action runs the reusable status-maintenance helper before writing action variables:

1. Confirm current origin position.
2. Clear current origin parameters.
3. Confirm robot arm is idle.
4. Move from current origin to the new `Sxx` station through the normal task submit path.

The exact PLC variables for "current origin" and "clear current origin parameters" are not confirmed yet. Runtime config can provide `current_origin_variable` and `clear_origin_variables`; until then the helper records these missing confirmations in the action result and still enforces the Busy idle check.

## Robot Task Variables

| Station | Action | Task Number | PC->PLC Variables | Reset Values |
|---|---:|---:|---|---|
| S01 | pick product | 1 | `S01出入料产品`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S01 | pick position | 2 | `S01取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S02 | place | 3 | `S02取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S02 | pick | 4 | `S02取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S03 | place | 5 | `S03取放料产品`, `S03取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S03 | pick | 6 | `S03取放料产品`, `S03取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S04 | place | 7 | `S04取放料编号`, `PLC_R任务号` | `S04取放料编号=0`, `PLC_R任务号=0` |
| S04 | pick | 8 | `S04取放料编号`, `PLC_R任务号` | `S04取放料编号=0`, `PLC_R任务号=0` |
| S05 | place | 9 | `PLC_R任务号` | `PLC_R任务号=0` |
| S05 | pick | 10 | `PLC_R任务号` | `PLC_R任务号=0` |
| S06 | place | 11 | `PLC_R任务号` | `PLC_R任务号=0` |
| S06 | pick | 12 | `PLC_R任务号` | `PLC_R任务号=0` |
| S071 | place | 13 | `S071取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S071 | pick | 14 | `S071取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S072 | place | 15 | `S072取放料产品`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S072 | pick | 16 | `S072取放料产品`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S08 | place | 17 | `S08取放料产品`, `S08取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S08 | pick | 18 | `S08取放料产品`, `S08取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S09 | place | 19 | `S09取放料产品`, `S09取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S09 | pick | 20 | `S09取放料产品`, `S09取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S10 | place | 21 | `S10取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S10 | pick | 22 | `S10取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S11 | place | 23 | `S11取放料产品`, `S11取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |
| S11 | pick | 24 | `S11取放料产品`, `S11取放料编号`, `PLC_R任务号` | all written PC->PLC variables reset to `0` |

## Sensor Gate Rules

Sensor values are PLC->PC. `True` means occupied; `False` means empty. Every pick action requires the source sensor to be `True`; every place action requires the target sensor to be `False`. If the gate fails, Uni-LabOS does not write action variables or `PLC_R任务号`.

Confirmed built-in mappings:

- S02 TIP positions 1-6: `传感器状态_上位机[0].NO[0]` to `[0].NO[5]`.
- S03 unused beakers/sample vials: mappings from `plc.py` `S3_UNUSED_BEAKER_SENSORS` and `S3_UNUSED_SAMPLE_VIAL_SENSORS`.
- S04 mixer positions 1-6: `传感器状态_上位机[2].NO[10]` to `[2].NO[15]`.
- S05 photo material sensor: `传感器状态_上位机[3].NO[0]`.
- S06 material sensor: `传感器状态_上位机[3].NO[1]`.
- S071 powder container sensors: `传感器状态_上位机[3].NO[8]` to `[3].NO[13]`.
- S09 TIP positions 1-2: `传感器状态_上位机[4].NO[5]` and `[4].NO[6]`.
- S09 beaker position 1: `传感器状态_上位机[4].NO[7]`.
- S10 liquid reagent sensors: mappings from `plc.py` `S10_LIQUID_REAGENT_SENSORS`.
- S11 used beakers/sample vials: mappings from `plc.py` `S11_USED_BEAKER_SENSORS` and `S11_USED_SAMPLE_VIAL_SENSORS`.

Actions that require a caller-supplied exact sensor variable because the source/target mapping is not yet confirmed in PLC docs:

- `submit_pick_from_s01(source_sensor_variable=...)`
- `submit_place_to_s072(target_sensor_variable=...)`
- `submit_pick_from_s072(source_sensor_variable=...)`
- `submit_place_to_s08(target_sensor_variable=...)`
- `submit_pick_from_s08(source_sensor_variable=...)`

## Field Confirmation Items

- Replace `机器人Busy信号` with the final PLC Busy variable name.
- Confirm whether the Busy signal always transitions `False -> True -> False` after `PLC_R任务号` is written.
- Confirm final origin/current-position variables for status maintenance, including the variable used to clear current origin parameters.
- Confirm exact source/target sensor mapping for S01, S072, S08, and S09 liquid reagent full/not-full selection.
- Confirm whether all PC->PLC task variables use `0` as the final reset value.
- S07固体加样准备 is not ready. The Feishu flowchart text is incomplete: "再从固体粉末容器瓶平台上与固体粉末容器瓶". Do not implement this preparation workflow until the missing PLC variables and complete motion text are confirmed.
