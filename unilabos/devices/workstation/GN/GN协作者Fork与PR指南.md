# GN 协作者 Fork 与 PR 指南（含 AI 提交流程）

本文档面向**外部协作者**：Fork [1412universe/Uni-Lab-OS](https://github.com/1412universe/Uni-Lab-OS)，在 `unilabos/devices/workstation/GN/` 下开发，并通过 Pull Request 合并到协作基线分支 **`lfl/gn-batch-skill`**。

可将本文档 **@ 引用给 Cursor / Claude 等 AI**，按 §6 的提示词模板自动完成 commit、push 与 PR 创建。

维护者（1412universe）向团队上游提 PR 的流程见同目录 [`GN协作与同步流程.md`](GN协作与同步流程.md)。

---

## 1. 仓库关系

```text
lixinyu1011/Uni-Lab-OS (团队 GN)          ← 最终合并目标（维护者操作）
        ↑
1412universe/Uni-Lab-OS (协作 Fork)       ← 请先 Fork 这个仓库
  基线分支：lfl/gn-batch-skill
        ↑ PR（协作者提交）
<你的用户名>/Uni-Lab-OS (你的 Fork)
  功能分支：lfl/gn-<你的名字>-<简述>
```

| 项 | 值 |
|---|---|
| **Fork 源仓库** | https://github.com/1412universe/Uni-Lab-OS |
| **协作基线分支（PR base）** | `lfl/gn-batch-skill` |
| **基线分支浏览** | https://github.com/1412universe/Uni-Lab-OS/tree/lfl/gn-batch-skill |
| **允许改动的目录** | `unilabos/devices/workstation/GN/` **仅此目录** |
| **团队上游（了解即可）** | https://github.com/lixinyu1011/Uni-Lab-OS/tree/GN |
| **设备接入 Skill** | `gn-opc-device-deploy/SKILL.md` |
| **现场 OPC UA 地址** | `opc.tcp://192.168.6.6:4840`（见 `GN_station.json`） |

---

## 2. 一次性环境配置

### 2.1 Fork 仓库

1. 打开 https://github.com/1412universe/Uni-Lab-OS  
2. 点击右上角 **Fork**  
3. 选择你的 GitHub 账号

### 2.2 Clone 与 Remote

将 `<你的GitHub用户名>` 替换为你的账号：

```bash
git clone git@github.com:<你的GitHub用户名>/Uni-Lab-OS.git
cd Uni-Lab-OS

# origin：你的 Fork（push 目标）
git remote -v

# collab：协作基线仓库（pull / 对齐用）
git remote add collab git@github.com:1412universe/Uni-Lab-OS.git 2>/dev/null || \
git remote set-url collab git@github.com:1412universe/Uni-Lab-OS.git

git fetch collab
```

### 2.3 SSH 验证

```bash
ssh -T git@github.com
# 期望：Hi <你的用户名>! You've successfully authenticated...
```

### 2.4 可选：快捷命令

```bash
git config alias.add-gn '!git add unilabos/devices/workstation/GN/'
```

---

## 3. 标准开发流程

```text
git fetch collab
    ↓
基于 collab/lfl/gn-batch-skill 新建 lfl/gn-<名字>-<简述>
    ↓
仅修改 unilabos/devices/workstation/GN/
    ↓
git add unilabos/devices/workstation/GN/
git diff --cached --name-only   # 必须全部在 GN/ 下
    ↓
commit → push 到你的 origin
    ↓
PR：base = 1412universe/Uni-Lab-OS 的 lfl/gn-batch-skill
    ↓
维护者 review → merge
```

### 3.1 新建功能分支

将 `<你的名字>`、`<简述>` 替换为实际值（示例：`lfl/gn-zhangsan-vacuum-oven`）：

```bash
git fetch collab
git checkout -b lfl/gn-<你的名字>-<简述> collab/lfl/gn-batch-skill
```

### 3.2 仅暂存 GN 目录

```bash
git add unilabos/devices/workstation/GN/
# 或：git add-gn

git diff --cached --name-only
```

**提交前检查：** 输出路径必须**全部**以 `unilabos/devices/workstation/GN/` 开头。

误加文件时：

```bash
git restore --staged <文件路径>
```

### 3.3 提交与 Push

```bash
git commit -m "feat(GN): <简要说明>"
git push -u origin lfl/gn-<你的名字>-<简述>
```

### 3.4 开新任务前同步基线

```bash
git fetch collab
git checkout lfl/gn-<你的名字>-<简述>
git rebase collab/lfl/gn-batch-skill
```

---

## 4. 手动创建 Pull Request

1. 打开 https://github.com/1412universe/Uni-Lab-OS  
2. **Pull requests → New pull request**  
3. 点击 **compare across forks**  
4. 设置：

| 项 | 值 |
|---|---|
| base repository | `1412universe/Uni-Lab-OS` |
| **base** | **`lfl/gn-batch-skill`**（⚠️ 不是 `main`） |
| head repository | `<你的GitHub用户名>/Uni-Lab-OS` |
| compare | `lfl/gn-<你的名字>-<简述>` |

**快捷链接**（替换占位符后浏览器打开）：

```text
https://github.com/1412universe/Uni-Lab-OS/compare/lfl/gn-batch-skill...<你的GitHub用户名>:Uni-Lab-OS:lfl/gn-<你的名字>-<简述>?expand=1
```

### 4.1 PR 标题

```text
feat(GN): <简要说明>
```

### 4.2 PR 描述模板

```markdown
## Summary
- （改了哪些驱动 / yaml / json / 工作流）

## Test plan
- [ ] 相关设备 `python unilabos/devices/workstation/GN/<驱动>.py` 调试通过
- [ ] 确认仅改动 `unilabos/devices/workstation/GN/`
- [ ] （其他现场验证项）
```

### 4.3 PR 自检清单

| 检查项 | 正确 |
|--------|------|
| base 分支 | `lfl/gn-batch-skill` |
| 改动范围 | 仅 `unilabos/devices/workstation/GN/` |
| commit 数量 | 与本次任务相关，通常 1～数个 |
| 不应出现 | 数百 commits / 数百 files（base 误选 `main`） |

---

## 5. 开发规范（GN 专用）

1. **只改** `unilabos/devices/workstation/GN/`，禁止 `git add .`  
2. 新设备驱动参照 `gn-opc-device-deploy/SKILL.md`  
3. 机械手 X 轴：CmdType 1/2 时 `Robot_XPosSet` 写**相对位移**；CmdType 0 工站动作写**绝对坐标**（见 `X轴位置(1).txt`）  
4. 不要提交：`.zip`、临时测试脚本、密钥、本机路径  
5. 代码注释与日志使用**简体中文**  
6. OPC 触发通用时序：写参 → `CmdType` → `CmdTrig=1` → 等完成反馈 → `CmdTrig/CmdType` 清零  

### 5.1 本地调试

```bash
pip install -e .

# 单设备
python unilabos/devices/workstation/GN/solid_weighing.py
python unilabos/devices/workstation/GN/robotic_arm.py
python unilabos/devices/workstation/GN/centrifuge_tube_liquid_handling.py

# 整站（无 ROS）
unilab --graph unilabos/devices/workstation/GN/GN_station.json --backend simple
```

---

## 6. AI 提交 PR 指南（复制给 Cursor）

在 Cursor 中 **@ 引用本文档** 及你的改动说明，使用下列提示词。将 `<占位符>` 替换为实际值。

### 6.1 首次配置仓库（新协作者）

```markdown
请按 @unilabos/devices/workstation/GN/GN协作者Fork与PR指南.md 帮我配置 Git：

- 我的 GitHub 用户名：<你的GitHub用户名>
- 协作 remote 名：collab → git@github.com:1412universe/Uni-Lab-OS.git
- 基于 collab/lfl/gn-batch-skill 创建分支：lfl/gn-<你的名字>-<简述>

完成后输出 `git remote -v` 和当前分支名。
```

### 6.2 改完代码后：commit + push + 开 PR

```markdown
请按 @unilabos/devices/workstation/GN/GN协作者Fork与PR指南.md 和
@unilabos/devices/workstation/GN/gn-opc-device-deploy/SKILL.md 提交 PR：

- 我的 GitHub 用户名：<你的GitHub用户名>
- 功能分支：lfl/gn-<你的名字>-<简述>
- PR base：1412universe/Uni-Lab-OS 的 lfl/gn-batch-skill
- 本次改动说明：<用 1～3 句话描述改了什么>
- 测试情况：<已测 / 未测及原因>

要求：
1. 只 git add unilabos/devices/workstation/GN/
2. 提交前运行 git diff --cached --name-only，确认路径全部在 GN/ 下
3. commit 信息：feat(GN): <简要说明>
4. push 到我的 origin
5. 用 gh 创建 PR（若无 gh 则给出 compare 链接）
6. PR body 使用文档 §4.2 模板，并填写 Test plan
7. 不要 push 到 lixinyu1011/Uni-Lab-OS
```

### 6.3 根据 Review 修改后更新 PR

```markdown
请按 @unilabos/devices/workstation/GN/GN协作者Fork与PR指南.md 更新已有 PR：

- 功能分支：lfl/gn-<你的名字>-<简述>
- Review 意见：<粘贴 reviewer 评论>
- 修改范围：仍仅限 unilabos/devices/workstation/GN/

在同一分支上 commit 并 push，让 PR 自动更新。不要 amend 已 push 的 commit，除非 review 明确要求。
```

### 6.4 开新任务前同步基线

```markdown
请按 @unilabos/devices/workstation/GN/GN协作者Fork与PR指南.md：

1. git fetch collab
2. 将当前分支 lfl/gn-<你的名字>-<简述> rebase 到 collab/lfl/gn-batch-skill
3. 若有冲突，仅处理 unilabos/devices/workstation/GN/ 下的文件
4. 完成后输出 git log --oneline -3
```

### 6.5 AI 执行检查清单（Agent 必读）

协作者让 AI 提 PR 时，Agent 必须逐项确认：

```text
[ ] 只 staged unilabos/devices/workstation/GN/ 下的文件
[ ] git diff --cached --name-only 无 GN 外路径
[ ] 未提交 .zip、密钥、临时测试文件
[ ] commit message 以 feat(GN): / fix(GN): 开头
[ ] push 目标为 origin（协作者自己的 Fork）
[ ] PR base = 1412universe/Uni-Lab-OS:lfl/gn-batch-skill
[ ] PR head = <协作者用户名>/Uni-Lab-OS:lfl/gn-<名字>-<简述>
[ ] 未向 lixinyu1011/Uni-Lab-OS 直接 push
```

---

## 7. 常见问题

### 7.1 无法 push 到 1412universe/Uni-Lab-OS

正常。协作者只能 push 到**自己的 Fork**，再提 PR 给 1412universe。

### 7.2 PR 显示几百个 commit / 文件

**原因：** base 误选为 `main`。  
**处理：** 关闭 PR，base 改选 **`lfl/gn-batch-skill`** 后重建。

### 7.3 HTTPS push 报 token 错误

改用 SSH：`git@github.com:<你的GitHub用户名>/Uni-Lab-OS.git`

### 7.4 工作区有其他目录的改动

只执行 `git add unilabos/devices/workstation/GN/`，其他目录改动不会进入 commit。

### 7.5 与维护者流程的区别

| 角色 | push 目标 | PR base |
|------|-----------|---------|
| **协作者（本文档）** | 自己的 Fork | `1412universe` → `lfl/gn-batch-skill` |
| **维护者** | `1412universe` Fork | `lixinyu1011` → `GN`（见 `GN协作与同步流程.md`） |

---

## 8. 相关文档

| 文档 | 用途 |
|------|------|
| [`GN协作与同步流程.md`](GN协作与同步流程.md) | 维护者向团队 upstream 提 PR |
| [`gn-opc-device-deploy/SKILL.md`](gn-opc-device-deploy/SKILL.md) | GN 设备驱动接入规范 |
| [`gn-opc-device-deploy/reference.md`](gn-opc-device-deploy/reference.md) | CmdType、板位、坐标速查 |
| [`GN_station.json`](GN_station.json) | 工站拓扑与子设备 config |

---

*最后更新：2026-07-27*
