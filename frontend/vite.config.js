import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // The API server (src/cua/api.py) runs on 5050. Proxying means the
      // frontend can just fetch('/api/...') with no CORS configuration
      // needed on either side.
      '/api': {
        target: 'http://127.0.0.1:5050',
        changeOrigin: true,
      },
    },
  },
})
