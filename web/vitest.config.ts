import { defineConfig } from 'vitest/config'

// Plain functions of app/utils: no Nuxt runtime needed (composables are covered by the end-to-end run of the package).
export default defineConfig({ test: { include: ['tests/**/*.test.ts'], environment: 'node' } })
