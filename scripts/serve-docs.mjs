import { exec } from 'node:child_process'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { createServer } from 'node:http'
import { dirname, extname, join, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const docsDir = join(root, 'docs')
const port = Number(process.env.DOCS_PORT || 3080)

const MIME_MAP = {
  '.html': 'text/html; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml'
}

/**
 * 在本地启动轻量静态服务器承载 Docsify 架构文档站，并自动唤醒系统默认浏览器打开。
 */
const server = createServer((req, res) => {
  const rawUrl = decodeURIComponent((req.url || '/').split('?')[0])
  const relPath = rawUrl === '/' ? 'index.html' : rawUrl.replace(/^\/+/, '')
  const safeTarget = resolve(docsDir, normalize(relPath))

  if (!safeTarget.startsWith(docsDir) || !existsSync(safeTarget)) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('Not Found')
    return
  }

  const finalFile = statSync(safeTarget).isDirectory() ? join(safeTarget, 'index.html') : safeTarget
  if (!existsSync(finalFile)) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('Not Found')
    return
  }

  const ext = extname(finalFile).toLowerCase()
  res.writeHead(200, {
    'Content-Type': MIME_MAP[ext] || 'application/octet-stream',
    'Cache-Control': 'no-cache'
  })
  res.end(readFileSync(finalFile))
})

server.listen(port, '127.0.0.1', () => {
  const url = `http://127.0.0.1:${port}`
  console.log(`[Docs] 本地架构文档站已启动: ${url}`)
  if (process.platform === 'win32') {
    exec(`start "" "${url}"`)
  } else if (process.platform === 'darwin') {
    exec(`open "${url}"`)
  }
})
