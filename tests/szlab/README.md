# SZLab Mixer 测试说明

## Docker UI 调试（推荐，新机器快速验证）

拉取镜像：

```bash
docker pull registry-1.docker.io/styxhuang/unilabos:latest
```

Mac Silicon 需指定平台：

```bash
docker pull --platform linux/amd64 registry-1.docker.io/styxhuang/unilabos:latest
```

启动 UI（前端 **http://localhost:50003/**）：

```bash
docker run --rm \
  --name unilabos-ui \
  --platform linux/amd64 \
  -p 50003:8000 \
  registry-1.docker.io/styxhuang/unilabos:latest
```

或使用 compose：

```bash
docker compose up
```

镜像内默认 preset 为 `szlab_mixer`，左侧可选 `transfer_liquid`、`run_solvent_addition` 等泵 action。页面 **OPC UA URL** 填现场地址（默认 `opc.tcp://jdht1471820.bohrium.tech:50001`）；本地伪 OPC 联调时改为 `opc.tcp://127.0.0.1:48506/`（需先在宿主机启动伪 server，见下文）。

命令行打印 Docker 说明：

```bash
python -m unilabos.devices.workstation.szlab_mixer.pump --docker-ui
```

---

## 单元测试（无需 OPC UA 服务）

```bash
cd /home/yuanbai/Uni-Lab-OS-styxhuang
PYTHONPATH=. pytest tests/szlab/test_szlab_mixer_pump.py -q
```

## OPC UA 集成测试（自动启动伪 server + flow daemon）

```bash
PYTHONPATH=. pytest tests/szlab/test_szlab_mixer_pump_opcua_ci.py -q
```

如需连外部 OPC UA（跳过本地伪设备）：

```bash
export UNILABOS_TEST_SZLAB_MIXER_OPCUA_URL=opc.tcp://your-host:50001/
PYTHONPATH=. pytest tests/szlab/test_szlab_mixer_pump_opcua_ci.py -q
```

## 手动启动伪设备（调试）

终端 1 — CSV 服务器：

```bash
PYTHONPATH=. python tests/pseudo_devices/common/opcua_csv_server.py \
  --csv unilabos/devices/workstation/szlab_mixer/pump_nodes.csv \
  --endpoint opc.tcp://127.0.0.1:48506/
```

终端 2 — flow daemon：

```bash
PYTHONPATH=. python tests/pseudo_devices/common/opcua_flow_daemon.py \
  --url opc.tcp://127.0.0.1:48506/ \
  --flow unilabos/devices/workstation/szlab_mixer/pump_flow.json
```

终端 3 — 跑 workflow 或 Python：

```bash
PYTHONPATH=. python -c "
from unilabos.devices.workstation.szlab_mixer.pump import SzlabMixerPumpDevice
from unilabos.devices.workstation.szlab_mixer.sensors import S06PipelineRoute

device = SzlabMixerPumpDevice(
    url='opc.tcp://127.0.0.1:48506/',
    timeout=8.0,
    pipeline_routes={(1, 'aspirate'): S06PipelineRoute(11, 21)},
)
print(device.transfer_liquid(pump=1, volume=10))
device.disconnect()
"
```

## 相关文件

| 文件 | 用途 |
|---|---|
| `unilabos/devices/workstation/szlab_mixer/pump_nodes.csv` | pump 专用 OPC 变量表（18 项） |
| `unilabos/devices/workstation/szlab_mixer/pump_flow.json` | 伪 PLC 完成信号规则 |
| `unilabos/devices/workstation/szlab_mixer/pump_debug.json` | 单独调试参数（action/阀位/URL） |
| `tests/szlab/pseudo_szlab_mixer_opcua_client.py` | 单元测试 pseudo client |

## 联调真机前你需要补的配置

1. **`pipeline_routes` 阀位值** — 真机前向 PLC 确认；虚拟调试可先用占位值（见下方第三节）
2. **`robot_addition_position` / `robot_stirrer_position`** — 虚拟调试可先用 `7` / `2` 验证写入
3. **（可选）扩充 CSV fixture** — 若真机变量名与 `苏州实验室_0610.csv` 有差异

---

## 虚拟调试：从零到跑通（按顺序做）

### 第 0 步：进入环境

```bash
cd ~/Uni-Lab-OS-styxhuang
conda activate unilab   # 或 mamba activate unilab
```

---

### 第 1 步：开终端 1 — 伪 OPC 变量表（一直开着）

```bash
cd ~/Uni-Lab-OS-styxhuang
PYTHONPATH=. python tests/pseudo_devices/common/opcua_csv_server.py \
  --csv unilabos/devices/workstation/szlab_mixer/pump_nodes.csv \
  --endpoint opc.tcp://127.0.0.1:48506/
```

看到 `OPC UA CSV 服务器已启动` → **不要关这个窗口**。

---

### 第 2 步：开终端 2 — 伪 PLC 逻辑（一直开着）

```bash
cd ~/Uni-Lab-OS-styxhuang
PYTHONPATH=. python tests/pseudo_devices/common/opcua_flow_daemon.py \
  --url opc.tcp://127.0.0.1:48506/ \
  --flow unilabos/devices/workstation/szlab_mixer/pump_flow.json
```

看到 `flow daemon 已启动` → **不要关这个窗口**。

---

### 第 3 步：开终端 3 — **在这里复位**（每次跑完整加液前建议执行）

**复位位置：** 任意新终端，在项目根目录运行：

```bash
cd ~/Uni-Lab-OS-styxhuang
PYTHONPATH=. python tests/szlab/reset_virtual_pump_opcua.py
```

成功会打印 18 个变量及其值，重点是：

| 变量 | 复位后 |
|---|---|
| `S06准备信号` | true |
| `S06允许加工` | true |
| `传感器状态_上位机[3].NO[1]` | true（烧杯在位） |
| `传感器状态_上位机[4].NO[12]` | true（储液瓶1） |
| `S06加工完成` | false |
| `S06参数写入完成` | false |

> 不用停终端 1、2，**只在终端 3 跑复位脚本**即可。

若复位失败 `Connection refused` → 回到第 1 步，终端 1 没起来。

---

### 第 4 步：开 UI（浏览器调试）

**方式 A — Docker（推荐，无需本地 Python 环境）**

```bash
docker run --rm --name unilabos-ui --platform linux/amd64 \
  -p 50003:8000 registry-1.docker.io/styxhuang/unilabos:latest
```

浏览器打开：**http://localhost:50003/**

**方式 B — 本地源码启动**

**仍在终端 3**（或新开终端 4）：

```bash
cd ~/Uni-Lab-OS-styxhuang
PYTHONPATH=. python -m scripts.run_workflow_local --ui --port 8014 --preset szlab_mixer
```

浏览器打开：**http://127.0.0.1:8014/**

页面上方配置：

| 字段 | 填什么 |
|---|---|
| **OPC UA URL** | `opc.tcp://127.0.0.1:48506/` |
| **Timeout** | `30` |
| CSV | 留空 |

---

### 第 5 步：跑「单步转液」`transfer_liquid`

左侧选 **transfer_liquid**，参数：

```
pump: 1
volume: 10
direction: aspirate
pipeline: aspirate
```

点执行 → 日志里应 `success: true`。

跑完后 `S06加工完成` 可能变成 true，**正常**，下一步前建议再跑一次第 3 步复位（或至少保证烧杯传感器仍是 true）。

---

### 第 6 步：跑「完整加液」`run_solvent_addition`

**先再做一次第 3 步复位**（推荐），然后参数：

```
pump: 1
aspirate_volume: 10
dispense_volume: 8
air_volume: 3
include_air_purge: true
skip_level_check: false
skip_robot: true
beaker_true_means_present: true
```

点执行 → 应 `success: true`，终端 2 会出现 3 次 `flow trigger`。

---

### 第 7 步：每轮测试循环

```
终端 1、2 一直开着
    ↓
终端 3：reset_virtual_pump_opcua.py   ← 复位在这里
    ↓
UI：跑 action
    ↓
失败 → 看 message，对照下表
```

| 报错 | 复位后还失败则检查 |
|---|---|
| 等待加液位放置烧杯超时 | 第 3 步是否执行；`传感器…[3].NO[1]` |
| 加工完成等待超时 | 终端 2 flow daemon 是否在跑 |
| 机械臂位号待定义 | UI 里 `skip_robot` 改为 true |

---

### 可选：不用 UI，命令行跑 workflow（方案 C）

终端 1、2 开着 → 终端 3 先复位 → 再执行：

```bash
PYTHONPATH=. python tests/szlab/reset_virtual_pump_opcua.py

PYTHONPATH=. python -m scripts.run_workflow_local \
  --workflow tests/szlab/example/szlab_mixer_virtual_workflow.json \
  --graph tests/szlab/example/szlab_mixer_virtual_pump_graph.json \
  --runtime-config tests/szlab/runtime_configs/szlab_mixer_pump_runtime.json \
  --url opc.tcp://127.0.0.1:48506/ \
  --timeout 30 \
  --log-file /tmp/szlab_virtual_run.log
```

---

## 联调真机前你需要补的配置

当前阶段应走**本地伪 OPC UA**，不要连 `jdht1471820.bohrium.tech`。

### 第三步：准备虚拟参数

虚拟阶段**不需要**等 PLC 给真实阀位，先用占位值把流程跑通即可。

| 参数 | 虚拟调试怎么填 | 说明 |
|---|---|---|
| `robot_addition_position` | `7` | 写进 graph config，验证 `S03_1取料编号=7` |
| `robot_stirrer_position` | `2` | 写进 graph config，验证 `S03_1放料编号=2` |
| `pipeline_routes` | 默认 `0` 或脚本里传 `11/21` | graph JSON 暂不支持 tuple 键；伪 server 只记录写入，用 `0` 也能跑通 |

已备好虚拟 graph：`tests/szlab/example/szlab_mixer_virtual_graph.json`（URL 指向 `127.0.0.1:48506`，含机械臂占位位号）。

**方式 A — 最快验证（一条命令，自动起伪 server）**

```bash
PYTHONPATH=. pytest tests/szlab/test_szlab_mixer_pump_opcua_ci.py -q -s
```

**方式 B — 手动三终端（适合看 OPC 变量变化）**

终端 1 — 伪 PLC 变量表：

```bash
cd ~/Uni-Lab-OS-styxhuang
PYTHONPATH=. python tests/pseudo_devices/common/opcua_csv_server.py \
  --csv unilabos/devices/workstation/szlab_mixer/pump_nodes.csv \
  --endpoint opc.tcp://127.0.0.1:48506/
```

终端 2 — 伪 PLC 逻辑（参数写入后翻转「加工完成」）：

```bash
cd ~/Uni-Lab-OS-styxhuang
PYTHONPATH=. python tests/pseudo_devices/common/opcua_flow_daemon.py \
  --url opc.tcp://127.0.0.1:48506/ \
  --flow unilabos/devices/workstation/szlab_mixer/pump_flow.json
```

终端 3 — 单步 Python 调 action：

```bash
cd ~/Uni-Lab-OS-styxhuang
PYTHONPATH=. python - <<'PY'
from unilabos.devices.workstation.szlab_mixer.pump import SzlabMixerPumpDevice
from unilabos.devices.workstation.szlab_mixer.sensors import S06PipelineRoute, default_s06_pipeline_routes

routes = default_s06_pipeline_routes()
routes[(1, "aspirate")] = S06PipelineRoute(control_valve=11, absolute_position=21)

device = SzlabMixerPumpDevice(
    url="opc.tcp://127.0.0.1:48506/",
    timeout=30,
    pipeline_routes=routes,
    robot_addition_position=7,
    robot_stirrer_position=2,
)
try:
    print("=== transfer_liquid ===")
    print(device.transfer_liquid(pump=1, volume=10, direction="aspirate", pipeline="aspirate"))
    print("=== run_solvent_addition ===")
    print(device.run_solvent_addition(
        pump=1, aspirate_volume=10, dispense_volume=8, air_volume=3, skip_robot=False,
    ))
finally:
    device.disconnect()
PY
```

成功标志：`success: True`；终端 2 有 flow trigger 日志；终端 1 侧可看到 `S06注射泵1抽液=10` 等写入。

**方式 C — workflow 命令行（伪 server 已启动时）**

先开终端 1（csv server）和终端 2（flow daemon），再开终端 3 执行：

```bash
PYTHONPATH=. python -m scripts.run_workflow_local \
  --workflow tests/szlab/example/szlab_mixer_virtual_workflow.json \
  --graph tests/szlab/example/szlab_mixer_virtual_pump_graph.json \
  --runtime-config tests/szlab/runtime_configs/szlab_mixer_pump_runtime.json \
  --url opc.tcp://127.0.0.1:48506/ \
  --timeout 30 \
  --log-file /tmp/szlab_virtual_run.log
```

> 使用 `szlab_mixer_pump_runtime.json`（仅 pump），避免虚拟阶段连接磁搅设备。

**方式 D — UI 调试（伪 server 已启动时）**

1. 启动 UI：`PYTHONPATH=. python -m scripts.run_workflow_local --ui --port 8014 --preset szlab_mixer`
2. 在界面把 OPC UA 地址改成 `opc.tcp://127.0.0.1:48506/`
3. 选 `transfer_liquid` 或 `run_solvent_addition` 点执行

> 磁搅 action 需要 S04x 变量，当前 CSV fixture 只有 S06；虚拟调试**先只测 pump**。

### 第四步（真机）— 参数与完整流程

真机 graph / workflow 已备好：

| 文件 | 用途 |
|---|---|
| `tests/szlab/example/szlab_mixer_production_graph.json` | 真机 device graph（含机械臂位号 + 阀位） |
| `tests/szlab/example/szlab_mixer_full_workflow.json` | 加液 → 磁搅 完整 workflow |
| `tests/szlab/presets/szlab_mixer.json` | Docker / UI preset（同上参数） |

**联调前向 PLC 确认并修改：**

- `pipeline_route_specs` 中各泵的 `control_valve` / `absolute_position`
- `robot_addition_position` / `robot_stirrer_position`（与 deck 位号一致）
- `run_stirring.position` 与 `robot_stirrer_position` 对应

**命令行跑完整流程：**

```bash
PYTHONPATH=. python -m scripts.run_workflow_local \
  --workflow tests/szlab/example/szlab_mixer_full_workflow.json \
  --graph tests/szlab/example/szlab_mixer_production_graph.json \
  --runtime-config tests/szlab/runtime_configs/szlab_mixer_runtime.json \
  --url opc.tcp://jdht1471820.bohrium.tech:50001 \
  --timeout 300 \
  --log-file /tmp/szlab_production_run.log
```

**发布 Docker 镜像（团队统一 UI）：**

```bash
docker login
./scripts/publish_unilabos_docker.sh
```

### 第五步：接入 CI（已完成）

`.github/workflows/ci-check.yml` 已包含 szlab pump 测试。本地可运行：

```bash
PYTHONPATH=. pytest tests/szlab/test_szlab_mixer_pump.py tests/szlab/test_szlab_mixer_pump_opcua_ci.py -q
```

