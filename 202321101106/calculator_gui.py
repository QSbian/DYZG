#!/usr/bin/env python3
"""
计算器 GUI — 仿 iOS 深色风格
支持四则运算、括号、乘方(^ / pow)、开方、负数、实数域、常量 pi/e
运行: python calculator_gui.py
"""

import math
import tkinter as tk
from tkinter import ttk
from fractions import Fraction


# ═══════════════════════════ 计算引擎 ════════════════════════════════════════

def tokenize(expr: str) -> list:
    tokens, i, n = [], 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1; continue
        if ch in "+-*/^(),":
            tokens.append(ch); i += 1; continue
        if ch.isdigit() or ch == ".":
            j = i
            while j < n and (expr[j].isdigit() or expr[j] == "."): j += 1
            tokens.append(expr[i:j]); i = j; continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (expr[j].isalpha() or expr[j] == "_"): j += 1
            tokens.append(expr[i:j]); i = j; continue
        raise ValueError(f"无法识别的字符: '{ch}'")
    return tokens


class _Calc:
    def __init__(self, tokens):
        self.t, self.p = tokens, 0

    def peek(self):
        return self.t[self.p] if self.p < len(self.t) else None

    def eat(self, x=None):
        if self.p >= len(self.t): raise ValueError("表达式意外结束")
        if x and self.t[self.p] != x:
            raise ValueError(f"期望 '{x}' 遇到 '{self.t[self.p]}'")
        v = self.t[self.p]; self.p += 1; return v

    def run(self):
        if not self.t: raise ValueError("表达式为空")
        r = self.expr()
        if self.peek(): raise ValueError(f"多余内容: '{self.peek()}'")
        return r

    def expr(self):
        v = self.term()
        while self.peek() in ("+", "-"):
            op = self.eat()
            v = v + self.term() if op == "+" else v - self.term()
        return v

    def term(self):
        v = self.pow_()
        while self.peek() in ("*", "/"):
            op = self.eat()
            r = self.pow_()
            if op == "*": v *= r
            else:
                if r == 0: raise ZeroDivisionError("除数不能为零")
                v /= r
        return v

    def pow_(self):
        v = self.unary()
        if self.peek() == "^":
            self.eat()
            v = _rpow(v, self.pow_())
        return v

    def unary(self):
        if self.peek() == "+": self.eat(); return self.unary()
        if self.peek() == "-": self.eat(); return -self.unary()
        return self.primary()

    def primary(self):
        t = self.peek()
        if t is None: raise ValueError("表达式意外结束")
        if t == "(":
            self.eat(); v = self.expr(); self.eat(")"); return v
        if t == "pow":
            self.eat(); self.eat("(")
            b = self.expr(); self.eat(","); e = self.expr(); self.eat(")")
            return _rpow(b, e)
        if t == "pi": self.eat(); return math.pi
        if t == "e":  self.eat(); return math.e
        try:
            self.eat(); return float(t)
        except ValueError:
            raise ValueError(f"无法解析: '{t}'")


def _rpow(base, exp):
    if isinstance(exp, float) and abs(exp - round(exp)) < 1e-12:
        return base ** round(exp)
    if base >= 0: return base ** exp
    fr = Fraction(exp).limit_denominator(10 ** 6)
    if fr.denominator % 2 == 0:
        raise ValueError(f"负数底数 {base} 的 {exp} 次幂在实数域无定义")
    return -((-base) ** exp)


def calc(expr: str) -> str:
    v = _Calc(tokenize(expr)).run()
    if abs(v - round(v)) < 1e-12:
        return str(int(round(v)))
    return f"{v:.12f}".rstrip("0").rstrip(".")


# ═══════════════════════════ 主题色 ══════════════════════════════════════════

BG       = "#1C1C1E"
SCREEN   = "#111111"
NUM_BG   = "#3A3A3C"
OP_BG    = "#FF9F0A"
FN_BG    = "#2C2C2E"
SPEC_BG  = "#636366"

NUM_FG   = "#FFFFFF"
OP_FG    = "#FFFFFF"
FN_FG    = "#FFFFFF"
SPEC_FG  = "#FFFFFF"

GRAY     = "#8E8E93"
RED      = "#FF453A"

# 字体（按系统优先级自动降级）
_FONTS = ["Microsoft YaHei UI", "Helvetica Neue", "Arial", "TkDefaultFont"]


def _f(size, bold=False):
    return (_FONTS[0], size, "bold" if bold else "normal")


# ═══════════════════════════ 应用主类 ════════════════════════════════════════

class CalcApp(tk.Tk):
    W = 82     # 按钮宽
    H = 68     # 按钮高
    G = 8      # 间距
    P = 12     # 外边距

    # (标签, col, row, colspan, bg, fg, action)
    BTNS = [
        # 行0 — 功能
        ("AC",      0, 0, 1, SPEC_BG, SPEC_FG, "ac"),
        ("+/-",     1, 0, 1, SPEC_BG, SPEC_FG, "negate"),
        ("%",       2, 0, 1, SPEC_BG, SPEC_FG, "percent"),
        ("÷",       3, 0, 1, OP_BG,   OP_FG,   "/"),
        # 行1
        ("7",       0, 1, 1, NUM_BG,  NUM_FG,  "7"),
        ("8",       1, 1, 1, NUM_BG,  NUM_FG,  "8"),
        ("9",       2, 1, 1, NUM_BG,  NUM_FG,  "9"),
        ("×",       3, 1, 1, OP_BG,   OP_FG,   "*"),
        # 行2
        ("4",       0, 2, 1, NUM_BG,  NUM_FG,  "4"),
        ("5",       1, 2, 1, NUM_BG,  NUM_FG,  "5"),
        ("6",       2, 2, 1, NUM_BG,  NUM_FG,  "6"),
        ("−",       3, 2, 1, OP_BG,   OP_FG,   "-"),
        # 行3
        ("1",       0, 3, 1, NUM_BG,  NUM_FG,  "1"),
        ("2",       1, 3, 1, NUM_BG,  NUM_FG,  "2"),
        ("3",       2, 3, 1, NUM_BG,  NUM_FG,  "3"),
        ("+",       3, 3, 1, OP_BG,   OP_FG,   "+"),
        # 行4
        ("0",       0, 4, 2, NUM_BG,  NUM_FG,  "0"),
        (".",       2, 4, 1, NUM_BG,  NUM_FG,  "."),
        ("=",       3, 4, 1, OP_BG,   OP_FG,   "eq"),
        # 行5 — 扩展
        ("( )",     0, 5, 1, FN_BG,   FN_FG,   "paren"),
        ("xʸ",      1, 5, 1, FN_BG,   "#FF9F0A","^"),
        ("√x",      2, 5, 1, FN_BG,   FN_FG,   "sqrt"),
        ("⌫",       3, 5, 1, FN_BG,   "#FF9F0A","backspace"),
        # 行6
        ("pow(,)",  0, 6, 1, FN_BG,   FN_FG,   "pow_func"),
        ("π",       1, 6, 1, FN_BG,   "#FF9F0A","pi"),
        ("e",       2, 6, 1, FN_BG,   "#FF9F0A","e_const"),
        ("CE",      3, 6, 1, FN_BG,   FN_FG,   "ce"),
    ]

    def __init__(self):
        super().__init__()
        self.title("计算器")
        self.resizable(False, False)
        self.configure(bg=BG)

        self._expr     = ""
        self._prev_ans = "0"
        self._just_eq  = False
        self._btn_ac   = None

        self._build_display()
        self._build_buttons()
        self._bind_keys()
        self._refresh()

    # ─── 显示屏 ──────────────────────────────────────────────────

    def _build_display(self):
        frm = tk.Frame(self, bg=SCREEN, padx=16, pady=10)
        frm.pack(fill="x", padx=self.P, pady=(self.P, 4))

        self._expr_var = tk.StringVar()
        tk.Label(frm, textvariable=self._expr_var,
                 font=_f(13), bg=SCREEN, fg=GRAY,
                 anchor="e", justify="right"
                 ).pack(fill="x")

        self._main_var = tk.StringVar(value="0")
        self._main_lbl = tk.Label(
            frm, textvariable=self._main_var,
            font=_f(42), bg=SCREEN, fg=NUM_FG,
            anchor="e", justify="right"
        )
        self._main_lbl.pack(fill="x")

    # ─── 按钮区 ──────────────────────────────────────────────────

    def _build_buttons(self):
        W, H, G, P = self.W, self.H, self.G, self.P
        frm = tk.Frame(self, bg=BG)
        frm.pack(padx=P, pady=(0, P))

        COLS, ROWS = 4, 7
        frm_w = COLS * W + (COLS - 1) * G
        frm_h = ROWS * H + (ROWS - 1) * G
        frm.configure(width=frm_w, height=frm_h)
        frm.pack_propagate(False)

        for (label, col, row, colspan, bg, fg, action) in self.BTNS:
            btn_w = W * colspan + G * (colspan - 1)
            fnt   = _f(20) if len(label) <= 3 else _f(13)

            btn = tk.Button(
                frm,
                text=label,
                font=fnt,
                bg=bg, fg=fg,
                activebackground=self._dim(bg, 45),
                activeforeground=fg,
                relief="flat",
                bd=0,
                highlightthickness=0,
                cursor="hand2",
                command=lambda a=action: self._act(a),
            )
            btn.place(x=col * (W + G), y=row * (H + G),
                      width=btn_w, height=H)

            if action == "ac":
                self._btn_ac = btn

    # ─── 键盘绑定 ────────────────────────────────────────────────

    def _bind_keys(self):
        self.bind("<Key>",       self._on_key)
        self.bind("<Return>",    lambda e: self._act("eq"))
        self.bind("<KP_Enter>",  lambda e: self._act("eq"))
        self.bind("<BackSpace>", lambda e: self._act("backspace"))
        self.bind("<Escape>",    lambda e: self._act("ac"))
        self.bind("<Delete>",    lambda e: self._act("ce"))

    def _on_key(self, event):
        ch = event.char
        if ch in "0123456789.+-*/^()":
            self._act(ch)
        elif ch in ("p", "P"):
            self._act("pi")
        elif ch in ("e", "E") and not (event.state & 4):
            self._act("e_const")

    # ─── 动作处理 ────────────────────────────────────────────────

    def _act(self, a: str):
        if a == "ac":
            self._expr, self._just_eq = "", False

        elif a == "ce":
            if self._just_eq:
                self._expr, self._just_eq = "", False
            elif self._expr:
                for kw in ("pow(", "pi", "e"):
                    if self._expr.endswith(kw):
                        self._expr = self._expr[:-len(kw)]; break
                else:
                    self._expr = self._expr[:-1]

        elif a == "backspace":
            if self._just_eq:
                self._expr, self._just_eq = "", False
            elif self._expr:
                self._expr = self._expr[:-1]

        elif a == "negate":
            self._expr = f"-({self._expr})" if self._expr else "-"

        elif a == "percent":
            if self._expr:
                self._expr = f"({self._expr})/100"

        elif a == "paren":
            last = self._expr[-1] if self._expr else ""
            opens  = self._expr.count("(")
            closes = self._expr.count(")")
            if opens > closes and last not in "+-*/^(,":
                self._expr += ")"
            else:
                if self._just_eq:
                    self._expr, self._just_eq = "", False
                self._expr += "("

        elif a == "sqrt":
            inner = self._expr or self._prev_ans or "0"
            self._expr    = f"pow({inner},1/2)"
            self._just_eq = False

        elif a == "pow_func":
            if self._just_eq:
                self._expr, self._just_eq = "", False
            self._expr += "pow("

        elif a == "pi":
            if self._just_eq:
                self._expr, self._just_eq = "", False
            self._expr += "pi"

        elif a == "e_const":
            if self._just_eq:
                self._expr, self._just_eq = "", False
            self._expr += "e"

        elif a == "^":
            if self._just_eq:
                self._expr = self._prev_ans
                self._just_eq = False
            self._expr += "^"

        elif a == "eq":
            if not self._expr: return
            try:
                result = calc(self._expr)
                self._expr_var.set(self._expr + " =")
                self._prev_ans = result
                self._expr     = result
                self._just_eq  = True
                self._set_main(result)
                if self._btn_ac: self._btn_ac.configure(text="AC")
                return
            except ZeroDivisionError:
                self._show_error("除数为零"); return
            except Exception as ex:
                self._show_error(str(ex)[:48]); return

        else:  # 普通字符
            if self._just_eq:
                self._expr    = self._prev_ans + a if a in "+-*/^" else a
                self._just_eq = False
            else:
                if a == "." and self._expr.endswith("."): pass
                else: self._expr += a

        self._refresh()

    # ─── 显示刷新 ────────────────────────────────────────────────

    def _refresh(self):
        if not self._just_eq:
            self._expr_var.set("")
        self._set_main(self._expr if self._expr else "0")
        if self._btn_ac:
            self._btn_ac.configure(text="C" if self._expr else "AC")

    def _set_main(self, text: str, error: bool = False):
        self._main_var.set(text)
        n = len(text)
        if error:
            self._main_lbl.configure(fg=RED, font=_f(28))
        else:
            self._main_lbl.configure(fg=NUM_FG)
            self._main_lbl.configure(
                font=_f(42) if n <= 9 else _f(28) if n <= 16 else _f(18)
            )

    def _show_error(self, _msg: str):
        self._expr_var.set(self._expr)
        self._set_main("错误", error=True)
        self._expr, self._just_eq = "", False
        self.after(2200, lambda: (
            self._set_main("0"),
            self._expr_var.set("")
        ))

    # ─── 工具 ────────────────────────────────────────────────────

    @staticmethod
    def _dim(hex_color: str, amount: int = 40) -> str:
        """加深颜色，用于按下效果。"""
        h = hex_color.lstrip("#")
        r = max(0, int(h[0:2], 16) - amount)
        g = max(0, int(h[2:4], 16) - amount)
        b = max(0, int(h[4:6], 16) - amount)
        return f"#{r:02X}{g:02X}{b:02X}"


# ═══════════════════════════ 启动 ════════════════════════════════════════════

if __name__ == "__main__":
    app = CalcApp()
    app.update_idletasks()
    rw = app.winfo_reqwidth()
    rh = app.winfo_reqheight()
    sw = app.winfo_screenwidth()
    sh = app.winfo_screenheight()
    app.geometry(f"{rw}x{rh}+{(sw - rw) // 2}+{(sh - rh) // 2}")
    app.mainloop()
