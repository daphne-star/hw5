import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dashboard talks to the FastAPI backend on :8000, which holds the Portkey key.
// Nothing secret is configured here — only a base URL.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
})
