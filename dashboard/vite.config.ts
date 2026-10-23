import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/static/' : '/',
  plugins: [vue()],
  // Built into the Python package so an installed SecondShift serves it.
  build: { outDir: '../secondshift/web', emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: process.env.VITE_PROXY_API ?? 'http://localhost:8000', changeOrigin: true },
      '/static': { target: process.env.VITE_PROXY_API ?? 'http://localhost:8000', changeOrigin: true },
      '/accounts': { target: process.env.VITE_PROXY_API ?? 'http://localhost:8000', changeOrigin: true },
      '/-/': { target: process.env.VITE_PROXY_API ?? 'http://localhost:8000', changeOrigin: true },
    },
  },
}))
