import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { extname, join, relative } from 'node:path'
import type { AppSettings, ChatMessage, CognitiveStep, SessionRecord, WorkspaceFileItem } from '../shared/protocol'

export const DEFAULT_FIVE_STEPS: CognitiveStep[] = [
  { id: 'perceive', title: '1. 环境感知与标准化', status: 'pending', detail: '扫描目录并批量转 Markdown 缓存' },
  { id: 'align', title: '2. 目标与约束对齐', status: 'pending', detail: '提炼要求与标准 -> 01-requirements.md' },
  { id: 'plan', title: '3. 全局规划与门禁', status: 'pending', detail: '编写目录/方案 -> 02-plan.md' },
  { id: 'execute', title: '4. 分治执行产出', status: 'pending', detail: '无锁并发子代理撰写草稿或执行计算' },
  { id: 'verify', title: '5. 客观验证与交付', status: 'pending', detail: '硬指标校验 + 独立审查 -> 标准交付物' }
]

const DEFAULT_SETTINGS: AppSettings = {
  apiKey: process.env.MAYI_API_KEY || process.env.MAYI_AGENTSCOPE_API_KEY || '',
  baseUrl: process.env.MAYI_BASE_URL || 'https://api.deepseek.com',
  model: process.env.MAYI_MODEL || 'deepseek-chat',
  autoApproveGates: false
}

/**
 * 极简本地文件持久化存储层：负责管理全局配置、会话元数据、消息记录与会话私有工作区目录。
 */
export class LocalAppStore {
  private readonly settingsPath: string
  private readonly sessionsPath: string
  private readonly messagesDir: string
  private readonly workspacesRoot: string

  constructor(private readonly dataRoot: string) {
    mkdirSync(this.dataRoot, { recursive: true })
    this.settingsPath = join(this.dataRoot, 'settings.json')
    this.sessionsPath = join(this.dataRoot, 'sessions.json')
    this.messagesDir = join(this.dataRoot, 'messages')
    this.workspacesRoot = join(this.dataRoot, 'workspaces')
    mkdirSync(this.messagesDir, { recursive: true })
    mkdirSync(this.workspacesRoot, { recursive: true })
  }

  /**
   * 读取应用模型配置，缺省字段自动回退到安全默认值。
   */
  getSettings(): AppSettings {
    if (!existsSync(this.settingsPath)) {
      return { ...DEFAULT_SETTINGS }
    }
    try {
      const parsed = JSON.parse(readFileSync(this.settingsPath, 'utf8'))
      return { ...DEFAULT_SETTINGS, ...parsed }
    } catch {
      return { ...DEFAULT_SETTINGS }
    }
  }

  /**
   * 增量合并保存模型与门禁配置。
   */
  saveSettings(patch: Partial<AppSettings>): AppSettings {
    const next: AppSettings = { ...this.getSettings(), ...patch }
    writeFileSync(this.settingsPath, JSON.stringify(next, null, 2), 'utf8')
    return next
  }

  /**
   * 获取按最近更新时间倒序排列的全部会话。
   */
  listSessions(): SessionRecord[] {
    if (!existsSync(this.sessionsPath)) return []
    try {
      const list = JSON.parse(readFileSync(this.sessionsPath, 'utf8')) as SessionRecord[]
      return list.sort((a, b) => b.updatedAt - a.updatedAt)
    } catch {
      return []
    }
  }

  /**
   * 按 ID 查找单个会话，不存在时抛出明确异常。
   */
  getSession(sessionId: string): SessionRecord {
    const found = this.listSessions().find((s) => s.id === sessionId)
    if (!found) throw new Error(`未找到会话: ${sessionId}`)
    return found
  }

  /**
   * 创建新会话并初始化其独立的工作区目录与默认五步认知进度。
   */
  createSession(input?: { title?: string; sourceDir?: string }): SessionRecord {
    const id = randomUUID()
    const workspaceDir = join(this.workspacesRoot, id)
    mkdirSync(workspaceDir, { recursive: true })
    const now = Date.now()
    const session: SessionRecord = {
      id,
      title: (input?.title || '新建智能体任务').trim().slice(0, 60),
      status: 'idle',
      workspaceDir,
      sourceDir: input?.sourceDir || workspaceDir,
      steps: DEFAULT_FIVE_STEPS.map((s) => ({ ...s })),
      createdAt: now,
      updatedAt: now
    }
    const all = [session, ...this.listSessions()]
    writeFileSync(this.sessionsPath, JSON.stringify(all, null, 2), 'utf8')
    return session
  }

  /**
   * 保存更新后的会话记录。
   */
  saveSession(updated: SessionRecord): SessionRecord {
    const all = this.listSessions()
    const idx = all.findIndex((s) => s.id === updated.id)
    const record = { ...updated, updatedAt: Date.now() }
    if (idx >= 0) {
      all[idx] = record
    } else {
      all.unshift(record)
    }
    writeFileSync(this.sessionsPath, JSON.stringify(all, null, 2), 'utf8')
    return record
  }

  /**
   * 删除指定会话及其消息与工作区目录。
   */
  deleteSession(sessionId: string): void {
    const remaining = this.listSessions().filter((s) => s.id !== sessionId)
    writeFileSync(this.sessionsPath, JSON.stringify(remaining, null, 2), 'utf8')
    rmSync(join(this.messagesDir, `${sessionId}.json`), { force: true })
    rmSync(join(this.workspacesRoot, sessionId), { recursive: true, force: true })
  }

  /**
   * 读取指定会话的全部历史消息。
   */
  getMessages(sessionId: string): ChatMessage[] {
    const file = join(this.messagesDir, `${sessionId}.json`)
    if (!existsSync(file)) return []
    try {
      return JSON.parse(readFileSync(file, 'utf8')) as ChatMessage[]
    } catch {
      return []
    }
  }

  /**
   * 新增或按消息 ID 原地更新单条聊天消息。
   */
  upsertMessage(sessionId: string, message: ChatMessage): ChatMessage {
    const list = this.getMessages(sessionId)
    const idx = list.findIndex((m) => m.id === message.id)
    if (idx >= 0) {
      list[idx] = message
    } else {
      list.push(message)
    }
    writeFileSync(join(this.messagesDir, `${sessionId}.json`), JSON.stringify(list, null, 2), 'utf8')
    return message
  }

  /**
   * 扫描会话工作区内面向用户展示的阶段文件与最终交付件清单。
   */
  scanWorkspaceFiles(sessionId: string): WorkspaceFileItem[] {
    const session = this.getSession(sessionId)
    const base = session.workspaceDir
    if (!existsSync(base)) return []
    const items: WorkspaceFileItem[] = []

    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name)
        const rel = relative(base, full).replace(/\\/g, '/')
        if (rel.startsWith('.workspace/scripts') || rel.startsWith('.workspace/.state')) continue
        if (entry.isDirectory()) {
          walk(full)
        } else if (entry.isFile()) {
          if (
            rel.startsWith('.workspace/parsed/') &&
            !(rel.endsWith('/full.md') || rel.endsWith('/toc.md') || rel.endsWith('manifest.json') || rel.endsWith('INDEX.md'))
          ) {
            continue
          }
          const st = statSync(full)
          items.push({
            path: rel,
            name: entry.name,
            ext: extname(entry.name).toLowerCase(),
            bytes: st.size,
            updatedAt: Math.round(st.mtimeMs),
            isDeliverable: rel.startsWith('deliverables/')
          })
        }
      }
    }

    walk(base)
    return items.sort((a, b) => Number(b.isDeliverable) - Number(a.isDeliverable) || a.path.localeCompare(b.path))
  }
}
