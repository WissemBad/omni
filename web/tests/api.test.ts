import { describe, expect, it } from 'vitest'
import { apiError } from '../app/utils/api'

describe('apiError', () => {
  it('uses the sentence of the server', () => {
    expect(apiError({ status: 409, data: { detail: 'Ce travail est déjà lancé.' } })).toBe(
      'Ce travail est déjà lancé.',
    )
  })
  it('names the fields of a validation error', () => {
    const detail = [{ loc: ['body', 'workers'], msg: 'value is not a valid integer' }]
    expect(apiError({ status: 422, data: { detail } })).toBe('Valeurs invalides (workers).')
  })
  it('is French for statuses without a sentence', () => {
    expect(apiError({ statusCode: 500 })).toContain('Erreur interne')
    expect(apiError({ status: 404 })).toBe('Introuvable.')
    expect(apiError({ status: 418 })).toBe('Erreur 418.')
  })
  it('explains an unreachable server', () => {
    expect(apiError(new TypeError('Failed to fetch'))).toContain('omni ne répond pas')
    expect(apiError(undefined)).toContain('omni ne répond pas')
  })
  it('never leaks the English text of the fetch library', () => {
    expect(
      apiError({ message: '[GET] "/api/x": 500 Internal Server Error', status: 500 }),
    ).not.toMatch(/Internal Server Error/)
  })
})
