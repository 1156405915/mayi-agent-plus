import { existsSync, realpathSync, statSync } from 'node:fs'
import { isAbsolute, relative, resolve, sep } from 'node:path'

const FORBIDDEN_SYSTEM_PREFIXES = [
  'c:\\windows',
  'c:\\program files',
  'c:\\program files (x86)',
  '/etc',
  '/usr',
  '/bin',
  '/sbin',
  '/system'
]

/**
 * 校验用户选择的外部资料文件夹是否为合法且非操作系统敏感根目录的本地绝对目录。
 */
export function validateSourceDirectory(dirPath: string): { ok: boolean; canonical?: string; reason?: string } {
  const trimmed = dirPath.trim()
  if (!trimmed || !isAbsolute(trimmed)) {
    return { ok: false, reason: '资料目录必须是本地绝对路径' }
  }
  if (!existsSync(trimmed)) {
    return { ok: false, reason: '指定的资料目录不存在' }
  }
  const canonical = realpathSync(trimmed)
  const stat = statSync(canonical)
  if (!stat.isDirectory()) {
    return { ok: false, reason: '所选路径不是文件夹' }
  }

  // 禁止直接将整个磁盘根目录（如 C:\ 或 D:\）设为扫描源，防止误扫全盘海量无关文件。
  if (/^[a-z]:\\?$/i.test(canonical) || canonical === '/') {
    return { ok: false, reason: '请勿直接选择磁盘根目录，请选择具体的资料子文件夹' }
  }

  const lower = canonical.toLowerCase()
  if (FORBIDDEN_SYSTEM_PREFIXES.some((prefix) => lower === prefix || lower.startsWith(prefix + sep))) {
    return { ok: false, reason: '禁止挂载操作系统敏感系统目录' }
  }

  return { ok: true, canonical }
}

/**
 * 将相对文件路径限制在当前会话工作区内，阻断任何 `..` 路径穿越读写。
 */
export function resolveSafeWorkspaceFile(workspaceDir: string, relPath: string): string {
  const base = resolve(workspaceDir)
  const cleaned = relPath.replace(/\\/g, '/').replace(/^\/+/, '')
  if (!cleaned || cleaned.includes('\0')) {
    throw new Error('无效的工作区文件路径')
  }
  const target = resolve(base, cleaned)
  const rel = relative(base, target)
  if (rel.startsWith('..') || isAbsolute(rel)) {
    throw new Error('越界访问被拒绝：目标文件不在当前会话工作区内')
  }
  return target
}
