<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { ChatMessage, SessionRecord } from '@shared/protocol'
import { renderSafeMarkdown } from '../utils/markdown'

const props = defineProps<{
  session: SessionRecord | null
  messages: ChatMessage[]
  activeSubagents: Array<{ id: string; title: string; status: string }>
}>()

const emit = defineEmits<{
  (e: 'pickSource'): void
  (e: 'send', prompt: string): void
  (e: 'cancel'): void
  (e: 'respondGate', payload: { gateId: string; approved: boolean; feedback: string }): void
  (e: 'selectWorkspaceFile', relPath: string): void
}>()

const draftPrompt = ref('')
const gateFeedbackMap = ref<Record<string, string>>({})
const feedRef = ref<HTMLElement | null>(null)

/**
 * 消息流更新时自动平滑滚动到底部，保持流式输出与门禁卡片始终处于视野中心。
 */
watch(
  () => props.messages.map((m) => `${m.id}:${m.text.length}:${m.tools.length}:${m.gate?.resolved}`).join('|'),
  async () => {
    await nextTick()
    if (feedRef.value) {
      feedRef.value.scrollTop = feedRef.value.scrollHeight
    }
  }
)

/**
 * 触发消息发送，阻止空内容或运行中重复提交。
 */
function submitPrompt(): void {
  const text = draftPrompt.value.trim()
  if (!text || !props.session || props.session.status === 'running') return
  draftPrompt.value = ''
  emit('send', text)
}

/**
 * 监听回车快捷键发送消息（Shift+Enter 保留换行）。
 */
function handleKeydown(ev: KeyboardEvent): void {
  if (ev.key === 'Enter' && !ev.shiftKey) {
    ev.preventDefault()
    submitPrompt()
  }
}

/**
 * 提交用户对规划门禁卡片的放行或调整决策。
 */
function submitGateDecision(gateId: string, approved: boolean): void {
  const feedback = (gateFeedbackMap.value[gateId] || '').trim()
  emit('respondGate', { gateId, approved, feedback })
}
</script>

<template>
  <section class="left-chat-pane">
    <header class="pane-topbar">
      <div style="font-weight: 600; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
        {{ session?.title || '请选择或新建任务' }}
      </div>
      <button v-if="session" class="source-pill" title="点击切换只读参考资料文件夹" @click="emit('pickSource')">
        📁 资料目录：{{ session.sourceDir }}
      </button>
    </header>

    <div ref="feedRef" class="chat-feed">
      <div
        v-if="messages.length === 0"
        style="margin: auto; text-align: center; color: var(--text-muted); max-width: 380px; line-height: 1.7"
      >
        <div style="font-size: 16px; font-weight: 600; color: var(--text-main); margin-bottom: 6px">
          通用五步智能体工作台
        </div>
        <div style="font-size: 13px">
          点击右上角挂载<strong>参考资料文件夹</strong>（支持 PDF / Word / Excel 离线秒级标准化），然后直接描述你的目标（如“提取招标要求并编写完整技术方案输出标准 Word”）。
        </div>
      </div>

      <div v-for="msg in messages" :key="msg.id" :class="['msg-card', msg.role]">
        <template v-if="msg.role === 'user'">{{ msg.text }}</template>

        <template v-else>
          <!-- 工具执行折叠轨迹 -->
          <div v-if="msg.tools.length > 0" class="tool-list">
            <details v-for="tool in msg.tools" :key="tool.id" class="tool-badge">
              <summary>
                <span>
                  {{ tool.status === 'running' ? '⏳' : tool.status === 'succeeded' ? '✅' : '❌' }}
                  <strong>{{ tool.toolName }}</strong>
                </span>
                <span v-if="tool.durationMs !== undefined">{{ tool.durationMs }} ms</span>
              </summary>
              <pre class="tool-pre">入参: {{ tool.input }}<template v-if="tool.output">&#10;结果: {{ tool.output }}</template></pre>
            </details>
          </div>

          <!-- 助手 Markdown 正文 -->
          <div
            v-if="msg.text"
            class="md-body"
            v-html="renderSafeMarkdown(msg.text)"
          />
          <div v-else-if="session?.status === 'running'" style="color: var(--text-muted); font-size: 13px">
            正在思考与调度工具...
          </div>

          <!-- 自适应人机确认门禁卡片 -->
          <div v-if="msg.gate" class="gate-card">
            <div class="gate-title">🚦 {{ msg.gate.title }}</div>
            <div style="font-size: 13px; color: #78350f; white-space: pre-wrap">{{ msg.gate.summary }}</div>

            <div v-if="msg.gate.reviewFiles.length > 0" class="gate-files">
              <span style="font-size: 12px; color: #92400e">点击在右侧预览或直接微调：</span>
              <button
                v-for="rf in msg.gate.reviewFiles"
                :key="rf"
                class="file-chip"
                @click="emit('selectWorkspaceFile', rf)"
              >
                📄 {{ rf }}
              </button>
            </div>

            <template v-if="!msg.gate.resolved">
              <input
                v-model="gateFeedbackMap[msg.gate.id]"
                type="text"
                placeholder="可选：输入补充修改要求（或直接在右侧修改保存 Markdown 后点击放行）"
                style="width: 100%; margin: 8px 0; padding: 7px 10px; border-radius: 6px; border: 1px solid #fcd34d; font-size: 12.5px"
              />
              <div style="display: flex; gap: 8px">
                <button class="btn btn-primary" @click="submitGateDecision(msg.gate.id, true)">
                  ✅ 确认无误，继续并发产出
                </button>
                <button class="btn" @click="submitGateDecision(msg.gate.id, false)">
                  ✏️ 按上述意见重新调整规划
                </button>
              </div>
            </template>
            <div v-else style="margin-top: 6px; font-size: 12px; color: #15803d; font-weight: 600">
              {{ msg.gate.approved ? '已确认放行' : '已要求重调规划' }}
              <template v-if="msg.gate.feedback">（备注：{{ msg.gate.feedback }}）</template>
            </div>
          </div>
        </template>
      </div>

      <div v-if="activeSubagents.length > 0" style="font-size: 12px; color: var(--primary); padding: 4px 2px">
        ⚡ 并发子代理执行中：
        <span v-for="sa in activeSubagents" :key="sa.id" style="margin-right: 10px">
          [{{ sa.title }}: {{ sa.status }}]
        </span>
      </div>
    </div>

    <footer class="composer-box">
      <textarea
        v-model="draftPrompt"
        class="composer-textarea"
        placeholder="输入任务指令（也可直接在消息里粘贴本地资料文件夹路径，按 Enter 发送，Shift+Enter 换行）..."
        :disabled="!session"
        @keydown="handleKeydown"
      />
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span style="font-size: 12px; color: var(--text-muted)">
          状态：{{
            session?.status === 'running'
              ? '执行中...'
              : session?.status === 'waiting_gate'
                ? '等待确认门禁'
                : '就绪'
          }}
        </span>
        <div style="display: flex; gap: 8px">
          <button
            v-if="session?.status === 'running' || session?.status === 'waiting_gate'"
            class="btn btn-danger"
            @click="emit('cancel')"
          >
            停止任务
          </button>
          <button
            class="btn btn-primary"
            :disabled="!session || session.status === 'running' || !draftPrompt.trim()"
            @click="submitPrompt"
          >
            发送指令
          </button>
        </div>
      </div>
    </footer>
  </section>
</template>
