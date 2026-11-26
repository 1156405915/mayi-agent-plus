/**
 * 前后端共享纯通用协议定义（零行业字段耦合）。
 */

export type SessionStatus = 'idle' | 'running' | 'waiting_gate' | 'error'

export type StepStatus = 'pending' | 'in_progress' | 'done' | 'skipped'

export interface AppSettings {
  apiKey: string
  baseUrl: string
  model: string
  autoApproveGates: boolean
}

export interface CognitiveStep {
  id: string
  title: string
  status: StepStatus
  detail: string
}

export interface ToolExecutionRecord {
  id: string
  toolName: string
  status: 'running' | 'succeeded' | 'failed'
  input: string
  output?: string
  durationMs?: number
}

export interface GatePrompt {
  id: string
  kind: string
  title: string
  summary: string
  reviewFiles: string[]
  resolved?: boolean
  approved?: boolean
  feedback?: string
}

export interface WorkspaceFileItem {
  path: string
  name: string
  ext: string
  bytes: number
  updatedAt: number
  isDeliverable: boolean
}

export interface ChatMessage {
  id: string
  sessionId: string
  role: 'user' | 'assistant'
  text: string
  tools: ToolExecutionRecord[]
  gate?: GatePrompt
  isError?: boolean
  createdAt: number
}

export interface SessionRecord {
  id: string
  title: string
  status: SessionStatus
  workspaceDir: string
  sourceDir: string
  steps: CognitiveStep[]
  createdAt: number
  updatedAt: number
}

export type RendererEvent =
  | { type: 'session.updated'; session: SessionRecord }
  | { type: 'message.updated'; sessionId: string; message: ChatMessage }
  | { type: 'workspace.files'; sessionId: string; files: WorkspaceFileItem[]; changedPath?: string }
  | { type: 'subagent.status'; sessionId: string; id: string; title: string; status: string }

export interface MayiDesktopApi {
  getSettings(): Promise<AppSettings>
  saveSettings(patch: Partial<AppSettings>): Promise<AppSettings>
  listSessions(): Promise<SessionRecord[]>
  createSession(input?: { title?: string; sourceDir?: string }): Promise<SessionRecord>
  deleteSession(sessionId: string): Promise<void>
  setSourceDir(sessionId: string, sourceDir: string): Promise<SessionRecord>
  pickDirectory(): Promise<string | null>
  getMessages(sessionId: string): Promise<ChatMessage[]>
  sendMessage(sessionId: string, prompt: string): Promise<void>
  cancelSession(sessionId: string): Promise<void>
  respondGate(sessionId: string, gateId: string, approved: boolean, feedback?: string): Promise<void>
  listWorkspaceFiles(sessionId: string): Promise<WorkspaceFileItem[]>
  readWorkspaceFile(sessionId: string, relPath: string): Promise<string>
  saveWorkspaceFile(sessionId: string, relPath: string, content: string): Promise<void>
  openWorkspacePath(sessionId: string, relPath: string): Promise<void>
  onEvent(listener: (event: RendererEvent) => void): () => void
}
