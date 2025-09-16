

# 🚀 Git 多人协作快速入门

## 场景假设

你和朋友在同一个项目里写代码：

* `main` 分支（默认分支，线上可用版本）
* 你要新写一个功能 `feature-A`
* 朋友要修一个 bug `fix-bug`

---

## 基本命令流程

### 1. 克隆项目

每个人先把远程仓库克隆到本地：

```bash
git clone https://github.com/xxx/project.git
cd project
```

---

### 2. 新建分支开发

在本地创建并切换到分支：

```bash
git checkout -b feature-A
```

解释：

* `-b` 表示新建一个分支。
* 你现在是在 `feature-A` 分支上写代码。

写完代码 → 添加 & 提交：

```bash
git add .
git commit -m "完成功能A"
```

推送分支到远程（第一次推送要带 `-u`）：

```bash
git push -u origin feature-A
```

---

### 3. 另一位同事的操作

朋友创建一个 bug 修复分支：

```bash
git checkout -b fix-bug
# 修改代码
git add .
git commit -m "修复登录bug"
git push -u origin fix-bug
```

---

### 4. 合并分支

通常流程是：

* **不要直接在本地合并**
* 在 GitHub/Gitee 上发起 **Pull Request (PR)**，由团队审核后合并到 `main`。

但你要理解命令行怎么做，本地也可以：

切回 `main` 分支：

```bash
git checkout main
```

拉取远程更新：

```bash
git pull origin main
```

合并分支：

```bash
git merge feature-A
```

解决冲突（如果有），然后提交：

```bash
git add .
git commit -m "合并 feature-A 到 main"
```

推送到远程：

```bash
git push origin main
```

---

### 5. 解决多人冲突的常见情况

假设你和朋友同时修改了 `index.html`，都提交了，结果推送时报错。

做法：

1. 先拉取远程更新

   ```bash
   git pull origin main
   ```

   可能会出现冲突。

2. Git 会在文件里标记冲突区域：

   ```html
   <<<<<<< HEAD
   你写的内容
   =======
   朋友写的内容
   >>>>>>> origin/main
   ```

3. 手动修改为最终正确的版本，然后提交：

   ```bash
   git add .
   git commit -m "解决冲突"
   git push origin main
   ```

---

## 🔑 记住的几个口诀

1. **新功能 → 新建分支**，不要在 `main` 上直接开发。
2. **提交到远程前 → 先 pull 更新**，避免冲突。
3. **遇到冲突不要慌**，Git 会告诉你冲突在哪，自己改完再提交。
4. **合并分支最好走 PR**，方便团队 review。

---

 `update`，其实在 Git 里 **没有单独叫 `update` 的命令**，但是它的功能对应的是 **更新本地仓库**：

* 从远程获取最新内容 → `git fetch`
* 把远程更新合并进本地分支 → `git pull`
  这就是我们日常说的「更新」。
---

# 📝 Git 常用命令清单

## 🟢 初始化 & 仓库操作

```bash
git init                # 在本地新建一个 Git 仓库
git clone <url>         # 克隆远程仓库到本地
```

---

## 📂 文件操作

```bash
git status              # 查看当前仓库状态（改动、未跟踪文件）
git add <file>          # 将某个文件加入暂存区
git add .               # 将所有修改过的文件加入暂存区
git commit -m "描述"    # 提交更改到本地仓库
```

---

## 🔄 更新与同步

```bash
git fetch               # 获取远程最新版本，但不合并
git pull                # 获取并自动合并到当前分支（= fetch + merge）
git push                # 将本地提交推送到远程
git push -u origin 分支名   # 第一次推送分支时使用，建立追踪关系
```

---

## 🌿 分支管理

```bash
git branch              # 查看本地分支
git branch -r           # 查看远程分支
git branch <分支名>     # 创建新分支
git checkout <分支名>   # 切换到指定分支
git checkout -b <分支名> # 创建并切换到新分支
git merge <分支名>      # 合并指定分支到当前分支
git branch -d <分支名>  # 删除本地分支
git push origin --delete <分支名> # 删除远程分支
```

---

## 🕒 历史记录

```bash
git log                 # 查看提交历史
git log --oneline       # 简洁模式查看提交历史
git diff                # 查看工作区与暂存区的差异
git diff --staged       # 查看已暂存的改动
```

---

## ⏪ 撤销与回退

```bash
git checkout -- <file>  # 丢弃工作区的修改（回到最后一次提交）
git reset HEAD <file>   # 取消已暂存的文件
git reset --hard <版本号> # 回退到某个历史提交（慎用）
git revert <版本号>     # 生成一次新的提交，用来撤销某个提交
```

---

## 👥 多人协作常用

```bash
git pull origin main            # 拉取远程 main 分支最新代码并合并
git fetch origin                # 获取远程更新，但不合并
git merge origin/main           # 手动合并远程 main 分支
git rebase origin/main          # 把本地提交放到远程 main 之后（更干净）
```

---

# 📊 Git 多人协作流程图

我建议一个图示：

```
        ┌───────────┐
        │ git clone │  ← 每个人先克隆远程仓库
        └─────┬─────┘
              │
              ▼
        ┌────────────┐
        │ git checkout -b feature-xxx │ ← 新建分支开发
        └────────────┘
              │
   ┌──────────┴───────────┐
   ▼                      ▼
git add .             git commit -m "说明"
   │                      │
   ▼                      ▼
git pull origin main  ← 确保更新
   │
   ▼
git push origin feature-xxx
   │
   ▼
 在 GitHub 上发起 Pull Request
   │
   ▼
 审核 / 合并到 main 分支
   │
   ▼
git pull origin main  ← 其他人同步最新代码
```
