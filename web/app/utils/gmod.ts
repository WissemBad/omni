import type { Prop } from '~/utils/types'

/** Where `omni` writes a converted prop inside the GMod addon (mirror of targets/source/build.py `_model_path`). */
export function modelPath(sid: string, p: Pick<Prop, 'rel' | 'key'>): string {
  const base = `omni/${sid}/`
  let path = base + p.rel
  if (path.length > 110)
    path = `${base}${(p.rel.split('/').pop() ?? '').slice(0, 30)}_${p.key.slice(-6).toLowerCase()}`
  return `models/${path}.mdl`
}

/** Console command that spawns a physics prop where the player looks. */
export const spawnCommand = (model: string) =>
  `lua_run local t=Entity(1):GetEyeTrace() local e=ents.Create("prop_physics") e:SetModel("${model}") e:SetPos(t.HitPos+Vector(0,0,40)) e:Spawn()`
