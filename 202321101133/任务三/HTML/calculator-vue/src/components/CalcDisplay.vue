<template>
  <div class="display-panel">
    <div v-if="history" class="history">{{ history }}</div>
    <div class="expression" :class="{ error: hasError }">
      {{ displayExpr }}
    </div>
    <div class="result" :class="resultClass">
      {{ resultStr }}
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  expression:   { type: String, default: '' },
  result:       { type: String, default: '0' },
  resultType:   { type: String, default: 'default' }, // 'default' | 'success' | 'error'
  history:      { type: String, default: '' },
  hasError:     { type: Boolean, default: false },
})

const displayExpr = computed(() => props.expression || '\u00A0')
const resultClass = computed(() => ({
  success: props.resultType === 'success',
  error:   props.resultType === 'error',
}))
</script>

<style scoped>
.display-panel {
  background: #16213e;
  border-radius: 16px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  border: 1px solid #2a3a5c;
  min-height: 130px;
  justify-content: flex-end;
  transition: all 0.2s;
}

.history {
  font-size: 13px;
  color: #888;
  text-align: right;
  min-height: 20px;
  word-break: break-all;
  line-height: 1.4;
  max-height: 40px;
  overflow-y: auto;
}

.expression {
  font-size: 22px;
  color: #eaeaea;
  text-align: right;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  word-break: break-all;
  min-height: 30px;
  line-height: 1.3;
  transition: color 0.15s;
}

.expression.error { color: #e94560; }

.result {
  font-size: 38px;
  font-weight: 700;
  text-align: right;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  word-break: break-all;
  line-height: 1.2;
  min-height: 48px;
  display: flex;
  align-items: flex-end;
  justify-content: flex-end;
  transition: color 0.3s;
  color: #eaeaea;
}

.result.success { color: #00b894; }
.result.error   { color: #e94560; font-size: 16px; font-weight: 400; }

@media (max-width: 480px) {
  .display-panel { padding: 12px 14px; min-height: 110px; border-radius: 12px; }
  .expression { font-size: 18px; }
  .result { font-size: 32px; min-height: 40px; }
  .result.error { font-size: 14px; }
}

@media (max-width: 360px) {
  .display-panel { min-height: 95px; }
  .expression { font-size: 16px; }
  .result { font-size: 28px; }
}
</style>
