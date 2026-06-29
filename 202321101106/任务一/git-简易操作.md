# Git 简易操作手册

> 适合刚学完 Git 基础的同学，涵盖日常最常用的操作，不搞复杂的，够用就好。

---

## 一、基本概念（快速回顾）

| 概念 | 说明 |
|------|------|
| 仓库（Repository） | 存放代码的地方，分本地仓库和远端仓库 |
| 提交（Commit） | 把修改记录下来，像存档一样 |
| 分支（Branch） | 独立的开发线，互不影响 |
| 克隆（Clone） | 把远端仓库复制到本地 |
| 推送（Push） | 把本地提交上传到远端 |
| 拉取（Pull） | 把远端最新内容同步到本地 |

---

## 二、第一次使用 Git

### 1. 配置用户信息（只需做一次）

```bash
git config --global user.name "你的名字"
git config --global user.email "你的邮箱@example.com"
```

### 2. 克隆远端仓库到本地

```bash
git clone https://gitee.com/xxx/项目名.git
```

克隆完之后，进入项目目录：

```bash
cd 项目名
```

---

## 三、日常工作流程（最常用）

### 每次开始工作前：先拉取最新代码

```bash
git pull origin main
```

> 💡 好习惯：每天写代码前先 pull，避免和队友代码冲突。

---

### 写完代码后：提交并推送

```bash
# 第一步：查看哪些文件被修改了
git status

# 第二步：把修改加入暂存区（. 表示所有修改的文件）
git add .

# 第三步：提交，写清楚本次改了什么
git commit -m "完成了登录功能的页面布局"

# 第四步：推送到远端
git push origin main
```

---

## 四、三人团队的常见使用场景

### 场景一：三人同时开发不同功能

**背景：** 小明做登录模块，小红做首页，小李做用户中心。

**做法：每人创建自己的分支**

```bash
# 小明创建并切换到自己的分支
git checkout -b feature/login

# 开发完毕，推送分支到远端
git push origin feature/login
```

```bash
# 小红创建自己的分支
git checkout -b feature/homepage
git push origin feature/homepage
```

```bash
# 小李同理
git checkout -b feature/user-center
git push origin feature/user-center
```

功能完成后，各自发起 **合并请求（Pull Request / Merge Request）**，由一个人负责合并到 `main` 分支。

---

### 场景二：拉取队友最新代码，合并到自己分支

```bash
# 先切回 main 分支，拉取最新内容
git checkout main
git pull origin main

# 再切回自己的功能分支
git checkout feature/login

# 把 main 的更新合并进来
git merge main
```

---

### 场景三：出现冲突怎么办？

当两个人修改了同一个文件的同一行，就会出现冲突。

```bash
# pull 或 merge 时提示冲突
git pull origin main
# 提示：CONFLICT (content): Merge conflict in xxx.txt
```

打开冲突文件，会看到类似这样的内容：

```
<<<<<<< HEAD
我写的内容（本地）
=======
队友写的内容（远端）
>>>>>>> origin/main
```

**处理方法：**
1. 手动编辑文件，保留正确内容，删除 `<<<<<<<`、`=======`、`>>>>>>>` 这些标记
2. 保存文件
3. 重新 add 和 commit

```bash
git add .
git commit -m "解决冲突：合并登录模块"
```

---

### 场景四：写错了，撤销最近一次提交

```bash
# 撤销提交，但保留修改的文件（最安全的方式）
git reset --soft HEAD~1
```

---

### 场景五：查看提交历史

```bash
# 查看提交记录
git log --oneline

# 示例输出：
# a3f8c12 完成登录功能
# b12d903 修复首页按钮bug
# c99ae11 初始化项目
```

---

## 五、常用命令速查表

| 命令 | 作用 |
|------|------|
| `git status` | 查看当前状态（哪些文件改了） |
| `git add .` | 把所有修改加入暂存区 |
| `git commit -m "说明"` | 提交并写备注 |
| `git push origin main` | 推送到远端 main 分支 |
| `git pull origin main` | 拉取远端 main 分支最新内容 |
| `git checkout -b 分支名` | 创建并切换到新分支 |
| `git checkout 分支名` | 切换分支 |
| `git merge 分支名` | 把某分支合并到当前分支 |
| `git log --oneline` | 查看简洁的提交历史 |
| `git reset --soft HEAD~1` | 撤销最近一次提交（保留文件） |

---

## 六、一句话总结工作流

```
拉代码(pull) → 写代码 → 查状态(status) → 加暂存(add) → 提交(commit) → 推送(push)
```

> 记住这个循环，日常 90% 的操作都在这里面了。加油！💪

---

*整理者：202321101106*  
*整理时间：2026年6月*
