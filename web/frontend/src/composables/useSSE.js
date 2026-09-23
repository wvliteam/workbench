import { ref, onMounted, onUnmounted } from 'vue'

/**
 * useSSE: 管理与后端 /api/events 的 Server-Sent Events 长连接。
 */
export function useSSE(onStateChange) {
  const status = ref('connecting') // 'connecting' | 'connected' | 'disconnected'
  const isPulsing = ref(false)
  const eventLogs = ref([])
  let eventSource = null

  const logEvent = (text) => {
    const time = new Date().toLocaleTimeString()
    eventLogs.value.unshift({ time, text })
    if (eventLogs.value.length > 100) {
      eventLogs.value.pop()
    }
  }

  const connect = () => {
    if (eventSource) {
      eventSource.close()
    }

    try {
      eventSource = new EventSource('/api/events')

      eventSource.onopen = () => {
        status.value = 'connected'
        logEvent('SSE 实时通信通道已建立')
      }

      eventSource.addEventListener('state_change', (e) => {
        try {
          const data = JSON.parse(e.data)
          const changedFiles = (data.changed_files || []).join(', ') || '工作区状态更新'
          logEvent(`收到变更事件: ${changedFiles} (Flow: ${data.flow})`)

          // 触发单次 0.6s 微弱视觉反馈
          isPulsing.value = true
          setTimeout(() => {
            isPulsing.value = false
          }, 600)

          if (onStateChange) {
            onStateChange(data)
          }
        } catch (err) {
          logEvent(`解析事件载荷失败: ${e.data}`)
        }
      })

      eventSource.onerror = () => {
        status.value = 'disconnected'
        logEvent('SSE 连接异常断开，正在尝试重连...')
      }
    } catch (err) {
      status.value = 'disconnected'
      logEvent(`初始化 SSE 异常: ${err.message}`)
    }
  }

  const disconnect = () => {
    if (eventSource) {
      eventSource.close()
      eventSource = null
    }
  }

  const clearLogs = () => {
    eventLogs.value = []
  }

  onMounted(() => {
    connect()
  })

  onUnmounted(() => {
    disconnect()
  })

  return {
    status,
    isPulsing,
    eventLogs,
    clearLogs,
    reconnect: connect,
  }
}
