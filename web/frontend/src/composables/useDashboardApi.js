/**
 * useDashboardApi: 与 Workbench 看板后端 REST API 通信。
 */

export function useDashboardApi() {
  const getJson = async (url) => {
    const res = await fetch(url)
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}))
      throw new Error(errBody.error || `HTTP ${res.status}`)
    }
    return res.json()
  }

  const fetchOverview = (flow) => {
    const q = flow ? `?flow=${encodeURIComponent(flow)}` : ''
    return getJson(`/api/overview${q}`)
  }

  const fetchTasks = (flow) => {
    const q = flow ? `?flow=${encodeURIComponent(flow)}` : ''
    return getJson(`/api/tasks${q}`)
  }

  const fetchTaskDetail = (id, flow) => {
    let url = `/api/task-detail?id=${encodeURIComponent(id)}`
    if (flow) url += `&flow=${encodeURIComponent(flow)}`
    return getJson(url)
  }

  const fetchGateLog = async (name, flow, format = 'text') => {
    let url = `/api/gate-log?name=${encodeURIComponent(name)}&format=${format}`
    if (flow) url += `&flow=${encodeURIComponent(flow)}`
    if (format === 'json') {
      return getJson(url)
    }
    const res = await fetch(url)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.text()
  }

  const fetchContracts = (flow) => {
    const q = flow ? `?flow=${encodeURIComponent(flow)}` : ''
    return getJson(`/api/contracts${q}`)
  }

  const fetchAuditLog = (flow, limit = 50) => {
    let url = `/api/audit?limit=${limit}`
    if (flow) url += `&flow=${encodeURIComponent(flow)}`
    return getJson(url)
  }

  return {
    fetchOverview,
    fetchTasks,
    fetchTaskDetail,
    fetchGateLog,
    fetchContracts,
    fetchAuditLog,
  }
}
