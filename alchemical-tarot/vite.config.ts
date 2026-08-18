import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// GitHub Pages serves this as a project site at /Cartoon-hero/alchemical-tarot/,
// so asset paths and the PWA scope need that prefix baked in for that target.
// Other hosts (Railway, local dev) serve from the domain root and don't set this.
const base = process.env.GITHUB_PAGES ? '/Cartoon-hero/alchemical-tarot/' : '/'

// https://vite.dev/config/
export default defineConfig({
  base,
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: 'Alchemical Tarot',
        short_name: 'Arcana',
        description: 'A personal tarot & oracle companion — readings, moon phases, natal chart, and a reading journal.',
        theme_color: '#131022',
        background_color: '#0b0a12',
        display: 'standalone',
        orientation: 'portrait',
        start_url: base,
        scope: base,
        icons: [
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,ico}'],
        navigateFallbackDenylist: [/^\/api/],
      },
    }),
  ],
})
