/** The share card: a rendered model on omni's dark background, with its name and key figures. */

export interface ShotInfo {
  title: string
  subtitle: string
  chips: string[]
  game: string
}

export const SHOT_W = 2400
export const SHOT_H = 1350
/** Where the model is drawn on the card: the right two thirds. */
export const SHOT_STAGE = { x: 672, y: 0, w: 1728, h: 1350 }

const FONT = "'Inter', 'Segoe UI', system-ui, sans-serif"
const MONO = "'JetBrains Mono', 'Cascadia Mono', Consolas, monospace"

function wrap(g: CanvasRenderingContext2D, text: string, width: number, lines: number): string[] {
  const out: string[] = []
  let line = ''
  for (const word of text.split(/\s+/)) {
    const next = line ? `${line} ${word}` : word
    if (g.measureText(next).width > width && line) {
      out.push(line)
      line = word
    } else line = next
  }
  if (line) out.push(line)
  if (out.length > lines) {
    out.length = lines
    let last = out[lines - 1]!
    while (last.length > 1 && g.measureText(`${last}…`).width > width) last = last.slice(0, -1)
    out[lines - 1] = `${last}…`
  }
  return out
}

function chip(g: CanvasRenderingContext2D, text: string, x: number, y: number) {
  g.font = `600 30px ${FONT}`
  const w = g.measureText(text).width + 44
  g.fillStyle = 'rgba(255,255,255,0.07)'
  g.strokeStyle = 'rgba(255,255,255,0.14)'
  g.lineWidth = 2
  g.beginPath()
  g.roundRect(x, y, w, 58, 29)
  g.fill()
  g.stroke()
  g.fillStyle = '#d9d6e6'
  g.textBaseline = 'middle'
  g.fillText(text, x + 22, y + 30)
  return w
}

/** The card as a PNG: `still` is the transparent render of the model (SHOT_STAGE.w x SHOT_STAGE.h). */
export function composeShot(still: HTMLCanvasElement, info: ShotInfo): Promise<Blob> {
  const c = document.createElement('canvas')
  c.width = SHOT_W
  c.height = SHOT_H
  const g = c.getContext('2d')!

  const bg = g.createLinearGradient(0, 0, 0, SHOT_H)
  bg.addColorStop(0, '#191425')
  bg.addColorStop(1, '#07070b')
  g.fillStyle = bg
  g.fillRect(0, 0, SHOT_W, SHOT_H)
  const glow = g.createRadialGradient(1500, 620, 0, 1500, 620, 1000)
  glow.addColorStop(0, 'rgba(139,92,246,0.30)')
  glow.addColorStop(1, 'rgba(139,92,246,0)')
  g.fillStyle = glow
  g.fillRect(0, 0, SHOT_W, SHOT_H)
  // floor light under the model
  const floor = g.createRadialGradient(1536, 1180, 0, 1536, 1180, 520)
  floor.addColorStop(0, 'rgba(167,139,250,0.22)')
  floor.addColorStop(1, 'rgba(167,139,250,0)')
  g.save()
  g.translate(0, 1180)
  g.scale(1, 0.22)
  g.translate(0, -1180)
  g.fillStyle = floor
  g.fillRect(900, 700, 1300, 900)
  g.restore()

  g.drawImage(still, SHOT_STAGE.x, SHOT_STAGE.y, SHOT_STAGE.w, SHOT_STAGE.h)

  // text column
  const x = 96
  g.textBaseline = 'alphabetic'
  g.fillStyle = '#a78bfa'
  g.font = `700 28px ${FONT}`
  g.fillText(info.game.toUpperCase(), x, 150)
  g.fillStyle = '#ffffff'
  g.font = `700 76px ${FONT}`
  const lines = wrap(g, info.title, 520, 3)
  lines.forEach((l, i) => g.fillText(l, x, 250 + i * 88))
  let y = 250 + lines.length * 88 + 12
  g.fillStyle = '#8f8aa3'
  g.font = `400 26px ${MONO}`
  for (const l of wrap(g, info.subtitle, 520, 2)) {
    g.fillText(l, x, y)
    y += 38
  }
  y += 36
  for (const t of info.chips) {
    chip(g, t, x, y)
    y += 74
  }

  // signature
  g.fillStyle = '#ffffff'
  g.font = `800 54px ${FONT}`
  g.fillText('omni', x, SHOT_H - 120)
  g.fillStyle = '#8f8aa3'
  g.font = `400 26px ${FONT}`
  g.fillText('github.com/WissemBad/omni', x, SHOT_H - 74)

  return new Promise((resolve, reject) =>
    c.toBlob((b) => (b ? resolve(b) : reject(new Error('image vide'))), 'image/png'),
  )
}
