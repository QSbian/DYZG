# 任务三: GUI 计算器

基于 PySide6 (Qt for Python) 的图形界面数学计算器。

## 环境准备

```bash
pip install PySide6
```

或在 VS Code 中打开终端执行:

```bash
pip install -r requirements.txt
```

## VS Code 推荐插件

1. **Python** (ms-python.python) — Python 语法支持
2. **Qt for Python** (seanwu.vscode-qt-for-python) — Qt/PySide 语法高亮与代码提示

安装方法: VS Code → 扩展(Ctrl+Shift+X) → 搜索插件名 → 安装

## 运行

```bash
python calculator_gui.py
```

## 功能

- 四则运算 `+ - * /`
- 乘方 `^` 和 `pow(m, n)`（n 支持分数）
- 开平方 `sqrt(x)`
- 括号控制优先级
- 负数、实数域
- 键盘输入 + 鼠标点击
- 深色主题 UI
