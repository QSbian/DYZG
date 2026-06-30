#!/usr/bin/env python3
"""
命令行科学计算器（交互式 REPL）
支持：四则运算、括号优先级、乘方(^)、pow函数、负数、实数域

用法：
  交互模式（推荐）：python calculator.py
  单次计算：        python calculator.py "表达式"

示例：
  $ python calculator.py
  >>> 1+5*pow(,1/2)/(2*(-5.2)^3)
  0.9822200728265817
  >>> quit
"""

import sys
import math

# ────────────────────────────────── 词法分析 ──────────────────────────────────

class TokenType:
    NUMBER = 'NUMBER'
    PLUS   = 'PLUS'
    MINUS  = 'MINUS'
    MUL    = 'MUL'
    DIV    = 'DIV'
    POW    = 'POW'          # ^ 运算符
    LPAREN = 'LPAREN'
    RPAREN = 'RPAREN'
    COMMA  = 'COMMA'
    FUNC   = 'FUNC'         # sin, cos, tan, log, ln, sqrt, abs, pow
    CONST  = 'CONST'        # pi, e (常量)
    EOF    = 'EOF'


# 已注册的函数名
FUNC_NAMES = {'pow', 'sin', 'cos', 'tan', 'log', 'ln', 'sqrt', 'abs'}

# 已注册的常量名 → 对应值
CONST_VALUES = {'pi': math.pi, 'e': math.e}


class Token:
    __slots__ = ('type', 'value')

    def __init__(self, type_: str, value=None):
        self.type = type_
        self.value = value

    def __repr__(self):
        if self.value is not None:
            return f"Token({self.type}, {self.value})"
        return f"Token({self.type})"


class Lexer:
    """将表达式字符串转为 token 流"""

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.length = len(text)

    def peek(self):
        if self.pos < self.length:
            return self.text[self.pos]
        return None

    def advance(self):
        ch = self.peek()
        self.pos += 1
        return ch

    def skip_whitespace(self):
        while self.peek() is not None and self.peek().isspace():
            self.advance()

    def scan_number(self):
        """扫描整数或小数，支持 .5 这种省略前导零的写法"""
        buf = ''
        while self.peek() is not None and (self.peek().isdigit() or self.peek() == '.'):
            buf += self.advance()
        return float(buf)

    def scan_identifier(self):
        """扫描标识符（函数名）"""
        buf = ''
        while self.peek() is not None and self.peek().isalpha():
            buf += self.advance()
        return buf.lower()

    def tokenize(self):
        tokens = []
        while self.pos < self.length:
            self.skip_whitespace()
            if self.pos >= self.length:
                break
            ch = self.peek()

            if ch.isdigit() or ch == '.':
                tokens.append(Token(TokenType.NUMBER, self.scan_number()))

            elif ch.isalpha():
                name = self.scan_identifier()
                if name in FUNC_NAMES:
                    tokens.append(Token(TokenType.FUNC, name))
                elif name in CONST_VALUES:
                    tokens.append(Token(TokenType.CONST, CONST_VALUES[name]))
                else:
                    raise ValueError(f"未识别的名称: '{name}'")

            elif ch == '+':
                self.advance()
                tokens.append(Token(TokenType.PLUS))
            elif ch == '-':
                self.advance()
                tokens.append(Token(TokenType.MINUS))
            elif ch == '*':
                self.advance()
                tokens.append(Token(TokenType.MUL))
            elif ch == '/':
                self.advance()
                tokens.append(Token(TokenType.DIV))
            elif ch == '^':
                self.advance()
                tokens.append(Token(TokenType.POW))
            elif ch == '(':
                self.advance()
                tokens.append(Token(TokenType.LPAREN))
            elif ch == ')':
                self.advance()
                tokens.append(Token(TokenType.RPAREN))
            elif ch == ',':
                self.advance()
                tokens.append(Token(TokenType.COMMA))
            else:
                raise ValueError(f"无法识别的字符: '{ch}'")

        tokens.append(Token(TokenType.EOF))
        return tokens


# ────────────────────────────────── 语法分析 + 求值 ──────────────────────────

class Parser:
    """
    递归下降解析器，同时完成求值。

    语法 (优先级从低到高)：
        expression  = term (('+' | '-') term)*
        term        = unary (('*' | '/') unary)*
        unary       = ('+' | '-') unary | power
        power       = primary ('^' power)?          ← 右结合
        primary     = NUMBER
                    | CONST (pi, e)
                    | '(' expression ')'
                    | FUNC '(' args ')'
                    | FUNC1 '(' expression ')'     ← 单参数函数
        args        = expression? (',' expression?)?   ← pow 允许参数留空，默认 1
    """

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos]

    def advance(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def expect(self, type_: str):
        tok = self.peek()
        if tok.type != type_:
            raise ValueError(f"语法错误：期望 {type_}，但遇到 {tok}")
        return self.advance()

    # ── 入口 ────────────────────────────────────────────────

    def parse(self):
        result = self.expression()
        if self.peek().type != TokenType.EOF:
            raise ValueError(f"语法错误：表达式末尾有多余内容 (token: {self.peek()})")
        return result

    # ── expression → term (('+'|'-') term)* ─────────────────

    def expression(self):
        left = self.term()
        while self.peek().type in (TokenType.PLUS, TokenType.MINUS):
            op = self.advance()
            right = self.term()
            if op.type == TokenType.PLUS:
                left = left + right
            else:
                left = left - right
        return left

    # ── term → unary (('*'|'/') unary)* ─────────────────────

    def term(self):
        left = self.unary()
        while self.peek().type in (TokenType.MUL, TokenType.DIV):
            op = self.advance()
            right = self.unary()
            if op.type == TokenType.MUL:
                left = left * right
            else:
                if right == 0:
                    raise ZeroDivisionError("数学错误：除以零")
                left = left / right
        return left

    # ── unary → ('+'|'-') unary | power ─────────────────────

    def unary(self):
        tok = self.peek()
        if tok.type == TokenType.MINUS:
            self.advance()
            return -self.unary()
        if tok.type == TokenType.PLUS:
            self.advance()
            return self.unary()
        return self.power()

    # ── power → primary ('^' power)?  (右结合) ──────────────

    def power(self):
        left = self.primary()
        if self.peek().type == TokenType.POW:
            self.advance()
            right = self.power()           # 递归在右侧 → 右结合
            return self._checked_pow(left, right)
        return left

    # ── primary → NUMBER | '(' expr ')' | FUNC '(' args ')' ─

    def primary(self):
        tok = self.peek()

        if tok.type == TokenType.NUMBER:
            self.advance()
            return tok.value

        if tok.type == TokenType.CONST:
            self.advance()
            return tok.value

        if tok.type == TokenType.LPAREN:
            self.advance()
            result = self.expression()
            self.expect(TokenType.RPAREN)
            return result

        if tok.type == TokenType.FUNC:
            return self._parse_func_call()

        raise ValueError(f"语法错误：意外的 token {tok}")

    # ── 函数调用 ────────────────────────────────────────────

    def _parse_func_call(self):
        func_name = self.advance().value       # 函数名
        self.expect(TokenType.LPAREN)

        # 单参数函数：sin, cos, tan, log, ln, sqrt, abs
        single_arg_funcs = {'sin', 'cos', 'tan', 'log', 'ln', 'sqrt', 'abs'}

        if func_name in single_arg_funcs:
            arg = self.expression()
            self.expect(TokenType.RPAREN)
            return self._dispatch_single(func_name, arg)

        # pow: 双参数（允许留空）
        args = []

        # ── 解析第一个参数 ──
        if self.peek().type == TokenType.RPAREN:
            pass                               # pow() 无参数
        elif self.peek().type == TokenType.COMMA:
            args.append(1.0)                   # pow(, ... → 第一个参数为空，默认 1
        else:
            args.append(self.expression())

        # ── 如果有逗号，解析第二个参数 ──
        if self.peek().type == TokenType.COMMA:
            self.advance()                     # 消耗逗号
            if self.peek().type == TokenType.RPAREN:
                args.append(1.0)               # pow(x, ) → 第二个参数为空，默认 1
            else:
                args.append(self.expression())

        self.expect(TokenType.RPAREN)

        if func_name == 'pow':
            if len(args) == 0:
                raise ValueError("pow() 需要至少 1 个参数")
            if len(args) == 1:
                args.append(1.0)
            if len(args) != 2:
                raise ValueError(f"pow() 最多接受 2 个参数，收到了 {len(args)} 个")
            m, n = args
            return self._checked_pow(m, n)

        raise ValueError(f"未实现的函数: {func_name}")

    # ── 单参数函数分派 ──────────────────────────────────────

    @staticmethod
    def _dispatch_single(func_name: str, arg: float) -> float:
        if func_name == 'sin':
            return math.sin(arg)
        if func_name == 'cos':
            return math.cos(arg)
        if func_name == 'tan':
            return math.tan(arg)
        if func_name == 'log':
            return math.log10(arg)
        if func_name == 'ln':
            return math.log(arg)
        if func_name == 'sqrt':
            if arg < 0:
                raise ValueError(f"实数域错误：sqrt({arg}) 在实数范围内无定义")
            return math.sqrt(arg)
        if func_name == 'abs':
            return abs(arg)
        raise ValueError(f"未实现的函数: {func_name}")

    # ── 乘方 / 开方（带实数域校验） ─────────────────────────

    @staticmethod
    def _checked_pow(base: float, exp: float) -> float:
        """
        base ^ exp，实数域约束：
        - 负底数 + 非整数指数 → 无实数解 → 报错
        """
        if base < 0 and exp != int(exp):
            raise ValueError(
                f"实数域错误：负底数 ({base}) 的 {exp} 次方在实数范围内无定义"
            )
        return math.pow(base, exp)


# ────────────────────────────────── 主入口 ────────────────────────────────────

def evaluate(expr: str) -> float:
    """解析并求值一个表达式字符串，出错时抛出异常。"""
    lexer = Lexer(expr)
    tokens = lexer.tokenize()
    parser = Parser(tokens)
    return parser.parse()


def format_result(value: float) -> str:
    """美化输出：整数不显示 .0，极小的 -0.0 修正为 0.0。"""
    if value == 0.0:
        return "0"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    # 去掉无意义的尾部零
    s = f"{value:.15g}"
    return s


HELP_TEXT = """
  ============================================
     命令行科学计算器 - 使用帮助
  ============================================
   运算符:  +  -  *  /  ^
   乘方:    2^3  ->  8    pow(2, 3)  ->  8
   开方:    pow(9, 1/2)  ->  3    9^(1/2)  ->  3
            sqrt(9)  ->  3
   括号:    (1+2)*3  ->  9
   负数:    -3 + 5  ->  2
  --------------------------------------------
   三角函数:
     sin(pi/6)  ->  0.5    cos(0)  ->  1
     tan(pi/4)  ->  1
   对数:
     ln(1)  ->  0    log(100)  ->  2
   其他:
     abs(-5)  ->  5    sqrt(2)  ->  1.414...
   常量:
     pi  ->  3.14159...    e  ->  2.71828...
  --------------------------------------------
   输入 quit / exit / q 退出
   输入 help 显示此帮助
   按 Ctrl+C 中断 / Ctrl+D 退出
  ============================================
"""


def repl():
    """交互式 REPL：读入 → 求值 → 打印 → 循环。"""
    print("===== 命令行科学计算器 =====", flush=True)
    print('输入 "help" 查看帮助，输入 "quit" 退出喵~', flush=True)
    print(flush=True)

    while True:
        try:
            line = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n拜拜喵~")
            break

        if not line:
            continue

        # 退出命令
        if line.lower() in ('quit', 'exit', 'q'):
            print("拜拜喵~")
            break

        # 帮助
        if line.lower() == 'help':
            print(HELP_TEXT)
            continue

        # 求值
        try:
            result = evaluate(line)
            print(format_result(result), flush=True)
        except (ValueError, ZeroDivisionError) as e:
            print(f"[错误] {e}", flush=True)

    print()  # 结尾空行


def main():
    # 如果给了命令行参数，走单次计算模式（兼容旧用法）
    if len(sys.argv) >= 2:
        expr = sys.argv[1]
        try:
            result = evaluate(expr)
            print(format_result(result))
        except (ValueError, ZeroDivisionError) as e:
            print(f"错误: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # 否则进入交互模式
        repl()


if __name__ == '__main__':
    main()
