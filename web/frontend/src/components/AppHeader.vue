<script setup>
defineProps({
  project: { type: String, default: 'workbench' },
  version: { type: String, default: '0.1.0' },
  flows: { type: Array, default: () => ['main'] },
  currentFlow: { type: String, default: 'main' },
  roleLocked: { type: Boolean, default: false },
  sseStatus: { type: String, default: 'connecting' },
  isPulsing: { type: Boolean, default: false },
  isRefreshing: { type: Boolean, default: false },
})

defineEmits(['change-flow', 'refresh'])
</script>

<template>
  <header class="app-header">
    <div class="header-left">
      <div class="brand">
        <!-- 工业风 Devtool SVG 图标（无系统 Emoji） -->
        <svg class="brand-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="3" y="3" width="7" height="7" rx="1"></rect>
          <rect x="14" y="3" width="7" height="7" rx="1"></rect>
          <rect x="14" y="14" width="7" height="7" rx="1"></rect>
          <rect x="3" y="14" width="7" height="7" rx="1"></rect>
          <path d="M10 6.5h4M10 17.5h4M6.5 10v4M17.5 10v4"></path>
        </svg>
        <span class="brand-name">Workbench Dashboard</span>
      </div>
      <span class="badge badge-project">{{ project }}</span>
      <span class="badge badge-version">v{{ version }}</span>
    </div>

    <div class="header-center">
      <div class="flow-selector-wrapper">
        <span class="selector-label">FLOW:</span>
        <select
          class="select-input"
          :value="currentFlow"
          @change="$emit('change-flow', $event.target.value)"
          title="切换需求线 Flow"
        >
          <option v-for="f in flows" :key="f" :value="f">{{ f }}</option>
        </select>
      </div>

      <div :class="['role-guard-badge', roleLocked ? 'locked' : 'warning']">
        <svg v-if="roleLocked" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
        </svg>
        <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="8" x2="12" y2="12"></line>
          <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
        <span>{{ roleLocked ? '角色锁生效' : '角色锁未设' }}</span>
      </div>
    </div>

    <div class="header-right">
      <div class="sse-status" title="Server-Sent Events 实时通道">
        <span :class="['pulse-dot', sseStatus, { pulse: isPulsing }]"></span>
        <span class="sse-text">{{ sseStatus === 'connected' ? 'LIVE SSE' : (sseStatus === 'connecting' ? 'CONNECTING' : 'OFFLINE') }}</span>
      </div>

      <button class="btn" :disabled="isRefreshing" @click="$emit('refresh')" title="立即重新获取数据">
        <svg class="refresh-icon" :class="{ spinning: isRefreshing }" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M23 4v6h-6"></path>
          <path d="M1 20v-6h6"></path>
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
        </svg>
        <span>刷新</span>
      </button>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  height: 48px;
  min-height: 48px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  z-index: 20;
}

.header-left, .header-center, .header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.2px;
}

.brand-icon {
  width: 18px;
  height: 18px;
  color: var(--accent);
}

.badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 7px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
  font-family: var(--font-mono);
}

.badge-project {
  background: rgba(88, 166, 255, 0.1);
  color: var(--accent);
  border: 1px solid rgba(88, 166, 255, 0.25);
}

.badge-version {
  background: rgba(110, 118, 129, 0.12);
  color: var(--text-muted);
  border: 1px solid var(--border-subtle);
}

.flow-selector-wrapper {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--bg-card);
  padding: 2px 8px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
}

.selector-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
  font-family: var(--font-mono);
}

.role-guard-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: var(--radius-sm);
}

.role-guard-badge.locked {
  background: var(--status-done-bg);
  color: var(--status-done);
  border: 1px solid rgba(63, 185, 80, 0.3);
}

.role-guard-badge.warning {
  background: var(--status-doing-bg);
  color: var(--status-doing);
  border: 1px solid rgba(210, 153, 34, 0.3);
}

.sse-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  font-family: var(--font-mono);
}

.pulse-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  transition: all 0.3s ease;
}

.pulse-dot.connected {
  background: var(--status-done);
  box-shadow: 0 0 6px var(--status-done);
}

.pulse-dot.connecting {
  background: var(--status-doing);
  box-shadow: 0 0 6px var(--status-doing);
}

.pulse-dot.disconnected {
  background: var(--status-blocked);
  box-shadow: 0 0 6px var(--status-blocked);
}

.pulse-dot.pulse {
  transform: scale(1.6);
  background: var(--accent);
  box-shadow: 0 0 10px var(--accent);
}

.refresh-icon.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
