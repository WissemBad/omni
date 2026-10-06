export type Capability = 'props' | 'characters' | 'textures' | 'sounds'

export interface SourceInfo {
  id: string
  title: string
  description?: string
  capabilities: Capability[]
  /** browsing works (built-in: first-run setup done; library game: prepared) */
  ready?: boolean
  engine?: string
  deployed: boolean
}

export interface SourceDetails extends SourceInfo {
  assets: number
  link: string
}

export interface Category {
  cat: string
  n: number
}

export interface Prop {
  key: string
  rel: string
  cat: string
  size: number
  skinned: number
  linked: number
  converted: boolean
}

export interface Page<T> {
  total: number
  items: T[]
}

export interface Facet {
  value: string
  n: number
}

export interface Character {
  id: string
  title: string
  kind: string
  mission: string
  role: string
  body: string
  count: number
  built?: boolean
  /** reward outfit (name or the game's own variation list) */
  reward?: boolean
  /** the game's name for this outfit (its variation enumeration), when it has one */
  variation?: string
}

export interface CharacterPage extends Page<Character> {
  facets: { mission: Facet[]; role: Facet[]; body: Facet[]; kind: Facet[] }
}

export interface BodyGroup {
  name: string
  options: string[]
}

export interface Preset {
  name: string
  variant: number
  skin: number
  bodygroups: Record<string, number>
}

export interface CharacterPreview {
  status: 'ready'
  built?: boolean
  model: string
  family: string
  lod: number
  groups: BodyGroup[]
  base: string[]
  skins: number
  presets: Preset[]
  skin_materials: Record<string, number>[]
  variants: { v: number; name: string; key: string }[]
  materials?: SourceMaterial[]
}

export interface Sound {
  file: string
  title?: string
  album?: string
  genre?: string
  language?: string
  sources?: string[]
  seconds: string
  channels: string
  rate: string
  codec: string
  named: boolean
  aliases: string[]
  alias_count: number
}

export interface SoundPage extends Page<Sound> {
  tops: Facet[]
  langs: Facet[]
}

export interface SoundStatus {
  exported: boolean
  path: string
  count: number
  summary: Record<string, unknown> | null
  running: string | null
  tagged?: boolean
}

export interface JobResult {
  key: string
  status: string
  model: string
  errors: string[]
  notes: string[]
  seconds: number
}

export type JobPhase = 'queued' | 'running' | 'done' | 'error' | 'cancelled' | 'interrupted'

export interface Job {
  id: string
  kind: 'props' | 'character' | 'sounds' | 'textures' | 'maintenance' | 'setup'
  label: string
  source: string
  phase: JobPhase
  cancellable?: boolean
  cancelling?: boolean
  heavy?: boolean
  /** Place in the queue (1 = next) while the job is waiting. */
  position?: number
  /** Current step of a multi-step job (own counter when `total` > 0, otherwise the job's). */
  stage?: { label: string; index: number; of: number; done: number; total: number } | null
  /** Seconds left in what the job is doing, as of `eta_at` (server epoch seconds); null while unknown. */
  eta?: number | null
  eta_at?: number
  done: number
  total: number
  error: string
  started: number
  ended: number | null
  last?: string
  failed?: number
  /** Results per status (OK, PARTIAL, FAILED, SKIPPED). */
  counts?: Record<string, number>
  model?: string
  models?: number
  /** A retry can replay this job (props, playermodels, sounds). */
  has_request?: boolean
  results?: JobResult[]
  results_total?: number
  log?: string[]
  summary?: Record<string, unknown> | null
}

/** Failed results of a job grouped by cause (GET /jobs/{id}/report). */
export interface JobCause {
  cause: string
  count: number
  examples: string[]
  sample: string
}

export interface PreviewBuilding {
  status: 'building'
  elapsed: number
  stage?: string
}

// ---- output inspector (omni/ui/output.py)
export interface OutputItem {
  path: string
  name: string
  folder: string
  kind: 'prop' | 'character' | 'model'
  mtime: number
  bytes: number
  title: string
}

export interface ViewerRoot {
  id: string
  label: string
  path: string
  external: boolean
  exists: boolean
  models: number | null
}

export interface OutputPage extends Page<OutputItem> {
  kinds: { prop: number; character: number; model: number }
  scanned: number
}

export interface OutputStats {
  models: number
  props: number
  characters: number
  bytes: number
  latest: number
  materials?: number
  textures?: number
  texture_bytes?: number
  path: string
  external: boolean
}

export interface OutputTexture {
  param: string
  label: string
  name: string
  path: string
  exists: boolean
  stock: boolean
  width?: number
  height?: number
  format?: string
  mips?: number
  frames?: number
  version?: string
  flags?: string[]
  reflectivity?: number[]
  bytes?: number
  alpha?: boolean
}

export interface OutputMaterial {
  name: string
  vmt: string | null
  shader: string
  params: Record<string, string>
  textures: OutputTexture[]
  flags: string[]
  raw: string
  alpha: 'OPAQUE' | 'MASK' | 'BLEND'
  origin?: {
    key: string
    name: string
    cls: string
    source: string
    textures: {
      key: string
      name: string
      fmt: string
      width: number
      height: number
      role: string
      slot: string
    }[]
  } | null
}

export interface OutputCheck {
  level: 'ok' | 'info' | 'warn' | 'error'
  title: string
  detail: string
}

export type Vec3 = [number, number, number]

export interface OutputBone {
  name: string
  parent: number
  flags: number
  pos: Vec3
}

export interface OutputHitbox {
  bone: number
  group: number
  name: string
  size: Vec3
  corners: Vec3[]
}

export interface OutputAttachment {
  name: string
  bone: number
  origin: Vec3
  axes: [Vec3, Vec3, Vec3]
}

export interface OutputBodyModel {
  name: string
  vertices: number
  triangles: number
  empty: boolean
}

export interface OutputBodyPart {
  name: string
  base: number
  models: OutputBodyModel[]
}

export interface OutputSequence {
  name: string
  activity: string
  loop: boolean
  frames: number
  fps: number
  blends: number
  events: number
}

export interface OutputFile {
  name: string
  path: string | null
  bytes: number
  exists: boolean
  required: boolean
  abs: string
}

export interface OutputModel {
  path: string
  kind: 'prop' | 'character'
  stem: string
  external: boolean
  model: {
    name: string
    version: number
    checksum: string
    length: number
    mass: number
    surfaceprop: string
    flags: string[]
    hull: [Vec3, Vec3]
    view: [Vec3, Vec3]
    eye: Vec3
    hull_box: Vec3[]
    view_box: Vec3[]
    bones: OutputBone[]
    bodyparts: OutputBodyPart[]
    textures: string[]
    cdtextures: string[]
    skins: number[][]
    skinrefs: number
    sequences: OutputSequence[]
    poseparams: { name: string; min: number; max: number }[]
    hitboxes: OutputHitbox[]
    attachments: OutputAttachment[]
    includes: string[]
    ikchains: { name: string; bones: string[] }[]
    ikautoplay: number
  }
  files: OutputFile[]
  bytes: number
  lods: { lod: number; triangles: number; vertices: number; switch: number }[]
  triangles: number
  vertices: number
  extent: Vec3 | null
  materials: OutputMaterial[]
  skin_materials: number[][]
  texture_count: number
  texture_bytes: number
  collision: { pieces: number; triangles: number } | null
  source: { key: string; rel: string; cat: string } | null
  checks: OutputCheck[]
  verdict: 'ok' | 'warn' | 'error'
}

// ---- v2: system, home, settings, textures, inspectors
export type CapabilityV2 = Capability | 'textures'

export interface SystemTool {
  key: string
  label: string
  ok: boolean
  path: string
}

export interface SystemInfo {
  version: string
  workspace: string
  exports: string
  home: string
  namespace: string
  cpus: number
  setup?: { ready: boolean; can_convert: boolean }
  rust: {
    native: boolean
    native_version: string
    native_error: string
  }
  tools: SystemTool[]
}

export interface Overview {
  id: string
  title: string
  description: string
  capabilities: string[]
  props?: { total: number; named: number; converted: number }
  characters?: { total: number; built: number }
  textures?: {
    ready: boolean
    building: boolean
    textures?: number
    bytes?: number
    named?: number
    used?: number
  }
  sounds?: {
    exported: boolean
    count: number
    bytes: number
    summary: Record<string, unknown> | null
  }
  addon: {
    path: string
    deployed: boolean
    link: string
    models: number
    addon: number | null
    previews: number | null
    gma: string
    gma_bytes: number
  }
  jobs: Job[]
}

export interface Settings {
  general: { open_browser: boolean; port: number; namespace: string }
  paths: { gmod: string; exports: string; assets: string }
  textures: { quality: string; encoder: number; lossless_normals: boolean }
  props: {
    physics: boolean
    lods: boolean
    collision: string
    workers: number
    blend: boolean
    gltf?: boolean
  }
  characters: { max_tris: number; preview_size: number }
  sounds: { format: string; workers: number; tags: boolean; skip_stubs: boolean; languages: string }
  texts?: { locr_key: string }
  viewer: { texture_size: number }
  updates: { check: boolean; token: string }
}

export interface UpdateInfo {
  current: string
  latest: string
  available: boolean
  notes: string
  url: string
  error: string
  asset: { name: string; size: number } | null
}

export interface GameTexture {
  key: string
  name: string
  folder: string
  fmt: string
  width: number
  height: number
  mips: number
  bytes: number
  role: string
  named: number
  materials: number
  models: number
}

export interface TexturePage extends Page<GameTexture> {
  facets: { fmt: Facet[]; role: Facet[] }
  building: boolean
  progress?: string
}

export interface TextureUserMaterial {
  key: string
  name: string
  cls: string
  source: string
  slot: string
  role: string
}

export interface TextureDetail extends Omit<GameTexture, 'materials' | 'models'> {
  game_path: string
  materials: TextureUserMaterial[]
  models: { key: string; rel: string; cat: string; converted: boolean }[]
  model_count: number
}

/** A game texture referenced by a material (props / characters inspectors). */
export interface SourceTexture {
  role: string
  slot: string
  key: string
  width?: number
  height?: number
  format?: string
  found?: boolean
  name?: string
  bytes?: number
}

export interface SourceMaterial {
  key: string
  name: string
  source_name: string
  class: string
  flags?: string[]
  params: Record<string, number[]>
  textures: SourceTexture[]
  unknown_slots?: string[]
}

export interface PropDetails {
  key: string
  rel: string
  cat: string
  name: string
  size: number
  kind: 'prop'
  skinned: boolean
  lod: number
  lods: number[]
  submeshes: number
  triangles: number
  vertices: number
  extent: Vec3 | null
  bones: number
  warnings: string[]
  materials: SourceMaterial[]
  variants: { index: number; slots: number; params: number }[]
  collision: { resource: string; dynamic: boolean; shapes: Record<string, number> } | null
  output: { path: string; converted: boolean; mtime: number }
}

// ---- setup (first run)
export interface SetupStatus {
  game: { path: string; ok: boolean; packages: string[]; detected: boolean }
  assets: { path: string; ok: boolean; chunks: string[] }
  names: { path: string; ok: boolean; bytes: number }
  gmod: { path: string; ok: boolean; gmad: boolean }
  studiomdl: { path: string; ok: boolean }
  ready: boolean
  can_convert: boolean
  data_dir: string
  free_bytes: number
}

/** One animation of a model's skeleton: per frame and bone, position (3) then quaternion (4), local, viewer frame. */
export interface Clip {
  frames: number
  fps: number
  loop: boolean
  bones: number
  data: Float32Array
}

export interface AnimSequence {
  index: number
  name: string
  activity: string
  frames: number
  fps: number
  loop: boolean
  seconds: number
}

export interface AnimCatalog {
  sources: { id: string; label: string; available: boolean; sequences: AnimSequence[] }[]
  default: { source: string; index: number } | null
  bones: number
}
