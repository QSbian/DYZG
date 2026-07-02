<template>
  <div class="btn-grid">
    <!-- Row 0: C    CE   ⌫    ÷ -->
    <button class="btn btn--func" @click="$emit('clear')">C</button>
    <button class="btn btn--func" @click="$emit('clearAll')">CE</button>
    <button class="btn btn--func" @click="$emit('backspace')">⌫</button>
    <button class="btn btn--op"   @click="$emit('append', '/')">÷</button>

    <!-- Row 1: sin  cos  tan  × -->
    <button class="btn btn--sci" @click="$emit('append', 'sin(')">sin</button>
    <button class="btn btn--sci" @click="$emit('append', 'cos(')">cos</button>
    <button class="btn btn--sci" @click="$emit('append', 'tan(')">tan</button>
    <button class="btn btn--op"  @click="$emit('append', '*')">×</button>

    <!-- Row 2: ln   log  √    − -->
    <button class="btn btn--sci" @click="$emit('append', 'ln(')">ln</button>
    <button class="btn btn--sci" @click="$emit('append', 'log(')">log</button>
    <button class="btn btn--sci" @click="$emit('append', 'sqrt(')">√</button>
    <button class="btn btn--op"  @click="$emit('append', '-')">−</button>

    <!-- Row 3: (    )    ^    + -->
    <button class="btn btn--num" @click="$emit('append', '(')">(</button>
    <button class="btn btn--num" @click="$emit('append', ')')">)</button>
    <button class="btn btn--num" @click="$emit('append', '^')">^</button>
    <button class="btn btn--op"  @click="$emit('append', '+')">+</button>

    <!-- Row 4: 7    8    9    = (span 2) -->
    <button class="btn btn--num" @click="$emit('append', '7')">7</button>
    <button class="btn btn--num" @click="$emit('append', '8')">8</button>
    <button class="btn btn--num" @click="$emit('append', '9')">9</button>
    <button class="btn btn--eq btn-span-2" @click="$emit('equals')">=</button>

    <!-- Row 5: 4    5    6 -->
    <button class="btn btn--num" @click="$emit('append', '4')">4</button>
    <button class="btn btn--num" @click="$emit('append', '5')">5</button>
    <button class="btn btn--num" @click="$emit('append', '6')">6</button>

    <!-- Row 6: 1    2    3    abs -->
    <button class="btn btn--num" @click="$emit('append', '1')">1</button>
    <button class="btn btn--num" @click="$emit('append', '2')">2</button>
    <button class="btn btn--num" @click="$emit('append', '3')">3</button>
    <button class="btn btn--sci" @click="$emit('append', 'abs(')">abs</button>

    <!-- Row 7: 0    .    π    pow -->
    <button class="btn btn--num" @click="$emit('append', '0')">0</button>
    <button class="btn btn--num" @click="$emit('append', '.')">.</button>
    <button class="btn btn--sci" @click="$emit('append', 'pi')">π</button>
    <button class="btn btn--sci" @click="$emit('append', 'pow(')">pow</button>
  </div>
</template>

<script setup>
defineEmits(['append', 'equals', 'clear', 'clearAll', 'backspace'])
</script>

<style scoped>
.btn-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}

.btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #2a3a5c;
  border-radius: 12px;
  font-size: 20px;
  font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-weight: 500;
  cursor: pointer;
  padding: 16px 0;
  transition: background 0.12s, transform 0.08s, box-shadow 0.12s;
  outline: none;
  -webkit-tap-highlight-color: transparent;
  user-select: none;
}

.btn:active {
  transform: scale(0.95);
  box-shadow: 0 0 12px rgba(233, 69, 96, 0.25);
}

.btn:focus-visible {
  box-shadow: 0 0 0 2px #e94560;
}

/* 类型 */
.btn--func {
  color: #888;
  background: #3a3f5c;
  font-size: 16px;
}
.btn--func:active { background: #0f3460; }

.btn--sci {
  color: #c8a2ff;
  background: #2a2d4a;
  font-size: 15px;
  font-weight: 600;
  border-color: #5a3f8c;
}
.btn--sci:active { background: #3d1f6e; }

.btn--op {
  color: #fff;
  background: #e94560;
  border-color: #e94560;
}
.btn--op:active { background: #b71c40; }

.btn--eq {
  color: #fff;
  background: #00b894;
  border-color: #00b894;
  font-size: 22px;
  font-weight: 700;
}
.btn--eq:active { background: #008060; }

.btn--num {
  color: #eaeaea;
  background: #1f2b47;
}
.btn--num:active { background: #0f3460; }

.btn-span-2 { grid-row: span 2; }

/* hover（仅桌面） */
@media (hover: hover) {
  .btn--func:hover { background: #4a5070; }
  .btn--sci:hover  { background: #3d2a5a; }
  .btn--op:hover   { background: #d13352; }
  .btn--eq:hover   { background: #00a37e; }
  .btn--num:hover  { background: #2e3d61; }
}

/* 移动端 */
@media (max-width: 480px) {
  .btn-grid { gap: 5px; }
  .btn {
    font-size: 18px;
    padding: 14px 0;
    border-radius: 8px;
  }
  .btn--func { font-size: 14px; }
  .btn--sci  { font-size: 13px; }
  .btn--eq   { font-size: 20px; }
  .btn:active { transform: scale(0.97); }
}

@media (max-width: 360px) {
  .btn { font-size: 16px; padding: 12px 0; }
  .btn--func { font-size: 13px; }
  .btn--sci  { font-size: 12px; }
  .btn--eq   { font-size: 18px; }
  .btn-grid  { gap: 4px; }
}
</style>
