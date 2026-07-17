import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 8080,
    proxy: {
      '/api': 'http://0.0.0.0:8001',
      '/ws': {
        target: 'ws://0.0.0.0:8001',
        ws: true,
      }
    }
  },
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    pool: 'threads',
    poolOptions: {
      threads: {
        singleThread: true,
      },
    },
    fileParallelism: false,
    testTimeout: 15000,
    hookTimeout: 15000,
    teardownTimeout: 5000,
    setupFiles: './src/test/setup.ts',
  },
})
