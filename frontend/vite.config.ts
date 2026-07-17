import { defineConfig, type Plugin } from 'vite'
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

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), unlimitedUploadTime],
  server: {
    port: 3000,
    open: true,
    proxy: {
      // Backend (FastAPI) — avoids CORS entirely in dev.
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // http-proxy would otherwise give up on the response after its
        // defaults; uploads legitimately take many minutes end-to-end.
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true
  }
})
