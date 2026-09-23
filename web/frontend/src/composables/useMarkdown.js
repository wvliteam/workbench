/**
 * useMarkdown.js: 基于成熟 marked 库的高性能组件化 Markdown 渲染器。
 * 支持完整 GFM 规范（表格、删除线、任务列表、代码块）、自定义渲染器安全防范与无缝代码复制。
 */

import { Marked, Renderer } from 'marked'

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

const markedInstance = new Marked({
  gfm: true,
  breaks: false,
})

markedInstance.use({
  renderer: {
    // 1. 代码块：保留契约结构与全局 onclick 复制钩子
    code(token, maybeLang) {
      const codeText = typeof token === 'object' && token !== null ? (token.text || '') : String(token || '')
      const rawLang = (typeof token === 'object' && token !== null ? token.lang : maybeLang) || ''
      const lang = escapeHtml(rawLang.trim() || 'code')
      const escapedCode = escapeHtml(codeText)
      return `<div class="code-container"><div class="code-header"><span>${lang}</span><button class="code-copy-btn" onclick="window.__wb_copy_snippet(this)">复制</button></div><pre><code>${escapedCode}</code></pre></div>`
    },

    // 2. 表格：用 table-wrapper 包裹支持横向滚动
    table(token, body) {
      if (typeof token === 'string' && typeof body === 'string') {
        return `<div class="table-wrapper"><table><thead>${token}</thead><tbody>${body}</tbody></table></div>`
      }
      const html = Renderer.prototype.table.call(this, token)
      return `<div class="table-wrapper">${html}</div>`
    },

    // 3. 链接：安全防范 javascript: / data: / vbscript: 伪协议，自动附带 target="_blank" rel="noopener"
    link(token, title, text) {
      let href = ''
      let linkTitle = ''
      let linkText = ''
      if (typeof token === 'object' && token !== null) {
        href = (token.href || '').trim()
        linkTitle = token.title ? String(token.title) : ''
        linkText = this.parser ? this.parser.parseInline(token.tokens || []) : (token.text || '')
      } else {
        href = String(token || '').trim()
        linkTitle = title ? String(title) : ''
        linkText = String(text || '')
      }

      if (/^(?:javascript|data|vbscript):/i.test(href)) {
        return linkText
      }

      const safeHref = escapeHtml(href)
      const titleAttr = linkTitle ? ` title="${escapeHtml(linkTitle)}"` : ''
      return `<a href="${safeHref}"${titleAttr} target="_blank" rel="noopener">${linkText}</a>`
    },
  },
})

/**
 * 将 Markdown 文本渲染为安全的 HTML。
 * 若无文本或纯空白字符，返回统一定义的空状态组件。
 *
 * @param {string} md - 待解析的 Markdown 原文字符串
 * @returns {string} 渲染后的 HTML 字符串
 */
export function renderMarkdown(md) {
  if (!md || !md.trim()) {
    return '<div class="empty-tab-state">暂无文本记录</div>'
  }
  return markedInstance.parse(md)
}

/**
 * Vue 组合式函数入口。
 */
export function useMarkdown() {
  return {
    renderMarkdown,
  }
}

export default renderMarkdown
