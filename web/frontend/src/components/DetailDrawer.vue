<script setup>
import { ref, computed } from 'vue'
import {
  Copy,
  Check,
  FileText,
  FolderGit2,
  CheckCircle2,
  XCircle,
  X,
  Folder,
  FileCode,
  Terminal,
  ArrowLeft,
  ArrowRight,
  Shield,
  Info,
  ChevronDown,
  ChevronRight,
} from 'lucide-vue-next'
import { renderMarkdown } from '../composables/useMarkdown.js'

const props = defineProps({
  isOpen: { type: Boolean, default: false },
  taskNode: { type: Object, default: null },
  taskDetail: { type: Object, default: null },
  activeTab: { type: String, default: 'notes' },
  allTasks: { type: Array, default: () => [] },
})

const emit = defineEmits(['close', 'switch-tab', 'focus-task'])

const expandedFiles = ref(new Set())

const toggleFileDiff = (file) => {
  const s = new Set(expandedFiles.value)
  if (s.has(file)) {
    s.delete(file)
  } else {
    s.add(file)
  }
  expandedFiles.value = s
}

const getDiffLineClass = (line) => {
  if (line.startsWith('+++') || line.startsWith('---')) return 'diff-meta'
  if (line.startsWith('+')) return 'diff-add'
  if (line.startsWith('-')) return 'diff-del'
  if (line.startsWith('@@')) return 'diff-hunk'
  return 'diff-ctx'
}

const downstreamTasks = computed(() => {
  if (!props.taskNode?.id || !props.allTasks?.length) return []
  const currentId = props.taskNode.id
  return props.allTasks.filter(
    (t) => Array.isArray(t.deps) && t.deps.includes(currentId)
  )
})

const copiedKey = ref(null)

const copyText = (text, key) => {
  if (!text) return
  const updateKey = () => {
    copiedKey.value = key
    setTimeout(() => {
      if (copiedKey.value === key) {
        copiedKey.value = null
      }
    }, 1500)
  }

  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(updateKey).catch(() => {})
  } else {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      document.execCommand('copy')
      updateKey()
    } catch (e) {}
    document.body.removeChild(ta)
  }
}

// 注册全局代码片段复制回调，供内嵌 HTML 中的 onclick 调用
window.__wb_copy_snippet = (btn) => {
  const container = btn.closest('.code-container')
  if (container) {
    const codeEl = container.querySelector('pre code')
    if (codeEl) {
      const text = codeEl.textContent
      const updateBtn = () => {
        const orig = btn.textContent
        btn.textContent = '✓ 已复制!'
        btn.classList.add('copied')
        setTimeout(() => {
          btn.textContent = orig
          btn.classList.remove('copied')
        }, 1500)
      }

      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text).then(updateBtn).catch(() => {})
      } else {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        try {
          document.execCommand('copy')
          updateBtn()
        } catch (e) {}
        document.body.removeChild(ta)
      }
    }
  }
}

const renderedNote = computed(() => {
  const detail = props.taskDetail
  if (!detail) return '<div class="empty-state">正在载入现场执行笔记...</div>'
  const md = detail.note_markdown || detail.notes || ''
  return renderMarkdown(md)
})

const renderedRawVerification = computed(() => {
  const ver = props.taskDetail?.verification
  if (ver && ver.raw) {
    return renderMarkdown(ver.raw)
  }
  return ''
})
</script>

<template>
  <div>
    <!-- 背景遮罩 -->
    <div :class="['drawer-backdrop', { open: isOpen }]" @click="$emit('close')"></div>

    <!-- 抽屉主体 -->
    <aside :class="['detail-drawer', { open: isOpen }]">
      <div v-if="taskNode" class="drawer-header">
        <div class="drawer-header-left">
          <span class="drawer-task-id mono">{{ taskNode.id }}</span>
          <span :class="['status-pill', taskNode.status || 'todo']">{{ taskNode.status || 'todo' }}</span>
        </div>
        <button class="drawer-close-btn" @click="$emit('close')" title="关闭抽屉 (快捷键 Esc)">
          <X :size="16" />
        </button>
      </div>

      <div v-if="taskNode" class="drawer-body">
        <h2 class="drawer-task-title">{{ taskNode.title }}</h2>

        <!-- 元数据网格 -->
        <div class="drawer-meta-grid">
          <div class="meta-item">
            <span class="meta-label">阶段</span>
            <span class="meta-value">{{ taskNode.phase || '-' }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">认领角色</span>
            <span class="meta-value">{{ taskNode.role || '未认领' }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">开始时间</span>
            <span class="meta-value mono">{{ taskNode.started || '-' }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">更新时间</span>
            <span class="meta-value mono">{{ taskNode.updated || '-' }}</span>
          </div>
          <div v-if="taskDetail?.owner" class="meta-item">
            <span class="meta-label">执行主体</span>
            <span class="meta-value mono">{{ taskDetail.owner === 'root' ? '主线程 (root)' : taskDetail.owner }}</span>
          </div>
        </div>

        <!-- 依赖拓扑关系 -->
        <div class="drawer-section">
          <h3 class="section-title">依赖拓扑关系</h3>
          <div class="deps-group">
            <div class="deps-label">前置依赖 (必须先完成):</div>
            <div class="deps-pills">
              <span
                v-for="depId in (taskNode.deps || [])"
                :key="depId"
                class="dep-pill mono"
                @click="$emit('focus-task', depId)"
                title="聚焦前置依赖任务"
              >
                <ArrowLeft :size="11" />
                <span>{{ depId }}</span>
              </span>
              <span v-if="!taskNode.deps || taskNode.deps.length === 0" class="text-muted">无前置依赖</span>
            </div>
          </div>

          <div class="deps-group">
            <div class="deps-label">后续阻塞任务 (依赖本任务推进):</div>
            <div class="deps-pills">
              <span
                v-for="downTask in downstreamTasks"
                :key="downTask.id"
                class="dep-pill mono downstream"
                @click="$emit('focus-task', downTask.id)"
                :title="downTask.title || `聚焦下游任务 ${downTask.id}`"
              >
                <ArrowRight :size="11" />
                <span>{{ downTask.id }}</span>
              </span>
              <span v-if="downstreamTasks.length === 0" class="text-muted">无后续依赖任务</span>
            </div>
          </div>
        </div>

        <!-- 抽屉 Tab 导航 -->
        <div class="drawer-tabs">
          <button
            :class="['drawer-tab-btn', { active: activeTab === 'notes' }]"
            @click="$emit('switch-tab', 'notes')"
          >
            <FileText :size="13" />
            <span>现场笔记</span>
          </button>
          <button
            :class="['drawer-tab-btn', { active: activeTab === 'changes' }]"
            @click="$emit('switch-tab', 'changes')"
          >
            <FolderGit2 :size="13" />
            <span>代码改动</span>
            <span class="tab-badge mono">{{ taskDetail?.artifacts?.length || (taskDetail?.write_scopes?.length ? `${taskDetail.write_scopes.length} 声明` : 0) }}</span>
            <span
              v-if="taskDetail?.diff_summary && (taskDetail.diff_summary.additions || taskDetail.diff_summary.deletions)"
              class="diff-summary-pill mono"
            >
              <span v-if="taskDetail.diff_summary.additions" class="diff-add-tag">+{{ taskDetail.diff_summary.additions }}</span>
              <span v-if="taskDetail.diff_summary.deletions" class="diff-del-tag">-{{ taskDetail.diff_summary.deletions }}</span>
            </span>
          </button>
          <button
            :class="['drawer-tab-btn', { active: activeTab === 'ver' }]"
            @click="$emit('switch-tab', 'ver')"
          >
            <CheckCircle2 :size="13" />
            <span>人工复核</span>
            <span class="tab-badge mono">{{ taskDetail?.verification?.commands?.length || 0 }}</span>
          </button>
        </div>

        <!-- Tab 1: 现场笔记 Markdown -->
        <div v-show="activeTab === 'notes'" class="tab-pane">
          <div v-if="taskDetail?.note_file" class="note-file-indicator mono">
            <FileText :size="12" />
            <span>{{ taskDetail.note_file }}</span>
          </div>
          <div class="drawer-markdown-body" v-html="renderedNote"></div>
        </div>

        <!-- Tab 2: 代码改动文件树 -->
        <div v-show="activeTab === 'changes'" class="tab-pane">
          <div class="artifacts-container">
            <template v-if="taskDetail && taskDetail.artifacts_by_project && Object.keys(taskDetail.artifacts_by_project).length > 0">
              <div
                v-for="(files, proj) in taskDetail.artifacts_by_project"
                :key="proj"
                class="project-group"
              >
                <div class="project-header">
                  <div class="project-header-left">
                    <Folder :size="13" class="project-icon" />
                    <span>{{ proj }}</span>
                  </div>
                  <span class="tab-badge mono">{{ files.length }}</span>
                </div>
                <div v-for="file in files" :key="file" class="file-card">
                  <div
                    :class="['file-item', { clickable: !!taskDetail?.diffs?.[file] }]"
                    @click="taskDetail?.diffs?.[file] && toggleFileDiff(file)"
                  >
                    <div class="file-item-left">
                      <button
                        v-if="taskDetail?.diffs?.[file]"
                        class="toggle-diff-btn"
                        :title="expandedFiles.has(file) ? '收起 Diff' : '展开查看 Diff'"
                        @click.stop="toggleFileDiff(file)"
                      >
                        <ChevronDown v-if="expandedFiles.has(file)" :size="12" />
                        <ChevronRight v-else :size="12" />
                      </button>
                      <FileCode :size="12" class="file-icon" />
                      <span class="file-path mono" :title="file">{{ file }}</span>
                      <span
                        v-if="taskDetail?.diffs?.[file]?.status"
                        :class="['status-tag mono', taskDetail.diffs[file].status]"
                      >
                        {{ taskDetail.diffs[file].status === 'added' ? 'A' : (taskDetail.diffs[file].status === 'deleted' ? 'D' : 'M') }}
                      </span>
                      <span v-if="taskDetail?.diffs?.[file]" class="file-diff-stats mono">
                        <span v-if="taskDetail.diffs[file].additions" class="diff-add-tag">+{{ taskDetail.diffs[file].additions }}</span>
                        <span v-if="taskDetail.diffs[file].deletions" class="diff-del-tag">-{{ taskDetail.diffs[file].deletions }}</span>
                      </span>
                    </div>
                    <div class="file-item-actions">
                      <button
                        v-if="taskDetail?.diffs?.[file]?.diff"
                        :class="['btn btn-sm copy-btn', { copied: copiedKey === `diff-${file}` }]"
                        @click.stop="copyText(taskDetail.diffs[file].diff, `diff-${file}`)"
                        title="复制该文件 Diff"
                      >
                        <Check v-if="copiedKey === `diff-${file}`" :size="11" />
                        <Copy v-else :size="11" />
                        <span>{{ copiedKey === `diff-${file}` ? '已复制 Diff' : '复制 Diff' }}</span>
                      </button>
                      <button
                        :class="['btn btn-sm copy-btn', { copied: copiedKey === file }]"
                        @click.stop="copyText(file, file)"
                        title="复制文件路径"
                      >
                        <Check v-if="copiedKey === file" :size="11" />
                        <Copy v-else :size="11" />
                        <span>{{ copiedKey === file ? '已复制' : '复制' }}</span>
                      </button>
                    </div>
                  </div>
                  <!-- 展开的代码 Diff 视窗 -->
                  <div
                    v-if="taskDetail?.diffs?.[file] && expandedFiles.has(file)"
                    class="diff-view-panel"
                  >
                    <div v-if="taskDetail.diffs[file].diff" class="diff-code-wrapper mono">
                      <div
                        v-for="(dline, lidx) in taskDetail.diffs[file].diff.split('\n')"
                        :key="lidx"
                        :class="['diff-line', getDiffLineClass(dline)]"
                      >
                        <span class="diff-line-content">{{ dline }}</span>
                      </div>
                    </div>
                    <div v-else class="diff-empty-hint text-muted">
                      无文本变动（空变更或二进制文件）
                    </div>
                  </div>
                </div>
              </div>
            </template>
            <template v-else-if="taskDetail?.artifacts?.length > 0">
              <div v-for="file in taskDetail.artifacts" :key="file" class="file-card">
                <div
                  :class="['file-item', { clickable: !!taskDetail?.diffs?.[file] }]"
                  @click="taskDetail?.diffs?.[file] && toggleFileDiff(file)"
                >
                  <div class="file-item-left">
                    <button
                      v-if="taskDetail?.diffs?.[file]"
                      class="toggle-diff-btn"
                      :title="expandedFiles.has(file) ? '收起 Diff' : '展开查看 Diff'"
                      @click.stop="toggleFileDiff(file)"
                    >
                      <ChevronDown v-if="expandedFiles.has(file)" :size="12" />
                      <ChevronRight v-else :size="12" />
                    </button>
                    <FileCode :size="12" class="file-icon" />
                    <span class="file-path mono" :title="file">{{ file }}</span>
                    <span
                      v-if="taskDetail?.diffs?.[file]?.status"
                      :class="['status-tag mono', taskDetail.diffs[file].status]"
                    >
                      {{ taskDetail.diffs[file].status === 'added' ? 'A' : (taskDetail.diffs[file].status === 'deleted' ? 'D' : 'M') }}
                    </span>
                    <span v-if="taskDetail?.diffs?.[file]" class="file-diff-stats mono">
                      <span v-if="taskDetail.diffs[file].additions" class="diff-add-tag">+{{ taskDetail.diffs[file].additions }}</span>
                      <span v-if="taskDetail.diffs[file].deletions" class="diff-del-tag">-{{ taskDetail.diffs[file].deletions }}</span>
                    </span>
                  </div>
                  <div class="file-item-actions">
                    <button
                      v-if="taskDetail?.diffs?.[file]?.diff"
                      :class="['btn btn-sm copy-btn', { copied: copiedKey === `diff-${file}` }]"
                      @click.stop="copyText(taskDetail.diffs[file].diff, `diff-${file}`)"
                      title="复制该文件 Diff"
                    >
                      <Check v-if="copiedKey === `diff-${file}`" :size="11" />
                      <Copy v-else :size="11" />
                      <span>{{ copiedKey === `diff-${file}` ? '已复制 Diff' : '复制 Diff' }}</span>
                    </button>
                    <button
                      :class="['btn btn-sm copy-btn', { copied: copiedKey === file }]"
                      @click.stop="copyText(file, file)"
                      title="复制文件路径"
                    >
                      <Check v-if="copiedKey === file" :size="11" />
                      <Copy v-else :size="11" />
                      <span>{{ copiedKey === file ? '已复制' : '复制' }}</span>
                    </button>
                  </div>
                </div>
                <!-- 展开的代码 Diff 视窗 -->
                <div
                  v-if="taskDetail?.diffs?.[file] && expandedFiles.has(file)"
                  class="diff-view-panel"
                >
                  <div v-if="taskDetail.diffs[file].diff" class="diff-code-wrapper mono">
                    <div
                      v-for="(dline, lidx) in taskDetail.diffs[file].diff.split('\n')"
                      :key="lidx"
                      :class="['diff-line', getDiffLineClass(dline)]"
                    >
                      <span class="diff-line-content">{{ dline }}</span>
                    </div>
                  </div>
                  <div v-else class="diff-empty-hint text-muted">
                    无文本变动（空变更或二进制文件）
                  </div>
                </div>
              </div>
            </template>
            <template v-else-if="taskDetail?.write_scopes && taskDetail.write_scopes.length > 0">
              <div class="scopes-group">
                <div class="scopes-header">
                  <div class="scopes-header-left">
                    <Shield :size="13" class="scope-icon" />
                    <span>任务声明写入范围 (Write Scopes)</span>
                  </div>
                  <span class="tab-badge mono">{{ taskDetail.write_scopes.length }}</span>
                </div>
                <div v-for="scope in taskDetail.write_scopes" :key="scope" class="file-item">
                  <div class="file-item-left">
                    <FileCode :size="12" class="file-icon" />
                    <span class="file-path mono" :title="scope">{{ scope }}</span>
                  </div>
                  <button
                    :class="['btn btn-sm copy-btn', { copied: copiedKey === scope }]"
                    @click="copyText(scope, scope)"
                    title="复制范围路径"
                  >
                    <Check v-if="copiedKey === scope" :size="11" />
                    <Copy v-else :size="11" />
                    <span>{{ copiedKey === scope ? '已复制' : '复制' }}</span>
                  </button>
                </div>
              </div>
            </template>
            <div v-else class="empty-state">
              <div v-if="taskDetail?.owner === 'root'" class="empty-hint">
                <Info :size="14" class="hint-icon" />
                <div>
                  <div class="hint-title">本任务由主线程直接统筹执行</div>
                  <div class="hint-desc">未通过独立角色 subagent 产生自动归并的产物流水。具体验证与改动详情可参阅「现场笔记」与「人工复核」Tab。</div>
                </div>
              </div>
              <div v-else>暂无关联改动文件</div>
            </div>
          </div>
        </div>

        <!-- Tab 3: 复核依据 -->
        <div v-show="activeTab === 'ver'" class="tab-pane">
          <div class="ver-container">
            <div
              v-for="(cmd, idx) in (taskDetail?.verification?.commands || [])"
              :key="idx"
              class="ver-card"
            >
              <div class="ver-header">
                <span class="meta-label">复核验证命令</span>
                <span
                  v-if="cmd.verdict"
                  :class="['status-pill', /pass|通过|成功/i.test(cmd.verdict) ? 'pass' : 'fail']"
                >
                  <CheckCircle2 v-if="/pass|通过|成功/i.test(cmd.verdict)" :size="11" />
                  <XCircle v-else :size="11" />
                  <span>{{ cmd.verdict }}</span>
                </span>
              </div>
              <div class="ver-cmd-line">
                <div class="cmd-text-wrap">
                  <Terminal :size="12" class="terminal-icon" />
                  <code class="mono">{{ cmd.command }}</code>
                </div>
                <button
                  :class="['btn btn-sm copy-btn', { copied: copiedKey === `cmd-${idx}` }]"
                  @click="copyText(cmd.command, `cmd-${idx}`)"
                  title="复制代码命令"
                >
                  <Check v-if="copiedKey === `cmd-${idx}`" :size="11" />
                  <Copy v-else :size="11" />
                  <span>{{ copiedKey === `cmd-${idx}` ? '已复制' : '复制' }}</span>
                </button>
              </div>
            </div>

            <div v-if="renderedRawVerification" class="drawer-markdown-body" v-html="renderedRawVerification"></div>
            <div v-if="(!taskDetail?.verification?.commands || taskDetail.verification.commands.length === 0) && !renderedRawVerification" class="empty-state">
              暂无复核证据记录
            </div>
          </div>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(2px);
  z-index: 80;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.25s ease;
}

.drawer-backdrop.open {
  opacity: 1;
  pointer-events: auto;
}

.detail-drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 480px;
  max-width: 92vw;
  background: var(--bg-surface);
  border-left: 1px solid var(--border-default);
  box-shadow: -8px 0 28px rgba(0, 0, 0, 0.7);
  transform: translateX(100%);
  transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  z-index: 90;
  display: flex;
  flex-direction: column;
}

.detail-drawer.open {
  transform: translateX(0);
}

.drawer-header {
  height: 52px;
  min-height: 52px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #0b0f16;
}

.drawer-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.drawer-task-id {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
}

.drawer-close-btn {
  background: transparent;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 6px;
  border-radius: var(--radius-sm);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}

.drawer-close-btn:hover {
  color: var(--text-primary);
  background: var(--bg-card);
}

.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.drawer-task-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.4;
  flex-shrink: 0;
}

.drawer-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  background: var(--bg-card);
  padding: 10px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-subtle);
  flex-shrink: 0;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.meta-label {
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
}

.meta-value {
  font-size: 12px;
  color: var(--text-primary);
}

.drawer-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
}

.section-title {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.deps-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: var(--bg-card);
  padding: 8px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-subtle);
}

.deps-label {
  font-size: 11px;
  color: var(--text-muted);
}

.deps-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.dep-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  background: rgba(56, 139, 253, 0.1);
  color: var(--accent);
  border: 1px solid rgba(88, 166, 255, 0.3);
  cursor: pointer;
  transition: all 0.15s;
}

.dep-pill:hover {
  background: rgba(56, 139, 253, 0.2);
}

.dep-pill.downstream {
  background: rgba(210, 153, 34, 0.1);
  color: #e3b341;
  border-color: rgba(210, 153, 34, 0.3);
}

.dep-pill.downstream:hover {
  background: rgba(210, 153, 34, 0.2);
}

.drawer-tabs {
  display: flex;
  flex-shrink: 0;
  min-height: 36px;
  border-bottom: 1px solid var(--border-default);
  background: #0b0f16;
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
  overflow: hidden;
  margin-top: 4px;
  position: sticky;
  top: -16px;
  z-index: 10;
}

.drawer-tab-btn {
  flex: 1;
  padding: 8px 6px;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  transition: all 0.15s;
}

.drawer-tab-btn:hover {
  color: var(--text-primary);
}

.drawer-tab-btn.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  background: var(--accent-subtle);
  font-weight: 600;
}

.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  background: var(--bg-card);
  color: var(--text-muted);
}

.drawer-tab-btn.active .tab-badge {
  background: rgba(88, 166, 255, 0.25);
  color: var(--accent);
}

.tab-pane {
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex-shrink: 0;
}

.note-file-indicator {
  font-size: 11px;
  color: var(--text-muted);
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.drawer-markdown-body {
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  font-size: 12.5px;
  line-height: 1.6;
  word-break: break-word;
}

/* 抽屉内 Markdown 元素渲染规范 */
.drawer-markdown-body :deep(.code-container) {
  margin: 10px 0;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: #06090e;
}

.drawer-markdown-body :deep(.code-header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 10px;
  background: #111722;
  border-bottom: 1px solid var(--border-subtle);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-muted);
}

.drawer-markdown-body :deep(.code-copy-btn) {
  background: transparent;
  border: 1px solid var(--border-default);
  color: var(--text-muted);
  border-radius: 3px;
  padding: 2px 6px;
  font-size: 10px;
  cursor: pointer;
  transition: all 0.15s ease;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.drawer-markdown-body :deep(.code-copy-btn:hover) {
  color: var(--text-primary);
  background: #1c2638;
  border-color: var(--accent);
}

.drawer-markdown-body :deep(.code-copy-btn.copied) {
  color: var(--status-done);
  border-color: var(--status-done);
  background: rgba(63, 185, 80, 0.1);
}

.drawer-markdown-body :deep(pre) {
  margin: 0;
  padding: 10px;
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.45;
  color: #e6edf3;
  background: #06090e;
}

.drawer-markdown-body :deep(.table-wrapper) {
  overflow-x: auto;
  margin: 10px 0;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
}

.drawer-markdown-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 11.5px;
}

.drawer-markdown-body :deep(th),
.drawer-markdown-body :deep(td) {
  border: 1px solid var(--border-subtle);
  padding: 6px 10px;
  text-align: left;
}

.drawer-markdown-body :deep(th) {
  background: #111722;
  color: var(--text-primary);
  font-weight: 600;
}

.drawer-markdown-body :deep(tr:nth-child(even) td) {
  background: rgba(255, 255, 255, 0.02);
}

.drawer-markdown-body :deep(blockquote) {
  border-left: 3px solid var(--accent);
  padding: 4px 10px;
  background: rgba(88, 166, 255, 0.06);
  color: var(--text-secondary);
  margin: 8px 0;
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}

.drawer-markdown-body :deep(ul),
.drawer-markdown-body :deep(ol) {
  padding-left: 20px;
  margin: 6px 0 8px 0;
}

.drawer-markdown-body :deep(li) {
  margin: 3px 0;
}

.drawer-markdown-body :deep(p) {
  margin-bottom: 8px;
}

.drawer-markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.drawer-markdown-body :deep(h1),
.drawer-markdown-body :deep(h2),
.drawer-markdown-body :deep(h3),
.drawer-markdown-body :deep(h4) {
  color: var(--text-primary);
  font-weight: 600;
  margin: 12px 0 6px 0;
}

.drawer-markdown-body :deep(h1) { font-size: 15px; }
.drawer-markdown-body :deep(h2) { font-size: 14px; }
.drawer-markdown-body :deep(h3) { font-size: 13px; }
.drawer-markdown-body :deep(h4) { font-size: 12px; }

.drawer-markdown-body :deep(a) {
  color: var(--accent);
  text-decoration: none;
}

.drawer-markdown-body :deep(a:hover) {
  text-decoration: underline;
}

.drawer-markdown-body :deep(code:not(pre code)) {
  background: rgba(110, 118, 129, 0.2);
  color: #79c0ff;
  padding: 2px 5px;
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  border: 1px solid rgba(88, 166, 255, 0.15);
}

.drawer-markdown-body :deep(input[type="checkbox"]) {
  margin-right: 6px;
  vertical-align: middle;
  accent-color: var(--accent);
}

.drawer-markdown-body :deep(.empty-tab-state) {
  color: var(--text-muted);
  text-align: center;
  padding: 20px 0;
  font-size: 12px;
}

/* 列表与容器 */
.artifacts-container, .ver-container {
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.project-group {
  background: #0d121a;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.project-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  background: var(--bg-card);
  font-weight: 600;
  font-size: 11.5px;
  color: var(--text-primary);
  border-bottom: 1px solid var(--border-subtle);
}

.project-header-left {
  display: flex;
  align-items: center;
  gap: 6px;
}

.project-icon {
  color: var(--accent);
  flex-shrink: 0;
}

.file-card {
  border-bottom: 1px solid var(--border-subtle);
}

.file-card:last-child {
  border-bottom: none;
}

.file-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 5px 10px;
  gap: 8px;
  transition: background-color 0.15s ease;
}

.file-item.clickable {
  cursor: pointer;
}

.file-item.clickable:hover {
  background: rgba(255, 255, 255, 0.025);
}

.toggle-diff-btn {
  background: transparent;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 2px;
  border-radius: var(--radius-sm);
  transition: color 0.15s ease, background-color 0.15s ease;
}

.toggle-diff-btn:hover {
  color: var(--accent);
  background: rgba(88, 166, 255, 0.12);
}

.file-item-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.status-tag {
  font-size: 9.5px;
  font-weight: 700;
  padding: 1px 4px;
  border-radius: 3px;
  line-height: 1.1;
  display: inline-block;
  flex-shrink: 0;
}

.status-tag.added {
  background: rgba(63, 185, 80, 0.18);
  color: #7ee787;
  border: 1px solid rgba(63, 185, 80, 0.35);
}

.status-tag.modified {
  background: rgba(210, 153, 34, 0.18);
  color: #e3b341;
  border: 1px solid rgba(210, 153, 34, 0.35);
}

.status-tag.deleted {
  background: rgba(248, 81, 73, 0.18);
  color: #f85149;
  border: 1px solid rgba(248, 81, 73, 0.35);
}

.file-diff-stats, .diff-summary-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  font-weight: 600;
  flex-shrink: 0;
}

.diff-add-tag {
  color: #7ee787;
}

.diff-del-tag {
  color: #f85149;
}

.diff-view-panel {
  background: #05080c;
  border-top: 1px solid var(--border-subtle);
  overflow-x: auto;
}

.diff-code-wrapper {
  font-size: 11px;
  line-height: 1.45;
  padding: 6px 0;
}

.diff-line {
  padding: 1px 12px;
  white-space: pre;
  font-family: var(--font-mono);
}

.diff-line.diff-add {
  background: rgba(63, 185, 80, 0.14);
  color: #7ee787;
}

.diff-line.diff-del {
  background: rgba(248, 81, 73, 0.14);
  color: #ffa198;
}

.diff-line.diff-hunk {
  background: rgba(56, 139, 253, 0.12);
  color: #79c0ff;
  font-weight: 500;
}

.diff-line.diff-meta {
  color: var(--text-muted);
  font-weight: 600;
}

.diff-line.diff-ctx {
  color: var(--text-secondary);
}

.diff-empty-hint {
  padding: 12px;
  font-size: 11px;
  text-align: center;
  font-style: italic;
}

.file-item-left {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  flex: 1;
}

.file-icon {
  color: var(--text-muted);
  flex-shrink: 0;
}

.file-path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: #79c0ff;
}

/* 复制按钮增强交互 */
.copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.copy-btn.copied {
  color: var(--status-done);
  border-color: rgba(63, 185, 80, 0.4);
  background: rgba(63, 185, 80, 0.1);
}

.ver-card {
  background: #0d121a;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ver-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.ver-cmd-line {
  background: var(--bg-canvas);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 5px 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.cmd-text-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  flex: 1;
}

.terminal-icon {
  color: var(--accent);
  flex-shrink: 0;
}

.ver-cmd-line code {
  color: var(--accent);
  font-size: 11px;
  word-break: break-all;
}

.scopes-group {
  background: #0d121a;
  border: 1px solid rgba(88, 166, 255, 0.2);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.scopes-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  background: rgba(56, 139, 253, 0.1);
  font-weight: 600;
  font-size: 11.5px;
  color: var(--accent);
  border-bottom: 1px solid rgba(88, 166, 255, 0.2);
}

.scopes-header-left {
  display: flex;
  align-items: center;
  gap: 6px;
}

.scope-icon {
  color: var(--accent);
  flex-shrink: 0;
}

.empty-hint {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px;
  background: rgba(56, 139, 253, 0.05);
  border: 1px solid rgba(56, 139, 253, 0.15);
  border-radius: var(--radius-sm);
  text-align: left;
  line-height: 1.5;
}

.empty-hint .hint-icon {
  color: var(--accent);
  flex-shrink: 0;
  margin-top: 2px;
}

.empty-hint .hint-title {
  font-weight: 600;
  font-size: 12px;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.empty-hint .hint-desc {
  font-size: 11.5px;
  color: var(--text-muted);
  margin: 0;
}

.empty-state {
  color: var(--text-muted);
  padding: 24px 12px;
  text-align: center;
  font-size: 12px;
}
</style>
