import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { StringDecoder } from 'node:string_decoder'
import type { ChatMessage, CognitiveStep, RendererEvent, ToolExecutionRecord } from '../shared/protocol'
import type { LocalAppStore } from './store'
import { validateSourceDirectory } from './workspace-guard'

/**
 * 从用户消息中智能识别显式附带的本地文件夹路径（支持带引号含空格路径，或未加引号的连续路径Token）。
 */
export function extractSourceDirFromPrompt(prompt: string): string | null {
  const quoted = prompt.match(/(?:资料目录|文件夹(?:路径|地址)?|目录(?:路径|地址)?)\s*[:：=]\s*(?:"([^"\r\n]+)"|'([^'\r\n]+)'|“([^”\r\n]+)”)/)
  const quotedHit = quoted?.[1] || quoted?.[2] || quoted?.[3]
  if (quotedHit) return quotedHit.trim()

  const unquoted = prompt.match(/(?:资料目录|文件夹(?:路径|地址)?|目录(?:路径|地址)?)\s*[:：=]\s*([a-zA-Z]:[\\/][^\s"“'\r\n]+|\/[^\s"“'\r\n]+)/)
  if (unquoted?.[1]) return unquoted[1].trim()

  for (const rawLine of prompt.split(/\r?\n/)) {
    const line = rawLine.trim().replace(/^["“']|["”']$/g, '')
    if (/^[a-zA-Z]:[\\/].+/.test(line) && existsSync(line)) {
      return line
    }
  }
  return null
}

/**
 * 探测可用的项目私有 Python 解释器（优先当前工程 .venv，开发态可平滑回退同级缓存环境）。
 */
export function resolvePythonExecutable(projectRoot: string): string {
  const winRel = join('.venv', 'Scripts', 'python.exe')
  const unixRel = join('.venv', 'bin', 'python')
  const rel = process.platform === 'win32' ? winRel : unixRel

  const candidates = [
    process.env.MAYI_PYTHON_EXECUTABLE,
    join(projectRoot, rel),
    join(resolve(projectRoot, '..', 'mayi-agent'), rel),
    'python'
  ].filter((item): item is string => Boolean(item))

  for (const cand of candidates) {
    if (cand === 'python' || existsSync(cand)) return cand
  }
  throw new Error('未找到可用的 Python 运行环境，请先执行 pnpm setup:python')
}

/**
 * 管理 Electron 主进程与 Python AgentScope 薄内核之间的 NDJSON 流式会话、门禁响应与进程中断。
 */
export class PythonRuntimeBridge {
  private readonly activeProcesses = new Map<string, ChildProcessWithoutNullStreams>()

  constructor(
    private readonly projectRoot: string,
    private readonly runtimeEntry: string,
    private readonly store: LocalAppStore,
    private readonly emitToRenderer: (event: RendererEvent) => void
  ) {}

  /**
   * 启动一轮用户对话推理，实时将流式增量、工具调用、五步进度与门禁卡片推送至前端工作台。
   */
  async runTurn(sessionId: string, prompt: string): Promise<void> {
    if (this.activeProcesses.has(sessionId)) {
      throw new Error('当前会话已有正在执行的任务，请先等待完成或点击停止')
    }

    let session = this.store.getSession(sessionId)
    const detectedDir = extractSourceDirFromPrompt(prompt)
    if (detectedDir) {
      const check = validateSourceDirectory(detectedDir)
      if (check.ok && check.canonical) {
        session = this.store.saveSession({ ...session, sourceDir: check.canonical })
      }
    }

    const previousMessages = this.store.getMessages(sessionId)
    if (previousMessages.length === 0 && session.title === '新建智能体任务') {
      session = this.store.saveSession({ ...session, title: prompt.trim().slice(0, 36) })
    }

    const userMsg: ChatMessage = {
      id: randomUUID(),
      sessionId,
      role: 'user',
      text: prompt.trim(),
      tools: [],
      createdAt: Date.now()
    }
    this.store.upsertMessage(sessionId, userMsg)
    this.emitToRenderer({ type: 'message.updated', sessionId, message: userMsg })

    const assistantMsg: ChatMessage = {
      id: randomUUID(),
      sessionId,
      role: 'assistant',
      text: '',
      tools: [],
      createdAt: Date.now()
    }
    this.store.upsertMessage(sessionId, assistantMsg)
    session = this.store.saveSession({ ...session, status: 'running' })
    this.emitToRenderer({ type: 'session.updated', session })
    this.emitToRenderer({ type: 'message.updated', sessionId, message: assistantMsg })

    const settings = this.store.getSettings()
    const pythonExe = resolvePythonExecutable(this.projectRoot)
    const historyPayload = previousMessages
      .filter((m) => !m.isError && m.text.trim())
      .slice(-16)
      .map((m) => ({ role: m.role, content: m.text }))

    const requestPayload = {
      workspace: session.workspaceDir,
      source_directory: session.sourceDir,
      prompt: prompt.trim(),
      history: historyPayload,
      config: {
        api_key: settings.apiKey,
        base_url: settings.baseUrl,
        model: settings.model,
        auto_approve_gates: settings.autoApproveGates
      }
    }

    await new Promise<void>((resolveTurn) => {
      const child = spawn(pythonExe, [this.runtimeEntry, '--ipc'], {
        cwd: this.projectRoot,
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true
      })
      this.activeProcesses.set(sessionId, child)

      let stdoutRest = ''
      let stderrText = ''
      const outDecoder = new StringDecoder('utf8')
      const errDecoder = new StringDecoder('utf8')

      const syncAssistant = (): void => {
        this.store.upsertMessage(sessionId, assistantMsg)
        this.emitToRenderer({ type: 'message.updated', sessionId, message: { ...assistantMsg, tools: [...assistantMsg.tools] } })
      }

      const handleEventFrame = (frame: Record<string, unknown>): void => {
        const type = String(frame.type || '')
        if (type === 'delta') {
          assistantMsg.text += String(frame.text || '')
          syncAssistant()
        } else if (type === 'tool') {
          const exec = frame.execution as ToolExecutionRecord
          if (!exec?.id) return
          const idx = assistantMsg.tools.findIndex((t) => t.id === exec.id)
          if (idx >= 0) {
            assistantMsg.tools[idx] = exec
          } else {
            assistantMsg.tools.push(exec)
          }
          syncAssistant()
        } else if (type === 'steps') {
          const steps = (frame.steps as CognitiveStep[]) || []
          session = this.store.saveSession({ ...session, steps })
          this.emitToRenderer({ type: 'session.updated', session })
        } else if (type === 'workspace_changed') {
          const files = this.store.scanWorkspaceFiles(sessionId)
          this.emitToRenderer({
            type: 'workspace.files',
            sessionId,
            files,
            changedPath: typeof frame.path === 'string' ? frame.path : undefined
          })
        } else if (type === 'subagent_status') {
          this.emitToRenderer({
            type: 'subagent.status',
            sessionId,
            id: String(frame.id || ''),
            title: String(frame.title || ''),
            status: String(frame.status || '')
          })
        } else if (type === 'gate') {
          assistantMsg.gate = {
            id: String(frame.id || ''),
            kind: String(frame.kind || 'plan_approval'),
            title: String(frame.title || '请确认规划方案'),
            summary: String(frame.summary || ''),
            reviewFiles: Array.isArray(frame.reviewFiles) ? frame.reviewFiles.map(String) : [],
            resolved: false
          }
          session = this.store.saveSession({ ...session, status: 'waiting_gate' })
          this.emitToRenderer({ type: 'session.updated', session })
          syncAssistant()
        } else if (type === 'result') {
          const finalText = String(frame.text || '').trim()
          if (finalText && !assistantMsg.text.trim()) {
            assistantMsg.text = finalText
          }
          syncAssistant()
          const files = this.store.scanWorkspaceFiles(sessionId)
          this.emitToRenderer({ type: 'workspace.files', sessionId, files })
        } else if (type === 'error') {
          assistantMsg.isError = true
          assistantMsg.text = assistantMsg.text
            ? `${assistantMsg.text}\n\n⚠️ 执行中止：${String(frame.message || '未知错误')}`
            : `⚠️ 执行错误：${String(frame.message || '未知错误')}`
          syncAssistant()
        }
      }

      child.stdout.on('data', (chunk: Buffer) => {
        stdoutRest += outDecoder.write(chunk)
        const lines = stdoutRest.split('\n')
        stdoutRest = lines.pop() || ''
        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed) continue
          try {
            handleEventFrame(JSON.parse(trimmed))
          } catch {
            // 忽略非 JSON 杂讯行
          }
        }
      })

      child.stderr.on('data', (chunk: Buffer) => {
        stderrText += errDecoder.write(chunk)
        if (stderrText.length > 8000) stderrText = stderrText.slice(-8000)
      })

      child.on('close', (code) => {
        this.activeProcesses.delete(sessionId)
        for (const tool of assistantMsg.tools) {
          if (tool.status === 'running') tool.status = code === 0 ? 'succeeded' : 'failed'
        }
        if (code !== 0 && !assistantMsg.isError && !assistantMsg.text.trim()) {
          assistantMsg.isError = true
          assistantMsg.text = `任务已停止或异常退出 (code=${code})${stderrText.trim() ? `\n${stderrText.trim().slice(-600)}` : ''}`
        }
        syncAssistant()
        const finalStatus = assistantMsg.isError ? 'error' : 'idle'
        session = this.store.saveSession({ ...this.store.getSession(sessionId), status: finalStatus })
        this.emitToRenderer({ type: 'session.updated', session })
        resolveTurn()
      })

      child.stdin.write(JSON.stringify(requestPayload) + '\n', 'utf8')
    })
  }

  /**
   * 将前端用户对门禁卡片的确认或修改意见写回正在挂起等待的 Python 子进程。
   */
  respondToGate(sessionId: string, gateId: string, approved: boolean, feedback = ''): void {
    const child = this.activeProcesses.get(sessionId)
    if (!child) throw new Error('该门禁对应的后台任务已结束，请直接发送新消息继续')

    const messages = this.store.getMessages(sessionId)
    const targetMsg = messages.find((m) => m.gate?.id === gateId)
    if (targetMsg?.gate) {
      targetMsg.gate = { ...targetMsg.gate, resolved: true, approved, feedback }
      this.store.upsertMessage(sessionId, targetMsg)
      this.emitToRenderer({ type: 'message.updated', sessionId, message: targetMsg })
    }

    const updatedSession = this.store.saveSession({ ...this.store.getSession(sessionId), status: 'running' })
    this.emitToRenderer({ type: 'session.updated', session: updatedSession })

    child.stdin.write(
      JSON.stringify({
        type: 'gate_response',
        id: gateId,
        approved,
        feedback
      }) + '\n',
      'utf8'
    )
  }

  /**
   * 立即终止指定会话正在运行的后台推理子进程。
   */
  cancel(sessionId: string): void {
    const child = this.activeProcesses.get(sessionId)
    if (child) {
      child.kill()
      this.activeProcesses.delete(sessionId)
    }
  }
}
