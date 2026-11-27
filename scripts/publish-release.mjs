import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const pkgPath = join(root, 'package.json')

/**
 * 解析命令行键值参数（支持 --version 0.2.0 / --bump patch / --url https://... / --scp root@ip:/path）。
 */
function parseArgs(argv) {
  const out = {}
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i]
    if (token.startsWith('--')) {
      const key = token.slice(2)
      const val = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : 'true'
      out[key] = val
    }
  }
  return out
}

/**
 * 按语义化版本号规则自增 patch / minor / major。
 */
function bumpSemver(version, kind) {
  const parts = version.split('.').map((n) => Number.parseInt(n, 10) || 0)
  while (parts.length < 3) parts.push(0)
  if (kind === 'major') return `${parts[0] + 1}.0.0`
  if (kind === 'minor') return `${parts[0]}.${parts[1] + 1}.0`
  return `${parts[0]}.${parts[1]}.${parts[2] + 1}`
}

/**
 * 执行本地构建命令并在失败时直接中止发版流程。
 */
function runStep(label, command, args) {
  console.log(`\n=== [${label}] ${command} ${args.join(' ')} ===`)
  const res = spawnSync(command, args, {
    cwd: root,
    stdio: 'inherit',
    shell: process.platform === 'win32'
  })
  if (res.status !== 0) {
    throw new Error(`[${label}] 失败，退出码: ${res.status}`)
  }
}

/**
 * 生成极简下载引导页 release/index.html，方便新用户在浏览器访问更新目录时一键下载最新完整安装包。
 */
function writeDownloadLandingPage(releaseDir, version, setupFileName) {
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>蚂蚁 Agent Plus 官方下载中心</title>
  <style>
    body { font-family: system-ui, -apple-system, "Microsoft YaHei", sans-serif; background: #f8fafc; color: #0f172a; display: flex; min-height: 100vh; margin: 0; align-items: center; justify-content: center; }
    .card { background: #fff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 36px 42px; max-width: 460px; text-align: center; box-shadow: 0 10px 25px rgba(15,23,42,0.06); }
    .ver { display: inline-block; background: #eff6ff; color: #1d4ed8; padding: 3px 10px; border-radius: 999px; font-size: 13px; font-weight: 600; margin-bottom: 14px; }
    .btn { display: inline-block; margin-top: 20px; background: #2563eb; color: #fff; text-decoration: none; padding: 12px 26px; border-radius: 8px; font-weight: 600; }
    .hint { color: #64748b; font-size: 13px; line-height: 1.6; margin-top: 16px; }
  </style>
</head>
<body>
  <div class="card">
    <div class="ver">最新版本 v${version}</div>
    <h2 style="margin: 0 0 8px;">🐜 蚂蚁 Agent Plus</h2>
    <div class="hint">内置离线 Python 3.12 文档标准化引擎与合肥青天评标质检体系，安装后自动监听服务器后续版本并支持后台增量升级。</div>
    <a class="btn" href="./${setupFileName}">下载 Windows 安装包 (.exe)</a>
  </div>
</body>
</html>
`
  writeFileSync(join(releaseDir, 'index.html'), html, 'utf8')
}

function main() {
  const args = parseArgs(process.argv.slice(2))
  const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'))

  // 1. 版本号更新与更新源地址固化
  if (args.version) {
    pkg.version = args.version.trim()
  } else if (args.bump) {
    pkg.version = bumpSemver(pkg.version, args.bump.trim())
  }

  if (args.url) {
    const normalizedUrl = args.url.trim().endsWith('/') ? args.url.trim() : `${args.url.trim()}/`
    pkg.build.publish = [{ provider: 'generic', url: normalizedUrl }]
  }

  writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n', 'utf8')
  const version = pkg.version
  const publishUrl = pkg.build?.publish?.[0]?.url || ''
  console.log(`[Release] 目标发版号: v${version} | 热更新服务器地址: ${publishUrl}`)

  // 2. 准备便携可重定位 Python + 编译前端主进程 + 生成 NSIS 安装包与 .blockmap 差分索引
  runStep('准备便携 Python', 'node', ['scripts/prepare-portable-python.mjs'])
  runStep('TypeScript 与前端构建', 'pnpm', ['build'])
  runStep('打包 NSIS 安装器与差分包', 'pnpm', ['exec', 'electron-builder', '--win', 'nsis', '--publish', 'never'])

  const releaseDir = join(root, 'release')
  const setupName = `mayi-agent-plus-setup-${version}.exe`
  const blockmapName = `${setupName}.blockmap`
  const latestYml = 'latest.yml'

  for (const required of [setupName, blockmapName, latestYml]) {
    if (!existsSync(join(releaseDir, required))) {
      throw new Error(`打包产物缺失: release/${required}`)
    }
  }
  writeDownloadLandingPage(releaseDir, version, setupName)
  console.log(`\n[Release] 本地打包完成！核心上传产物位于 release/ 目录：`)
  console.log(`  1. release/${setupName}`)
  console.log(`  2. release/${blockmapName}  (供旧客户端只下载几MB差异块)`)
  console.log(`  3. release/${latestYml}  (版本元数据索引，务必最后上传！)`)
  console.log(`  4. release/index.html  (浏览器网页下载引导页)`)

  // 3. 若传入 --scp root@server_ip:/var/www/mayi-updates，按原子顺序上传（先传大文件，最后覆盖 latest.yml）
  if (args.scp) {
    const target = args.scp.trim().replace(/\/+$/, '')
    runStep(
      '上传安装包与差分块（第1步：先传实体文件）',
      'scp',
      [
        join(releaseDir, setupName),
        join(releaseDir, blockmapName),
        join(releaseDir, 'index.html'),
        `${target}/`
      ]
    )
    runStep(
      '原子切换最新版本索引（第2步：最后覆盖 latest.yml）',
      'scp',
      [join(releaseDir, latestYml), `${target}/`]
    )
    console.log(`\n✅ v${version} 已成功原子发布至服务器 ${target}！所有在线客户端将在后台自动监听并增量下载升级。`)
  }
}

main()
