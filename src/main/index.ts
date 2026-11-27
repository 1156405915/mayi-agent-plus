import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { AppSettings, RendererEvent } from '../shared/protocol'
import { PythonRuntimeBridge } from './runtime-bridge'
import { LocalAppStore } from './store'
import { AppAutoUpdaterService } from './updater'
import { resolveSafeWorkspaceFile, validateSourceDirectory } from './workspace-guard'

const currentDir = dirname(fileURLToPath(import.meta.url))
let mainWindow: BrowserWindow | null = null

/**
 * 定位打包态与开发态下的项目根目录及 Python 运行时入口脚本。
 */
function resolveRuntimePaths(): { projectRoot: string; runtimeEntry: string } {
  if (app.isPackaged) {
    const resRoot = process.resourcesPath
    return {
      projectRoot: resRoot,
      runtimeEntry: join(resRoot, 'runtime', 'main.py')
    }
  }
  const devRoot = resolve(currentDir, '../..')
  return {
    projectRoot: devRoot,
    runtimeEntry: join(devRoot, 'runtime', 'main.py')
  }
}

/**
 * 向渲染层安全广播后端实时事件。
 */
function broadcast(event: RendererEvent): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('mayi:event', event)
  }
}

/**
 * 创建承载双栏智能体工作台的主窗口。
 */
function createMainWindow(): BrowserWindow {
  const preloadPath = join(currentDir, '../preload/index.cjs')
  const win = new BrowserWindow({
    width: 1460,
    height: 920,
    minWidth: 1120,
    minHeight: 700,
    title: '蚂蚁 Agent Plus - 通用智能体工作台',
    autoHideMenuBar: true,
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  if (process.env.ELECTRON_RENDERER_URL) {
    void win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void win.loadFile(join(currentDir, '../renderer/index.html'))
  }
  return win
}

app.whenReady().then(() => {
  const dataRoot = app.isPackaged
    ? join(app.getPath('userData'), 'workspace-data')
    : join(resolve(currentDir, '../..'), '.mayi-plus-data')
  const { projectRoot, runtimeEntry } = resolveRuntimePaths()
  const store = new LocalAppStore(dataRoot)
  const bridge = new PythonRuntimeBridge(projectRoot, runtimeEntry, store, broadcast)
  const updater = new AppAutoUpdaterService(store, broadcast)

  ipcMain.handle('settings:get', () => store.getSettings())
  ipcMain.handle('settings:save', (_e, patch: Partial<AppSettings>) => {
    const prevUrl = store.getSettings().updateUrl
    const saved = store.saveSettings(patch)
    // 若用户修改了热更新服务器地址，立即异步触发一次新源连通性与版本探测
    if (saved.updateUrl && saved.updateUrl !== prevUrl) {
      void updater.checkForUpdates(true)
    }
    return saved
  })
  ipcMain.handle('updater:getState', () => updater.getState())
  ipcMain.handle('updater:check', () => updater.checkForUpdates(false))
  ipcMain.handle('updater:quitAndInstall', () => updater.quitAndInstall())

  ipcMain.handle('session:list', () => store.listSessions())
  ipcMain.handle('session:create', (_e, input?: { title?: string; sourceDir?: string }) => {
    if (input?.sourceDir) {
      const check = validateSourceDirectory(input.sourceDir)
      if (!check.ok) throw new Error(check.reason)
      return store.createSession({ ...input, sourceDir: check.canonical })
    }
    return store.createSession(input)
  })
  ipcMain.handle('session:delete', (_e, sessionId: string) => {
    bridge.cancel(sessionId)
    store.deleteSession(sessionId)
  })
  ipcMain.handle('session:setSourceDir', (_e, sessionId: string, sourceDir: string) => {
    const check = validateSourceDirectory(sourceDir)
    if (!check.ok || !check.canonical) throw new Error(check.reason || '无效目录')
    const updated = store.saveSession({ ...store.getSession(sessionId), sourceDir: check.canonical })
    broadcast({ type: 'session.updated', session: updated })
    return updated
  })
  ipcMain.handle('session:pickDirectory', async () => {
    if (!mainWindow) return null
    const res = await dialog.showOpenDialog(mainWindow, {
      title: '选择任务参考资料文件夹（只读挂载）',
      properties: ['openDirectory']
    })
    return res.canceled || res.filePaths.length === 0 ? null : res.filePaths[0]
  })

  ipcMain.handle('session:getMessages', (_e, sessionId: string) => store.getMessages(sessionId))
  ipcMain.handle('session:send', (_e, sessionId: string, prompt: string) => {
    void bridge.runTurn(sessionId, prompt)
  })
  ipcMain.handle('session:cancel', (_e, sessionId: string) => {
    bridge.cancel(sessionId)
  })
  ipcMain.handle('session:respondGate', (_e, sessionId: string, gateId: string, approved: boolean, feedback?: string) => {
    bridge.respondToGate(sessionId, gateId, approved, feedback)
  })

  ipcMain.handle('workspace:listFiles', (_e, sessionId: string) => store.scanWorkspaceFiles(sessionId))
  ipcMain.handle('workspace:readFile', (_e, sessionId: string, relPath: string) => {
    const session = store.getSession(sessionId)
    const full = resolveSafeWorkspaceFile(session.workspaceDir, relPath)
    if (!existsSync(full)) throw new Error(`文件不存在: ${relPath}`)
    return readFileSync(full, 'utf8')
  })
  ipcMain.handle('workspace:saveFile', (_e, sessionId: string, relPath: string, content: string) => {
    const session = store.getSession(sessionId)
    const full = resolveSafeWorkspaceFile(session.workspaceDir, relPath)
    mkdirSync(dirname(full), { recursive: true })
    writeFileSync(full, content, 'utf8')
    broadcast({
      type: 'workspace.files',
      sessionId,
      files: store.scanWorkspaceFiles(sessionId),
      changedPath: relPath
    })
  })
  ipcMain.handle('workspace:openPath', async (_e, sessionId: string, relPath: string) => {
    const session = store.getSession(sessionId)
    const full = resolveSafeWorkspaceFile(session.workspaceDir, relPath)
    if (existsSync(full)) {
      await shell.openPath(full)
    }
  })

  mainWindow = createMainWindow()
  updater.start()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) mainWindow = createMainWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
