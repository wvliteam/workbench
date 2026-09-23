<script setup>
import { ref } from 'vue'
import {
  Check,
  AlertCircle,
  CircleDot,
  Circle,
  ChevronRight,
} from 'lucide-vue-next'

const PHASE_NAMES = {
  clarify: '需求澄清',
  analyze: '现状分析',
  design: '方案设计',
  develop: '开发实现',
  verify: '测试验证',
  retro: '总结复盘',
}

const props = defineProps({
  phases: { type: Array, default: () => [] },
  selectedPhase: { type: String, default: null },
})

const emit = defineEmits(['select-phase'])

const hoveredPhase = ref(null)
const popoverPos = ref({ x: 0, y: 0 })

const handlePhaseClick = (phase) => {
  emit('select-phase', phase)
}

const getPhaseStatusDesc = (p) => {
  if (p.is_current) return '当前进行中 (Current Phase)'
  if (p.passed) return '准出门禁已通过 (Passed Gate)'
  if (p.forced) return '已豁免/强制放行 (Forced Waiver)'
  return '待执行 / 未开始 (Pending)'
}

const getStatusBadgeText = (p) => {
  if (p.is_current) return 'CURRENT'
  if (p.passed) return 'PASSED'
  if (p.forced) return 'FORCED'
  return 'PENDING'
}

const handleMouseEnter = (p, event) => {
  hoveredPhase.value = p
  const rect = event.currentTarget.getBoundingClientRect()
  popoverPos.value = {
    x: rect.left + rect.width / 2,
    y: rect.bottom + 8,
  }
}

const handleMouseLeave = () => {
  hoveredPhase.value = null
}
</script>

<template>
  <div class="pipeline-section">
    <div class="pipeline-bar">
      <template v-for="(p, idx) in phases" :key="p.phase">
        <div
          :class="[
            'phase-step',
            p.status || 'pending',
            {
              'is-current': p.is_current,
              'is-selected': selectedPhase === p.phase,
            }
          ]"
          role="button"
          tabindex="0"
          :aria-label="`${p.name_cn || PHASE_NAMES[p.phase] || p.phase}: ${getPhaseStatusDesc(p)}`"
          @click="handlePhaseClick(p.phase)"
          @keydown.enter="handlePhaseClick(p.phase)"
          @keydown.space.prevent="handlePhaseClick(p.phase)"
          @mouseenter="handleMouseEnter(p, $event)"
          @mouseleave="handleMouseLeave"
        >
          <span class="phase-indicator">
            <CircleDot v-if="p.is_current" :size="13" class="indicator-icon is-current" />
            <Check v-else-if="p.passed" :size="13" class="indicator-icon passed" />
            <AlertCircle v-else-if="p.forced" :size="13" class="indicator-icon forced" />
            <Circle v-else :size="9" class="indicator-icon pending" />
          </span>
          <span class="phase-name-cn">{{ p.name_cn || PHASE_NAMES[p.phase] || p.phase }}</span>
          <span class="phase-name-en">{{ p.phase }}</span>
        </div>
        <ChevronRight
          v-if="idx < phases.length - 1"
          :size="14"
          class="phase-arrow"
        />
      </template>
    </div>

    <!-- 阶段悬浮详细提示 (Teleport 到 body 避免父级容器 overflow 截断) -->
    <Teleport to="body">
      <div
        v-if="hoveredPhase"
        class="phase-hover-popover"
        :style="{
          left: `${popoverPos.x}px`,
          top: `${popoverPos.y}px`,
        }"
      >
        <div class="popover-header">
          <span class="popover-title-cn">{{ hoveredPhase.name_cn || PHASE_NAMES[hoveredPhase.phase] || hoveredPhase.phase }}</span>
          <span class="popover-title-en mono">{{ hoveredPhase.phase }}</span>
          <span :class="['popover-badge', hoveredPhase.status || 'pending']">
            {{ getStatusBadgeText(hoveredPhase) }}
          </span>
        </div>
        <div class="popover-desc">
          <span class="popover-status-text">{{ getPhaseStatusDesc(hoveredPhase) }}</span>
        </div>
        <div class="popover-hint">
          {{ selectedPhase === hoveredPhase.phase ? '已选中：再次点击取消阶段聚焦' : '点击此阶段：在画布中聚焦并高亮任务节点' }}
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.pipeline-section {
  height: 42px;
  min-height: 42px;
  background: #0b0f16;
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  padding: 0 16px;
  overflow-x: auto;
  z-index: 15;
}

.pipeline-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
}

.phase-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  font-size: 12px;
  white-space: nowrap;
  transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
  cursor: pointer;
  user-select: none;
}

.phase-step:hover {
  border-color: var(--border-focus);
  background: rgba(255, 255, 255, 0.05);
  transform: translateY(-1px);
}

.phase-step.is-current {
  background: rgba(56, 139, 253, 0.12);
  border-color: var(--border-focus);
}

.phase-step.passed {
  border-color: rgba(63, 185, 80, 0.35);
  background: rgba(63, 185, 80, 0.05);
}

.phase-step.forced {
  border-color: rgba(210, 153, 34, 0.35);
  background: rgba(210, 153, 34, 0.05);
}

.phase-step.pending {
  opacity: 0.55;
}

.phase-step.pending:hover {
  opacity: 0.85;
}

/* 交互选中高亮态 */
.phase-step.is-selected {
  border-color: var(--accent) !important;
  background: rgba(56, 139, 253, 0.18) !important;
  box-shadow: 0 0 0 1px var(--accent), 0 2px 8px rgba(56, 139, 253, 0.25);
  opacity: 1 !important;
}

.phase-indicator {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
}

.indicator-icon.is-current {
  color: var(--accent);
}

.indicator-icon.passed {
  color: var(--status-done);
}

.indicator-icon.forced {
  color: var(--status-doing);
}

.indicator-icon.pending {
  color: var(--text-muted);
}

.phase-name-cn {
  font-weight: 600;
  color: var(--text-primary);
}

.phase-name-en {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-muted);
}

.phase-arrow {
  color: #3b4354;
  flex-shrink: 0;
  user-select: none;
}
</style>

<style>
/* Teleport 浮层全局样式 */
.phase-hover-popover {
  position: fixed;
  transform: translateX(-50%);
  background: var(--bg-surface, #161b22);
  border: 1px solid var(--border-default, #30363d);
  border-radius: var(--radius-md, 6px);
  padding: 8px 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.65);
  pointer-events: none;
  z-index: 9999;
  font-size: 11px;
  min-width: 190px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  animation: popover-fade-in 0.12s ease-out;
}

@keyframes popover-fade-in {
  from {
    opacity: 0;
    transform: translateX(-50%) translateY(-3px);
  }
  to {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
}

.popover-header {
  display: flex;
  align-items: center;
  gap: 6px;
}

.popover-title-cn {
  font-weight: 600;
  color: var(--text-primary, #f0f6fc);
  font-size: 12px;
}

.popover-title-en {
  color: var(--text-muted, #8b949e);
  font-size: 11px;
}

.popover-badge {
  margin-left: auto;
  font-size: 10px;
  font-family: var(--font-mono, monospace);
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 10px;
}

.popover-badge.current {
  background: rgba(56, 139, 253, 0.2);
  color: var(--accent, #58a6ff);
}

.popover-badge.passed {
  background: rgba(63, 185, 80, 0.2);
  color: var(--status-done, #3fb950);
}

.popover-badge.forced {
  background: rgba(210, 153, 34, 0.2);
  color: var(--status-doing, #d29922);
}

.popover-badge.pending {
  background: rgba(110, 118, 129, 0.2);
  color: var(--text-muted, #8b949e);
}

.popover-desc {
  color: var(--text-secondary, #c9d1d9);
  line-height: 1.4;
}

.popover-hint {
  font-size: 10px;
  color: var(--accent, #58a6ff);
  margin-top: 2px;
  border-top: 1px dashed var(--border-subtle, #21262d);
  padding-top: 4px;
}
</style>
