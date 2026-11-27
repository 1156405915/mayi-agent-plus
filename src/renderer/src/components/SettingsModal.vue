<script setup lang="ts">
import { reactive, watch } from 'vue'
import type { AppSettings, AppUpdateState } from '@shared/protocol'

const props = defineProps<{
  visible: boolean
  settings: AppSettings
  updateState: AppUpdateState
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'save', patch: AppSettings): void
  (e: 'checkUpdate'): void
  (e: 'installUpdate'): void
}>()

const form = reactive<AppSettings>({
  apiKey: '',
  baseUrl: 'https://api.deepseek.com',
  model: 'deepseek-chat',
  autoApproveGates: false,
  updateUrl: ''
})

watch(
  () => props.visible,
  (open) => {
    if (open) {
      Object.assign(form, props.settings)
      form.updateUrl = props.settings.updateUrl || ''
    }
  },
  { immediate: true }
)

/**
 * 提交并持久化模型连接、门禁策略与热更新服务器源地址设置。
 */
function handleSave(): void {
  emit('save', {
    apiKey: form.apiKey.trim(),
    baseUrl: form.baseUrl.trim() || 'https://api.deepseek.com',
    model: form.model.trim() || 'deepseek-chat',
    autoApproveGates: form.autoApproveGates,
    updateUrl: (form.updateUrl || '').trim()
  })
}
</script>

<template>
  <div v-if="visible" class="modal-mask" @click.self="emit('close')">
    <div class="modal-card">
      <h3 style="margin: 0 0 16px; font-size: 16px">模型、执行策略与热更新设置</h3>

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

      <hr style="border: none; border-top: 1px solid var(--border); margin: 14px 0" />

      <div class="form-field">
        <label>自动更新服务器地址（留空则使用安装包内置服务器地址）</label>
        <input
          v-model="form.updateUrl"
          type="text"
          placeholder="例如：https://updates.your-domain.com/mayi-plus/"
        />
      </div>

      <div
        style="
          display: flex;
          justify-content: space-between;
          align-items: center;
          font-size: 12px;
          color: var(--text-muted);
          background: var(--bg-soft, #f6f8fa);
          padding: 8px 10px;
          border-radius: 6px;
        "
      >
        <div>
          当前版本：<strong>v{{ updateState.currentVersion }}</strong>
          <span v-if="updateState.status === 'checking'" style="margin-left: 8px">正在连接服务器检查...</span>
          <span v-else-if="updateState.status === 'not-available'" style="margin-left: 8px; color: #16a34a">
            已是最新版本
          </span>
          <span v-else-if="updateState.status === 'downloading'" style="margin-left: 8px; color: #2563eb">
            正在后台增量下载 v{{ updateState.latestVersion }} ({{ updateState.percent ?? 0 }}%)
          </span>
          <span v-else-if="updateState.status === 'downloaded'" style="margin-left: 8px; color: #16a34a">
            新版本 v{{ updateState.latestVersion }} 已下载完毕
          </span>
          <span v-else-if="updateState.errorMessage" style="margin-left: 8px; color: #dc2626">
            {{ updateState.errorMessage }}
          </span>
        </div>
        <button
          v-if="updateState.status === 'downloaded'"
          type="button"
          class="btn btn-primary"
          style="padding: 4px 10px; font-size: 12px"
          @click="emit('installUpdate')"
        >
          立即重启升级
        </button>
        <button
          v-else
          type="button"
          class="btn"
          style="padding: 4px 10px; font-size: 12px"
          :disabled="updateState.status === 'checking' || updateState.status === 'downloading'"
          @click="emit('checkUpdate')"
        >
          检查更新
        </button>
      </div>

      <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn btn-primary" @click="handleSave">保存设置</button>
      </div>
    </div>
  </div>
</template>
