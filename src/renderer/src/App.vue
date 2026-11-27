<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type {
  AppSettings,
  AppUpdateState,
  ChatMessage,
  CognitiveStep,
  SessionRecord,
  WorkspaceFileItem
} from '@shared/protocol'
import ChatStreamPane from './components/ChatStreamPane.vue'
import CognitiveWorkspacePane from './components/CognitiveWorkspacePane.vue'
import SettingsModal from './components/SettingsModal.vue'

const DEFAULT_STEPS: CognitiveStep[] = [
  { id: 'perceive', title: '1. 环境感知与标准化', status: 'pending', detail: '扫描目录并批量转 Markdown' },
  { id: 'align', title: '2. 目标与约束对齐', status: 'pending', detail: '提炼要求 -> 01-requirements.md' },
  { id: 'plan', title: '3. 全局规划与门禁', status: 'pending', detail: '编写目录 -> 02-plan.md' },
  { id: 'execute', title: '4. 分治执行产出', status: 'pending', detail: '并发撰写草稿或执行计算' },
  { id: 'verify', title: '5. 客观验证与交付', status: 'pending', detail: '硬检 + 独立审查 -> 交付' }
]

const settings = ref<AppSettings>({
  apiKey: '',
  baseUrl: 'https://api.deepseek.com',
  model: 'deepseek-chat',
  autoApproveGates: false
})
const showSettings = ref(false)
const sessions = ref<SessionRecord[]>([])
const activeSessionId = ref<string | null>(null)
const messages = ref<ChatMessage[]>([])
const workspaceFiles = ref<WorkspaceFileItem[]>([])
const selectedFilePath = ref('')
const selectedFileContent = ref('')
const subagents = ref<Array<{ id: string; title: string; status: string }>>([])
const activityLabel = ref('')
const updateState = ref<AppUpdateState>({
  status: 'idle',
  currentVersion: '0.1.0'
})

let unsubscribeEvents: (() => void) | null = null

const activeSession = computed(
  () => sessions.value.find((s) => s.id === activeSessionId.value) || null
)

/**
 * 切换当前激活会话并同步加载其历史消息与工作区文件树。
 */
async function selectSession(sessionId: string): Promise<void> {
  activeSessionId.value = sessionId
  selectedFilePath.value = ''
  selectedFileContent.value = ''
  subagents.value = []
  messages.value = await window.mayiApi.getMessages(sessionId)
  workspaceFiles.value = await window.mayiApi.listWorkspaceFiles(sessionId)
  const preferred =
    workspaceFiles.value.find((f) => f.isDeliverable) ||
    workspaceFiles.value.find((f) => f.path === '02-plan.md') ||
    workspaceFiles.value.find((f) => f.path === '01-requirements.md') ||
    workspaceFiles.value[0]
  if (preferred) {
    await openWorkspaceFileInPane(preferred.path)
  }
}

/**
 * 在右侧工作区面板读取并展示指定相对路径文件。
 */
async function openWorkspaceFileInPane(relPath: string): Promise<void> {
  if (!activeSessionId.value) return
  selectedFilePath.value = relPath
  if (/\.(md|txt|json|csv|py|yaml|yml)$/i.test(relPath)) {
    try {
      selectedFileContent.value = await window.mayiApi.readWorkspaceFile(activeSessionId.value, relPath)
    } catch {
      selectedFileContent.value = ''
    }
  } else {
    selectedFileContent.value = ''
  }
}

/**
 * 新建空白智能体任务会话。
 */
async function handleCreateSession(): Promise<void> {
  const created = await window.mayiApi.createSession()
  sessions.value = [created, ...sessions.value]
  await selectSession(created.id)
}

/**
 * 删除指定会话并自动切换到剩余首个会话或新建会话。
 */
async function handleDeleteSession(sessionId: string): Promise<void> {
  await window.mayiApi.deleteSession(sessionId)
  sessions.value = sessions.value.filter((s) => s.id !== sessionId)
  if (activeSessionId.value === sessionId) {
    if (sessions.value.length > 0) {
      await selectSession(sessions.value[0].id)
    } else {
      await handleCreateSession()
    }
  }
}

/**
 * 弹出系统文件夹选择器以挂载只读参考资料目录。
 */
async function handlePickSourceDir(): Promise<void> {
  if (!activeSessionId.value) return
  const picked = await window.mayiApi.pickDirectory()
  if (!picked) return
  const updated = await window.mayiApi.setSourceDir(activeSessionId.value, picked)
  const idx = sessions.value.findIndex((s) => s.id === updated.id)
  if (idx >= 0) sessions.value[idx] = updated
}

/**
 * 发送任务指令前检查 API Key 是否已填写。
 */
async function handleSendPrompt(prompt: string): Promise<void> {
  if (!activeSessionId.value) return
  if (!settings.value.apiKey.trim()) {
    showSettings.value = true
    return
  }
  subagents.value = []
  await window.mayiApi.sendMessage(activeSessionId.value, prompt)
}

/**
 * 保存右侧工作区微调后的文件变更。
 */
async function handleSaveWorkspaceFile(payload: { relPath: string; content: string }): Promise<void> {
  if (!activeSessionId.value) return
  await window.mayiApi.saveWorkspaceFile(activeSessionId.value, payload.relPath, payload.content)
  selectedFileContent.value = payload.content
}

/**
 * 中断当前激活会话正在执行的后台任务。
 */
async function handleCancelSession(): Promise<void> {
  if (!activeSessionId.value) return
  await window.mayiApi.cancelSession(activeSessionId.value)
}

/**
 * 将用户的门禁确认或修改意见转发至后端运行时。
 */
async function handleRespondGate(payload: { gateId: string; approved: boolean; feedback: string }): Promise<void> {
  if (!activeSessionId.value) return
  await window.mayiApi.respondGate(activeSessionId.value, payload.gateId, payload.approved, payload.feedback)
}

/**
 * 调用系统默认程序（如 Microsoft Word）打开指定工作区文件。
 */
async function handleOpenExternal(relPath: string): Promise<void> {
  if (!activeSessionId.value) return
  await window.mayiApi.openWorkspacePath(activeSessionId.value, relPath)
}

/**
 * 持久化更新模型与门禁策略配置。
 */
async function handleSaveSettings(patch: AppSettings): Promise<void> {
  settings.value = await window.mayiApi.saveSettings(patch)
  showSettings.value = false
}

/**
 * 手动触发一次服务端新版本检测。
 */
async function handleCheckUpdate(): Promise<void> {
  updateState.value = await window.mayiApi.checkForUpdates()
}

/**
 * 安装已后台下载完成的更新包：若当前有正在生成的会话，先弹窗二次确认以免误断任务。
 */
async function handleInstallUpdate(): Promise<void> {
  const hasRunning = sessions.value.some((s) => s.status === 'running')
  if (hasRunning) {
    const ok = window.confirm('当前有任务正在后台运行，立即重启升级会中断当前轮次（已完成检查点可续跑），确认立即重启升级吗？')
    if (!ok) return
  }
  await window.mayiApi.quitAndInstallUpdate()
}

onMounted(async () => {
  settings.value = await window.mayiApi.getSettings()
  updateState.value = await window.mayiApi.getUpdateState()
  sessions.value = await window.mayiApi.listSessions()
  if (sessions.value.length === 0) {
    await handleCreateSession()
  } else {
    await selectSession(sessions.value[0].id)
  }

  unsubscribeEvents = window.mayiApi.onEvent((event) => {
    if (event.type === 'session.updated') {
      const idx = sessions.value.findIndex((s) => s.id === event.session.id)
      if (idx >= 0) {
        sessions.value[idx] = event.session
      } else {
        sessions.value.unshift(event.session)
      }
      if (event.session.status === 'idle' || event.session.status === 'error') {
        subagents.value = []
        activityLabel.value = ''
      }
    } else if (event.type === 'activity.status') {
      if (event.sessionId === activeSessionId.value) {
        activityLabel.value = event.label
      }
    } else if (event.type === 'message.updated') {
      if (event.sessionId !== activeSessionId.value) return
      const idx = messages.value.findIndex((m) => m.id === event.message.id)
      if (idx >= 0) {
        messages.value[idx] = event.message
      } else {
        messages.value.push(event.message)
      }
      // 当触发规划门禁且带有待审阅文件时，自动在右侧打开大纲供用户查看或微调
      if (event.message.gate && !event.message.gate.resolved && event.message.gate.reviewFiles.length > 0) {
        const planTarget =
          event.message.gate.reviewFiles.find((f) => f.includes('02-plan')) ||
          event.message.gate.reviewFiles[0]
        void openWorkspaceFileInPane(planTarget)
      }
    } else if (event.type === 'workspace.files') {
      if (event.sessionId !== activeSessionId.value) return
      workspaceFiles.value = event.files
      if (event.changedPath && event.changedPath !== '.') {
        if (!selectedFilePath.value || selectedFilePath.value === event.changedPath) {
          void openWorkspaceFileInPane(event.changedPath)
        }
      }
    } else if (event.type === 'subagent.status') {
      if (event.sessionId !== activeSessionId.value) return
      const idx = subagents.value.findIndex((item) => item.id === event.id)
      if (event.status === 'running') {
        if (idx >= 0) subagents.value[idx] = { id: event.id, title: event.title, status: event.status }
        else subagents.value.push({ id: event.id, title: event.title, status: event.status })
      } else if (idx >= 0) {
        subagents.value.splice(idx, 1)
      }
    } else if (event.type === 'updater.status') {
      updateState.value = event.state
    }
  })
})

onUnmounted(() => {
  unsubscribeEvents?.()
})
</script>

<template>
  <div class="workbench-shell">
    <!-- 最左侧：极简会话与设置导航栏 -->
    <nav class="session-rail">
      <div class="rail-header">
        <span class="brand-title">🐜 Mayi Plus</span>
        <button
          class="btn btn-primary"
          style="padding: 4px 9px; font-size: 12px"
          @click="handleCreateSession"
        >
          + 新建
        </button>
      </div>

      <div class="session-list">
        <div
          v-for="item in sessions"
          :key="item.id"
          :class="['session-item', { active: item.id === activeSessionId }]"
          @click="selectSession(item.id)"
        >
          <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
            {{ item.title }}
          </span>
          <span
            title="删除会话"
            style="opacity: 0.65; font-size: 12px; padding: 0 4px"
            @click.stop="handleDeleteSession(item.id)"
          >
            ✕
          </span>
        </div>
      </div>

      <div class="rail-footer" style="display: flex; flex-direction: column; gap: 6px">
        <!-- 实时自动热更新提醒条：后台下载时显示进度，下载完成后一键静默重启升级 -->
        <div
          v-if="updateState.status === 'downloading'"
          style="font-size: 11.5px; background: #eff6ff; color: #1d4ed8; padding: 6px 8px; border-radius: 6px"
        >
          <div>⬇️ 正在下载新版本 v{{ updateState.latestVersion }} ({{ updateState.percent ?? 0 }}%)</div>
          <div style="height: 4px; background: #dbeafe; border-radius: 2px; margin-top: 4px; overflow: hidden">
            <div
              :style="{ width: `${updateState.percent ?? 0}%`, height: '100%', background: '#2563eb', transition: 'width 0.2s' }"
            />
          </div>
        </div>
        <button
          v-else-if="updateState.status === 'downloaded'"
          class="btn btn-primary"
          style="width: 100%; background: #16a34a; border-color: #16a34a; font-size: 12px"
          @click="handleInstallUpdate"
        >
          🚀 升级至 v{{ updateState.latestVersion }}（点击重启）
        </button>

        <button class="btn" style="width: 100%" @click="showSettings = true">
          ⚙️ 设置 · v{{ updateState.currentVersion }}
        </button>
      </div>
    </nav>

    <!-- 左主栏：流式对话 + 工具轨迹 + 门禁确认卡 -->
    <ChatStreamPane
      :session="activeSession"
      :messages="messages"
      :active-subagents="subagents"
      :activity-label="activityLabel"
      @pick-source="handlePickSourceDir"
      @send="handleSendPrompt"
      @cancel="handleCancelSession"
      @respond-gate="handleRespondGate"
      @select-workspace-file="openWorkspaceFileInPane"
    />

    <!-- 右主栏：五步进度看板 + 工作区产物实时预览/在线微调 -->
    <CognitiveWorkspacePane
      :session-id="activeSessionId"
      :steps="activeSession?.steps || DEFAULT_STEPS"
      :files="workspaceFiles"
      :selected-path="selectedFilePath"
      :file-content="selectedFileContent"
      @select-file="openWorkspaceFileInPane"
      @save-file="handleSaveWorkspaceFile"
      @open-external="handleOpenExternal"
    />

    <SettingsModal
      :visible="showSettings"
      :settings="settings"
      :update-state="updateState"
      @close="showSettings = false"
      @save="handleSaveSettings"
      @check-update="handleCheckUpdate"
      @install-update="handleInstallUpdate"
    />
  </div>
</template>
