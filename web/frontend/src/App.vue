<script setup>
import { ref, computed, onMounted } from 'vue'
import { onKeyStroke } from '@vueuse/core'
import { useDashboardApi } from './composables/useDashboardApi.js'
import { useSSE } from './composables/useSSE.js'

import AppHeader from './components/AppHeader.vue'
import PipelineBar from './components/PipelineBar.vue'
import FloatingHUD from './components/FloatingHUD.vue'
import DAGCanvas from './components/DAGCanvas.vue'
import DetailDrawer from './components/DetailDrawer.vue'
import BottomConsole from './components/BottomConsole.vue'

const api = useDashboardApi()

// 核心应用状态
const currentFlow = ref('main')
const overview = ref({})
const tasksDAG = ref({ nodes: [], edges: [] })
const selectedTaskId = ref(null)
const selectedTaskDetail = ref(null)
const selectedPhase = ref(null)
const activeFilter = ref('all')
const isRefreshing = ref(false)

// 画布视图状态
const scale = ref(1.0)
const translateX = ref(40)
const translateY = ref(40)

// 抽屉与控制台面板状态
const isDrawerOpen = ref(false)
const activeDrawerTab = ref('notes')
const isConsoleOpen = ref(false)
const activeConsoleTab = ref('gate')
const consoleHeight = ref(280)

// 细节数据缓存
const gateLogs = ref({})
const contractsData = ref({ contracts: [], unlocks: {}, disputes: {} })
const auditData = ref([])

// 计算属性
const flowsList = computed(() => {
  const list = overview.value?.flows || ['main']
  if (!list.includes(currentFlow.value)) {
    return [currentFlow.value, ...list]
  }
  return list
})

const selectedTaskNode = computed(() => {
  if (!selectedTaskId.value) return null
  return (tasksDAG.value.nodes || []).find((n) => n.id === selectedTaskId.value) || null
})

const totalTasks = computed(() => overview.value?.total_tasks || 0)
const doneTasks = computed(() => overview.value?.done_tasks || 0)
const doingTasks = computed(() => {
  return (tasksDAG.value.nodes || []).filter((n) => n.status === 'doing').length
})
const blockedTasks = computed(() => {
  return (tasksDAG.value.nodes || []).filter((n) => n.status === 'blocked' || n.status === 'stale').length
})
const progressPercent = computed(() => {
  if (totalTasks.value <= 0) return 0
  return Math.round((doneTasks.value / totalTasks.value) * 100)
})

// 门禁与日志加载
const loadGateLog = async (gateName) => {
  try {
    const text = await api.fetchGateLog(gateName, currentFlow.value, 'text')
    gateLogs.value[gateName] = text
  } catch (err) {
    gateLogs.value[gateName] = `加载门禁日志失败: ${err.message}`
  }
}

const loadAllGateLogs = async () => {
  const gates = overview.value?.configured_gates || ['test', 'lint', 'build']
  await Promise.all(gates.map((g) => loadGateLog(g)))
}

// 数据获取
const loadData = async (flowName) => {
  isRefreshing.value = true
  const targetFlow = flowName || currentFlow.value || 'main'
  try {
    const [ov, tasks] = await Promise.all([
      api.fetchOverview(targetFlow),
      api.fetchTasks(targetFlow),
    ])
    overview.value = ov
    tasksDAG.value = tasks
    currentFlow.value = ov.current_flow || targetFlow

    // 预拉取所有门禁日志以供全局健康度聚合计算
    loadAllGateLogs()

    // 如果控制台打开，联动拉取对应数据
    if (isConsoleOpen.value) {
      if (activeConsoleTab.value === 'contracts') {
        loadContracts()
      } else if (activeConsoleTab.value === 'audit') {
        loadAudit()
      }
    }
  } catch (err) {
    console.error('加载看板数据失败:', err)
  } finally {
    isRefreshing.value = false
  }
}

// 切换 Flow
const handleFlowChange = (newFlow) => {
  currentFlow.value = newFlow
  selectedTaskId.value = null
  selectedPhase.value = null
  isDrawerOpen.value = false
  loadData(newFlow)
  setTimeout(() => fitToView(), 50)
}

// 切换阶段选择并聚焦任务
const handleSelectPhase = (phase) => {
  if (selectedPhase.value === phase) {
    selectedPhase.value = null
    fitToView()
    return
  }
  selectedPhase.value = phase
  focusPhaseNodes(phase)
}

// 聚焦指定阶段的所有任务节点
const focusPhaseNodes = (phase) => {
  const nodes = (tasksDAG.value.nodes || []).filter((n) => n.phase === phase)
  if (nodes.length === 0) return

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const n of nodes) {
    minX = Math.min(minX, n.x)
    minY = Math.min(minY, n.y)
    maxX = Math.max(maxX, n.x + n.width)
    maxY = Math.max(maxY, n.y + n.height)
  }

  const cw = window.innerWidth
  const ch = window.innerHeight - 90 - (isConsoleOpen.value ? consoleHeight.value : 0)
  const pad = 80
  const graphW = maxX - minX + pad * 2
  const graphH = maxY - minY + pad * 2
  const scaleX = cw / Math.max(graphW, 100)
  const scaleY = ch / Math.max(graphH, 100)
  let s = Math.min(scaleX, scaleY, 1.25)
  s = Math.max(0.3, Math.min(s, 2.0))

  scale.value = s
  translateX.value = (cw - (maxX - minX) * s) / 2 - minX * s
  translateY.value = (ch - (maxY - minY) * s) / 2 - minY * s
}

// 选择并打开任务详情抽屉
const handleSelectTask = async (taskId) => {
  selectedTaskId.value = taskId
  isDrawerOpen.value = true
  try {
    const detail = await api.fetchTaskDetail(taskId, currentFlow.value)
    selectedTaskDetail.value = detail
  } catch (err) {
    console.error('获取任务细节失败:', err)
  }
}

// 聚焦任务并居中
const handleFocusTask = (taskId) => {
  const node = (tasksDAG.value.nodes || []).find((n) => n.id === taskId)
  if (!node) return

  const windowW = window.innerWidth
  const visibleH = window.innerHeight - (isConsoleOpen.value ? consoleHeight.value : 0)
  scale.value = Math.max(scale.value, 0.95)
  translateX.value = windowW / 2 - (node.x + node.width / 2) * scale.value
  translateY.value = visibleH / 2 - (node.y + node.height / 2) * scale.value

  handleSelectTask(taskId)
}

// 画布视图自适应居中（感知控制台展开与高度遮挡补偿）
const fitToView = () => {
  const nodes = tasksDAG.value.nodes || []
  if (nodes.length === 0) {
    scale.value = 1.0
    translateX.value = 40
    translateY.value = 40
    return
  }

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const n of nodes) {
    minX = Math.min(minX, n.x)
    minY = Math.min(minY, n.y)
    maxX = Math.max(maxX, n.x + n.width)
    maxY = Math.max(maxY, n.y + n.height)
  }

  const cw = window.innerWidth
  const ch = window.innerHeight - 90 - (isConsoleOpen.value ? consoleHeight.value : 0) // 减去 Header 与 Pipeline 及打开的控制台
  const pad = 60
  const graphW = maxX - minX + pad * 2
  const graphH = maxY - minY + pad * 2
  const scaleX = cw / Math.max(graphW, 100)
  const scaleY = ch / Math.max(graphH, 100)
  let s = Math.min(scaleX, scaleY, 1.15)
  s = Math.max(0.3, Math.min(s, 2.0))

  scale.value = s
  translateX.value = (cw - (maxX - minX) * s) / 2 - minX * s
  translateY.value = (ch - (maxY - minY) * s) / 2 - minY * s
}

const resetView = () => {
  scale.value = 1.0
  translateX.value = 40
  translateY.value = 40
}

const zoomBy = (factor) => {
  const oldScale = scale.value
  let newScale = Math.max(0.25, Math.min(oldScale * factor, 2.5))
  if (newScale === oldScale) return

  const cx = window.innerWidth / 2
  const cy = window.innerHeight / 2
  translateX.value = cx - (cx - translateX.value) * (newScale / oldScale)
  translateY.value = cy - (cy - translateY.value) * (newScale / oldScale)
  scale.value = newScale
}

const loadContracts = async () => {
  try {
    const data = await api.fetchContracts(currentFlow.value)
    contractsData.value = data
  } catch (err) {
    console.error('加载契约失败:', err)
  }
}

const loadAudit = async (limit = 50) => {
  try {
    const entries = await api.fetchAuditLog(currentFlow.value, limit)
    auditData.value = entries
  } catch (err) {
    console.error('加载审计流水失败:', err)
  }
}

// 底部控制台 Tab 切换与交互
const handleSwitchConsoleTab = (tab) => {
  activeConsoleTab.value = tab
  if (tab === 'gate') loadAllGateLogs()
  else if (tab === 'contracts') loadContracts()
  else if (tab === 'audit') loadAudit()
}

const toggleConsole = () => {
  isConsoleOpen.value = !isConsoleOpen.value
  if (isConsoleOpen.value && activeConsoleTab.value === 'gate') {
    loadAllGateLogs()
  }
}

const handleRefreshGate = (gateName) => {
  if (gateName) {
    loadGateLog(gateName)
  } else {
    loadAllGateLogs()
  }
}

// SSE 实时推送接入
const { status: sseStatus, isPulsing, eventLogs, clearLogs } = useSSE((data) => {
  // 收到 state_change 自动无刷新平滑更新当前 Flow
  loadData(currentFlow.value)
})

// 全局键盘快捷键 (通过 @vueuse/core 的 onKeyStroke 声明式绑定)
const isNotInInput = () => !document.activeElement || !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)

onKeyStroke('Escape', () => {
  isDrawerOpen.value = false
  isConsoleOpen.value = false
  selectedTaskId.value = null
  selectedPhase.value = null
})

onKeyStroke(['f', 'F'], () => {
  if (isNotInInput()) {
    fitToView()
  }
})

onKeyStroke('0', () => {
  if (isNotInInput()) {
    resetView()
  }
})

onKeyStroke(['+', '='], () => {
  if (isNotInInput()) {
    zoomBy(1.15)
  }
})

onKeyStroke(['-', '_'], () => {
  if (isNotInInput()) {
    zoomBy(0.85)
  }
})

onMounted(() => {
  const params = new URLSearchParams(window.location.search)
  const initialFlow = params.get('flow') || currentFlow.value
  loadData(initialFlow)
})
</script>

<template>
  <div class="dashboard-root">
    <!-- 顶部状态导航栏 -->
    <AppHeader
      :project="overview.project || 'workbench'"
      :version="overview.version || '0.1.0'"
      :flows="flowsList"
      :current-flow="currentFlow"
      :role-locked="Boolean(overview.role_locked)"
      :sse-status="sseStatus"
      :is-pulsing="isPulsing"
      :is-refreshing="isRefreshing"
      @change-flow="handleFlowChange"
      @refresh="loadData()"
    />

    <!-- 六阶段流水线进度阶梯 -->
    <PipelineBar
      :phases="overview.phases || []"
      :selected-phase="selectedPhase"
      @select-phase="handleSelectPhase"
    />

    <!-- 画布与工作区 -->
    <div class="canvas-viewport">
      <!-- 悬浮控制面板 (HUD) -->
      <FloatingHUD
        :total="totalTasks"
        :done="doneTasks"
        :doing="doingTasks"
        :blocked="blockedTasks"
        :progress="progressPercent"
        :active-filter="activeFilter"
        :zoom-scale="scale"
        :unconfigured-gates="overview.unconfigured_gates || []"
        @change-filter="activeFilter = $event"
        @zoom-in="zoomBy(1.15)"
        @zoom-out="zoomBy(0.85)"
        @fit-view="fitToView"
        @reset-view="resetView"
      />

      <!-- SVG 拓扑交互画布 -->
      <DAGCanvas
        :tasks-d-a-g="tasksDAG"
        :selected-task-id="selectedTaskId"
        :selected-phase="selectedPhase"
        :active-filter="activeFilter"
        :scale="scale"
        :translate-x="translateX"
        :translate-y="translateY"
        @select-task="handleSelectTask"
        @fit-view="fitToView"
        @update:scale="scale = $event"
        @update:translate-x="translateX = $event"
        @update:translate-y="translateY = $event"
      />

      <!-- 任务执行细节侧边抽屉 -->
      <DetailDrawer
        :is-open="isDrawerOpen"
        :task-node="selectedTaskNode"
        :task-detail="selectedTaskDetail"
        :all-tasks="tasksDAG.nodes || []"
        :active-tab="activeDrawerTab"
        @close="isDrawerOpen = false; selectedTaskId = null"
        @switch-tab="activeDrawerTab = $event"
        @focus-task="handleFocusTask"
      />

      <!-- 底部多功能控制台 -->
      <BottomConsole
        :is-open="isConsoleOpen"
        :console-height="consoleHeight"
        :active-tab="activeConsoleTab"
        :gate-logs="gateLogs"
        :configured-gates="overview.configured_gates || ['test', 'lint', 'build']"
        :gate-waivers="overview.gate_waivers || {}"
        :contracts-data="contractsData"
        :audit-data="auditData"
        :event-logs="eventLogs"
        @update:console-height="consoleHeight = $event"
        @toggle="toggleConsole"
        @close="isConsoleOpen = false"
        @switch-tab="handleSwitchConsoleTab"
        @select-gate="loadGateLog($event)"
        @refresh-gate="handleRefreshGate($event)"
        @refresh-contracts="loadContracts()"
        @refresh-audit="loadAudit()"
        @filter-audit="loadAudit($event)"
        @clear-events="clearLogs"
      />
    </div>
  </div>
</template>

<style scoped>
.dashboard-root {
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
  background: var(--bg-canvas);
}

.canvas-viewport {
  position: relative;
  flex: 1;
  display: flex;
  overflow: hidden;
}
</style>
