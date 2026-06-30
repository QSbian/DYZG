#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI 计算器 —— 基于 PySide6 (Qt for Python)
==========================================
用法: python calculator_gui.py

功能: 四则运算、括号、乘方(^/pow)、开方(sqrt)、负数、实数域
"""

import sys
import math
import re

# ────────────── 命令行计算器核心（复用任务二）──────────────

class CalcError(Exception):
    pass


class Calculator:
    """递归下降数学表达式解析器"""

    def __init__(self, expression: str):
        self.tokens = self._tokenize(expression)
        self.pos = 0

    def _tokenize(self, expr: str):
        tokens = []
        i, n = 0, len(expr)
        while i < n:
            ch = expr[i]
            if ch.isspace():
                i += 1
                continue
            if ch.isalpha() or ch == '_':
                start = i
                while i < n and (expr[i].isalnum() or expr[i] == '_'):
                    i += 1
                tokens.append(('FUNC', expr[start:i]))
                continue
            if ch.isdigit() or ch == '.':
                start = i
                dots = 0
                while i < n:
                    c = expr[i]
                    if c.isdigit():
                        i += 1
                    elif c == '.' and dots == 0:
                        dots = 1
                        i += 1
                    elif c.lower() == 'e':
                        i += 1
                        if i < n and expr[i] in '+-':
                            i += 1
                    else:
                        break
                tokens.append(('NUM', float(expr[start:i])))
                continue
            if ch in '+-*/^(),':
                tokens.append(('OP', ch))
                i += 1
                continue
            raise CalcError(f"无法识别的字符: '{ch}'")
        tokens.append(('END', None))
        return tokens

    def _peek(self):
        return self.tokens[self.pos]

    def _consume(self, typ=None, val=None):
        t = self.tokens[self.pos]
        if typ is not None and t[0] != typ:
            raise CalcError(f"语法错误: 期望 {typ}, 实际 {t}")
        if val is not None and t[1] != val:
            raise CalcError(f"语法错误: 期望 '{val}', 实际 '{t[1]}'")
        self.pos += 1
        return t

    def parse(self):
        result = self._expression()
        if self._peek()[0] != 'END':
            raise CalcError(f"多余的 token: {self._peek()}")
        return result

    def _expression(self):
        left = self._term()
        while self._peek()[0] == 'OP' and self._peek()[1] in '+-':
            op = self._consume('OP')[1]
            right = self._term()
            left = left + right if op == '+' else left - right
        return left

    def _term(self):
        left = self._unary()
        while self._peek()[0] == 'OP' and self._peek()[1] in '*/':
            op = self._consume('OP')[1]
            right = self._unary()
            if op == '*':
                left *= right
            else:
                if right == 0:
                    raise CalcError("数学错误: 除以零")
                left /= right
        return left

    def _unary(self):
        if self._peek()[0] == 'OP' and self._peek()[1] in '+-':
            op = self._consume('OP')[1]
            val = self._unary()
            return -val if op == '-' else val
        return self._power()

    def _power(self):
        base = self._atom()
        if self._peek()[0] == 'OP' and self._peek()[1] == '^':
            self._consume('OP', '^')
            exp = self._unary()
            base = base ** exp
        return base

    def _atom(self):
        t = self._peek()
        if t[0] == 'NUM':
            return self._consume('NUM')[1]
        if t[0] == 'OP' and t[1] == '(':
            self._consume('OP', '(')
            val = self._expression()
            self._consume('OP', ')')
            return val
        if t[0] == 'FUNC':
            return self._func_call()
        raise CalcError(f"语法错误: 意外的 token {t}")

    def _func_call(self):
        name = self._consume('FUNC')[1]
        self._consume('OP', '(')
        args = self._arg_list()
        self._consume('OP', ')')
        if name == 'pow':
            if len(args) < 2:
                raise CalcError("pow(m,n) 需要两个参数")
            return args[0] ** args[1]
        if name == 'sqrt':
            if len(args) < 1:
                raise CalcError("sqrt(x) 需要一个参数")
            return math.sqrt(args[0])
        func_map = {
            'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
            'log': math.log, 'log10': math.log10, 'log2': math.log2,
            'exp': math.exp, 'abs': abs,
        }
        if name in func_map:
            if len(args) < 1:
                raise CalcError(f"{name}() 至少需要一个参数")
            return func_map[name](*args)
        raise CalcError(f"未知函数: {name}()")

    def _arg_list(self):
        args = []
        if self._peek()[0] == 'OP' and self._peek()[1] == ')':
            return args
        while True:
            if self._peek()[0] == 'OP' and self._peek()[1] == ',':
                if not args:
                    raise CalcError("函数首参数不能为空")
                self._consume('OP', ',')
                args.append(self._expression())
            else:
                args.append(self._expression())
                if not (self._peek()[0] == 'OP' and self._peek()[1] == ','):
                    break
                self._consume('OP', ',')
        return args


# ────────────── PySide6 GUI ──────────────

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QGridLayout, QPushButton, QLineEdit, QLabel, QSizePolicy,
    QListWidget, QListWidgetItem
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QKeyEvent


STYLE = """
QMainWindow {
    background-color: #1e1e2e;
}
QLineEdit#display {
    background-color: #2a2a3e;
    color: #cdd6f4;
    border: 2px solid #45475a;
    border-radius: 10px;
    padding: 12px 16px;
    font-size: 24px;
    selection-background-color: #89b4fa;
}
QLabel#result_label {
    color: #a6e3a1;
    font-size: 18px;
    padding: 4px 16px;
    min-height: 28px;
}
QLabel#error_label {
    color: #f38ba8;
    font-size: 14px;
    padding: 2px 16px;
    min-height: 20px;
}
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 8px;
    font-size: 16px;
    font-weight: bold;
    padding: 10px;
    min-height: 24px;
}
QPushButton:hover {
    background-color: #45475a;
}
QPushButton:pressed {
    background-color: #585b70;
}
QPushButton#btn_equals {
    background-color: #89b4fa;
    color: #1e1e2e;
    font-size: 20px;
}
QPushButton#btn_equals:hover {
    background-color: #74c7ec;
}
QPushButton#btn_clear {
    background-color: #f38ba8;
    color: #1e1e2e;
}
QPushButton#btn_clear:hover {
    background-color: #eba0ac;
}
QPushButton#btn_backspace {
    background-color: #fab387;
    color: #1e1e2e;
}
QPushButton#btn_backspace:hover {
    background-color: #f9e2af;
}
QPushButton#btn_func {
    background-color: #cba6f7;
    color: #1e1e2e;
}
QPushButton#btn_func:hover {
    background-color: #b4befe;
}
QPushButton#btn_op {
    background-color: #6c7086;
    color: #cdd6f4;
}
QPushButton#btn_op:hover {
    background-color: #7f849c;
}
QListWidget#history_list {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    font-size: 13px;
    padding: 4px;
    outline: none;
}
QListWidget#history_list::item {
    border-bottom: 1px solid #313244;
    padding: 4px 8px;
}
QListWidget#history_list::item:hover {
    background-color: #313244;
    color: #89b4fa;
}
QListWidget#history_list::item:selected {
    background-color: #45475a;
    color: #89b4fa;
}
QLabel#history_title {
    color: #a6adc8;
    font-size: 13px;
    font-weight: bold;
    padding: 2px 4px;
}
QPushButton#btn_clear_history {
    background-color: transparent;
    color: #f38ba8;
    border: 1px solid #f38ba8;
    border-radius: 4px;
    font-size: 11px;
    padding: 2px 8px;
    min-height: 20px;
}
QPushButton#btn_clear_history:hover {
    background-color: #f38ba8;
    color: #1e1e2e;
}
"""


class CalculatorGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("数学计算器 — DYZG")
        self.setMinimumSize(420, 620)
        self.resize(440, 680)

        # 居中
        self.setStyleSheet(STYLE)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(8)

        # ── 显示区 ──
        self.display = QLineEdit()
        self.display.setObjectName("display")
        self.display.setPlaceholderText("输入表达式或点击按钮...")
        self.display.setAlignment(Qt.AlignRight)
        self.display.returnPressed.connect(self.calculate)
        main_layout.addWidget(self.display)

        self.result_label = QLabel("")
        self.result_label.setObjectName("result_label")
        self.result_label.setAlignment(Qt.AlignRight)
        main_layout.addWidget(self.result_label)

        self.error_label = QLabel("")
        self.error_label.setObjectName("error_label")
        self.error_label.setAlignment(Qt.AlignRight)
        main_layout.addWidget(self.error_label)

        # ── 计算记录 ──
        hist_header = QHBoxLayout()
        hist_title = QLabel("计算记录 (最多 20 条)")
        hist_title.setObjectName("history_title")
        hist_header.addWidget(hist_title)
        hist_header.addStretch()

        self.btn_clear_history = QPushButton("清空")
        self.btn_clear_history.setObjectName("btn_clear_history")
        self.btn_clear_history.clicked.connect(self.clear_history)
        hist_header.addWidget(self.btn_clear_history)
        main_layout.addLayout(hist_header)

        self.history_list = QListWidget()
        self.history_list.setObjectName("history_list")
        self.history_list.setMaximumHeight(160)
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history_list.itemClicked.connect(self.on_history_clicked)
        main_layout.addWidget(self.history_list)

        # ── 按钮区 ──
        grid = QGridLayout()
        grid.setSpacing(6)

        buttons = [
            # 行, 列, 行跨, 列跨, 文本, 回调, 样式名
            (0, 0, 1, 1, "C",     self.clear_all,    "btn_clear"),
            (0, 1, 1, 1, "⌫",     self.backspace,    "btn_backspace"),
            (0, 2, 1, 1, "(",     lambda: self.insert("("), "btn_op"),
            (0, 3, 1, 1, ")",     lambda: self.insert(")"), "btn_op"),

            (1, 0, 1, 1, "pow",  lambda: self.insert("pow("), "btn_func"),
            (1, 1, 1, 1, "sqrt", lambda: self.insert("sqrt("), "btn_func"),
            (1, 2, 1, 1, "^",    lambda: self.insert("^"), "btn_op"),
            (1, 3, 1, 1, "/",    lambda: self.insert("/"), "btn_op"),

            (2, 0, 1, 1, "7",    lambda: self.insert("7"), ""),
            (2, 1, 1, 1, "8",    lambda: self.insert("8"), ""),
            (2, 2, 1, 1, "9",    lambda: self.insert("9"), ""),
            (2, 3, 1, 1, "*",    lambda: self.insert("*"), "btn_op"),

            (3, 0, 1, 1, "4",    lambda: self.insert("4"), ""),
            (3, 1, 1, 1, "5",    lambda: self.insert("5"), ""),
            (3, 2, 1, 1, "6",    lambda: self.insert("6"), ""),
            (3, 3, 1, 1, "-",    lambda: self.insert("-"), "btn_op"),

            (4, 0, 1, 1, "1",    lambda: self.insert("1"), ""),
            (4, 1, 1, 1, "2",    lambda: self.insert("2"), ""),
            (4, 2, 1, 1, "3",    lambda: self.insert("3"), ""),
            (4, 3, 1, 1, "+",    lambda: self.insert("+"), "btn_op"),

            (5, 0, 1, 1, "(",    lambda: self.insert("("), "btn_op"),
            (5, 1, 1, 1, "0",    lambda: self.insert("0"), ""),
            (5, 2, 1, 1, ".",    lambda: self.insert("."), ""),
            (5, 3, 1, 1, "=",    self.calculate,     "btn_equals"),
        ]

        for row, col, rspan, cspan, text, callback, style_id in buttons:
            btn = QPushButton(text)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            if style_id:
                btn.setObjectName(style_id)
            btn.clicked.connect(callback)
            grid.addWidget(btn, row, col, rspan, cspan)

        main_layout.addLayout(grid)

        # ── 键盘提示 ──
        hint = QLabel("键盘输入 | Enter = 计算 | Esc = 清空")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #6c7086; font-size: 12px; padding: 4px;")
        main_layout.addWidget(hint)

        self.display.setFocus()

    # ── 按钮操作 ──

    def insert(self, text: str):
        """在光标处插入文本"""
        self.display.insert(text)
        self.display.setFocus()

    def backspace(self):
        """删除光标前一个字符（或选区）"""
        cursor = self.display.cursorPosition()
        if cursor > 0:
            text = self.display.text()
            self.display.setText(text[:cursor - 1] + text[cursor:])
            self.display.setCursorPosition(cursor - 1)
        self.display.setFocus()

    def clear_all(self):
        self.display.clear()
        self.result_label.setText("")
        self.error_label.setText("")
        self.display.setFocus()

    def calculate(self):
        expr = self.display.text().strip()
        self.error_label.setText("")
        self.result_label.setText("")

        if not expr:
            return

        try:
            calc = Calculator(expr)
            result = calc.parse()

            # 整数不显示 .0
            if isinstance(result, float) and result == int(result) and not math.isinf(result):
                result_str = str(int(result))
            else:
                # 保留合理小数
                result_str = f"{result:.10g}"

            self.result_label.setText(f"= {result_str}")
            self.add_to_history(expr, result_str)
        except CalcError as e:
            self.error_label.setText(str(e))
        except ZeroDivisionError:
            self.error_label.setText("数学错误: 除以零")
        except OverflowError:
            self.error_label.setText("数值溢出")
        except Exception as e:
            self.error_label.setText(f"错误: {e}")

        self.display.setFocus()

    # ── 计算记录 ──

    def add_to_history(self, expression: str, result: str):
        """添加一条记录到历史列表（上限 20 条）"""
        item_text = f"{expression}  =  {result}"
        item = QListWidgetItem(item_text)
        item.setData(Qt.UserRole, expression)
        self.history_list.insertItem(0, item)
        # 超过 20 条删除最旧的
        while self.history_list.count() > 20:
            self.history_list.takeItem(self.history_list.count() - 1)

    def clear_history(self):
        """清空历史记录"""
        self.history_list.clear()

    def on_history_clicked(self, item: QListWidgetItem):
        """点击历史记录 → 回填表达式到输入框"""
        expr = item.data(Qt.UserRole)
        if expr:
            self.display.setText(expr)
            self.result_label.setText("")
            self.error_label.setText("")
            self.display.setFocus()

    # ── 键盘事件 ──

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key_Escape:
            self.clear_all()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self.calculate()
        else:
            super().keyPressEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("数学计算器")
    window = CalculatorGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
