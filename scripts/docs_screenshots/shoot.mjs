// Take the screenshots in a plan with headless Chrome, driven over CDP.
//
//   node shoot.mjs PLAN.json BASE_URL SIGN_IN_PATH OUT_DIR [ONLY]
//
// Each shot opens `path`, runs its `steps` and saves OUT_DIR/<name>.png. `selector` crops to an element, or to the box
// around several. A step is JavaScript evaluated in the page and awaited,
// {"click": selector, "text": optional text it must contain}, or
// {"fill": selector, "value": text}. `clip` crops to [x, y, width, height]. Set CHROME to the
// browser binary when it is not in the usual place. Needs Node 22 or later.
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'

const [planPath, baseUrl, signInPath, outDir, only] = process.argv.slice(2)
const plan = JSON.parse(readFileSync(planPath, 'utf8'))
const candidates = {
  win32: ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'],
  darwin: ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'],
  linux: ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'],
}[process.platform] ?? []
const chromePath = process.env.CHROME ?? candidates.find((path) => existsSync(path))
if (!chromePath) throw new Error('Chrome not found. Set CHROME to its binary.')

const port = 9333
const chrome = spawn(
  chromePath,
  [
    '--headless=new',
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${mkdtempSync(join(tmpdir(), 'docs-shots-'))}`,
    '--hide-scrollbars',
    '--no-first-run',
    'about:blank',
  ],
  { stdio: 'ignore' },
)
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

let targets = []
for (let i = 0; i < 50 && !targets.some((t) => t.type === 'page'); i++) {
  await sleep(200)
  targets = await fetch(`http://127.0.0.1:${port}/json`)
    .then((r) => r.json())
    .catch(() => [])
}
const socket = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl)
await new Promise((resolve) => socket.addEventListener('open', resolve))
let lastId = 0
const pending = new Map()
socket.addEventListener('message', (e) => {
  const message = JSON.parse(e.data)
  pending.get(message.id)?.(message)
  pending.delete(message.id)
})
const send = (method, params = {}) =>
  new Promise((resolve) => {
    const id = ++lastId
    pending.set(id, resolve)
    socket.send(JSON.stringify({ id, method, params }))
  })
let failed = false
const evaluate = async (expression) => {
  const { result } = await send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  })
  if (result.exceptionDetails) {
    failed = true
    console.error('Step failed:', expression.slice(0, 100))
    console.error(' ', result.exceptionDetails.exception?.description?.split('\n')[0])
  }
  return result.result?.value
}
const script = (step) => {
  if (typeof step === 'string') return step
  const find = `[...document.querySelectorAll(${JSON.stringify(step.click ?? step.fill)})]
    .find((el) => el.textContent.includes(${JSON.stringify(step.text ?? '')}))`
  const missing = `throw new Error('No element for ${JSON.stringify(step).replaceAll("'", '')}')`
  if (step.click) return `(() => { const el = ${find}; if (!el) ${missing}; el.click() })()`
  return `(() => { const el = ${find}; if (!el) ${missing}
    el.focus(); el.value = ${JSON.stringify(step.value)}
    el.dispatchEvent(new Event('input', { bubbles: true })) })()`
}
const boxOf = (selectors) =>
  evaluate(`(() => {
    const boxes = ${JSON.stringify([selectors].flat())}.map((s) => {
      const el = document.querySelector(s)
      if (!el) throw new Error('No element for ' + s)
      return el.getBoundingClientRect()
    })
    const x = Math.min(...boxes.map((b) => b.left)), y = Math.min(...boxes.map((b) => b.top))
    const right = Math.max(...boxes.map((b) => b.right)), bottom = Math.max(...boxes.map((b) => b.bottom))
    return { x: x + scrollX, y: y + scrollY, width: right - x, height: bottom - y }
  })()`)

await send('Page.enable')
await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false })
await send('Page.navigate', { url: baseUrl + signInPath })
await sleep(2500)

for (const shot of plan) {
  if (only && !shot.name.includes(only)) continue
  await send('Emulation.setDeviceMetricsOverride', {
    width: shot.width,
    height: shot.height,
    deviceScaleFactor: shot.scale ?? 1.5,
    mobile: shot.width < 600,
  })
  await send('Page.navigate', { url: baseUrl + shot.path })
  await sleep(shot.wait ?? 2500)
  // Dialogs animate in. Screenshots should catch them settled. A shot with
  // "animate": true keeps animations, for overlays that need them to open.
  if (!shot.animate) await evaluate(`document.head.insertAdjacentHTML('beforeend',
    '<style>*, *::before, *::after { transition: none !important; animation: none !important }</style>')`)
  for (const step of shot.steps ?? []) {
    await evaluate(script(step))
    await sleep(500)
  }
  let clip
  if (shot.selector) clip = { ...(await boxOf(shot.selector)), scale: 1 }
  if (shot.clip) {
    const [x, y, width, height] = shot.clip
    clip = { x, y, width, height, scale: 1 }
  }
  if (shot.selector && !clip.width) continue
  const { result } = await send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: Boolean(clip),
    ...(clip ? { clip } : {}),
  })
  const file = join(outDir, `${shot.name}.png`)
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(file, Buffer.from(result.data, 'base64'))
  console.log('Saved', shot.name)
}
socket.close()
chrome.kill()
process.exit(failed ? 1 : 0)
