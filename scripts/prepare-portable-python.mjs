import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const venvDir = join(root, '.venv')
const pyvenvCfgPath = join(venvDir, 'pyvenv.cfg')
const requirementsPath = join(root, 'runtime', 'requirements.txt')
const outPythonDir = join(root, 'build-python')
const stampPath = join(outPythonDir, '.portable-stamp.json')

const EXCLUDED_STDLIB_DIRS = ['test', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', '__pycache__']
// 仅剔除顶层安装器工具包（严格限定在 site-packages 根级匹配，绝不误伤子包内的 pipeline 等同名模块）。
const TOP_LEVEL_SITE_EXCLUDE_RE = /^(?:pip|setuptools|wheel|_distutils_hack)(?:[-.]|$)/i

/**
 * 从 .venv/pyvenv.cfg 解析底层独立 CPython 发行版根目录。
 */
function resolveBaseCpythonHome() {
  if (!existsSync(pyvenvCfgPath)) {
    throw new Error('未找到 .venv/pyvenv.cfg，请先运行 pnpm setup:python 初始化开发环境。')
  }
  const cfgText = readFileSync(pyvenvCfgPath, 'utf8')
  const homeMatch = cfgText.match(/^home\s*=\s*(.+)$/m)
  if (!homeMatch) {
    throw new Error('无法从 .venv/pyvenv.cfg 解析 home 路径。')
  }
  const homeDir = homeMatch[1].trim()
  if (!existsSync(homeDir)) {
    throw new Error(`底层 Python 目录不存在: ${homeDir}`)
  }
  return homeDir
}

/**
 * Windows 下优先使用多线程 robocopy 极速复制上万个 Python 小文件（约 8~15 秒完成）；其他平台走 cpSync。
 */
function fastCopyTree(srcDir, dstDir, excludeDirNames) {
  mkdirSync(dstDir, { recursive: true })
  if (process.platform === 'win32') {
    const args = [
      srcDir,
      dstDir,
      '/E',
      '/MT:16',
      '/NFL',
      '/NDL',
      '/NJH',
      '/NJS',
      '/NP',
      '/XD',
      ...excludeDirNames,
      '/XF',
      '*.pyc',
      '*.pdb'
    ]
    const res = spawnSync('robocopy', args, { encoding: 'utf8' })
    // robocopy 退出码 0~7 均代表复制成功（含新文件复制），>=8 才是错误。
    if (res.status === null || res.status >= 8) {
      throw new Error(`robocopy 复制失败 (code=${res.status}):\n${res.stderr || res.stdout}`)
    }
    return
  }

  const excluded = new Set(excludeDirNames)
  cpSync(srcDir, dstDir, {
    recursive: true,
    filter: (src) => {
      const base = src.replace(/\\/g, '/').split('/').pop() || ''
      return !excluded.has(base) && !base.endsWith('.pyc') && !base.endsWith('.pdb')
    }
  })
}

/**
 * 组装零外部路径依赖的可重定位便携式 Python 运行时目录（build-python/），
 * 使打包后的安装包在任何未安装 Python 的纯净客户电脑上开箱即用。
 */
function buildPortablePython() {
  const reqHash = createHash('sha256').update(readFileSync(requirementsPath)).digest('hex')
  const expectedStamp = JSON.stringify({ version: 2, platform: process.platform, reqHash })
  const targetExe =
    process.platform === 'win32' ? join(outPythonDir, 'python.exe') : join(outPythonDir, 'bin', 'python3')

  if (existsSync(targetExe) && existsSync(stampPath) && readFileSync(stampPath, 'utf8') === expectedStamp) {
    console.log('[PortablePython] 便携式 Python 运行时缓存已就绪，跳过重复复制。')
    return
  }

  const baseHome = resolveBaseCpythonHome()
  console.log(`[PortablePython] 正在从 ${baseHome} 高速构建离线可重定位 Python 运行时...`)
  rmSync(outPythonDir, { recursive: true, force: true })

  // 1. 复制独立 CPython 解释器与精简标准库
  fastCopyTree(baseHome, outPythonDir, EXCLUDED_STDLIB_DIRS)

  // 2. 将 .venv 中已验证的第三方依赖合并注入便携式运行时的 site-packages
  const venvSitePackages =
    process.platform === 'win32'
      ? join(venvDir, 'Lib', 'site-packages')
      : join(venvDir, 'lib', 'python3.12', 'site-packages')
  const targetSitePackages =
    process.platform === 'win32'
      ? join(outPythonDir, 'Lib', 'site-packages')
      : join(outPythonDir, 'lib', 'python3.12', 'site-packages')

  if (!existsSync(venvSitePackages)) {
    throw new Error(`未找到虚拟环境依赖目录: ${venvSitePackages}`)
  }

  fastCopyTree(venvSitePackages, targetSitePackages, ['__pycache__'])

  // 清理顶层无需随客户端分发的 pip / setuptools / wheel 构建期工具包
  for (const entry of readdirSync(targetSitePackages)) {
    if (TOP_LEVEL_SITE_EXCLUDE_RE.test(entry)) {
      rmSync(join(targetSitePackages, entry), { recursive: true, force: true })
    }
  }

  // 3. 移除任何可能残留的 pyvenv.cfg，确保解释器 100% 走相对自身目录寻址
  rmSync(join(outPythonDir, 'pyvenv.cfg'), { force: true })

  // 4. 使用 -I（隔离宿主环境变量）实测验证全部核心引擎、AgentScope 与 OCR 依赖可独立加载
  const probe = [
    'import sys, docx, legacy_doc, markdown_it, numpy, onnxruntime, openpyxl, PIL, pypdf, pypdfium2, rapidocr_onnxruntime, xlrd',
    'from agentscope.agent import ReActAgent',
    'from agentscope.model import OpenAIChatModel',
    'print("PORTABLE_OK:" + sys.executable)'
  ].join('; ')
  const res = spawnSync(targetExe, ['-I', '-c', probe], { encoding: 'utf8' })
  if (res.status !== 0) {
    rmSync(outPythonDir, { recursive: true, force: true })
    throw new Error(`便携式 Python 独立性自检失败:\n${res.stderr || res.stdout}`)
  }

  writeFileSync(stampPath, expectedStamp, 'utf8')
  console.log(`[PortablePython] 构建并自检通过: ${res.stdout.trim()}`)
}

buildPortablePython()
