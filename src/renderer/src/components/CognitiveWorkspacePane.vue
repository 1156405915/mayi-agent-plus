<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { CognitiveStep, WorkspaceFileItem } from '@shared/protocol'
import { renderSafeMarkdown } from '../utils/markdown'

const props = defineProps<{
  sessionId: string | null
  steps: CognitiveStep[]
  files: WorkspaceFileItem[]
  selectedPath: string
  fileContent: string
}>()

const emit = defineEmits<{
  (e: 'selectFile', relPath: string): void
  (e: 'saveFile', payload: { relPath: string; content: string }): void
  (e: 'openExternal', relPath: string): void
}>()

const editMode = ref(false)
const editableText = ref('')
const dirty = ref(false)

watch(
  () => [props.selectedPath, props.fileContent] as const,
  ([_path, content]) => {
    if (!dirty.value) {
      editableText.value = content
    }
  },
  { immediate: true }
)

watch(
  () => props.selectedPath,
  () => {
    dirty.value = false
    editMode.value = false
    editableText.value = props.fileContent
  }
)

const isTextEditable = computed(() => {
  if (!props.selectedPath) return false
  return /\.(md|txt|json|csv|py|yaml|yml)$/i.test(props.selectedPath)
})

const renderedHtml = computed(() => renderSafeMarkdown(editableText.value))

/**
 * 切换预览与微调编辑模式。
 */
function toggleEditMode(): void {
  editMode.value = !editMode.value
}

/**
 * 保存用户在右侧工作区手工微调后的 Markdown 文件（如微调 02-plan.md 目录或某章草稿）。
 */
function saveChanges(): void {
  if (!props.selectedPath) return
  emit('saveFile', { relPath: props.selectedPath, content: editableText.value })
  dirty.value = false
  editMode.value = false
}

/**
 * 格式化文件字节体积为人类易读单位。
 */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}
</script>

<template>
  <aside class="right-workspace-pane">
    <!-- 顶部：五步底层认知闭环实时看板 -->
    <div class="steps-board">
      <div style="display: flex; justify-content: space-between; align-items: center">
        <strong style="font-size: 13.5px">五步底层认知闭环进度</strong>
        <span style="font-size: 12px; color: var(--text-muted)">感知 → 对齐 → 规划 → 分治 → 验证交付</span>
      </div>
      <div class="steps-grid">
        <div v-for="step in steps" :key="step.id" :class="['step-card', step.status]">
          <div style="font-weight: 600; margin-bottom: 3px">
            {{
              step.status === 'done'
                ? '✅'
                : step.status === 'in_progress'
                  ? '🔵'
                  : step.status === 'skipped'
                    ? '⏭️'
                    : '⚪'
            }}
            {{ step.title }}
          </div>
          <div style="color: var(--text-muted); font-size: 11px; line-height: 1.35">
            {{ step.detail }}
          </div>
        </div>
      </div>
    </div>

    <!-- 下部：工作区文件树 + Markdown 实时预览与在线微调器 -->
    <div class="workspace-split">
      <div class="ws-file-tree">
        <div style="font-size: 12px; font-weight: 600; color: var(--text-muted); padding: 4px 6px 8px">
          工作区产物 ({{ files.length }})
        </div>
        <div v-if="files.length === 0" style="font-size: 12px; color: var(--text-muted); padding: 8px 6px">
          任务启动后，解析缓存、标准清单、目录规划、章节草稿与最终 Word 交付物将实时显示在此处。
        </div>
        <div
          v-for="f in files"
          :key="f.path"
          :class="[
            'ws-file-item',
            { active: f.path === selectedPath, deliverable: f.isDeliverable }
          ]"
          @click="emit('selectFile', f.path)"
        >
          <div>{{ f.isDeliverable ? '📦 ' : '📄 ' }}{{ f.path }}</div>
          <div style="font-size: 11px; color: var(--text-muted); margin-top: 2px">
            {{ formatBytes(f.bytes) }}
          </div>
        </div>
      </div>

      <div class="ws-editor-area">
        <div class="editor-toolbar">
          <div style="font-size: 12.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
            {{ selectedPath || '未选择工作区文件' }}
            <span v-if="dirty" style="color: var(--warning); margin-left: 6px">● 未保存</span>
          </div>
          <div v-if="selectedPath" style="display: flex; gap: 6px">
            <template v-if="isTextEditable">
              <button class="btn" @click="toggleEditMode">
                {{ editMode ? '👁️ 预览视图' : '✏️ 微调编辑' }}
              </button>
              <button v-if="editMode || dirty" class="btn btn-primary" @click="saveChanges">
                💾 保存修改
              </button>
            </template>
            <button class="btn" @click="emit('openExternal', selectedPath)">
              📂 外部应用打开
            </button>
          </div>
        </div>

        <div
          v-if="!selectedPath"
          style="margin: auto; color: var(--text-muted); font-size: 13px; text-align: center; padding: 24px"
        >
          在左侧文件列表点击任意文件（如 <code>01-requirements.md</code>、<code>02-plan.md</code> 或章节草稿），即可在此实时预览排版效果或手工微调内容。
        </div>

        <div
          v-else-if="!isTextEditable"
          style="margin: auto; text-align: center; padding: 32px"
        >
          <div style="font-size: 15px; font-weight: 600; margin-bottom: 10px">
            已生成二进制交付文件：{{ selectedPath }}
          </div>
          <button class="btn btn-primary" @click="emit('openExternal', selectedPath)">
            使用本机 Office / 默认程序打开查看
          </button>
        </div>

        <textarea
          v-else-if="editMode"
          v-model="editableText"
          class="md-raw-editor"
          @input="dirty = true"
        />

        <div v-else class="md-preview" v-html="renderedHtml" />
      </div>
    </div>
  </aside>
</template>
