import { app } from 'electron'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import electronUpdater from 'electron-updater'
import type { AppUpdateState, RendererEvent } from '../shared/protocol'
import type { LocalAppStore } from './store'

const { autoUpdater } = electronUpdater

// 后台轮询间隔：每 30 分钟静默探测一次服务器 latest.yml 是否发布了新版本。
const POLL_INTERVAL_MS = 30 * 60 * 1000

/**
 * 桌面端自动热更新管理器：
 * 1. 对接自有云服务器 Nginx / 静态目录（generic provider），读取 latest.yml 与 .blockmap 差分索引；
 * 2. 发现新版本后无需人工确认即自动在后台增量下载变动块（autoDownload = true）；
 * 3. 实时向前端推送下载百分比与网速，支持一键重启升级或关闭软件时静默完成安装。
 */
export class AppAutoUpdaterService {
  private state: AppUpdateState
  private pollTimer: NodeJS.Timeout | null = null
  private initialized = false

  constructor(
    private readonly store: LocalAppStore,
    private readonly broadcast: (event: RendererEvent) => void
  ) {
    this.state = {
      status: 'idle',
      currentVersion: app.getVersion()
    }
  }

  /**
   * 获取当前最新的更新状态快照。
   */
  getState(): AppUpdateState {
    return { ...this.state }
  }

  /**
   * 更新内部状态并向前端工作台实时广播。
   */
  private setState(patch: Partial<AppUpdateState>): AppUpdateState {
    this.state = {
      ...this.state,
      ...patch,
      currentVersion: app.getVersion()
    }
    this.broadcast({ type: 'updater.status', state: this.getState() })
    return this.getState()
  }

  /**
   * 解析有效的服务器热更新地址：允许用户在设置面板动态覆盖，未填写则沿用安装包内置的 app-update.yml。
   */
  private applyFeedUrlIfConfigured(): boolean {
    const customUrl = (this.store.getSettings().updateUrl || process.env.MAYI_UPDATE_URL || '').trim()
    if (customUrl) {
      const normalized = customUrl.endsWith('/') ? customUrl : `${customUrl}/`
      autoUpdater.setFeedURL({
        provider: 'generic',
        url: normalized
      })
      return true
    }

    // 检查安装包 resources 目录下是否内置了非占位符的 app-update.yml
    const builtInConfig = join(process.resourcesPath, 'app-update.yml')
    if (app.isPackaged && existsSync(builtInConfig)) {
      return true
    }
    return false
  }

  /**
   * 绑定 electron-updater 生命周期事件并启动开机自动检测与定时轮询。
   */
  start(): void {
    if (this.initialized) return
    this.initialized = true

    autoUpdater.autoDownload = true
    autoUpdater.autoInstallOnAppQuit = true
    autoUpdater.allowDowngrade = false

    autoUpdater.on('checking-for-update', () => {
      this.setState({ status: 'checking', errorMessage: undefined })
    })

    autoUpdater.on('update-available', (info) => {
      const notes =
        typeof info.releaseNotes === 'string'
          ? info.releaseNotes
          : Array.isArray(info.releaseNotes)
            ? info.releaseNotes.map((n) => n.note || '').join('\n')
            : undefined
      this.setState({
        status: 'available',
        latestVersion: info.version,
        releaseNotes: notes,
        percent: 0,
        checkedAt: Date.now(),
        errorMessage: undefined
      })
    })

    autoUpdater.on('update-not-available', (info) => {
      this.setState({
        status: 'not-available',
        latestVersion: info?.version || app.getVersion(),
        checkedAt: Date.now(),
        errorMessage: undefined
      })
    })

    autoUpdater.on('download-progress', (progress) => {
      this.setState({
        status: 'downloading',
        percent: Math.round(progress.percent * 10) / 10,
        transferredBytes: progress.transferred,
        totalBytes: progress.total,
        bytesPerSecond: progress.bytesPerSecond,
        errorMessage: undefined
      })
    })

    autoUpdater.on('update-downloaded', (info) => {
      this.setState({
        status: 'downloaded',
        latestVersion: info.version,
        percent: 100,
        checkedAt: Date.now(),
        errorMessage: undefined
      })
    })

    autoUpdater.on('error', (err) => {
      this.setState({
        status: 'error',
        errorMessage: err?.message ? err.message.slice(0, 240) : String(err),
        checkedAt: Date.now()
      })
    })

    // 仅在正式打包后的客户端（或显式配置了 dev-app-update.yml 调试时）启动后台自动监听
    if (app.isPackaged) {
      setTimeout(() => {
        void this.checkForUpdates(true)
      }, 6000)

      this.pollTimer = setInterval(() => {
        // 已下载待重启或正在下载中时无需重复触发检查
        if (this.state.status === 'downloading' || this.state.status === 'downloaded') {
          return
        }
        void this.checkForUpdates(true)
      }, POLL_INTERVAL_MS)
    }
  }

  /**
   * 主动触发一次版本检查（若发现新版本会自动开始后台差分下载）。
   */
  async checkForUpdates(silent = false): Promise<AppUpdateState> {
    if (!app.isPackaged) {
      const devCfg = join(app.getAppPath(), 'dev-app-update.yml')
      if (!existsSync(devCfg) && !(this.store.getSettings().updateUrl || '').trim()) {
        return this.setState({
          status: 'idle',
          errorMessage: silent ? undefined : '当前处于本地源码开发模式，打包安装为正式版 (.exe) 后自动生效热更新。'
        })
      }
      autoUpdater.forceDevUpdateConfig = true
    }

    if (!this.applyFeedUrlIfConfigured()) {
      return this.setState({
        status: 'idle',
        errorMessage: silent ? undefined : '尚未配置更新服务器地址，请在设置中填写更新源 URL。'
      })
    }

    try {
      await autoUpdater.checkForUpdates()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      if (!silent) {
        this.setState({ status: 'error', errorMessage: msg.slice(0, 240), checkedAt: Date.now() })
      }
    }
    return this.getState()
  }

  /**
   * 立即退出当前应用并静默执行 NSIS 差分覆盖安装，完成后自动重新拉起新版客户端。
   */
  quitAndInstall(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer)
      this.pollTimer = null
    }
    // isSilent = true 免去用户再点安装向导下一步，isForceRunAfter = true 装完立即自动启动新版本
    autoUpdater.quitAndInstall(true, true)
  }
}
