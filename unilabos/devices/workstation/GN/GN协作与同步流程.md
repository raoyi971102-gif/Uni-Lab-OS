# GN 工作站协作与同步流程

本文档说明如何**仅提交** `unilabos/devices/workstation/GN/` 目录的改动，并推送到主仓库 **[1412universe/Uni-Lab-OS](https://github.com/1412universe/Uni-Lab-OS)** 的 **`GN-20260803`** 分支。

> **说明：** 以后以 `1412universe/Uni-Lab-OS` 为唯一工作仓库，不再使用 `lixinyu1011/Uni-Lab-OS`。

---

## 1. 仓库与账号关系

| 角色 | 仓库 / 账号 | 说明 |
|------|-------------|------|
| 主仓库 | [1412universe/Uni-Lab-OS](https://github.com/1412universe/Uni-Lab-OS) | 唯一 remote（`origin`），协作目标分支 = **`GN-20260803`** |
| 本地工作目录 | `D:\Download\Uni-Lab-OS` | 开发环境 |
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
cd D:\Download\Uni-Lab-OS

# origin：主仓库（唯一 remote）
git remote set-url origin git@github.com:1412universe/Uni-Lab-OS.git

# 若本地仍有旧的 upstream（lixinyu1011），可删除
git remote remove upstream 2>$null

git remote -v
```

期望结果：

```text
origin    git@github.com:1412universe/Uni-Lab-OS.git (fetch/push)
```

### 2.3 可选：快捷命令

```bash
git config alias.add-gn "!git add unilabos/devices/workstation/GN/"
```

之后可用 `git add-gn` 代替完整路径。

---

## 3. 标准开发流程（每次改动）

```text
同步 origin/GN-20260803
    ↓
新建功能分支 lfl/gn-xxx（或直接在 GN-20260803 上改，视团队约定）
    ↓
仅修改 GN 目录
    ↓
git add unilabos/devices/workstation/GN/
    ↓
commit → push 到 origin/GN-20260803（或功能分支后提 PR）
```

### 3.1 同步最新 GN-20260803

```bash
cd D:\Download\Uni-Lab-OS

git fetch origin
git checkout GN-20260803
git pull origin GN-20260803
```

> 若本地尚未创建该分支，可先执行：
> `git checkout -b GN-20260803 origin/GN-20260803`

### 3.2 新建功能分支（可选）

分支命名建议：`lfl/gn-<简述>`，例如 `lfl/gn-v1.1`、`lfl/gn-vacuum-door`。

```bash
git checkout -b lfl/gn-v1.1
```

也可直接在 `GN-20260803` 上开发并 push（本仓库为自有主仓时适用）。

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

### 3.5 推送到主仓库

```bash
# 直接推协作分支
git push -u origin GN-20260803

# 或推功能分支
git push -u origin lfl/gn-v1.1
```

---

## 4. 创建 Pull Request（可选）

若使用功能分支开发，再向 `GN-20260803` 提 PR：

1. 打开 https://github.com/1412universe/Uni-Lab-OS
2. **Pull requests → New pull request**
3. 设置：

| 项 | 值 |
|----|-----|
| base repository | `1412universe/Uni-Lab-OS` |
| **base** | **`GN-20260803`**（⚠️ 不是 `main`） |
| compare | `lfl/gn-v1.1`（你的功能分支） |

4. 填写 Title / Description → **Create pull request**

### 4.1 PR 自检清单

| 检查项 | 正确示例 |
|--------|----------|
| base 分支 | `GN-20260803` |
| commit 数量 | 通常 1～数个，与本次任务相关 |
| 改动文件 | 仅 `unilabos/devices/workstation/GN/` 下文件 |
| 不应出现 | 大量无关文件（说明 base 误选为 `main`） |

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

## 5. 合并后：本地同步

```bash
cd D:\Download\Uni-Lab-OS

git fetch origin
git checkout GN-20260803
git pull origin GN-20260803

# 可选：删除已合并的本地功能分支
git branch -d lfl/gn-v1.1
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

### 7.2 误连到旧仓库 `lixinyu1011`

以后**不要**再配置 / 使用 `lixinyu1011/Uni-Lab-OS`。确认：

```bash
git remote -v
# 应只有 origin → 1412universe/Uni-Lab-OS
```

### 7.3 PR 改动文件过多（数百 commit / 文件）

**原因：** base 误选为 `main` 而非 `GN-20260803`。  
**处理：** 关闭错误 PR，按 §4 重新创建，base 选 **`GN-20260803`**。

### 7.4 工作区还有其他目录的未提交改动

只 add GN 目录即可，其他改动不会进入 commit：

```bash
git add unilabos/devices/workstation/GN/
```

---

## 8. 快速命令参考

```bash
# 开始新任务
git fetch origin
git checkout GN-20260803
git pull origin GN-20260803
# 可选：git checkout -b lfl/gn-<name>

# 提交（仅 GN）
git add unilabos/devices/workstation/GN/
git diff --cached --name-only
git commit -m "feat(GN): ..."
git push -u origin GN-20260803   # 或功能分支名

# 同步
git fetch origin
git checkout GN-20260803
git pull origin GN-20260803
```

---

## 9. 参考链接

- 主仓库协作分支：https://github.com/1412universe/Uni-Lab-OS/tree/GN-20260803
- 主仓库：https://github.com/1412universe/Uni-Lab-OS

---

*最后更新：2026-08-03*
