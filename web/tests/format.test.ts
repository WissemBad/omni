import { describe, expect, it } from 'vitest'
import { crumb, fmtBytes, fmtDuration, fmtEta, leaf, parent, titleCase } from '../app/utils/format'

describe('format', () => {
  it('writes sizes', () => {
    expect(fmtBytes(512)).toBe('512 o')
    expect(fmtBytes(2048)).toBe('2 Ko')
    expect(fmtBytes(5 * 1024 ** 2)).toBe('5.0 Mo')
    expect(fmtBytes(3 * 1024 ** 3)).toBe('3.0 Go')
  })
  it('writes durations', () => {
    expect(fmtDuration(0)).toBe('—')
    expect(fmtDuration('x')).toBe('—')
    expect(fmtDuration(2.5)).toBe('2.50 s')
    expect(fmtDuration(125)).toBe('2:05')
  })
  it('rounds the remaining time the way people read it', () => {
    expect(fmtEta(null)).toBe('estimation en cours…')
    expect(fmtEta(3)).toBe('quelques secondes')
    expect(fmtEta(42)).toBe('≈ 40 s restantes')
    expect(fmtEta(600)).toBe('≈ 10 min restantes')
    expect(fmtEta(7200)).toBe('≈ 2 h 00 restantes')
  })
  it('shapes asset paths', () => {
    expect(titleCase('crate_a-big')).toBe('Crate a big')
    expect(leaf('props/crate/crate_a')).toBe('crate_a')
    expect(parent('props/crate/crate_a')).toBe('props/crate')
    expect(crumb('a/b/b/b/leaf')).toBe('A › B')
  })
})
