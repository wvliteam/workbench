<script setup>
import { Maximize2, RotateCcw, Plus, Minus, AlertTriangle } from 'lucide-vue-next'

defineProps({
  total: { type: Number, default: 0 },
  done: { type: Number, default: 0 },
  doing: { type: Number, default: 0 },
  blocked: { type: Number, default: 0 },
  progress: { type: Number, default: 0 },
  activeFilter: { type: String, default: 'all' },
  zoomScale: { type: Number, default: 1.0 },
  unconfiguredGates: { type: Array, default: () => [] },
})

defineEmits(['change-filter', 'zoom-in', 'zoom-out', 'fit-view', 'reset-view'])
</script>

<template>
  <div class="floating-hud">
    <!-- 任务指标概览 -->
    <div class="hud-card stats-card">
      <div class="stat-item"><span>总数</span><strong>{{ total }}</strong></div>
      <div class="stat-item"><span class="dot done"></span><span>已完成</span><strong class="text-done">{{ done }}</strong></div>
      <div class="stat-item"><span class="dot doing"></span><span>进行中</span><strong class="text-doing">{{ doing }}</strong></div>
      <div class="stat-item"><span class="dot blocked"></span><span>阻断</span><strong class="text-blocked">{{ blocked }}</strong></div>
      <div class="progress-wrapper" title="任务完成率">
        <div class="progress-bar-bg">
          <div class="progress-bar-fill" :style="{ width: `${progress}%` }"></div>
        </div>
        <span class="progress-text">{{ progress }}%</span>
      </div>
      <div v-if="unconfiguredGates.length > 0" class="warn-pill" :title="`未配门禁: ${unconfiguredGates.join(', ')}`">
        <AlertTriangle :size="12" :stroke-width="2" />
        <span>未配门禁: {{ unconfiguredGates.join(', ') }}</span>
      </div>
    </div>

    <!-- 过滤器与画布控制组合 -->
    <div class="hud-card controls-card">
      <div class="filter-group">
        <button
          v-for="filter in [
            { key: 'all', label: '全部' },
            { key: 'doing', label: '进行中' },
            { key: 'done', label: '已完成' },
            { key: 'blocked', label: '阻断' },
            { key: 'todo', label: '待处理' }
          ]"
          :key="filter.key"
          :class="['filter-btn', { active: activeFilter === filter.key }]"
          @click="$emit('change-filter', filter.key)"
        >
          {{ filter.label }}
        </button>
      </div>

      <div class="divider"></div>

      <div class="zoom-controls">
        <button class="btn btn-icon btn-sm" @click="$emit('zoom-out')" title="缩小 (−)">
          <Minus :size="12" :stroke-width="2" />
        </button>
        <span class="zoom-level mono">{{ Math.round(zoomScale * 100) }}%</span>
        <button class="btn btn-icon btn-sm" @click="$emit('zoom-in')" title="放大 (+)">
          <Plus :size="12" :stroke-width="2" />
        </button>
        <button class="btn btn-sm" @click="$emit('fit-view')" title="居中自适应视图 (快捷键 F)">
          <Maximize2 :size="12" :stroke-width="2" />
          <span>自适应</span>
        </button>
        <button class="btn btn-sm" @click="$emit('reset-view')" title="重置视角 (快捷键 0)">
          <RotateCcw :size="12" :stroke-width="2" />
          <span>重置</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.floating-hud {
  position: absolute;
  top: 14px;
  right: 16px;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  z-index: 10;
  pointer-events: none;
}

.hud-card {
  pointer-events: auto;
  background: rgba(14, 19, 27, 0.88);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: 6px 12px;
  display: flex;
  align-items: center;
  gap: 12px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--text-muted);
}

.stat-item strong {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-primary);
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.dot.done { background: var(--status-done); }
.dot.doing { background: var(--status-doing); }
.dot.blocked { background: var(--status-blocked); }

.progress-wrapper {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: 4px;
}

.progress-bar-bg {
  width: 70px;
  height: 5px;
  background: var(--bg-card);
  border-radius: 3px;
  overflow: hidden;
  border: 1px solid var(--border-subtle);
}

.progress-bar-fill {
  height: 100%;
  background: var(--status-done);
  transition: width 0.3s ease;
}

.progress-text {
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-muted);
}

.warn-pill {
  font-size: 11px;
  font-weight: 500;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--status-doing-bg);
  color: var(--status-doing);
  border: 1px solid rgba(210, 153, 34, 0.3);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.filter-group {
  display: flex;
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.filter-btn {
  padding: 3px 8px;
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}

.filter-btn:hover {
  color: var(--text-primary);
  background: var(--bg-card-hover);
}

.filter-btn.active {
  color: var(--text-primary);
  background: var(--border-default);
  font-weight: 600;
}

.divider {
  width: 1px;
  height: 16px;
  background: var(--border-subtle);
}

.zoom-controls {
  display: flex;
  align-items: center;
  gap: 4px;
}

.zoom-level {
  font-size: 11px;
  color: var(--text-muted);
  min-width: 38px;
  text-align: center;
}
</style>
