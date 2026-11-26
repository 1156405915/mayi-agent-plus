import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { extractSourceDirFromPrompt } from '../../src/main/runtime-bridge'
import { LocalAppStore } from '../../src/main/store'
import { resolveSafeWorkspaceFile, validateSourceDirectory } from '../../src/main/workspace-guard'

describe('Mayi Agent Plus 宿主核心单元测试', () => {
  const tempDirs: string[] = []

  afterEach(() => {
    for (const dir of tempDirs.splice(0)) {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('能够安全拦截越界路径穿越并放行合法工作区相对路径', () => {
    const ws = mkdtempSync(join(tmpdir(), 'mayi-ws-'))
    tempDirs.push(ws)

    const valid = resolveSafeWorkspaceFile(ws, 'drafts/01-overview.md')
    expect(valid).toContain('01-overview.md')
    expect(() => resolveSafeWorkspaceFile(ws, '../../Windows/System32/cmd.exe')).toThrow(/越界访问/)
  })

  it('禁止直接挂载操作系统根目录并允许挂载真实业务子文件夹', () => {
    const bizDir = mkdtempSync(join(tmpdir(), 'mayi-src-'))
    tempDirs.push(bizDir)

    const okRes = validateSourceDirectory(bizDir)
    expect(okRes.ok).toBe(true)

    if (process.platform === 'win32') {
      expect(validateSourceDirectory('C:\\').ok).toBe(false)
      expect(validateSourceDirectory('C:\\Windows').ok).toBe(false)
    }
  })

  it('支持会话创建、五步状态初始化、消息更新及交付文件识别扫描', () => {
    const root = mkdtempSync(join(tmpdir(), 'mayi-store-'))
    tempDirs.push(root)

    const store = new LocalAppStore(root)
    const session = store.createSession({ title: '高新区标书编纂' })
    expect(session.steps).toHaveLength(5)
    expect(session.steps[0]?.id).toBe('perceive')

    mkdirSync(join(session.workspaceDir, 'deliverables'), { recursive: true })
    writeFileSync(join(session.workspaceDir, '01-requirements.md'), '# 提取要求', 'utf8')
    writeFileSync(join(session.workspaceDir, 'deliverables', '技术方案.docx'), 'fake-docx', 'utf8')

    const files = store.scanWorkspaceFiles(session.id)
    expect(files.some((f) => f.path === 'deliverables/技术方案.docx' && f.isDeliverable)).toBe(true)
    expect(files.some((f) => f.path === '01-requirements.md' && !f.isDeliverable)).toBe(true)
  })

  it('可从自然语言提示词中提取显式标注的资料目录地址', () => {
    const extracted = extractSourceDirFromPrompt('请帮我分析资料目录：D:\\project-test\\bid-docs 并生成完整方案')
    expect(extracted).toBe('D:\\project-test\\bid-docs')
  })
})
