/**
 * 科学计算器 — 词法分析 → 语法分析 → 求值
 *
 * 支持的语法：
 *   四则运算:  +  -  *  /
 *   乘方:     2^3  或  pow(2, 3)
 *   开方:     sqrt(9)  或  pow(9, 1/2)  或  9^(1/2)
 *   三角函数: sin(x)  cos(x)  tan(x)
 *   对数:     log(x) (log10)  ln(x) (自然对数)
 *   其他:     abs(x)  sqrt(x)
 *   常量:     pi  e
 *   括号:     (1 + 2) * 3
 *   负数:     -3 + 5
 *   pow 参数可留空，默认 1
 */

// ═══════════════ 词法分析器 ═══════════════

const FUNC_NAMES = ['pow', 'sin', 'cos', 'tan', 'log', 'ln', 'sqrt', 'abs']
const CONST_VALUES = { pi: Math.PI, e: Math.E }

class Lexer {
  constructor(text) {
    this.text = text
    this.pos = 0
  }

  peek() {
    return this.pos < this.text.length ? this.text[this.pos] : null
  }

  advance() {
    return this.text[this.pos++]
  }

  skipWS() {
    while (this.peek() && /\s/.test(this.peek())) this.pos++
  }

  scanNumber() {
    const start = this.pos
    while (this.peek() && (/\d/.test(this.peek()) || this.peek() === '.'))
      this.pos++
    return parseFloat(this.text.slice(start, this.pos))
  }

  scanId() {
    const start = this.pos
    while (this.peek() && /[a-zA-Z]/.test(this.peek())) this.pos++
    return this.text.slice(start, this.pos).toLowerCase()
  }

  tokenize() {
    const ts = []
    while (this.pos < this.text.length) {
      this.skipWS()
      if (this.pos >= this.text.length) break

      const ch = this.peek()

      if (/\d/.test(ch) || ch === '.') {
        ts.push({ t: 'num', v: this.scanNumber() })
      } else if (/[a-zA-Z]/.test(ch)) {
        const name = this.scanId()
        if (FUNC_NAMES.includes(name)) ts.push({ t: 'func', n: name })
        else if (name in CONST_VALUES) ts.push({ t: 'const', v: CONST_VALUES[name] })
        else throw new Error(`未识别的名称: '${name}'`)
      } else {
        this.advance()
        switch (ch) {
          case '+': ts.push({ t: 'plus' });  break
          case '-': ts.push({ t: 'minus' }); break
          case '*': ts.push({ t: 'mul' });   break
          case '/': ts.push({ t: 'div' });   break
          case '^': ts.push({ t: 'powop' }); break
          case '(': ts.push({ t: 'lpar' });  break
          case ')': ts.push({ t: 'rpar' });  break
          case ',': ts.push({ t: 'comma' }); break
          default:
            throw new Error(`无法识别的字符: '${ch}'`)
        }
      }
    }
    ts.push({ t: 'eof' })
    return ts
  }
}

// ═══════════════ 语法分析 + 求值 ═══════════════

class Parser {
  constructor(tokens) {
    this.ts = tokens
    this.pos = 0
  }

  peek() { return this.ts[this.pos] }
  next() { return this.ts[this.pos++] }

  expect(type) {
    if (this.ts[this.pos].t !== type)
      throw new Error('语法错误：括号不匹配')
    return this.ts[this.pos++]
  }

  parse() {
    const r = this.expression()
    if (this.peek().t !== 'eof')
      throw new Error('语法错误：表达式末尾有多余内容')
    return r
  }

  // expression → term (('+'|'-') term)*
  expression() {
    let left = this.term()
    while (this.peek().t === 'plus' || this.peek().t === 'minus') {
      const op = this.next()
      const right = this.term()
      left = op.t === 'plus' ? left + right : left - right
    }
    return left
  }

  // term → unary (('*'|'/') unary)*
  term() {
    let left = this.unary()
    while (this.peek().t === 'mul' || this.peek().t === 'div') {
      const op = this.next()
      const right = this.unary()
      if (op.t === 'div' && right === 0)
        throw new Error('数学错误：除以零')
      left = op.t === 'mul' ? left * right : left / right
    }
    return left
  }

  // unary → ('+'|'-') unary | power
  unary() {
    if (this.peek().t === 'minus') { this.next(); return -this.unary() }
    if (this.peek().t === 'plus')  { this.next(); return this.unary() }
    return this.power()
  }

  // power → primary ('^' power)?  右结合
  power() {
    const left = this.primary()
    if (this.peek().t === 'powop') {
      this.next()
      const right = this.power()
      return this.checkedPow(left, right)
    }
    return left
  }

  // primary → NUMBER | CONST | '(' expr ')' | FUNC '(' args ')'
  primary() {
    const tok = this.peek()
    if (tok.t === 'num')    { this.next(); return tok.v }
    if (tok.t === 'const')  { this.next(); return tok.v }
    if (tok.t === 'lpar')   { this.next(); const r = this.expression(); this.expect('rpar'); return r }
    if (tok.t === 'func')   return this.parseFunc()
    throw new Error('语法错误：意外的 token')
  }

  parseFunc() {
    const fname = this.next().n
    this.expect('lpar')

    // 单参数函数
    const singleArg = ['sin', 'cos', 'tan', 'log', 'ln', 'sqrt', 'abs']
    if (singleArg.includes(fname)) {
      const arg = this.expression()
      this.expect('rpar')
      return this.dispatchSingle(fname, arg)
    }

    // pow: 双参数（允许留空）
    const args = []

    // 第一个参数
    if (this.peek().t === 'rpar')         { /* 无参 */ }
    else if (this.peek().t === 'comma')   { args.push(1) }
    else                                  { args.push(this.expression()) }

    // 第二个参数
    if (this.peek().t === 'comma') {
      this.next()
      if (this.peek().t === 'rpar') { args.push(1) }
      else                          { args.push(this.expression()) }
    }

    this.expect('rpar')

    if (fname === 'pow') {
      if (args.length === 0) throw new Error('pow() 缺少参数')
      if (args.length === 1) args.push(1)
      return this.checkedPow(args[0], args[1])
    }

    throw new Error(`未实现的函数: ${fname}`)
  }

  // 单参数函数分派
  dispatchSingle(name, arg) {
    switch (name) {
      case 'sin':  return Math.sin(arg)
      case 'cos':  return Math.cos(arg)
      case 'tan':  return Math.tan(arg)
      case 'log':  return Math.log10(arg)
      case 'ln':   return Math.log(arg)
      case 'sqrt':
        if (arg < 0) throw new Error(`实数域错误：sqrt(${arg}) 在实数范围内无定义`)
        return Math.sqrt(arg)
      case 'abs':  return Math.abs(arg)
      default: throw new Error(`未实现的函数: ${name}`)
    }
  }

  // 实数域约束
  checkedPow(base, exp) {
    if (base < 0 && exp !== Math.floor(exp))
      throw new Error(`实数域错误：负底数 (${base}) 的 ${exp} 次方无实数解`)
    return Math.pow(base, exp)
  }
}

// ═══════════════ 对外接口 ═══════════════

export function evaluate(expr) {
  const lexer = new Lexer(expr)
  const parser = new Parser(lexer.tokenize())
  return parser.parse()
}

export function formatResult(value) {
  if (value === 0) return '0'
  if (value === Math.floor(value) && Math.abs(value) < 1e15)
    return String(Math.floor(value))
  return parseFloat(value.toPrecision(15)).toString()
}
