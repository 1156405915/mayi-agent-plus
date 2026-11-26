import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: true
})

/**
 * 将 Markdown 文本安全渲染为经过 XSS 净化的 HTML 字符串，供对话流与工作区右侧预览面板复用。
 */
export function renderSafeMarkdown(raw: string): string {
  if (!raw?.trim()) return ''
  return DOMPurify.sanitize(md.render(raw))
}
