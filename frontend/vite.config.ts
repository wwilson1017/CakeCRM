import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// VITE_API_PORT lets multiple dev instances run side by side
const api = { target: `http://localhost:${process.env.VITE_API_PORT || 8000}`, changeOrigin: true }

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': api,
      // Connect your own AI (#322): /mcp and its OAuth server live at the origin root.
      '/mcp': api,
      '/.well-known': api,
      '/authorize': api,
      '/token': api,
      '/register': api,
    },
  },
})
