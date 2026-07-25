import { defineConfig, loadEnv, type Plugin, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Node's HTTP server aborts any request whose BODY takes longer than
 * requestTimeout (default 300 000 ms since Node 18) — a multi-GB CSV upload
 * streaming through the dev proxy dies mid-transfer at exactly 5 minutes,
 * silently from the user's point of view. 0 disables the limit. The 60 s
 * headersTimeout stays at its default, which is the part that matters for
 * slowloris-style protection (irrelevant on a local dev server anyway).
 */
const unlimitedUploadTime: Plugin = {
  name: 'ptt:unlimited-upload-time',
  configureServer(server) {
    if (server.httpServer) server.httpServer.requestTimeout = 0
  },
  configurePreviewServer(server) {
    if (server.httpServer) server.httpServer.requestTimeout = 0
  },
}

function normalizeBasePath(value: string): string {
  const withLeadingSlash = value.startsWith('/') ? value : `/${value}`
  return withLeadingSlash.endsWith('/')
    ? withLeadingSlash
    : `${withLeadingSlash}/`
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // Production lives at http://heliweb/ptt/. Development deliberately stays
  // at http://localhost:3000/ unless VITE_BASE_PATH overrides either mode.
  const base = normalizeBasePath(
    env.VITE_BASE_PATH || (mode === 'production' ? '/ptt/' : '/')
  )
  const apiPrefix = `${base}api`
  const stripBasePath = base === '/'
    ? (path: string) => path
    : (path: string) => path.slice(base.length - 1)
  const proxy: Record<string, ProxyOptions> = {
    // Backend (FastAPI) — avoids CORS entirely in dev/preview.
    [apiPrefix]: {
      target: 'http://127.0.0.1:8000',
      changeOrigin: true,
      // FastAPI routes remain /api/...; nginx performs the same rewrite for
      // production requests arriving as /ptt/api/....
      rewrite: stripBasePath,
      // http-proxy would otherwise give up on the response after its
      // defaults; uploads legitimately take many minutes end-to-end.
      timeout: 0,
      proxyTimeout: 0,
    },
  }

  return {
    base,
    plugins: [react(), unlimitedUploadTime],
    server: {
      port: 3000,
      open: base,
      proxy,
    },
    preview: {
      proxy,
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
    },
  }
})
