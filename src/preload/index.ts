import { contextBridge, ipcRenderer } from 'electron'
import type { AppSettings, MayiDesktopApi, RendererEvent } from '../shared/protocol'

/**
 * 通过 contextBridge 向渲染层暴露最小化、强类型的安全桌面 API。
 */
const api: MayiDesktopApi = {
  getSettings: () => ipcRenderer.invoke('settings:get'),
  saveSettings: (patch: Partial<AppSettings>) => ipcRenderer.invoke('settings:save', patch),
  listSessions: () => ipcRenderer.invoke('session:list'),
  createSession: (input) => ipcRenderer.invoke('session:create', input),
  deleteSession: (sessionId) => ipcRenderer.invoke('session:delete', sessionId),
  setSourceDir: (sessionId, sourceDir) => ipcRenderer.invoke('session:setSourceDir', sessionId, sourceDir),
  pickDirectory: () => ipcRenderer.invoke('session:pickDirectory'),
  getMessages: (sessionId) => ipcRenderer.invoke('session:getMessages', sessionId),
  sendMessage: (sessionId, prompt) => ipcRenderer.invoke('session:send', sessionId, prompt),
  cancelSession: (sessionId) => ipcRenderer.invoke('session:cancel', sessionId),
  respondGate: (sessionId, gateId, approved, feedback) =>
    ipcRenderer.invoke('session:respondGate', sessionId, gateId, approved, feedback),
  listWorkspaceFiles: (sessionId) => ipcRenderer.invoke('workspace:listFiles', sessionId),
  readWorkspaceFile: (sessionId, relPath) => ipcRenderer.invoke('workspace:readFile', sessionId, relPath),
  saveWorkspaceFile: (sessionId, relPath, content) => ipcRenderer.invoke('workspace:saveFile', sessionId, relPath, content),
  openWorkspacePath: (sessionId, relPath) => ipcRenderer.invoke('workspace:openPath', sessionId, relPath),
  onEvent: (listener: (event: RendererEvent) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, payload: RendererEvent): void => listener(payload)
    ipcRenderer.on('mayi:event', handler)
    return () => ipcRenderer.removeListener('mayi:event', handler)
  }
}

contextBridge.exposeInMainWorld('mayiApi', api)
