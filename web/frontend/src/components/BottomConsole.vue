<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { useClipboard } from '@vueuse/core'
import {
  Copy,
  Check,
  Terminal,
  ArrowDown,
  RefreshCw,
  X,
  AlertTriangle,
  Maximize2,
  Minimize2,
} from 'lucide-vue-next'
import { ansiToHtml } from '../composables/useAnsi.js'

const props = defineProps({
  isOpen: { type: Boolean, default: false },
  consoleHeight: { type: Number, default: 280 },
  activeTab: { type: String, default: 'gate' },
  gateLogs: { type: Object, default: () => ({}) },
  configuredGates: { type: Array, default: () => ['test', 'lint', 'build'] },
  gateWaivers: { type: Object, default: () => ({}) },
  contractsData: { type: Object, default: () => ({ contracts: [], unlocks: {}, disputes: {} }) },
  auditData: { type: Array, default: () => [] },
  eventLogs: { type: Array, default: () => [] },
})

const emit = defineEmits([
  'toggle',
  'close',
  'switch-tab',
  'select-gate',
  'refresh-gate',
  'refresh-contracts',
  'refresh-audit',
  'filter-audit',
  'clear-events',
  'update:consoleHeight',
])

const selectedGate = ref('test')
const auditFilter = ref('all')
const auditLimit = ref(50)
const terminalBody = ref(null)

// 高度调节与最大化状态
const isDragging = ref(false)
const isMaximized = ref(false)
const savedHeight = ref(280)

let startY = 0
let startHeight = 0

const onResizeMouseDown = (e) => {
  isDragging.value = true
  startY = e.clientY
  startHeight = props.consoleHeight
  window.addEventListener('mousemove', onResizeMouseMove)
  window.addEventListener('mouseup', onResizeMouseUp)
  document.body.style.cursor = 'row-resize'
  document.body.style.userSelect = 'none'
}

const onResizeMouseMove = (e) => {
  if (!isDragging.value) return
  const delta = startY - e.clientY
  const minH = 180
  const maxH = Math.round(window.innerHeight * 0.8)
  const newHeight = Math.max(minH, Math.min(startHeight + delta, maxH))
  if (isMaximized.value) {
    isMaximized.value = false
  }
  emit('update:consoleHeight', newHeight)
}

const onResizeMouseUp = () => {
  if (!isDragging.value) return
  isDragging.value = false
  window.removeEventListener('mousemove', onResizeMouseMove)
  window.removeEventListener('mouseup', onResizeMouseUp)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}

onUnmounted(() => {
  onResizeMouseUp()
})

const toggleMaximize = () => {
  const maxH = Math.round(window.innerHeight * 0.8)
  if (isMaximized.value) {
    emit('update:consoleHeight', savedHeight.value || 280)
    isMaximized.value = false
  } else {
    savedHeight.value = props.consoleHeight
    emit('update:consoleHeight', maxH)
    isMaximized.value = true
  }
}

const { copy, copied } = useClipboard({ copiedDuring: 1500, legacy: true })

const currentGateLogText = computed(() => {
  return props.gateLogs[selectedGate.value] || ''
})

const currentGateRenderedHtml = computed(() => {
  if (!currentGateLogText.value) {
    return '<span class="text-muted">该门禁尚未生成日志文件或无需执行。</span>'
  }
  return ansiToHtml(currentGateLogText.value)
})

// 单门禁状态（当前选中项）
const currentGateStatus = computed(() => {
  const g = selectedGate.value
  if (props.gateWaivers[g]) return { text: 'WAIVED', class: 'waived' }
  const log = currentGateLogText.value
  if (!log) return null
  if (/failed|FAIL|error|ERROR/i.test(log)) return { text: 'FAIL', class: 'fail' }
  if (/passed|PASS|通过|成功/i.test(log)) return { text: 'PASS', class: 'pass' }
  return null
})

// 全局门禁健康度聚合（防假绿遮蔽）
const globalGateHealth = computed(() => {
  const gates = props.configuredGates || []
  if (gates.length === 0) {
    return { status: 'none', text: '', class: '' }
  }

  let failCount = 0
  let passCount = 0
  let waivedCount = 0
  let pendingCount = 0

  for (const g of gates) {
    if (props.gateWaivers[g]) {
      waivedCount++
      continue
    }
    const log = props.gateLogs[g] || ''
    if (!log) {
      pendingCount++
      continue
    }
    if (/failed|FAIL|error|ERROR/i.test(log)) {
      failCount++
    } else if (/passed|PASS|通过|成功/i.test(log)) {
      passCount++
    } else {
      pendingCount++
    }
  }

  // 若任意未豁免门禁日志包含失败，全局标红并显示 N FAIL
  if (failCount > 0) {
    return {
      status: 'fail',
      text: `${failCount} FAIL`,
      class: 'fail',
    }
  }

  // 若所有配置门禁均已完成且通过
  if (passCount + waivedCount >= gates.length && gates.length > 0) {
    return {
      status: 'pass',
      text: 'ALL PASS',
      class: 'pass',
    }
  }

  if (passCount > 0) {
    return {
      status: 'pass',
      text: `${passCount} PASS`,
      class: 'pass',
    }
  }

  if (waivedCount > 0) {
    return {
      status: 'waived',
      text: `${waivedCount} WAIVED`,
      class: 'waived',
    }
  }

  return {
    status: 'pending',
    text: '',
    class: '',
  }
})

const toggleStatusClass = computed(() => {
  if (globalGateHealth.value.status === 'fail') return 'error'
  if (globalGateHealth.value.status === 'waived') return 'warning'
  if (globalGateHealth.value.status === 'pass') return 'ok'
  return 'ok'
})

const filteredAuditEntries = computed(() => {
  let list = props.auditData || []
  if (auditFilter.value !== 'all') {
    list = list.filter((e) => (e.event || '').startsWith(auditFilter.value))
  }
  return [...list].reverse()
})

const copyGateLog = () => {
  if (!currentGateLogText.value) return
  copy(currentGateLogText.value)
}

const scrollToBottom = () => {
  if (terminalBody.value) {
    terminalBody.value.scrollTop = terminalBody.value.scrollHeight
  }
}

const onGateChange = (e) => {
  selectedGate.value = e.target.value
  emit('select-gate', e.target.value)
}
</script>

<template>
  <div>
    <!-- 底部控制台展开/收起开关 -->
    <button class="console-toggle-btn" @click="$emit('toggle')" title="展开/收起控制台">
      <Terminal :size="14" :stroke-width="2" />
      <span>控制台与门禁日志</span>
      <span :class="['status-dot', toggleStatusClass]"></span>
    </button>

    <!-- 底部控制台抽屉面板 -->
    <div
      :class="['console-drawer', { open: isOpen, dragging: isDragging }]"
      :style="{ height: `${consoleHeight}px` }"
    >
      <!-- 顶部拖拽调节手柄 -->
      <div
        class="console-resizer"
        @mousedown.prevent="onResizeMouseDown"
        title="按住上下拖拽调节高度"
      >
        <div class="resizer-handle"></div>
      </div>

      <div class="console-header">
        <div class="console-tabs">
          <button
            :class="['console-tab-btn', { active: activeTab === 'gate' }]"
            @click="$emit('switch-tab', 'gate')"
          >
            <span>门禁日志</span>
            <span v-if="globalGateHealth.text" :class="['tab-badge-sm', globalGateHealth.class]">
              {{ globalGateHealth.text }}
            </span>
          </button>

          <button
            :class="['console-tab-btn', { active: activeTab === 'contracts' }]"
            @click="$emit('switch-tab', 'contracts')"
          >
            <span>契约与争议</span>
            <span class="tab-badge-sm">{{ contractsData?.contracts?.length || 0 }}</span>
          </button>

          <button
            :class="['console-tab-btn', { active: activeTab === 'audit' }]"
            @click="$emit('switch-tab', 'audit')"
          >
            <span>审计流水</span>
            <span class="tab-badge-sm">{{ auditData?.length || 0 }}</span>
          </button>

          <button
            :class="['console-tab-btn', { active: activeTab === 'events' }]"
            @click="$emit('switch-tab', 'events')"
          >
            <span>实时事件</span>
            <span class="tab-badge-sm">{{ eventLogs?.length || 0 }}</span>
          </button>

          <button
            :class="['console-tab-btn', { active: activeTab === 'api' }]"
            @click="$emit('switch-tab', 'api')"
          >
            <span>API 探测</span>
          </button>
        </div>

        <div class="console-header-actions">
          <button
            class="btn btn-sm btn-icon-text"
            @click="toggleMaximize"
            :title="isMaximized ? '还原控制台高度' : '全屏展开控制台'"
          >
            <Minimize2 v-if="isMaximized" :size="12" :stroke-width="2" />
            <Maximize2 v-else :size="12" :stroke-width="2" />
            <span>{{ isMaximized ? '还原' : '最大化' }}</span>
          </button>
          <button class="btn btn-sm" @click="$emit('close')" title="收起控制台 (快捷键 Esc)">
            <X :size="12" :stroke-width="2" />
            <span>收起</span>
          </button>
        </div>
      </div>

      <div class="console-body">
        <!-- Panel 1: 门禁日志 -->
        <div v-show="activeTab === 'gate'" class="console-pane">
          <div class="console-toolbar">
            <div class="toolbar-left">
              <span class="toolbar-label">门禁命令:</span>
              <select class="select-input select-sm" :value="selectedGate" @change="onGateChange">
                <option v-for="g in configuredGates" :key="g" :value="g">
                  {{ g }} {{ gateWaivers[g] ? '(已豁免)' : '' }}
                </option>
              </select>
              <span v-if="gateWaivers[selectedGate]" class="gate-waiver-text">
                豁免: {{ gateWaivers[selectedGate] }}
              </span>
              <span v-if="currentGateStatus" :class="['status-pill', currentGateStatus.class]">
                {{ currentGateStatus.text }}
              </span>
            </div>
            <div class="toolbar-right">
              <button class="btn btn-sm" @click="$emit('refresh-gate', selectedGate)">
                <RefreshCw :size="12" :stroke-width="2" />
                <span>刷新</span>
              </button>
              <button class="btn btn-sm" @click="copyGateLog">
                <Check v-if="copied" :size="12" :stroke-width="2" />
                <Copy v-else :size="12" :stroke-width="2" />
                <span>{{ copied ? '已复制' : '复制' }}</span>
              </button>
              <button class="btn btn-sm" @click="scrollToBottom">
                <ArrowDown :size="12" :stroke-width="2" />
                <span>到底部</span>
              </button>
            </div>
          </div>

          <div class="terminal-container">
            <div ref="terminalBody" class="terminal-body mono" v-html="currentGateRenderedHtml"></div>
          </div>
        </div>

        <!-- Panel 2: 契约与争议 -->
        <div v-show="activeTab === 'contracts'" class="console-pane">
          <div class="console-toolbar">
            <div class="toolbar-left">
              <span class="toolbar-label">契约总数: <strong>{{ contractsData?.contracts?.length || 0 }}</strong></span>
              <span class="toolbar-label">临时解冻: <strong class="text-doing">{{ Object.keys(contractsData?.unlocks || {}).length }}</strong></span>
              <span class="toolbar-label">争议熔断: <strong class="text-blocked">{{ Object.keys(contractsData?.disputes || {}).length }}</strong></span>
            </div>
            <button class="btn btn-sm" @click="$emit('refresh-contracts')">
              <RefreshCw :size="12" :stroke-width="2" />
              <span>刷新</span>
            </button>
          </div>

          <div v-if="Object.keys(contractsData?.disputes || {}).length > 0" class="dispute-banner">
            <AlertTriangle :size="14" :stroke-width="2" />
            <span>契约争议熔断中！涉及契约: {{ Object.keys(contractsData.disputes).join(', ') }}</span>
          </div>

          <div class="contracts-list">
            <div
              v-for="c in (contractsData?.contracts || [])"
              :key="c.name"
              class="contract-card"
            >
              <div class="contract-card-header">
                <span class="contract-name mono">{{ c.name }}</span>
                <span
                  :class="[
                    'status-pill',
                    contractsData?.disputes?.[c.name] ? 'blocked' : (c.is_unlocked ? 'doing' : 'done')
                  ]"
                >
                  {{ contractsData?.disputes?.[c.name] ? 'DISPUTED' : (c.is_unlocked ? 'UNLOCKED' : 'LOCKED') }}
                </span>
              </div>
              <div class="contract-meta mono">
                <span>Owner: <b>{{ c.owner || '-' }}</b></span>
                <span>版本: <code>v{{ c.version || '1.0.0' }}</code></span>
                <span>SHA: <code>{{ c.sha ? c.sha.slice(0, 8) : '-' }}</code></span>
                <span>消费方: {{ (c.consumers || []).join(', ') || '无' }}</span>
              </div>
              <div v-if="c.unlock" class="contract-unlock-box">
                <b>开窗原因:</b> {{ c.unlock.reason }} (申报角色: {{ c.unlock.role }})
              </div>
            </div>

            <div v-if="!contractsData?.contracts || contractsData.contracts.length === 0" class="empty-state">
              当前需求线尚未登记任何接口契约
            </div>
          </div>
        </div>

        <!-- Panel 3: 审计流水 -->
        <div v-show="activeTab === 'audit'" class="console-pane">
          <div class="console-toolbar">
            <div class="toolbar-left">
              <span class="toolbar-label">过滤事件:</span>
              <select v-model="auditFilter" class="select-input select-sm">
                <option value="all">全部事件</option>
                <option value="phase_">阶段流转 (phase_*)</option>
                <option value="task_">任务变更 (task_*)</option>
                <option value="contract_">契约操作 (contract_*)</option>
                <option value="dispute_">争议熔断 (dispute_*)</option>
              </select>
              <span class="toolbar-label" style="margin-left: 8px;">条数:</span>
              <select v-model="auditLimit" class="select-input select-sm" @change="$emit('filter-audit', auditLimit)">
                <option :value="20">20 条</option>
                <option :value="50">50 条</option>
                <option :value="100">100 条</option>
              </select>
            </div>
            <button class="btn btn-sm" @click="$emit('refresh-audit')">
              <RefreshCw :size="12" :stroke-width="2" />
              <span>刷新流水</span>
            </button>
          </div>

          <div class="audit-waterfall">
            <div v-for="(e, idx) in filteredAuditEntries" :key="idx" class="audit-item">
              <span class="audit-time mono">{{ (e.at || e.timestamp || '').replace('T', ' ').slice(11, 19) }}</span>
              <span :class="['audit-badge mono', (e.event || '').split('_')[0]]">{{ e.event }}</span>
              <span class="audit-actor">{{ e.role || e.actor || e.owner || '-' }}</span>
              <span class="audit-detail" :title="e.reason || e.title || e.name || e.desc || JSON.stringify(e)">
                {{ e.reason || e.title || e.name || e.desc || JSON.stringify(e) }}
              </span>
            </div>

            <div v-if="filteredAuditEntries.length === 0" class="empty-state">
              暂无符合条件的审计记录
            </div>
          </div>
        </div>

        <!-- Panel 4: 实时事件流 -->
        <div v-show="activeTab === 'events'" class="console-pane">
          <div class="console-toolbar">
            <span class="toolbar-label">实时通道: /api/events (SSE)</span>
            <button class="btn btn-sm" @click="$emit('clear-events')">清空记录</button>
          </div>
          <div class="event-log-container mono">
            <div v-for="(log, idx) in eventLogs" :key="idx" class="event-log-item">
              <span class="log-time text-muted">[{{ log.time }}]</span>
              <span>{{ log.text }}</span>
            </div>
            <div v-if="eventLogs.length === 0" class="empty-state">
              暂无实时事件
            </div>
          </div>
        </div>

        <!-- Panel 5: REST API 探测 -->
        <div v-show="activeTab === 'api'" class="console-pane">
          <div class="api-probes-container">
            <ul class="api-list">
              <li v-for="ep in [
                { path: '/api/overview', desc: '全局概览与阶段状态' },
                { path: '/api/tasks', desc: 'DAG 拓扑节点与边' },
                { path: '/api/task-detail?id=T1', desc: '单任务细节与改动树' },
                { path: '/api/gate-log?name=test', desc: '门禁执行日志' },
                { path: '/api/contracts', desc: '契约列表与开窗状态' },
                { path: '/api/audit?limit=20', desc: '审计流水瀑布' },
              ]" :key="ep.path" class="api-item">
                <a :href="ep.path" target="_blank" class="api-link mono">{{ ep.path }}</a>
                <span class="api-desc">{{ ep.desc }}</span>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.console-toggle-btn {
  position: absolute;
  bottom: 12px;
  left: 16px;
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  padding: 6px 12px;
  cursor: pointer;
  z-index: 50;
  display: flex;
  align-items: center;
  gap: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  transition: all 0.2s ease;
}

.console-toggle-btn:hover {
  background: var(--bg-card-hover);
  color: var(--text-primary);
  border-color: var(--border-focus);
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.status-dot.ok { background: var(--status-done); }
.status-dot.warning { background: var(--status-doing); }
.status-dot.error { background: var(--status-blocked); }

.console-drawer {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  background: #0a0e15;
  border-top: 1px solid var(--border-default);
  box-shadow: 0 -8px 24px rgba(0, 0, 0, 0.6);
  transform: translateY(100%);
  transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1), height 0.15s ease;
  z-index: 60;
  display: flex;
  flex-direction: column;
}

.console-drawer.open {
  transform: translateY(0);
}

.console-drawer.dragging {
  transition: none !important;
}

.console-resizer {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 8px;
  cursor: row-resize;
  z-index: 70;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s ease;
}

.console-resizer:hover,
.console-drawer.dragging .console-resizer {
  background: rgba(88, 166, 255, 0.15);
}

.resizer-handle {
  width: 36px;
  height: 3px;
  background: var(--border-default);
  border-radius: 2px;
  transition: background 0.15s ease, width 0.15s ease;
}

.console-resizer:hover .resizer-handle,
.console-drawer.dragging .resizer-handle {
  background: var(--accent);
  width: 48px;
}

.console-header {
  height: 38px;
  min-height: 38px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 14px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-subtle);
}

.console-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.btn-icon-text {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.console-tabs {
  display: flex;
  gap: 2px;
  height: 100%;
}

.console-tab-btn {
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  padding: 0 12px;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.15s;
}

.console-tab-btn:hover {
  color: var(--text-primary);
}

.console-tab-btn.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  background: var(--accent-subtle);
  font-weight: 600;
}

.tab-badge-sm {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-family: var(--font-mono);
  background: var(--bg-card);
  color: var(--text-muted);
}

.tab-badge-sm.pass { background: var(--status-done-bg); color: var(--status-done); }
.tab-badge-sm.fail { background: var(--status-blocked-bg); color: var(--status-blocked); }
.tab-badge-sm.waived { background: var(--status-doing-bg); color: var(--status-doing); }

.console-body {
  flex: 1;
  overflow: hidden;
  display: flex;
  position: relative;
}

.console-pane {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.console-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 14px;
  background: #0c1018;
  border-bottom: 1px solid var(--border-subtle);
}

.toolbar-left, .toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.toolbar-label {
  color: var(--text-muted);
  font-size: 11.5px;
}

.gate-waiver-text {
  font-size: 11px;
  color: var(--status-doing);
}

.terminal-container {
  flex: 1;
  background: #06090e;
  margin: 8px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.terminal-body {
  flex: 1;
  overflow-y: auto;
  padding: 10px 14px;
  font-size: 12px;
  line-height: 1.45;
  color: #d1d7e0;
  white-space: pre-wrap;
  word-break: break-all;
}

.dispute-banner {
  margin: 8px 14px 0;
  padding: 6px 12px;
  background: var(--status-blocked-bg);
  border: 1px solid var(--status-blocked);
  border-radius: var(--radius-sm);
  color: #ff7b72;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.contracts-list, .audit-waterfall, .event-log-container, .api-probes-container {
  flex: 1;
  overflow-y: auto;
  padding: 8px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.contract-card {
  background: #0d121a;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.contract-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.contract-name {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
}

.contract-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  color: var(--text-muted);
}

.contract-unlock-box {
  background: #06090e;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
  font-size: 11px;
  color: var(--status-doing);
}

.audit-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 10px;
  background: #0c1018;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  font-size: 11.5px;
}

.audit-time {
  color: var(--text-muted);
  font-size: 11px;
  min-width: 70px;
}

.audit-badge {
  display: inline-flex;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-size: 10.5px;
  font-weight: 600;
}
.audit-badge.phase { background: rgba(88, 166, 255, 0.15); color: #79c0ff; }
.audit-badge.task { background: var(--status-done-bg); color: #56d364; }
.audit-badge.contract { background: rgba(163, 113, 247, 0.15); color: #d2a8ff; }
.audit-badge.dispute { background: var(--status-blocked-bg); color: #ff7b72; }

.audit-actor {
  color: var(--text-primary);
  font-weight: 500;
  min-width: 90px;
}

.audit-detail {
  color: var(--text-secondary);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-log-item {
  padding: 4px 0;
  border-bottom: 1px dashed var(--border-subtle);
  font-size: 11px;
  display: flex;
  gap: 8px;
}

.api-list {
  list-style: none;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

.api-item {
  background: #0c1018;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.api-link {
  color: var(--accent);
  text-decoration: none;
  font-size: 11.5px;
}

.api-link:hover {
  text-decoration: underline;
}

.api-desc {
  color: var(--text-muted);
  font-size: 11px;
}

.empty-state {
  color: var(--text-muted);
  padding: 24px 12px;
  text-align: center;
  font-size: 12px;
}
</style>
