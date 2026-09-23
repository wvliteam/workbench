/**
 * useAnsi: 将终端包含 ANSI 转义序列的文本精确还原为带样式的 HTML 字符串。
 */

const FG_COLORS = {
  30: '#484f58', 31: '#ff7b72', 32: '#3fb950', 33: '#d29922',
  34: '#58a6ff', 35: '#bc8cff', 36: '#39c5cf', 37: '#e6edf3',
  90: '#8b949e', 91: '#ffa198', 92: '#56d364', 93: '#e3b341',
  94: '#79c0ff', 95: '#d2a8ff', 96: '#56d4dd', 97: '#ffffff',
}

const BG_COLORS = {
  40: '#21262d', 41: '#b62324', 42: '#1b4721', 43: '#543e0c',
  44: '#1f487a', 45: '#5a32a3', 46: '#1b4b50', 47: '#8b949e',
  100: '#30363d', 101: '#f85149', 102: '#2ea043', 103: '#bb8009',
  104: '#388bfd', 105: '#8957e5', 106: '#39c5cf', 107: '#f0f6fc',
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function color256(idx) {
  if (idx < 16) {
    return FG_COLORS[idx < 8 ? 30 + idx : 90 + idx - 8] || '#ffffff'
  }
  if (idx >= 16 && idx <= 231) {
    const n = idx - 16
    const r = Math.floor(n / 36) * 51
    const g = Math.floor((n % 36) / 6) * 51
    const b = (n % 6) * 51
    return `rgb(${r},${g},${b})`
  }
  if (idx >= 232 && idx <= 255) {
    const gray = 8 + (idx - 232) * 10
    return `rgb(${gray},${gray},${gray})`
  }
  return '#ffffff'
}

export function ansiToHtml(text) {
  if (!text) return '<span class="text-muted">暂无日志内容</span>'

  const str = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const re = /\x1b\[([0-9;]*)m|\x1b\[[0-9;]*[a-zA-Z]/g
  let lastIndex = 0
  let match
  let html = ''

  let state = {
    bold: false,
    dim: false,
    italic: false,
    underline: false,
    fg: null,
    bg: null,
  }

  function getSpanStyle() {
    const styles = []
    if (state.bold) styles.push('font-weight:700')
    if (state.dim) styles.push('opacity:0.65')
    if (state.italic) styles.push('font-style:italic')
    if (state.underline) styles.push('text-decoration:underline')
    if (state.fg) styles.push(`color:${state.fg}`)
    if (state.bg) styles.push(`background-color:${state.bg}`)
    return styles.join(';')
  }

  let openSpan = false

  while ((match = re.exec(str)) !== null) {
    const plainText = str.slice(lastIndex, match.index)
    if (plainText) {
      html += escapeHtml(plainText)
    }
    lastIndex = match.index + match[0].length

    if (!match[0].endsWith('m')) {
      continue
    }

    const rawCodes = match[1] ? match[1].split(';').map((s) => parseInt(s, 10)) : [0]
    let i = 0
    while (i < rawCodes.length) {
      const code = isNaN(rawCodes[i]) ? 0 : rawCodes[i]
      if (code === 0) {
        state = { bold: false, dim: false, italic: false, underline: false, fg: null, bg: null }
      } else if (code === 1) {
        state.bold = true
      } else if (code === 2) {
        state.dim = true
      } else if (code === 3) {
        state.italic = true
      } else if (code === 4) {
        state.underline = true
      } else if (code === 22) {
        state.bold = false
        state.dim = false
      } else if (code === 23) {
        state.italic = false
      } else if (code === 24) {
        state.underline = false
      } else if (code === 39) {
        state.fg = null
      } else if (code === 49) {
        state.bg = null
      } else if (code === 38 && rawCodes[i + 1] === 5 && rawCodes[i + 2] !== undefined) {
        state.fg = color256(rawCodes[i + 2])
        i += 2
      } else if (code === 48 && rawCodes[i + 1] === 5 && rawCodes[i + 2] !== undefined) {
        state.bg = color256(rawCodes[i + 2])
        i += 2
      } else if (FG_COLORS[code]) {
        state.fg = FG_COLORS[code]
      } else if (BG_COLORS[code]) {
        state.bg = BG_COLORS[code]
      }
      i++
    }

    if (openSpan) {
      html += '</span>'
      openSpan = false
    }

    const spanStyle = getSpanStyle()
    if (spanStyle) {
      html += `<span style="${spanStyle}">`
      openSpan = true
    }
  }

  if (lastIndex < str.length) {
    html += escapeHtml(str.slice(lastIndex))
  }
  if (openSpan) {
    html += '</span>'
  }

  return html
}
