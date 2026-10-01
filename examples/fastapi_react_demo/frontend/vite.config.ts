import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

const backendTarget = process.env.E2E_BACKEND_URL || 'http://0.0.0.0:8001'
const websocketTarget = backendTarget.replace(/^http/, 'ws')

const productionChunk = (rawId: string): string | undefined => {
  const id = rawId.replace(/\\/g, '/')
  if (!id.includes('/node_modules/')) return undefined
  const modulePath = id.split('/node_modules/').pop() || ''
  if (/^(leaflet|react-leaflet|@react-leaflet)\//.test(modulePath)) return 'map-vendor'
  if (modulePath.startsWith('antd/')) return 'antd-vendor'
  // rc-picker 是 antd 依赖树中体积最大的叶子包；单独拆出后，其余
  // @ant-design/rc 基础包可以留在同一 chunk，避免人为制造循环 chunk。
  if (modulePath.startsWith('rc-picker/')) return 'rc-picker-vendor'
  if (/^(@ant-design|@rc-component|rc-)/.test(modulePath)) return 'ant-rc-vendor'
  return 'vendor'
}

// https://vitejs.dev/config/
export default defineConfig({
  // 运行时与前端共享 examples/fastapi_react_demo/.env；仅 VITE_ 前缀变量会注入浏览器构建。
  envDir: '..',
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 8080,
    proxy: {
      '/api': backendTarget,
      '/ws': {
        target: websocketTarget,
        ws: true,
      }
    }
  },
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
    // 首屏业务入口已降至约 250 kB；Ant Design 共享包约 522 kB，保留单一
    // 稳定缓存块比拆出循环依赖块更可靠，因此单独采用贴近实际的告警线。
    chunkSizeWarningLimit: 550,
    rollupOptions: {
      output: {
        manualChunks: productionChunk,
        onlyExplicitManualChunks: true,
      },
    },
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
