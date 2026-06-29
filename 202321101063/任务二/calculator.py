#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
命令行计算器
===========
支持: 加减乘除、括号优先级、乘方(^/pow)、开方、负数、实数域

用法:
    python calculator.py "表达式"

示例:
    python calculator.py "1+2*3"
    python calculator.py "pow(2,3)+5"
    python calculator.py "5*pow(25,1/2)"
    python calculator.py "1+5*pow(5,1/2)/(2*(-5.2)^3)"
"""

import sys
import math


class CalcError(Exception):
    """计算器错误"""
    pass


class Calculator:
    """基于递归下降的数学表达式解析器"""

    def __init__(self, expression: str):
        self.tokens = self._tokenize(expression)
        self.pos = 0

    # ──────────────────── 词法分析 ────────────────────

    def _tokenize(self, expr: str):
        """将表达式字符串拆分为 token 列表"""
        tokens = []
        i, n = 0, len(expr)

        while i < n:
            ch = expr[i]

            # 空白跳过
            if ch.isspace():
                i += 1
                continue

            # 函数名: pow, sqrt, sin, cos 等
            if ch.isalpha() or ch == '_':
                start = i
                while i < n and (expr[i].isalnum() or expr[i] == '_'):
                    i += 1
                tokens.append(('FUNC', expr[start:i]))
                continue

            # 数字: 整数、小数、科学计数法(1.5e-3)
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

            # 运算符和分隔符
            if ch in '+-*/^(),':
                tokens.append(('OP', ch))
                i += 1
                continue

            raise CalcError(f"无法识别的字符: '{ch}' 位置 {i}")

        tokens.append(('END', None))
        return tokens

    # ──────────────────── 工具方法 ────────────────────

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

    # ──────────────────── 语法分析 & 求值 ────────────────────
    #
    # 优先级（由低到高）:
    #   expression  →  + -
    #   term        →  * /
    #   unary       →  + - (一元)
    #   power       →  ^ (右结合)
    #   atom        →  数字 | (expr) | func(args)

    def parse(self):
        """入口"""
        result = self._expression()
        if self._peek()[0] != 'END':
            raise CalcError(f"多余的 token: {self._peek()}")
        return result

    def _expression(self):
        """expression = term (('+'|'-') term)*"""
        left = self._term()
        while self._peek()[0] == 'OP' and self._peek()[1] in '+-':
            op = self._consume('OP')[1]
            right = self._term()
            left = left + right if op == '+' else left - right
        return left

    def _term(self):
        """term = unary (('*'|'/') unary)*"""
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
        """unary = ('+'|'-') unary | power"""
        if self._peek()[0] == 'OP' and self._peek()[1] in '+-':
            op = self._consume('OP')[1]
            val = self._unary()
            return -val if op == '-' else val
        return self._power()

    def _power(self):
        """power = atom ('^' unary)?   —— 右结合"""
        base = self._atom()
        if self._peek()[0] == 'OP' and self._peek()[1] == '^':
            self._consume('OP', '^')
            exp = self._unary()   # 右结合关键: 递归到 unary 而非 term
            base = base ** exp
        return base

    def _atom(self):
        """atom = NUM | '(' expression ')' | FUNC '(' args ')'"""
        t = self._peek()

        # 数字
        if t[0] == 'NUM':
            return self._consume('NUM')[1]

        # 括号
        if t[0] == 'OP' and t[1] == '(':
            self._consume('OP', '(')
            val = self._expression()
            self._consume('OP', ')')
            return val

        # 函数调用
        if t[0] == 'FUNC':
            return self._func_call()

        raise CalcError(f"语法错误: 意外的 token {t}")

    def _func_call(self):
        """解析函数调用: func(arg1, arg2, ...)"""
        name = self._consume('FUNC')[1]
        self._consume('OP', '(')
        args = self._arg_list()
        self._consume('OP', ')')

        # ── 内置函数 ──
        if name == 'pow':
            if len(args) < 2:
                raise CalcError(
                    "pow() 需要两个参数: pow(底数, 指数)\n"
                    "  正确示例: pow(2, 3) = 8, pow(4, 1/2) = 2"
                )
            m, n = args[0], args[1]
            return m ** n

        if name == 'sqrt':
            if len(args) < 1:
                raise CalcError("sqrt() 需要一个参数: sqrt(x)")
            return math.sqrt(args[0])

        # 可扩展更多数学函数
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
        """解析逗号分隔的参数列表"""
        args = []
        if self._peek()[0] == 'OP' and self._peek()[1] == ')':
            return args  # 无参数

        while True:
            # 支持省略首参数: pow(, 1/2) → 第一个参数为空
            if self._peek()[0] == 'OP' and self._peek()[1] == ',':
                if not args:
                    raise CalcError(
                        "函数参数错误: 第一个参数为空\n"
                        "  pow() 需两个完整参数，如 pow(5, 1/2)\n"
                        "  不支持 pow(,n) 省略写法"
                    )
                self._consume('OP', ',')
                args.append(self._expression())
            else:
                args.append(self._expression())
                if not (self._peek()[0] == 'OP' and self._peek()[1] == ','):
                    break
                self._consume('OP', ',')
        return args


# ──────────────────── 入口 ────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法:  python calculator.py \"表达式\"")
        print()
        print("支持运算:")
        print("  + - * /     四则运算")
        print("  ^          乘方（如 2^3 = 8）")
        print("  pow(m,n)   乘方函数（n 支持分数，如 pow(4, 1/2) = 2）")
        print("  sqrt(x)    开平方")
        print("  ()         括号控制优先级")
        print()
        print("示例:")
        print('  python calculator.py "1+2*3"')
        print('  python calculator.py "pow(2, 3) + 5"')
        print('  python calculator.py "5*pow(25, 1/2)"')
        print('  python calculator.py "(-5.2)^3"')
        print('  python calculator.py "1+5*pow(5,1/2)/(2*(-5.2)^3)"')
        sys.exit(1)

    expr = sys.argv[1]
    try:
        calc = Calculator(expr)
        result = calc.parse()
        print(result)
    except CalcError as e:
        print(f"[错误] {e}", file=sys.stderr)
        sys.exit(1)
    except OverflowError:
        print("[错误] 数值溢出", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
