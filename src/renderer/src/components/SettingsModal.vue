<script setup lang="ts">
import { reactive, watch } from 'vue'
import type { AppSettings } from '@shared/protocol'

const props = defineProps<{
  visible: boolean
  settings: AppSettings
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'save', patch: AppSettings): void
}>()

const form = reactive<AppSettings>({
  apiKey: '',
  baseUrl: 'https://api.deepseek.com',
  model: 'deepseek-chat',
  autoApproveGates: false
})

watch(
  () => props.visible,
  (open) => {
    if (open) {
      Object.assign(form, props.settings)
    }
  },
  { immediate: true }
)

/**
 * 提交并持久化模型连接与门禁策略设置。
 */
function handleSave(): void {
  emit('save', {
    apiKey: form.apiKey.trim(),
    baseUrl: form.baseUrl.trim() || 'https://api.deepseek.com',
    model: form.model.trim() || 'deepseek-chat',
    autoApproveGates: form.autoApproveGates
  })
}
</script>

<template>
  <div v-if="visible" class="modal-mask" @click.self="emit('close')">
    <div class="modal-card">
      <h3 style="margin: 0 0 16px; font-size: 16px">模型与执行策略设置</h3>

      <div class="form-field">
        <label>API Base URL（兼容 OpenAI / DeepSeek 接口）</label>
        <input v-model="form.baseUrl" type="text" placeholder="https://api.deepseek.com" />
      </div>

      <div class="form-field">
        <label>API Key</label>
        <input v-model="form.apiKey" type="password" placeholder="sk-..." />
      </div>

      <div class="form-field">
        <label>模型名称 (Model)</label>
        <input v-model="form.model" type="text" placeholder="deepseek-chat" />
      </div>

      <div class="form-field" style="display: flex; align-items: center; gap: 8px">
        <input id="autoGate" v-model="form.autoApproveGates" type="checkbox" />
        <label for="autoGate" style="margin: 0; font-weight: 400">
          全自动放行规划门禁（关闭时复杂任务会在产出目录大纲后暂停供确认）
        </label>
      </div>

      <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn btn-primary" @click="handleSave">保存设置</button>
      </div>
    </div>
  </div>
</template>
