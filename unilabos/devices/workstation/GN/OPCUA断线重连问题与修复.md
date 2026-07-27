# GN 工作站 OPC UA 断线重连问题与修复

> 影响范围：`unilabos/devices/workstation/GN/`（10 个模块共享 1 台 PLC）
> 涉及文件：`base_opcua_client.py` / `gn_plc_client.py` / `gn_station_base.py`

## 一、问题现象

启动后每 20~24 秒日志刷一次假循环：

```
读取变量 System_IsReady 失败:
✓ OPC UA 断线重连成功
读取变量 System_IsReady 失败:
✓ OPC UA 断线重连成功
...
```

后期改成应用层探针后，症状变成：

```
GnPlcClient keepalive probe 失败（target=System_IsReady）:
  "The node id refers to a node that does not exist ..."(BadNodeIdUnknown)
✓ OPC UA 断线重连成功
[5 秒后重复]
```

## 二、根因（3 个叠加）

### R1. GN 版本基类删掉了「原版的 30s 主动 keepalive 线程」

对比原始 `unilabos/devices/base_opcua_client.py`：
- **原版**：`_connection_monitor_worker` 后台线程每 30s 主动 `client.get_namespace_array()` 探活，断线自动重连
- **GN 版**（`workstation/GN/base_opcua_client.py`）：整段删掉，只留下"读写失败被动重连"

工站空闲无业务读写时，PLC 端应用层 idle 超时（<20s）就会踢连接，无人保活。

### R2. `_read_raw / _write_raw` 异常不分级

读写失败一律触发 `_reconnect()` → **重建整个 session + 重建订阅**。任何节点级错误（`BadNodeIdUnknown` / 权限 / 类型）都会 kill 掉全站会话（9 个子设备共享），代价与副作用极大。

### R3. `_connection_pool` 与 `GnPlcClient._singletons` 双重簿记

- 基类里的 `_connection_pool` 隐式池按 URL 共享 client，但**无引用计数**
- 上层 `GnPlcClient._singletons` 显式单例 + 引用计数
- 两者语义重叠：一台设备 `disconnect()` 会误关全站的 client，池里还留着已死引用

### R4.（触发假循环的直接元凶）`System_IsReady` 在服务端不存在

CSV 里存的 `ns=4;s=|var|Inovance-X86-Linux.Application.OPC_UA.System_IsReady` 与当前 PLC 固件的 namespace 对不上；服务端返回 `BadNodeIdUnknown`。原 keepalive 把这当成「链路挂」→ 无脑 `_reconnect()` → session 被反复重建，比不做 keepalive 更差。

## 三、修复

| 编号 | 文件 | 改动 | 目的 |
|-----|------|------|------|
| F1 | `gn_plc_client.py` | 新增 `_keepalive_worker` + `_keepalive_probe`，默认间隔 5s | 补回原版主动 keepalive，间隔缩到 PLC idle 超时以下 |
| F2 | `base_opcua_client.py` | 新增 `_probe_connection()`（读标准节点 `ns=0;i=2259`），`_read_raw/_write_raw` 用探针分级 | 节点级错误不再 kill session，只在链路真挂时重连 |
| F3 | `base_opcua_client.py` | 删除类属性 `_connection_pool` 及 `__init__` 里两段池代码 | 消除双重簿记，共享 client 统一走 `GnPlcClient._singletons` + 引用计数 |
| F4 | `gn_plc_client.py` | `_keepalive_probe` 识别 `BadNodeIdUnknown` → 探标准节点若成功 → **永久切换 target** 到标准节点 + 打 1 条 WARN | CSV 节点过时时 session 仍能保活，无假循环 |

### 探针分级决策图

```
_read_raw(name):
   use_node(name).read()
   ├─ 成功 ────────────────────────────── 返回值
   └─ 失败
       ├─ _probe_connection() 通过 ──── 节点级错误，返回 None，不动 session
       └─ _probe_connection() 失败 ─── 链路挂，_reconnect() + 重试一次
```

```
_keepalive_probe(target):
   read(target)
   ├─ 成功 ────────────────────────────── True
   └─ 失败
       ├─ BadNodeIdUnknown 且 fallback 成功 ── 永久切换 target，True（session OK）
       └─ 其它异常 ─────────────────────────── False（真断线，worker 触发重连）
```

## 四、修复后行为

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| 常态（无业务读写） | 每 20~24s "读挂→重连" 一轮 | 5s 一次静默探针，无日志 |
| CSV 节点已在服务端消失 | 每 5s "探针失败→无脑重连" 死循环 | 启动 1 条 WARN 切换标准节点，之后静默 |
| 某单个节点读失败（打错 id / 类型不匹配） | 全站 session 被 kill，9 台设备连带受影响 | 只这一次读返回失败，session 不动 |
| 链路真断线 | 3s 限流下重连 1 次 | 同左（保留原语义） |

## 五、后续排查建议

R4 揭示 CSV 与 PLC 当前固件已有节点漂移，`System_IsReady` 只是被 keepalive 暴露的第一个。业务代码里凡是 `set_node_value / get_node_value` 引用的节点都可能踩坑。建议：

1. 从 PLC 重新导出 OPC UA address space，与 `opcua_gn1.3.6.csv` 做 diff
2. 或在 `GNWorkstation` 里加一个 `verify_nodes` 诊断动作，启动时列出所有 CSV 中已找不到的节点

---
**改动全景**：4 处修复共 ~110 行增 / ~140 行删（含删除的 `_connection_pool` + 原低效循环）。所有 GN 模块驱动业务层 API 保持不变，向后兼容。
