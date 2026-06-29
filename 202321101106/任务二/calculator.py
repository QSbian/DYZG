#!/usr/bin/env python3
"""
命令行计算器 — 支持四则运算、括号、乘方（^ / pow）、开方、负数、实数域

用法:
    python calculator.py "表达式"

示例:
    python calculator.py "1+5*pow(5,1/2)/(2*(-5.2)^3)"
    python calculator.py "pow(8,1/3)"
    python calculator.py "(-2)^(1/3) + 3*pi"
"""

import sys
import math
from fractions import Fraction


# ---------------------------------------------------------------------------
# 词法分析 — 将输入字符串拆分为 token 列表
# ---------------------------------------------------------------------------
def tokenize(expr: str) -> list:
    """把中缀表达式字符串拆成 token 列表。"""
    tokens = []
    i = 0
    n = len(expr)

    while i < n:
        ch = expr[i]

        # 跳过空白字符
        if ch.isspace():
            i += 1
            continue

        # 单字符运算符 & 分隔符
        if ch in "+-*/^(),":
            tokens.append(ch)
            i += 1
            continue

        # 数字（整数 / 小数）
        if ch.isdigit() or ch == ".":
            j = i
            while j < n and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            tokens.append(expr[i:j])
            i = j
            continue

        # 标识符：函数名 / 常量（pi, e …）
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (expr[j].isalpha() or expr[j] == "_"):
                j += 1
            tokens.append(expr[i:j])
            i = j
            continue

        raise ValueError(f"无法识别的字符: '{ch}' (位置 {i})")

    return tokens


# ---------------------------------------------------------------------------
# 递归下降解析器 + 求值
# ---------------------------------------------------------------------------
class Calculator:
    """递归下降解析器，边解析边求值。"""

    def __init__(self, tokens: list):
        self.tokens = tokens
        self.pos = 0

    # ---- 工具方法 ----

    def peek(self):
        """查看当前 token，不前进。"""
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def consume(self, expected=None):
        """消耗一个 token；如果指定 expected 则做精确匹配。"""
        if self.pos >= len(self.tokens):
            raise ValueError("表达式意外结束")
        if expected is not None and self.tokens[self.pos] != expected:
            raise ValueError(
                f"期望 '{expected}'，实际遇到 '{self.tokens[self.pos]}' (位置 {self.pos})"
            )
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    # ---- 入口 ----

    def parse(self):
        """解析并求值整个表达式。"""
        if not self.tokens:
            raise ValueError("表达式为空")
        result = self.expression()
        if self.pos < len(self.tokens):
            raise ValueError(f"多余的 token: '{self.peek()}' (位置 {self.pos})")
        return result

    # ---- 文法层级 ----

    # expression  →  term  (('+' | '-')  term)*
    def expression(self):
        left = self.term()
        while self.peek() in ("+", "-"):
            op = self.consume()
            right = self.term()
            if op == "+":
                left += right
            else:
                left -= right
        return left

    # term  →  power  (('*' | '/')  power)*
    def term(self):
        left = self.power()
        while self.peek() in ("*", "/"):
            op = self.consume()
            right = self.power()
            if op == "*":
                left *= right
            elif op == "/":
                if right == 0:
                    raise ZeroDivisionError("除数不能为零")
                left /= right
        return left

    # power  →  unary  ('^'  power)?        ← 右结合
    def power(self):
        left = self.unary()
        if self.peek() == "^":
            self.consume()
            right = self.power()          # 递归调用 power，实现右结合
            left = self._real_pow(left, right)
        return left

    # unary  →  ('+' | '-')  unary  |  primary
    def unary(self):
        if self.peek() == "+":
            self.consume()
            return self.unary()
        if self.peek() == "-":
            self.consume()
            return -self.unary()
        return self.primary()

    # primary  →  NUMBER | '(' expression ')' | FUNCTION '(' args ')' | CONSTANT
    def primary(self):
        tok = self.peek()

        if tok is None:
            raise ValueError("表达式意外结束")

        # ---- 括号 ----
        if tok == "(":
            self.consume()
            result = self.expression()
            self.consume(")")
            return result

        # ---- pow 函数 ----
        if tok == "pow":
            self.consume()
            self.consume("(")
            base = self.expression()
            self.consume(",")
            exp = self.expression()
            self.consume(")")
            return self._real_pow(base, exp)

        # ---- 常量 ----
        if tok == "pi":
            self.consume()
            return math.pi
        if tok == "e":
            self.consume()
            return math.e

        # ---- 数字 ----
        try:
            self.consume()
            return float(tok)
        except ValueError:
            raise ValueError(f"无法解析的 token: '{tok}'")

    # ---- 实数域乘方 ----
    @staticmethod
    def _real_pow(base: float, exp: float) -> float:
        """
        在实数域内完成 base ^ exp 运算。

        规则：
          1. exp 是整数（或极接近整数）→ 直接使用 **
          2. base >= 0                → 直接使用 **
          3. base < 0, 指数可化为分数且分母为奇数 → 返回实根
          4. base < 0, 指数分母为偶数 / 非有理数 → 报错（实数域无定义）
        """
        # 1. 整数指数（容忍浮点误差）
        if isinstance(exp, float) and abs(exp - round(exp)) < 1e-12:
            return base ** round(exp)

        # 2. 非负底数
        if base >= 0:
            return base ** exp

        # 3. 负数底数 → 尝试化为最简分数
        try:
            frac = Fraction(exp).limit_denominator(10 ** 6)
            if frac.denominator % 2 == 0:
                raise ValueError(
                    f"负数 {base} 的 {exp} 次幂（分母 {frac.denominator} 为偶数）"
                    f" 在实数域无定义"
                )
            # 分母为奇数 → 先计算正底数的幂，再取负号
            return -((-base) ** exp)
        except ValueError:
            raise


# ---------------------------------------------------------------------------
# 输出格式化
# ---------------------------------------------------------------------------
def fmt(value: float) -> str:
    """美化数值输出：极近整数→整数；否则去掉尾部多余的零。"""
    if isinstance(value, complex):
        return str(value)                     # 保留兜底（理论上不走这里）
    if abs(value - round(value)) < 1e-12:
        return str(int(round(value)))
    s = f"{value:.15f}"
    s = s.rstrip("0").rstrip(".")
    return s


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python calculator.py \"表达式\"")
        print("示例: python calculator.py \"1+5*pow(5,1/2)/(2*(-5.2)^3)\"")
        print()
        print("支持:")
        print("  加减乘除  +  -  *  /")
        print("  乘方      ^ 运算符  /  pow(底数, 指数)")
        print("  括号      ( )")
        print("  负数      -3  (-2)^(1/3) …")
        print("  常量      pi  e")
        print("  开方      pow(9, 1/2)  → 3")
        sys.exit(1)

    expr = sys.argv[1]

    if not expr.strip():
        print("错误: 表达式为空", file=sys.stderr)
        sys.exit(1)

    try:
        tokens = tokenize(expr)
        calc = Calculator(tokens)
        result = calc.parse()
        print(fmt(result))
    except ZeroDivisionError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"语法错误: {e}", file=sys.stderr)
        sys.exit(1)
    except OverflowError:
        print("错误: 数值溢出", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
