<script setup lang="ts">
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'
import { type GLTF, GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import type { Clip } from '~/utils/types'

type Shading = 'textured' | 'clay' | 'normals' | 'checker'

/**
 * 3D viewport shared by the props and characters workbenches. Loads a GLB, frames it, and exposes the loaded
 * scene so a page can switch parts/materials (bodygroups, skins) and call `sync()` afterwards.
 * Rendering is on demand: an idle viewport costs nothing.
 */
const props = defineProps<{
  url?: string
  /** Message shown over the viewport while the page prepares the model. */
  busy?: string
  /** `character`: framed on a standing figure from the front instead of an object at 3/4. */
  mode?: 'object' | 'character'
  /** A second model shown on the left of a draggable divider (the original beside the converted output). */
  compareUrl?: string
  compare?: boolean
  /** How surfaces are drawn: with their textures, as plain clay, as normals or with a UV checker. */
  shading?: Shading
}>()

const split = defineModel<number>('split', { default: 0.5 })

const emit = defineEmits<{
  loaded: [gltf: GLTF, size: THREE.Vector3]
  failed: [message: string]
}>()

const host = useTemplateRef<HTMLElement>('host')
const root = shallowRef<THREE.Object3D | null>(null)
const rootB = shallowRef<THREE.Object3D | null>(null)
const parser = shallowRef<GLTF['parser'] | null>(null)
const size = ref<[number, number, number] | null>(null)
const loading = ref(false)
const error = ref('')
const wire = ref(false)
const grid = ref(true)
const spin = ref(false)

// ---- skeleton animation (the clip drives the bones of the skinned meshes)
let skin: { bones: THREE.Bone[]; rest: { p: THREE.Vector3; q: THREE.Quaternion }[] } | null = null
let clip: Clip | null = null
let lastTick = 0
const qa = new THREE.Quaternion()
const qb = new THREE.Quaternion()
const hasSkeleton = ref(false)
const playing = ref(true)
const speed = ref(1)
const loopAnim = ref(true) // looping is on by default, whatever the sequence itself says
const clipTime = ref(0)
const clipLength = ref(0)

function findSkeleton(scene3: THREE.Object3D) {
  skin = null
  scene3.traverse((o) => {
    const mesh = o as THREE.SkinnedMesh
    if (!mesh.isSkinnedMesh) return
    mesh.frustumCulled = false // the rest-pose bounds would cull a moving model
    if (!skin)
      skin = {
        bones: mesh.skeleton.bones,
        rest: mesh.skeleton.bones.map((b) => ({ p: b.position.clone(), q: b.quaternion.clone() })),
      }
  })
  hasSkeleton.value = !!skin
}

function restPose() {
  if (!skin) return
  skin.bones.forEach((b, i) => {
    b.position.copy(skin!.rest[i]!.p)
    b.quaternion.copy(skin!.rest[i]!.q)
  })
}

function applyClip(t: number) {
  if (!clip || !skin) return
  const n = clip.frames
  let f = t * clip.fps
  let i0: number
  let i1: number
  if (loopAnim.value) {
    f = ((f % n) + n) % n
    i0 = Math.floor(f)
    i1 = (i0 + 1) % n
  } else {
    f = Math.min(Math.max(f, 0), n - 1)
    i0 = Math.floor(f)
    i1 = Math.min(i0 + 1, n - 1)
  }
  const a = f - i0
  const B = clip.bones
  const d = clip.data
  for (let b = 0; b < Math.min(B, skin.bones.length); b++) {
    const o0 = (i0 * B + b) * 7
    const o1 = (i1 * B + b) * 7
    const bone = skin.bones[b]!
    bone.position.set(
      d[o0]! + (d[o1]! - d[o0]!) * a,
      d[o0 + 1]! + (d[o1 + 1]! - d[o0 + 1]!) * a,
      d[o0 + 2]! + (d[o1 + 2]! - d[o0 + 2]!) * a,
    )
    qa.set(d[o0 + 3]!, d[o0 + 4]!, d[o0 + 5]!, d[o0 + 6]!)
    qb.set(d[o1 + 3]!, d[o1 + 4]!, d[o1 + 5]!, d[o1 + 6]!)
    bone.quaternion.copy(qa).slerp(qb, a)
  }
}

/** Plays a clip on the loaded skeleton, or shows the rest pose again with `null`. */
function setAnimation(c: Clip | null, autoplay = true) {
  clip = c
  clipTime.value = 0
  clipLength.value = c ? c.frames / c.fps : 0
  if (c) {
    playing.value = autoplay
    applyClip(0)
  } else restPose()
  requestRender()
}

function seek(t: number) {
  clipTime.value = Math.min(Math.max(t, 0), clipLength.value)
  applyClip(clipTime.value)
  requestRender()
}

function tickAnimation(now: number) {
  const dt = lastTick ? Math.min((now - lastTick) / 1000, 0.1) : 0
  lastTick = now
  if (!clip || !playing.value || !clipLength.value) return
  let t = clipTime.value + dt * speed.value
  if (loopAnim.value) t %= clipLength.value
  else if (t >= clipLength.value) {
    t = clipLength.value
    playing.value = false
  }
  clipTime.value = t
  applyClip(t)
  dirty = true
}

let renderer: THREE.WebGLRenderer
let scene: THREE.Scene
let camera: THREE.PerspectiveCamera
let controls: OrbitControls
let gridHelper: THREE.GridHelper
let checkerTexture: THREE.CanvasTexture | undefined
let envTarget: THREE.WebGLRenderTarget | undefined
let observer: ResizeObserver
let dirty = true
let token = 0
let tokenB = 0
const overrides: Partial<Record<Shading, THREE.Material>> = {}
const box = new THREE.Box3()
const center = new THREE.Vector3()
const extent = new THREE.Vector3()

function requestRender() {
  dirty = true
}

function disposeObject(obj: THREE.Object3D | null) {
  if (!obj) return
  scene.remove(obj)
  const textures = new Set<THREE.Texture>()
  obj.traverse((o) => {
    const mesh = o as THREE.Mesh
    if (!mesh.isMesh) return
    mesh.geometry.dispose()
    for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      for (const value of Object.values(m))
        if ((value as THREE.Texture)?.isTexture) textures.add(value as THREE.Texture)
      m.dispose()
    }
  })
  for (const t of textures) t.dispose()
}

/** Free the GPU memory of a glTF that was loaded but is no longer wanted (a newer model replaced it). */
function discard(scene3: THREE.Object3D) {
  const textures = new Set<THREE.Texture>()
  scene3.traverse((o) => {
    const mesh = o as THREE.Mesh
    if (!mesh.isMesh) return
    mesh.geometry.dispose()
    for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      for (const value of Object.values(m))
        if ((value as THREE.Texture)?.isTexture) textures.add(value as THREE.Texture)
      m.dispose()
    }
  })
  for (const t of textures) t.dispose()
}

function disposeRoot() {
  disposeObject(root.value)
  root.value = null
}

function disposeRootB() {
  disposeObject(rootB.value)
  rootB.value = null
}

function view(dir: [number, number, number]) {
  const fov = (camera.fov * Math.PI) / 180
  const big = Math.max(extent.x, extent.y, extent.z) || 1
  const fit = props.mode === 'character' ? Math.max(extent.y, extent.x * 0.9) : big
  const dist =
    (fit / (2 * Math.tan(fov / 2))) * (props.mode === 'character' ? 1.25 : 1.45) + big * 0.35
  const d = new THREE.Vector3(...dir).normalize()
  controls.target.copy(center)
  camera.position.copy(center).addScaledVector(d, dist)
  camera.near = dist / 200
  camera.far = dist * 200
  camera.updateProjectionMatrix()
  controls.minDistance = dist * 0.05
  controls.maxDistance = dist * 8
  controls.update()
  requestRender()
}

const DEFAULT_DIR = (): [number, number, number] =>
  props.mode === 'character' ? [0.35, 0.12, 1] : [1, 0.65, 1.25]
const frame = () => view(DEFAULT_DIR())

function applyWire() {
  for (const r of [root.value, rootB.value]) {
    r?.traverse((o) => {
      const mesh = o as THREE.Mesh
      if (!mesh.isMesh) return
      for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
        ;(m as THREE.MeshStandardMaterial).wireframe = wire.value
      }
    })
  }
}

/** Call after changing visibility or materials of the loaded scene. */
function sync() {
  applyWire()
  requestRender()
}

async function load(url?: string) {
  const mine = ++token
  error.value = ''
  if (!url) {
    disposeRoot()
    size.value = null
    requestRender()
    return
  }
  loading.value = true
  try {
    const gltf = await new GLTFLoader().loadAsync(url)
    if (mine !== token) {
      discard(gltf.scene)
      return
    }
    disposeRoot()
    clip = null
    root.value = gltf.scene
    parser.value = gltf.parser
    findSkeleton(gltf.scene)
    scene.add(gltf.scene)
    box.setFromObject(gltf.scene)
    box.getCenter(center)
    box.getSize(extent)
    // sit on the grid
    gridHelper.position.y = box.min.y
    size.value = [extent.x, extent.y, extent.z]
    applyWire()
    frame()
    emit('loaded', gltf, extent.clone())
  } catch (e) {
    if (mine !== token) return
    error.value = e instanceof Error ? e.message : String(e)
    emit('failed', error.value)
  } finally {
    if (mine === token) loading.value = false
  }
}

async function loadB(url?: string) {
  const mine = ++tokenB
  if (!url) {
    disposeRootB()
    requestRender()
    return
  }
  try {
    const gltf = await new GLTFLoader().loadAsync(url)
    if (mine !== tokenB) {
      discard(gltf.scene)
      return
    }
    disposeRootB()
    rootB.value = gltf.scene
    scene.add(gltf.scene)
    gltf.scene.visible = false
    applyWire()
    requestRender()
  } catch {
    if (mine === tokenB) disposeRootB()
  }
}

function shadingMaterial(kind: Shading): THREE.Material | null {
  if (kind === 'textured') return null
  if (!overrides[kind]) {
    if (kind === 'clay')
      overrides.clay = new THREE.MeshStandardMaterial({
        color: 0xc8ccd6,
        roughness: 0.85,
        metalness: 0,
        side: THREE.DoubleSide,
      })
    else if (kind === 'normals')
      overrides.normals = new THREE.MeshNormalMaterial({ side: THREE.DoubleSide })
    else {
      const c = document.createElement('canvas')
      c.width = c.height = 256
      const g = c.getContext('2d')!
      for (let y = 0; y < 8; y++) {
        for (let x = 0; x < 8; x++) {
          g.fillStyle = (x + y) % 2 ? '#e9e9ef' : '#5b5b73'
          g.fillRect(x * 32, y * 32, 32, 32)
        }
      }
      g.fillStyle = '#8b5cf6'
      g.font = 'bold 20px sans-serif'
      for (let i = 0; i < 8; i++) g.fillText(String(i), i * 32 + 10, 22)
      const t = new THREE.CanvasTexture(c)
      checkerTexture = t
      t.colorSpace = THREE.SRGBColorSpace
      t.wrapS = t.wrapT = THREE.RepeatWrapping
      t.anisotropy = 8
      overrides.checker = new THREE.MeshStandardMaterial({
        map: t,
        roughness: 0.9,
        metalness: 0,
        side: THREE.DoubleSide,
      })
    }
  }
  const m = overrides[kind]!
  ;(m as THREE.MeshStandardMaterial).wireframe = wire.value
  return m
}

/** Draws the scene, swapping every model mesh's material for the active shading mode during the pass only. */
function renderPass() {
  const swap = shadingMaterial(props.shading ?? 'textured')
  const saved: [THREE.Mesh, THREE.Material | THREE.Material[]][] = []
  if (swap) {
    for (const r of [root.value, rootB.value]) {
      r?.traverse((o) => {
        const mesh = o as THREE.Mesh
        if (!mesh.isMesh) return
        saved.push([mesh, mesh.material])
        mesh.material = swap
      })
    }
  }
  renderer.render(scene, camera)
  for (const [mesh, mat] of saved) mesh.material = mat
}

function renderFrame() {
  const a = root.value
  const b = rootB.value
  if (!(props.compare && a && b)) {
    if (a) a.visible = true
    if (b) b.visible = false
    renderPass()
    return
  }
  // left of the divider: the original (b), right: the output (a); one camera, two scissored passes
  const w = host.value!.clientWidth
  const h = host.value!.clientHeight
  const x = Math.round(w * split.value)
  renderer.setScissorTest(false)
  renderer.clear()
  renderer.autoClear = false
  renderer.setScissorTest(true)
  a.visible = false
  b.visible = true
  renderer.setScissor(0, 0, x, h)
  renderPass()
  a.visible = true
  b.visible = false
  renderer.setScissor(x, 0, w - x, h)
  renderPass()
  renderer.setScissorTest(false)
  renderer.autoClear = true
}

function resize() {
  const el = host.value
  if (!el?.clientWidth) return
  renderer.setSize(el.clientWidth, el.clientHeight, false)
  camera.aspect = el.clientWidth / el.clientHeight
  camera.updateProjectionMatrix()
  requestRender()
}

onMounted(() => {
  const el = host.value as HTMLElement
  renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: true,
    powerPreference: 'high-performance',
  })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.domElement.className = 'size-full block'
  el.appendChild(renderer.domElement)

  scene = new THREE.Scene()
  const pmrem = new THREE.PMREMGenerator(renderer)
  envTarget = pmrem.fromScene(new RoomEnvironment(), 0.04)
  scene.environment = envTarget.texture
  scene.environmentIntensity = 0.75
  pmrem.dispose()
  scene.add(new THREE.HemisphereLight(0xffffff, 0x555566, 0.7))
  const sun = new THREE.DirectionalLight(0xffffff, 1.7)
  sun.position.set(3, 6, 4)
  scene.add(sun)

  gridHelper = new THREE.GridHelper(20, 40, 0x8b8b99, 0x8b8b99)
  const gm = gridHelper.material as THREE.LineBasicMaterial
  gm.transparent = true
  gm.opacity = 0.22
  scene.add(gridHelper)

  camera = new THREE.PerspectiveCamera(40, 1, 0.01, 1000)
  camera.position.set(2, 1.5, 3)
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.12
  controls.autoRotateSpeed = 1.4
  controls.addEventListener('change', requestRender)

  observer = new ResizeObserver(resize)
  observer.observe(el)
  resize()

  renderer.setAnimationLoop((now: number) => {
    tickAnimation(now)
    controls.autoRotate = spin.value
    const moved = controls.update()
    if (moved || dirty) {
      dirty = false
      renderFrame()
    }
  })
  load(props.url)
  loadB(props.compareUrl)
})

onBeforeUnmount(() => {
  token++
  observer?.disconnect()
  renderer?.setAnimationLoop(null)
  tokenB++
  disposeRoot()
  disposeRootB()
  for (const m of Object.values(overrides)) m?.dispose()
  checkerTexture?.dispose()
  envTarget?.dispose()
  gridHelper?.geometry.dispose()
  ;(gridHelper?.material as THREE.Material | undefined)?.dispose()
  controls?.dispose()
  renderer?.dispose()
  renderer?.forceContextLoss() // WebView2 allows ~16 WebGL contexts: a page revisited often would run out
  renderer?.domElement.remove()
})

watch(() => props.url, load)
watch(() => props.compareUrl, loadB)
watch([() => props.compare, () => props.shading, split], requestRender)
watch(wire, sync)
watch(grid, (v) => {
  if (gridHelper) gridHelper.visible = v
  requestRender()
})
watch(
  () => props.mode,
  () => root.value && frame(),
)

/**
 * A still of the loaded model at an arbitrary size, on a transparent background: the camera keeps its direction but
 * is refitted to the new aspect, helpers (grid, `hide`) are left out, then everything is put back. The returned
 * canvas is a copy: it stays valid after the viewport is redrawn.
 */
function renderStill(
  w: number,
  h: number,
  hide: (THREE.Object3D | null | undefined)[] = [],
): HTMLCanvasElement | null {
  const r = root.value
  if (!r || !renderer) return null
  const saved = {
    pos: camera.position.clone(),
    target: controls.target.clone(),
    aspect: camera.aspect,
    ratio: renderer.getPixelRatio(),
  }
  const hidden = [gridHelper, ...hide].filter((o): o is THREE.Object3D => !!o && o.visible)
  const bVisible = rootB.value?.visible
  hidden.forEach((o) => (o.visible = false))
  if (rootB.value) rootB.value.visible = false
  r.visible = true
  try {
    const b = new THREE.Box3().setFromObject(r)
    const sphere = b.getBoundingSphere(new THREE.Sphere())
    const dir = camera.position.clone().sub(controls.target).normalize()
    camera.aspect = w / h
    const vf = THREE.MathUtils.degToRad(camera.fov)
    const hf = 2 * Math.atan(Math.tan(vf / 2) * camera.aspect)
    const dist = (sphere.radius / Math.sin(Math.min(vf, hf) / 2)) * 1.02
    camera.position.copy(sphere.center).addScaledVector(dir, dist)
    camera.lookAt(sphere.center)
    camera.updateProjectionMatrix()
    renderer.setPixelRatio(1)
    renderer.setSize(w, h, false)
    renderPass()
    const out = document.createElement('canvas')
    out.width = w
    out.height = h
    out.getContext('2d')!.drawImage(renderer.domElement, 0, 0)
    return out
  } finally {
    hidden.forEach((o) => (o.visible = true))
    if (rootB.value && bVisible !== undefined) rootB.value.visible = bVisible
    camera.position.copy(saved.pos)
    controls.target.copy(saved.target)
    camera.aspect = saved.aspect
    camera.updateProjectionMatrix()
    renderer.setPixelRatio(saved.ratio)
    resize()
  }
}

defineExpose({
  root,
  parser,
  size,
  sync,
  frame,
  view,
  getScene: () => scene,
  getBox: () => box.clone(),
  setAnimation,
  renderStill,
  seek,
  hasSkeleton,
  playing,
  speed,
  loopAnim,
  clipTime,
  clipLength,
})

let dragging = false
function dragStart(e: PointerEvent) {
  dragging = true
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}
function dragMove(e: PointerEvent) {
  if (!dragging || !host.value) return
  if (!(e.buttons & 1)) {
    dragging = false
    return
  }
  const r = host.value.getBoundingClientRect()
  split.value = Math.min(0.97, Math.max(0.03, (e.clientX - r.left) / r.width))
}

const views = [
  { label: 'Face', dir: [0, 0.05, 1] },
  { label: 'Profil', dir: [1, 0.05, 0] },
  { label: 'Dos', dir: [0, 0.05, -1] },
  { label: 'Dessus', dir: [0, 1, 0.001] },
] as const
</script>

<template>
  <WAmbient class="size-full overflow-hidden" :intensity="0.22">
    <div ref="host" class="absolute inset-0" />

    <div class="pointer-events-none absolute inset-x-3 top-3 z-10 flex items-start justify-between gap-3">
      <div class="pointer-events-auto min-w-0"><slot name="overlay" /></div>
      <div class="pointer-events-auto flex flex-col items-end gap-2"><slot name="overlay-end" /></div>
    </div>

    <div class="pointer-events-none absolute inset-x-0 bottom-3 z-10 flex justify-center px-3">
      <div class="wi-glass wi-glass--pill pointer-events-auto flex items-center gap-0.5 p-1">
        <UTooltip text="Recadrer">
          <UButton icon="i-ri-focus-3-line" color="neutral" variant="ghost" size="sm" aria-label="Recadrer" @click="frame" />
        </UTooltip>
        <span class="mx-0.5 h-4 w-px bg-default" />
        <UButton
          v-for="v in views"
          :key="v.label"
          :label="v.label"
          color="neutral"
          variant="ghost"
          size="sm"
          class="max-sm:hidden"
          @click="view([...v.dir] as [number, number, number])"
        />
        <span class="mx-0.5 h-4 w-px bg-default max-sm:hidden" />
        <UTooltip text="Rotation automatique">
          <UButton
            icon="i-ri-refresh-line"
            :color="spin ? 'primary' : 'neutral'"
            :variant="spin ? 'soft' : 'ghost'"
            size="sm"
            aria-label="Rotation automatique"
            @click="spin = !spin"
          />
        </UTooltip>
        <UTooltip text="Filaire">
          <UButton
            icon="i-ri-shape-line"
            :color="wire ? 'primary' : 'neutral'"
            :variant="wire ? 'soft' : 'ghost'"
            size="sm"
            aria-label="Filaire"
            @click="wire = !wire"
          />
        </UTooltip>
        <UTooltip text="Grille">
          <UButton
            icon="i-ri-layout-grid-line"
            :color="grid ? 'primary' : 'neutral'"
            :variant="grid ? 'soft' : 'ghost'"
            size="sm"
            aria-label="Grille"
            @click="grid = !grid"
          />
        </UTooltip>
      </div>
    </div>

    <template v-if="compare && compareUrl && rootB && root">
      <div class="pointer-events-none absolute inset-y-0 z-10" :style="{ left: `${split * 100}%` }">
        <div class="absolute inset-y-0 -ml-px w-0.5 bg-primary/80" />
        <div
          role="slider"
          aria-label="Comparer la source et la sortie"
          aria-orientation="horizontal"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuenow="Math.round(split * 100)"
          tabindex="0"
          class="pointer-events-auto absolute top-1/2 -ml-4 flex size-8 -translate-y-1/2 cursor-ew-resize touch-none items-center justify-center rounded-full border border-default bg-default text-primary shadow-lg"
          @pointerdown="dragStart"
          @pointermove="dragMove"
          @keydown.left.prevent="split = Math.max(0.03, split - 0.02)"
          @keydown.right.prevent="split = Math.min(0.97, split + 0.02)"
        >
          <UIcon name="i-ri-arrow-left-right-line" class="size-4" />
        </div>
      </div>
      <UBadge class="pointer-events-none absolute bottom-16 left-3 z-10" label="Original du jeu" color="neutral" variant="subtle" />
      <UBadge class="pointer-events-none absolute bottom-16 right-3 z-10" label="Converti" color="primary" variant="subtle" />
    </template>

    <Transition
      enter-active-class="transition-opacity duration-200"
      leave-active-class="transition-opacity duration-150"
      enter-from-class="opacity-0"
      leave-to-class="opacity-0"
    >
      <div
        v-if="loading || busy"
        class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-default/55 backdrop-blur-[2px]"
      >
        <UIcon name="i-ri-loader-4-line" class="size-7 animate-spin text-primary" />
        <p class="max-w-xs text-center text-sm text-toned">{{ busy || 'Chargement du modèle…' }}</p>
      </div>
    </Transition>

    <div v-if="error" class="absolute inset-0 z-20 flex items-center justify-center p-6">
      <UEmpty icon="i-ri-error-warning-line" title="Aperçu impossible" :description="error" />
    </div>
    <div v-else-if="!url && !busy" class="absolute inset-0 z-0 flex items-center justify-center p-6">
      <slot name="empty">
        <UEmpty icon="i-ri-box-3-line" title="Aucun aperçu" description="Sélectionne un élément dans la liste." />
      </slot>
    </div>
  </WAmbient>
</template>
