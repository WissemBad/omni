// omni front-end: a client-side app (no SSR) built to static files that `omni ui` serves next to the API.
export default defineNuxtConfig({
  extends: ['@wissem-industries/ui'],
  compatibilityDate: '2026-09-19',
  ssr: false,
  devtools: { enabled: false },
  app: {
    head: {
      title: 'omni',
      meta: [{ name: 'description', content: 'Convertisseur multi-sources vers Garry’s Mod' }],
    },
  },
  css: ['~/assets/css/app.css'],
  // `bun run dev` talks to a running `omni ui` (port 8770).
  nitro: {
    devProxy: { '/api': { target: 'http://127.0.0.1:8770/api', changeOrigin: true } },
  },
  // The layer enables view transitions: they make the 3D viewport flash on route changes.
  experimental: { viewTransition: false },
})
