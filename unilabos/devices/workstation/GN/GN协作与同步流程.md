# GN 工作站协作与同步流程

本文档说明如何**仅提交** `unilabos/devices/workstation/GN/` 目录的改动，并通过 Pull Request 合并到团队仓库 **`lixinyu1011/Uni-Lab-OS` 的 `GN` 分支**。

---

## 1. 仓库与账号关系

| 角色 | 仓库 / 账号 | 说明 |
|------|-------------|------|
| 团队上游 | [lixinyu1011/Uni-Lab-OS](https://github.com/lixinyu1011/Uni-Lab-OS) | PR 合并目标，**base 分支 = `GN`** |
| 个人 Fork | [1412universe/Uni-Lab-OS](https://github.com/1412universe/Uni-Lab-OS) | 本地 push 目标 |
| 本地工作目录 | `/Users/fenglongli/Downloads/Uni-Lab-OS` | 开发环境 |
| 仅提交范围 | `unilabos/devices/workstation/GN/` | **禁止** `git add .` |

---

## 2. 一次性环境配置

### 2.1 SSH 认证（推荐）

```bash
# 测试 GitHub SSH
ssh -T git@github.com
# 期望输出：Hi 1412universe! You've successfully authenticated...
```

公钥需已添加到 GitHub：**Settings → SSH and GPG keys**。

### 2.2 配置 Git Remote

在仓库根目录执行：

```bash
cd /Users/fenglongli/Downloads/Uni-Lab-OS

# origin：自己的 fork（用于 push）
git remote set-url origin git@github.com:1412universe/Uni-Lab-OS.git

# upstream：团队仓库（用于 pull / PR 目标）
git remote add upstream git@github.com:lixinyu1011/Uni-Lab-OS.git 2>/dev/null || \
git remote set-url upstream git@github.com:lixinyu1011/Uni-Lab-OS.git

git remote -v
```

期望结果：

```text
origin    git@github.com:1412universe/Uni-Lab-OS.git (fetch/push)
upstream  git@github.com:lixinyu1011/Uni-Lab-OS.git (fetch/push)
```

### 2.3 可选：快捷命令

```bash
git config alias.add-gn '!git add unilabos/devices/workstation/GN/'
```

之后可用 `git add-gn` 代替完整路径。

---

## 3. 标准开发流程（每次改动）

```text
同步 upstream/GN
    ↓
新建功能分支 lfl/gn-xxx
    ↓
仅修改 GN 目录
    ↓
git add unilabos/devices/workstation/GN/
    ↓
commit → push 到 origin（fork）
    ↓
提 PR：1412universe/lfl/gn-xxx → lixinyu1011/GN
    ↓
Review → Merge
    ↓
本地 GN 与 upstream/GN 同步
```

### 3.1 同步最新 GN

```bash
cd /Users/fenglongli/Downloads/Uni-Lab-OS

git fetch upstream
git checkout GN
git reset --hard upstream/GN
```

### 3.2 新建功能分支

分支命名建议：`lfl/gn-<简述>`，例如 `lfl/gn-v1.1`、`lfl/gn-vacuum-door`。

```bash
git checkout -b lfl/gn-v1.1
```

> **不要在 `GN` 分支上直接 commit 再 push**，应始终使用功能分支 + PR。

### 3.3 仅暂存 GN 目录

```bash
git add unilabos/devices/workstation/GN/
# 或：git add-gn
```

**提交前必查：**

```bash
git diff --cached --name-only
```

输出路径应**全部**以 `unilabos/devices/workstation/GN/` 开头。

若误加了其他文件：

```bash
git restore --staged <文件路径>
```

### 3.4 提交

```bash
git commit -m "feat(GN): 简要说明本次改动"
```

### 3.5 推送到 Fork

```bash
git push -u origin lfl/gn-v1.1
```

---

## 4. 创建 Pull Request

1. 打开 https://github.com/lixinyu1011/Uni-Lab-OS
2. **Pull requests → New pull request**
3. 点击 **compare across forks**
4. 设置：

| 项 | 值 |
|----|-----|
| base repository | `lixinyu1011/Uni-Lab-OS` |
| **base** | **`GN`**（⚠️ 不是 `main`） |
| head repository | `1412universe/Uni-Lab-OS` |
| compare | `lfl/gn-v1.1`（你的功能分支） |

5. 填写 Title / Description → **Create pull request**
6. 将 PR 链接发给 reviewer（如 lixinyu1011）

### 4.1 PR 自检清单

| 检查项 | 正确示例 |
|--------|----------|
| base 分支 | `GN` |
| commit 数量 | 通常 1～数个，与本次任务相关 |
| 改动文件 | 仅 `unilabos/devices/workstation/GN/` 下文件 |
| 不应出现 | 612 commits、700 files（说明 base 误选为 `main`） |

### 4.2 PR 描述模板

```markdown
## Summary
- 更新 GN 工作站 OPC UA 设备驱动（仅 `unilabos/devices/workstation/GN/`）
- （列出主要变更：驱动 / csv / yaml 等）

## Test plan
- [ ] 各设备 `__main__` 调试菜单连通性测试
- [ ] （其他现场验证项）
```

---

## 5. PR 合并后：本地同步

PR 被 merge 到 `lixinyu1011/GN` 后：

```bash
cd /Users/fenglongli/Downloads/Uni-Lab-OS

git fetch upstream
git checkout GN
git reset --hard upstream/GN

# 可选：删除已合并的本地功能分支
git branch -d lfl/gn-v1.1
```

若仍需在 fork 的 `origin/GN` 上保持一致：

```bash
git push origin GN
```

---

## 6. PR 审查期间如需修改

在**同一功能分支**上继续改：

```bash
git checkout lfl/gn-v1.1

# 改代码…
git add unilabos/devices/workstation/GN/
git commit -m "fix(GN): 根据 review 修改 xxx"
git push origin lfl/gn-v1.1
```

PR 会自动更新，无需重新创建。

---

## 7. 常见问题

### 7.1 HTTPS 报 `Invalid username or token`

GitHub 已不支持账号密码 push HTTPS。改用 SSH（见 §2.1），或将 remote 改为 `git@github.com:...`。

### 7.2 `Permission denied to 1412universe`（push 到 lixinyu1011）

**不能**直接 push 到 `lixinyu1011/Uni-Lab-OS`（无写权限）。应 push 到 **自己的 fork**（`origin`），再提跨 fork PR。

### 7.3 PR 改动文件过多（数百 commit / 文件）

**原因：** base 误选为 `main` 而非 `GN`。  
**处理：** 关闭错误 PR，按 §4 重新创建，base 选 **`GN`**。

### 7.4 commit 在 `GN` 上但还没 push

可基于当前 commit 建分支再 push：

```bash
git branch lfl/gn-xxx
git checkout lfl/gn-xxx
git push -u origin lfl/gn-xxx
```

### 7.5 工作区还有其他目录的未提交改动

只 add GN 目录即可，其他改动不会进入 commit：

```bash
git add unilabos/devices/workstation/GN/
```

---

## 8. 快速命令参考

```bash
# 开始新任务
git fetch upstream && git checkout GN && git reset --hard upstream/GN
git checkout -b lfl/gn-<name>

# 提交（仅 GN）
git add unilabos/devices/workstation/GN/
git diff --cached --name-only
git commit -m "feat(GN): ..."
git push -u origin lfl/gn-<name>

# 合并后同步
git fetch upstream && git checkout GN && git reset --hard upstream/GN
```

---

## 9. 参考链接

- 团队 GN 分支：https://github.com/lixinyu1011/Uni-Lab-OS/tree/GN
- 个人 Fork：https://github.com/1412universe/Uni-Lab-OS
- V1.0 示例 PR：`feat(GN): V1.0` → base `GN`，1 commit，26 files

---

*最后更新：2026-07-17*
