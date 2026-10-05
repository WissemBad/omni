import * as THREE from 'three'
import type { OutputModel, Vec3 } from '~/utils/types'

/**
 * Debug layers drawn over a converted model. The API sends every position already in viewer space
 * (Y-up, metres), so nothing here converts coordinates.
 */
export type LayerName = 'skeleton' | 'hitboxes' | 'attachments' | 'bounds' | 'collision' | 'player'

const BOX_EDGES: [number, number][] = [
  [0, 1], [2, 3], [4, 5], [6, 7], // along z
  [0, 2], [1, 3], [4, 6], [5, 7], // along y
  [0, 4], [1, 5], [2, 6], [3, 7], // along x
]

function overlayMaterial<T extends THREE.Material>(m: T): T {
  m.depthTest = false
  m.transparent = true
  return m
}

function lines(points: number[], color: THREE.ColorRepresentation, opacity = 1): THREE.LineSegments {
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(points, 3))
  const l = new THREE.LineSegments(g, overlayMaterial(new THREE.LineBasicMaterial({ color, opacity })))
  l.renderOrder = 20
  return l
}

function boxLines(corners: Vec3[]): number[] {
  return BOX_EDGES.flatMap(([a, b]) => [...corners[a]!, ...corners[b]!])
}

function skeleton(m: OutputModel): THREE.Group {
  const g = new THREE.Group()
  const seg: number[] = []
  const pts: number[] = []
  m.model.bones.forEach((b) => {
    pts.push(...b.pos)
    const p = m.model.bones[b.parent]
    if (p) seg.push(...p.pos, ...b.pos)
  })
  g.add(lines(seg, 0x8b5cf6))
  const dots = new THREE.BufferGeometry()
  dots.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
  const points = new THREE.Points(dots, overlayMaterial(new THREE.PointsMaterial({ color: 0xc4b5fd, size: 6, sizeAttenuation: false })))
  points.renderOrder = 21
  g.add(points)
  return g
}

function hitboxes(m: OutputModel): THREE.Group {
  const g = new THREE.Group()
  const byGroup = new Map<number, number[]>()
  for (const h of m.model.hitboxes) {
    const arr = byGroup.get(h.group) ?? []
    arr.push(...boxLines(h.corners))
    byGroup.set(h.group, arr)
  }
  for (const [group, pts] of byGroup) g.add(lines(pts, new THREE.Color().setHSL(((group * 47) % 360) / 360, 0.85, 0.6), 0.9))
  return g
}

function attachments(m: OutputModel): THREE.Group {
  const g = new THREE.Group()
  const colors = [0xef4444, 0x22c55e, 0x3b82f6]
  for (const a of m.model.attachments) {
    a.axes.forEach((end, i) => g.add(lines([...a.origin, ...end], colors[i]!)))
  }
  return g
}

function bounds(m: OutputModel): THREE.Group {
  const g = new THREE.Group()
  g.add(lines(boxLines(m.model.hull_box as Vec3[]), 0xf59e0b, 0.9))
  g.add(lines(boxLines(m.model.view_box as Vec3[]), 0x94a3b8, 0.55))
  const e = m.model.eye
  g.add(lines([e[0] - 0.04, e[1], e[2], e[0] + 0.04, e[1], e[2], e[0], e[1] - 0.04, e[2], e[0], e[1] + 0.04, e[2], e[0], e[1], e[2] - 0.04, e[0], e[1], e[2] + 0.04], 0x38bdf8))
  return g
}

/** Source's standing player hull (32 x 72 x 32 units), placed beside the model to judge its scale. */
export function playerScale(box: THREE.Box3): THREE.Group {
  const g = new THREE.Group()
  const w = 32 / 39.37
  const h = 72 / 39.37
  const geo = new THREE.BoxGeometry(w, h, w)
  const fill = new THREE.Mesh(geo, overlayMaterial(new THREE.MeshBasicMaterial({ color: 0x38bdf8, opacity: 0.12 })))
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), overlayMaterial(new THREE.LineBasicMaterial({ color: 0x38bdf8, opacity: 0.9 })))
  fill.renderOrder = edges.renderOrder = 19
  g.add(fill, edges)
  g.position.set(box.max.x + w * 0.8 + 0.1, box.min.y + h / 2, (box.min.z + box.max.z) / 2)
  return g
}

export function collisionLayer(pieces: number[][]): THREE.Group {
  const g = new THREE.Group()
  const palette = [0x22c55e, 0x10b981, 0x84cc16, 0x14b8a6, 0xa3e635]
  pieces.forEach((flat, i) => {
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(flat, 3))
    const color = palette[i % palette.length]!
    const fill = new THREE.Mesh(geo, overlayMaterial(new THREE.MeshBasicMaterial({ color, opacity: 0.12, side: THREE.DoubleSide })))
    const wire = new THREE.LineSegments(new THREE.WireframeGeometry(geo), overlayMaterial(new THREE.LineBasicMaterial({ color, opacity: 0.85 })))
    fill.renderOrder = 17
    wire.renderOrder = 18
    g.add(fill, wire)
  })
  return g
}

/** One group per layer; the page toggles `.visible`. Collision and player scale come from elsewhere. */
export function buildLayers(m: OutputModel): Partial<Record<LayerName, THREE.Group>> {
  return {
    skeleton: skeleton(m),
    hitboxes: hitboxes(m),
    attachments: attachments(m),
    bounds: bounds(m),
  }
}

export function disposeGroup(g: THREE.Object3D) {
  g.traverse((o) => {
    const x = o as THREE.Mesh
    x.geometry?.dispose()
    const mat = x.material as THREE.Material | THREE.Material[] | undefined
    for (const m of Array.isArray(mat) ? mat : mat ? [mat] : []) m.dispose()
  })
  g.removeFromParent()
}

/** A marker that follows the selected bone. */
export function boneMarker(): THREE.Mesh {
  const m = new THREE.Mesh(new THREE.SphereGeometry(0.018, 16, 12), overlayMaterial(new THREE.MeshBasicMaterial({ color: 0xfacc15 })))
  m.renderOrder = 30
  m.visible = false
  return m
}
