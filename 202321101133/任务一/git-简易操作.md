# Git 简易操作教程

> 写给自己的 Git 速查笔记，三人团队协作场景，够用就好。

---

## 一、Git 是什么？

Git 是一个**版本控制工具**，帮你记录代码的每一次修改。

你可以把它想象成游戏的"存档系统"——每完成一个功能就存个档，出错了可以随时回到之前的存档。

### 三个关键概念

| 概念 | 通俗理解 |
|------|----------|
| 工作区 | 你电脑上的代码文件夹，随便改 |
| 暂存区 | "购物车"，把你想要保存的修改先放进来 |
| 仓库 | 真正的"存档点"，commit之后才算存上了 |

```
工作区 ──add──> 暂存区 ──commit──> 本地仓库 ──push──> 远程仓库(GitHub)
```

---

## 二、第一天：克隆项目到本地

老板把项目建好了，你要把代码拉到自己的电脑上。

```bash
# 克隆仓库（只需要做一次）
git clone https://github.com/你的团队/项目名.git

# 进入项目目录
cd 项目名
```

---

## 三、日常工作流程（每天都要做的）

假设你今天的任务是"修复登录页面的bug"，流程如下：

### 第1步：开始工作前，先拉取最新代码

```bash
git pull
```

> 一定要先 pull！不然别人改的东西你没拿到，后面容易冲突。

### 第2步：创建一个新分支

不要在 main 分支上直接改！养成开分支的习惯。

```bash
# 创建并切换到新分支
git checkout -b fix-login-bug

# 分支命名建议：功能/类型-简短描述
# 比如：feature-用户登录、fix-首页样式、update-文档
```

### 第3步：写代码...

改完文件之后，看看自己改了什么：

```bash
# 查看修改了哪些文件
git status

# 查看具体改了哪些内容
git diff
```

### 第4步：把修改添加到暂存区

```bash
# 添加单个文件
git add 文件名.py

# 添加所有修改
git add .

# 再次确认暂存区里有什么
git status
```

### 第5步：提交（存档）

```bash
git commit -m "修复：登录页面按钮点击无反应的问题"
```

**提交信息怎么写？** 看下面：

```
好的提交信息：
  ✅ 修复：登录页面按钮点击无反应
  ✅ 新增：用户注册功能
  ✅ 优化：首页加载速度

不好的提交信息：
  ❌ 改了改
  ❌ update
  ❌ 111
```

### 第6步：推送到远程仓库

```bash
# 第一次推送这个分支
git push -u origin fix-login-bug

# 之后直接
git push
```

### 第7步：在 GitHub 上创建 Pull Request（PR）

推完之后去 GitHub 网站，会看到一个绿色的 "Compare & pull request" 按钮，点它，写一下你做了什么，让队友 review。

**第8步：合并后切回 main，删掉旧分支**

```bash
git checkout main
git pull
git branch -d fix-login-bug
```

---

## 四、三人团队协作场景

假设你和 A、B 三个人一起开发。

### 场景1：A 提交了新代码，你还没拉

```bash
# 你正在写代码，A 说"我合并了一个功能到 main"

# 先保存自己的修改（暂存）
git stash

# 拉取最新代码
git pull

# 恢复自己的修改
git stash pop
```

### 场景2：你和 B 改了同一个文件（冲突了！）

这是最"吓人"但也最常见的情况。别慌：

```bash
# pull 的时候提示 CONFLICT：
git pull
# >>> CONFLICT (content): Merge conflict in app.py

# 打开冲突的文件，会看到这样的标记：
# <<<<<<< HEAD
# 你写的代码
# =======
# B写的代码
# >>>>>>> branch-name

# 你需要手动选择保留谁的代码，然后删掉标记
# 比如保留两个人的版本：

# 处理完冲突后：
git add app.py
git commit -m "解决合并冲突：app.py中用户验证逻辑"
git push
```

> **经验：** 多沟通！改同一个文件之前先在群里说一声，能省很多事。

### 场景3：你不小心在 main 分支上改了代码

```bash
# 别慌，创建一个新分支把你的修改带走
git checkout -b my-new-feature

# 这样就切换到了新分支，修改都在新分支上
# main 分支没有任何变化
```

### 场景4：代码写烂了，想回到上一个存档

```bash
# 回到上一个 commit（修改保留在工作区）
git reset --soft HEAD~1

# 彻底丢弃所有修改，回到上一个 commit（修改全部删除！慎用！）
git reset --hard HEAD~1
```

### 场景5：临时帮队友修bug，自己的代码还没写完

```bash
# 1. 保存当前工作
git stash

# 2. 切到修bug的分支
git checkout -b hotfix

# 3. 修完提交推送

# 4. 回到原来的分支
git checkout 原来的分支

# 5. 恢复之前的工作
git stash pop
```

---

## 五、常用命令速查表

| 命令 | 作用 | 使用频率 |
|------|------|----------|
| `git status` | 查看当前状态 | 随时用 |
| `git pull` | 拉取远程最新代码 | 开始工作前 |
| `git add .` | 添加所有修改到暂存区 | commit之前 |
| `git commit -m "..."` | 提交存档 | 每完成一个小功能 |
| `git push` | 推送到远程 | commit之后 |
| `git checkout -b 分支名` | 创建并切换分支 | 开始新任务 |
| `git checkout main` | 切换到main分支 | 合并后 |
| `git branch` | 查看所有分支 | 需要切换时 |
| `git stash` | 暂存当前修改 | 临时切任务 |
| `git stash pop` | 恢复暂存的修改 | 切回来时 |
| `git log --oneline` | 查看提交历史 | 查记录 |
| `git diff` | 查看具体修改内容 | commit之前 |

---

## 六、团队约定（我们三人的规矩）

1. **main 分支永远是可运行的代码**，不要直接往 main 上 push
2. **干活前先 pull，干完及时 push**
3. **分支命名规范**：`feature-功能名` / `fix-问题名`
4. **commit 信息用中文**，说清楚改了啥
5. **每天至少 pull 一次**，别攒好几天
6. **遇到冲突别硬来**，群里问一下再处理

---

## 七、一句话总结

> **pull → 开分支 → 写代码 → add → commit → push → 提PR → 合并 → 切回main**

这就是你的 Git 日常工作流，熟了之后十分钟就能走完一圈。
