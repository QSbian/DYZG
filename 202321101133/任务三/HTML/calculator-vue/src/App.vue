<template>
  <div class="app">
    <div class="calculator">
      <!-- 显示区 -->
      <CalcDisplay
        :expression="expr"
        :result="resultStr"
        :resultType="resultType"
        :history="history"
        :hasError="hasError"
      />

      <!-- 按钮区 -->
      <CalcButtons
        @append="onAppend"
        @equals="onEquals"
        @clear="onClear"
        @clearAll="onClearAll"
        @backspace="onBackspace"
      />

      <!-- 底部帮助 -->
      <div class="bottom-bar">
        <button class="btn-help muted" @click="showHelp">使用帮助</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import CalcDisplay from './components/CalcDisplay.vue'
import CalcButtons from './components/CalcButtons.vue'
import { evaluate, formatResult } from './engine/calculatorEngine.js'

// ── 状态 ──────────────────────────────────
const expr       = ref('')
const resultStr  = ref('0')
const resultType = ref('default')   // 'default' | 'success' | 'error'
const history    = ref('')
const hasError   = ref(false)

// ── 按钮逻辑 ──────────────────────────────

function onAppend(ch) {
  if (hasError.value) {
    expr.value = ch
    hasError.value = false
  } else {
    expr.value += ch
  }
  if (resultType.value === 'error') {
    resultStr.value = '0'
    resultType.value = 'default'
  }
}

function onEquals() {
  const raw = expr.value.trim()
  if (!raw) { resultStr.value = '0'; return }
  try {
    const val = evaluate(raw)
    const str = formatResult(val)
    // 更新历史
    const line = raw + ' = ' + str
    const lines = history.value
      ? (history.value + '\n' + line).split('\n').slice(-4)
      : [line]
    history.value = lines.join('\n')
    expr.value = str
    resultStr.value = str
    resultType.value = 'success'
    hasError.value = false
  } catch (e) {
    resultStr.value = e.message
    resultType.value = 'error'
    hasError.value = true
  }
}

function onClear() {
  expr.value = ''
  resultStr.value = '0'
  resultType.value = 'default'
  hasError.value = false
}

function onClearAll() {
  expr.value = ''
  resultStr.value = '0'
  resultType.value = 'default'
  history.value = ''
  hasError.value = false
}

function onBackspace() {
  expr.value = expr.value.slice(0, -1)
  if (hasError.value) {
    hasError.value = false
    resultStr.value = '0'
    resultType.value = 'default'
  }
}

// ── 键盘输入 ─────────────────────────────

function onKeydown(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return
  const key = e.key
  if (/^[0-9.]$/.test(key) || ['+', '-', '*', '/', '^', '(', ')', ','].includes(key)) {
    e.preventDefault()
    onAppend(key)
  } else if (key === 'Enter') {
    e.preventDefault()
    onEquals()
  } else if (key === 'Backspace') {
    e.preventDefault()
    onBackspace()
  } else if (key === 'Escape') {
    e.preventDefault()
    onClear()
  }
}

onMounted(()  => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

// ── 帮助弹窗 ─────────────────────────────

function showHelp() {
  alert(
    '科学计算器使用说明\n\n' +
    '运算符:  +  -  ×  ÷  ^\n' +
    '乘方:    2^3  →  8\n' +
    '         pow(2, 3)  →  8\n' +
    '开方:    sqrt(9)  →  3\n' +
    '         pow(9, 1/2)  →  3\n' +
    '         9^(1/2)  →  3\n' +
    '括号:    (1+2)*3  →  9\n' +
    '负数:    -3 + 5  →  2\n\n' +
    '三角函数:\n' +
    '  sin(π/6)  →  0.5    cos(0)  →  1\n' +
    '  tan(π/4)  →  1\n' +
    '对数:\n' +
    '  ln(1)  →  0    log(100)  →  2\n' +
    '其他:\n' +
    '  abs(-5)  →  5    sqrt(2)  →  1.414...\n' +
    '常量:\n' +
    '  π  →  3.14159...    e  →  2.71828...\n\n' +
    'pow(m, n): m 的 n 次方\n' +
    'pow 参数留空默认 1\n' +
    '  如 pow(,1/2) = 1\n\n' +
    '键盘快捷键:\n' +
    '  Enter = 计算\n' +
    '  Backspace = 退格\n' +
    '  Esc = 清除'
  )
}
</script>

<style>
/* ════════════ 全局重置 ════════════ */
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }

html, body {
  height: 100%;
  background: #1a1a2e;
  color: #eaeaea;
  font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  -webkit-tap-highlight-color: transparent;
  -webkit-font-smoothing: antialiased;
}

#app {
  height: 100%;
}
</style>

<style scoped>
.app {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  min-height: 100dvh;
  padding: 12px;
}

.calculator {
  width: 100%;
  max-width: 440px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.bottom-bar {
  display: flex;
  justify-content: flex-end;
  margin-top: 2px;
}

.btn-help {
  background: transparent;
  color: #888;
  border: none;
  font-size: 13px;
  padding: 6px 12px;
  cursor: pointer;
  font-family: inherit;
}

.btn-help:hover { color: #eaeaea; }

/* 桌面端加卡片效果 */
@media (min-width: 768px) {
  .calculator {
    box-shadow: 0 8px 40px rgba(0,0,0,0.5);
    border-radius: 20px;
    padding: 20px;
    background: #12122a;
  }
}

/* 移动端撑满 */
@media (max-width: 480px) {
  .app { padding: 6px; align-items: stretch; }
  .calculator { max-width: 100%; gap: 6px; }
}
</style>
