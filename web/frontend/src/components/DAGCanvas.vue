<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  tasksDAG: { type: Object, default: () => ({ nodes: [], edges: [] }) },
  selectedTaskId: { type: String, default: null },
  selectedPhase: { type: String, default: null },
  activeFilter: { type: String, default: 'all' },
  scale: { type: Number, default: 1.0 },
  translateX: { type: Number, default: 40 },
  translateY: { type: Number, default: 40 },
})

const emit = defineEmits([
  'select-task',
  'update:scale',
  'update:translateX',
  'update:translateY',
  'fit-view',
])

const canvasContainer = ref(null)
const isDragging = ref(false)
const dragStartX = ref(0)
const dragStartY = ref(0)
const hasDraggedSinceMouseDown = ref(false)
const isSpacePressed = ref(false)

const hoveredTaskId = ref(null)
const upstreamSet = ref(new Set())
const downstreamSet = ref(new Set())

// 计算可见节点
const visibleNodes = computed(() => {
  const nodes = props.tasksDAG.nodes || []
  if (props.activeFilter === 'all') return nodes
  return nodes.filter((n) => {
    if (props.activeFilter === 'doing') return n.status === 'doing'
    if (props.activeFilter === 'done') return n.status === 'done'
    if (props.activeFilter === 'blocked') return n.status === 'blocked' || n.status === 'stale'
    if (props.activeFilter === 'todo') return n.status === 'todo'
    return true
  })
})

const visibleNodeIds = computed(() => new Set(visibleNodes.value.map((n) => n.id)))

// 计算贝塞尔连线数据
const computedEdges = computed(() => {
  const nodes = props.tasksDAG.nodes || []
  const edges = props.tasksDAG.edges || []
  const nodeMap = new Map(nodes.map((n) => [n.id, n]))

  return edges
    .map((e) => {
      const u = nodeMap.get(e.source)
      const v = nodeMap.get(e.target)
      if (!u || !v) return null

      // 如果任一端点在当前过滤器中被隐藏，则连线隐藏
      const isVisible = visibleNodeIds.value.has(e.source) && visibleNodeIds.value.has(e.target)

      const x1 = u.x + u.width
      const y1 = u.y + u.height / 2
      const x2 = v.x
      const y2 = v.y + v.height / 2
      const dx = Math.max(30, (x2 - x1) * 0.5)
      const pathD = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`

      // 判断高亮状态
      let highlightClass = ''
      if (hoveredTaskId.value) {
        const s = e.source
        const t = e.target
        const hId = hoveredTaskId.value
        if (upstreamSet.value.has(s) && (upstreamSet.value.has(t) || t === hId)) {
          highlightClass = 'highlight-upstream'
        } else if ((s === hId || downstreamSet.value.has(s)) && downstreamSet.value.has(t)) {
          highlightClass = 'highlight-downstream'
        } else {
          highlightClass = 'dimmed'
        }
      } else if (props.selectedPhase) {
        if (u.phase === props.selectedPhase || v.phase === props.selectedPhase) {
          highlightClass = 'highlight-phase-edge'
        } else {
          highlightClass = 'dimmed'
        }
      }

      return {
        source: e.source,
        target: e.target,
        d: pathD,
        isVisible,
        highlightClass,
      }
    })
    .filter(Boolean)
})

// 双向拓扑依赖追溯 (BFS)
const handleMouseEnter = (taskId) => {
  hoveredTaskId.value = taskId
  const edges = props.tasksDAG.edges || []

  // 1. 查找全部前置祖先
  const ancestors = new Set()
  const qUp = [taskId]
  while (qUp.length > 0) {
    const curr = qUp.shift()
    for (const e of edges) {
      if (e.target === curr && !ancestors.has(e.source)) {
        ancestors.add(e.source)
        qUp.push(e.source)
      }
    }
  }
  upstreamSet.value = ancestors

  // 2. 查找全部后置后代
  const descendants = new Set()
  const qDown = [taskId]
  while (qDown.length > 0) {
    const curr = qDown.shift()
    for (const e of edges) {
      if (e.source === curr && !descendants.has(e.target)) {
        descendants.add(e.target)
        qDown.push(e.target)
      }
    }
  }
  downstreamSet.value = descendants
}

const handleMouseLeave = () => {
  hoveredTaskId.value = null
  upstreamSet.value = new Set()
  downstreamSet.value = new Set()
}

// 节点卡片高亮类判定
const getNodeHighlightClass = (nodeId) => {
  if (hoveredTaskId.value) {
    if (nodeId === hoveredTaskId.value) return 'highlight-source'
    if (upstreamSet.value.has(nodeId)) return 'highlight-upstream'
    if (downstreamSet.value.has(nodeId)) return 'highlight-downstream'
    return 'dimmed'
  }
  if (props.selectedPhase) {
    const node = (props.tasksDAG.nodes || []).find((n) => n.id === nodeId)
    if (node && node.phase === props.selectedPhase) {
      return 'highlight-phase-match'
    }
    return 'dimmed'
  }
  return ''
}

// 键盘空格键监听 (Space + Drag Figma/Miro 手势)
const handleKeyDown = (e) => {
  if (e.code === 'Space' && !isSpacePressed.value) {
    const activeEl = document.activeElement
    const tag = activeEl ? activeEl.tagName : ''
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || activeEl?.isContentEditable) {
      return
    }
    isSpacePressed.value = true
    e.preventDefault()
  }
}

const handleKeyUp = (e) => {
  if (e.code === 'Space') {
    isSpacePressed.value = false
  }
}

const handleWindowBlur = () => {
  isSpacePressed.value = false
  isDragging.value = false
}

// 画布拖拽平移
const handleMouseDown = (e) => {
  if (e.button !== 0) return // 仅响应鼠标主键
  hasDraggedSinceMouseDown.value = false

  // 空格未按下时，点击卡片或按钮由元素自身处理；空格按下时允许在任何区域平移视口
  if (!isSpacePressed.value && (e.target.closest('.node-card') || e.target.closest('button'))) {
    return
  }

  if (isSpacePressed.value) {
    e.preventDefault()
  }

  isDragging.value = true
  dragStartX.value = e.clientX - props.translateX
  dragStartY.value = e.clientY - props.translateY
}

const handleMouseMove = (e) => {
  if (!isDragging.value) return
  hasDraggedSinceMouseDown.value = true
  emit('update:translateX', e.clientX - dragStartX.value)
  emit('update:translateY', e.clientY - dragStartY.value)
}

const handleMouseUp = () => {
  isDragging.value = false
  setTimeout(() => {
    hasDraggedSinceMouseDown.value = false
  }, 50)
}

// 双击画布空白处快速自适应 (Double Click to Fit)
const handleCanvasDblClick = (e) => {
  if (e.target.closest('.node-card') || e.target.closest('button')) return
  emit('fit-view')
}

// 节点卡片点击选择
const handleCardClick = (taskId) => {
  if (isSpacePressed.value || hasDraggedSinceMouseDown.value) return
  emit('select-task', taskId)
}

// 画布鼠标滚轮缩放
const handleWheel = (e) => {
  e.preventDefault()
  const factor = e.deltaY < 0 ? 1.1 : 0.9
  const oldScale = props.scale
  let newScale = oldScale * factor
  newScale = Math.max(0.25, Math.min(newScale, 2.5))
  if (newScale === oldScale) return

  const rect = canvasContainer.value.getBoundingClientRect()
  const mouseX = e.clientX - rect.left
  const mouseY = e.clientY - rect.top

  const newTranslateX = mouseX - (mouseX - props.translateX) * (newScale / oldScale)
  const newTranslateY = mouseY - (mouseY - props.translateY) * (newScale / oldScale)

  emit('update:scale', newScale)
  emit('update:translateX', newTranslateX)
  emit('update:translateY', newTranslateY)
}

onMounted(() => {
  window.addEventListener('mousemove', handleMouseMove)
  window.addEventListener('mouseup', handleMouseUp)
  window.addEventListener('keydown', handleKeyDown)
  window.addEventListener('keyup', handleKeyUp)
  window.addEventListener('blur', handleWindowBlur)
})

onUnmounted(() => {
  window.removeEventListener('mousemove', handleMouseMove)
  window.removeEventListener('mouseup', handleMouseUp)
  window.removeEventListener('keydown', handleKeyDown)
  window.removeEventListener('keyup', handleKeyUp)
  window.removeEventListener('blur', handleWindowBlur)
})
</script>

<template>
  <main
    ref="canvasContainer"
    :class="[
      'canvas-wrapper',
      {
        dragging: isDragging,
        'is-space-pressed': isSpacePressed,
      }
    ]"
    @mousedown="handleMouseDown"
    @wheel="handleWheel"
    @dblclick="handleCanvasDblClick"
  >
    <svg id="dag-svg" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#3a475d" />
        </marker>
        <marker id="arrow-highlight-up" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#58a6ff" />
        </marker>
        <marker id="arrow-highlight-down" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#8b949e" />
        </marker>
      </defs>

      <g :transform="`matrix(${scale} 0 0 ${scale} ${translateX} ${translateY})`">
        <!-- 拓扑连线层 -->
        <g id="dag-edges">
          <path
            v-for="edge in computedEdges"
            :key="`${edge.source}->${edge.target}`"
            v-show="edge.isVisible"
            :class="['dag-edge', edge.highlightClass]"
            :d="edge.d"
            :marker-end="edge.highlightClass.includes('highlight-upstream')
              ? 'url(#arrow-highlight-up)'
              : (edge.highlightClass.includes('highlight-downstream') ? 'url(#arrow-highlight-down)' : 'url(#arrow)')"
          />
        </g>

        <!-- 任务节点层 -->
        <g id="dag-nodes">
          <foreignObject
            v-for="node in visibleNodes"
            :key="node.id"
            class="dag-node-wrapper"
            :class="getNodeHighlightClass(node.id)"
            :x="node.x"
            :y="node.y"
            :width="node.width"
            :height="node.height"
          >
            <div
              :class="[
                'node-card',
                `status-${node.status || 'todo'}`,
                { selected: selectedTaskId === node.id }
              ]"
              @click.stop="handleCardClick(node.id)"
              @mouseenter="handleMouseEnter(node.id)"
              @mouseleave="handleMouseLeave"
            >
              <div class="node-top">
                <span class="node-id mono">{{ node.id }}</span>
                <span class="node-role" :title="node.role || '未认领'">{{ node.role || '未认领' }}</span>
                <span :class="['status-pill', node.status || 'todo']">{{ node.status || 'todo' }}</span>
              </div>

              <div class="node-title" :title="node.title">{{ node.title }}</div>

              <div class="node-bottom">
                <span class="node-phase mono">{{ node.phase || '' }}</span>
                <div class="node-badges">
                  <span v-if="node.has_cycle" class="badge-cycle" title="存在循环依赖">⚠ 环</span>
                  <span v-if="node.artifacts_count > 0" class="badge-artifacts mono" title="代码改动文件数">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                      <polyline points="14 2 14 8 20 8"></polyline>
                    </svg>
                    {{ node.artifacts_count }}
                  </span>
                </div>
              </div>
            </div>
          </foreignObject>
        </g>
      </g>
    </svg>

    <!-- 空状态指示 -->
    <div v-if="visibleNodes.length === 0" class="canvas-empty-state">
      <svg class="empty-icon" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
      </svg>
      <h3>当前 Flow 暂无任务</h3>
      <p>该需求线尚未生成任务，或所有任务已被过滤。</p>
    </div>
  </main>
</template>

<style scoped>
.canvas-wrapper {
  position: relative;
  flex: 1;
  overflow: hidden;
  background-color: var(--bg-canvas);
  background-image: radial-gradient(rgba(255, 255, 255, 0.08) 1px, transparent 1px);
  background-size: 24px 24px;
  user-select: none;
  cursor: grab;
}

.canvas-wrapper.dragging {
  cursor: grabbing;
}

/* Space 按住平移态 (Figma/Miro 专业手势) */
.canvas-wrapper.is-space-pressed,
.canvas-wrapper.is-space-pressed .node-card {
  cursor: grab !important;
}

.canvas-wrapper.is-space-pressed.dragging,
.canvas-wrapper.is-space-pressed.dragging .node-card {
  cursor: grabbing !important;
}

#dag-svg {
  width: 100%;
  height: 100%;
  display: block;
}

/* 拓扑连线 */
.dag-edge {
  stroke: #2a3446;
  stroke-width: 2;
  fill: none;
  transition: stroke 0.2s, stroke-width 0.2s, opacity 0.2s;
}

.dag-edge.highlight-upstream {
  stroke: var(--accent) !important;
  stroke-width: 2.5 !important;
  stroke-dasharray: 6 3;
  animation: flow-dash 1s linear infinite;
}

.dag-edge.highlight-downstream {
  stroke: var(--text-muted) !important;
  stroke-width: 2 !important;
  stroke-dasharray: 4 4;
}

@keyframes flow-dash {
  to { stroke-dashoffset: -18; }
}

/* 节点容器与卡片 */
.dag-node-wrapper {
  transition: opacity 0.2s ease, filter 0.2s ease;
}

.node-card {
  width: 100%;
  height: 100%;
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  cursor: pointer;
  transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
  position: relative;
}

.node-card:hover {
  transform: translateY(-2px);
  border-color: var(--border-focus);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.6);
}

.node-card.selected {
  border-color: var(--accent) !important;
  box-shadow: 0 0 0 2px rgba(88, 166, 255, 0.3), 0 8px 20px rgba(0, 0, 0, 0.7) !important;
}

/* 状态左边线 */
.node-card.status-done { border-left: 3px solid var(--status-done); }
.node-card.status-doing { border-left: 3px solid var(--status-doing); border-color: rgba(210, 153, 34, 0.6); }
.node-card.status-blocked, .node-card.status-stale { border-left: 3px solid var(--status-blocked); border-color: rgba(248, 81, 73, 0.6); }
.node-card.status-skipped { border-left: 3px solid var(--status-skipped); opacity: 0.75; }
.node-card.status-todo { border-left: 3px solid #484f58; }

/* 依赖高亮 */
.dag-node-wrapper.highlight-source .node-card {
  border-color: var(--accent) !important;
  box-shadow: 0 0 12px rgba(88, 166, 255, 0.5) !important;
}
.dag-node-wrapper.highlight-upstream .node-card {
  border-color: #388bfd !important;
}
.dag-node-wrapper.highlight-downstream .node-card {
  border-color: var(--text-muted) !important;
}
.dag-node-wrapper.highlight-phase-match .node-card {
  border-color: var(--accent) !important;
  box-shadow: 0 0 16px rgba(88, 166, 255, 0.45) !important;
  transform: translateY(-2px);
}
.dag-edge.highlight-phase-edge {
  stroke: rgba(88, 166, 255, 0.65) !important;
  stroke-width: 2.2 !important;
}
.dag-node-wrapper.dimmed, .dag-edge.dimmed {
  opacity: 0.18 !important;
  filter: grayscale(80%);
}

.node-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  font-size: 11px;
}

.node-id {
  font-weight: 700;
  color: var(--text-primary);
}

.node-role {
  color: var(--text-muted);
  font-size: 11px;
  max-width: 90px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.node-title {
  font-size: 12px;
  line-height: 1.35;
  color: var(--text-secondary);
  margin: 3px 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.node-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-muted);
}

.node-badges {
  display: flex;
  align-items: center;
  gap: 4px;
}

.badge-cycle {
  color: var(--status-blocked);
  font-weight: bold;
}

.badge-artifacts {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--accent);
}

.canvas-empty-state {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  color: var(--text-muted);
  pointer-events: none;
}

.empty-icon {
  margin-bottom: 8px;
  color: var(--text-muted);
}
</style>
