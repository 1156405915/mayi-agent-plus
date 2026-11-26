import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const UV_VERSION = '0.12.19'
const UV_INSTALLER_SHA256 = {
  ps1: 'f44cf87798d181653f4c160ee28df6353ccf0ac4efea16c9a32abff694c8f45b',
  sh: '61b349611f1b6e1ba33645f30c36da5287df2609dd7af8605d96a031435eb35b'
}
const SETUP_VERSION = 1
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const pythonVersion = readFileSync(join(root, '.python-version'), 'utf8').trim()
const requirementsPath = join(root, 'runtime', 'requirements.txt')
const toolsDirectory = join(root, '.tools')
const siblingToolsDirectory = resolve(root, '..', 'mayi-agent', '.tools')

// 若同级历史项目已下载过 uv 与受管 Python 缓存，优先复用以避免重复外网拉取大体积运行时。
const sharedToolsBase = existsSync(join(siblingToolsDirectory, 'uv')) ? siblingToolsDirectory : toolsDirectory
const uvDirectory = join(sharedToolsBase, 'uv')
const pythonDirectory = join(sharedToolsBase, 'python')
const cacheDirectory = join(sharedToolsBase, 'uv-cache')
const venvDirectory = join(root, '.venv')
const venvPython = process.platform === 'win32'
  ? join(venvDirectory, 'Scripts', 'python.exe')
  : join(venvDirectory, 'bin', 'python')
const uvExecutable = process.platform === 'win32'
  ? join(uvDirectory, 'uv.exe')
  : join(uvDirectory, 'uv')
const stampPath = join(venvDirectory, '.mayi-plus-python-setup.json')
const requirementsHash = createHash('sha256').update(readFileSync(requirementsPath)).digest('hex')
const expectedStamp = JSON.stringify({ setupVersion: SETUP_VERSION, pythonVersion, requirementsHash })

const commandEnvironment = {
  ...process.env,
  UV_CACHE_DIR: cacheDirectory,
  UV_PYTHON_INSTALL_DIR: pythonDirectory,
  UV_NO_MODIFY_PATH: '1',
  UV_UNMANAGED_INSTALL: uvDirectory
}

/**
 * 在受控环境变量下执行外部命令，失败时抛出包含退出详情的异常。
 */
function run(executable, args, options = {}) {
  const result = spawnSync(executable, args, {
    cwd: root,
    env: commandEnvironment,
    encoding: 'utf8',
    stdio: options.capture ? 'pipe' : 'inherit'
  })
  if (result.error) throw result.error
  if (result.status !== 0) {
    const detail = options.capture ? `\n${result.stderr || result.stdout}` : ''
    throw new Error(`命令执行失败：${executable} ${args.join(' ')}${detail}`)
  }
  return result.stdout?.trim() || ''
}

/**
 * 校验当前虚拟环境是否已安装匹配的 64 位 Python 及全部核心依赖。
 */
function environmentIsReady() {
  if (!existsSync(venvPython) || !existsSync(stampPath)) return false
  if (readFileSync(stampPath, 'utf8') !== expectedStamp) return false
  const probe = [
    'import json, platform, struct',
    'import docx, legacy_doc, markdown_it, numpy, onnxruntime, openpyxl, PIL, pypdf, pypdfium2, rapidocr_onnxruntime, xlrd',
    'from agentscope.agent import ReActAgent',
    'from agentscope.model import OpenAIChatModel',
    'print(json.dumps({"version": platform.python_version(), "bits": struct.calcsize("P") * 8}))'
  ].join('; ')
  try {
    const output = run(venvPython, ['-I', '-c', probe], { capture: true })
    const info = JSON.parse(output)
    return info.version === pythonVersion && info.bits === 64
  } catch {
    return false
  }
}

/**
 * 确保受管 uv 可执行文件存在并通过固定版本及哈希校验。
 */
async function ensureUv() {
  if (existsSync(uvExecutable)) {
    const installedVersion = run(uvExecutable, ['--version'], { capture: true })
    if (installedVersion.startsWith(`uv ${UV_VERSION} `) || installedVersion === `uv ${UV_VERSION}`) return
  }

  mkdirSync(uvDirectory, { recursive: true })
  const extension = process.platform === 'win32' ? 'ps1' : 'sh'
  const installerPath = join(tmpdir(), `mayi-plus-uv-${UV_VERSION}-${process.pid}.${extension}`)
  const installerUrl = `https://astral.sh/uv/${UV_VERSION}/install.${extension}`
  console.log(`[Python] 正在下载 uv ${UV_VERSION}...`)
  const response = await fetch(installerUrl, { signal: AbortSignal.timeout(60_000) })
  if (!response.ok) throw new Error(`下载 uv 安装器失败：HTTP ${response.status}`)
  const installer = Buffer.from(await response.arrayBuffer())
  const actualHash = createHash('sha256').update(installer).digest('hex')
  if (actualHash !== UV_INSTALLER_SHA256[extension]) throw new Error('uv 安装器完整性校验失败')
  writeFileSync(installerPath, installer)
  try {
    if (process.platform === 'win32') {
      run('powershell.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', installerPath])
    } else {
      run('sh', [installerPath])
    }
  } finally {
    rmSync(installerPath, { force: true })
  }
}

/**
 * 准备项目私有虚拟环境并同步 AgentScope 与离线文档引擎依赖。
 */
async function main() {
  if (process.env.MAYI_SKIP_PYTHON_SETUP === '1') {
    console.log('[Python] 已通过 MAYI_SKIP_PYTHON_SETUP=1 跳过环境准备。')
    return
  }
  if (environmentIsReady()) {
    console.log(`[Python] 项目环境已就绪（Python ${pythonVersion}）。`)
    return
  }

  await ensureUv()
  console.log(`[Python] 正在准备项目私有 Python ${pythonVersion}...`)
  if (!existsSync(pythonDirectory)) {
    run(uvExecutable, ['python', 'install', '--no-bin', pythonVersion])
  }
  if (!existsSync(venvPython)) {
    run(uvExecutable, ['venv', '--python', pythonVersion, '--managed-python', venvDirectory])
  }
  console.log('[Python] 正在同步 AgentScope 薄内核与文档引擎依赖...')
  try {
    // 优先命中本地 uv-cache 离线极速链接安装，缓存缺失时自动回退联网下载。
    run(uvExecutable, ['pip', 'install', '--offline', '--python', venvPython, '--requirement', requirementsPath])
  } catch {
    run(uvExecutable, ['pip', 'install', '--python', venvPython, '--requirement', requirementsPath])
  }

  mkdirSync(venvDirectory, { recursive: true })
  writeFileSync(stampPath, expectedStamp)
  if (!environmentIsReady()) {
    rmSync(stampPath, { force: true })
    throw new Error('Python 环境安装后验证失败')
  }
  console.log(`[Python] 环境准备完成：${venvPython}`)
}

main().catch((error) => {
  console.error(`[Python] ${error instanceof Error ? error.message : String(error)}`)
  process.exitCode = 1
})
