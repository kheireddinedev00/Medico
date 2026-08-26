import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API is proxied rather than called cross-origin, so no CORS configuration is needed
// on the Laravel side and the token never travels as a URL parameter.
//
// Two servers have to be running for this to work:
//   engine  — venv/Scripts/python -m uvicorn service.app:app --port 8001   (project root)
//   api     — php artisan serve                                            (api/)
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // Reading a scanned report takes about a minute per page on the free tier, and a
        // multi-page PDF is several. Left at the defaults the dev proxy gives up partway
        // through and the doctor sees a network error for work the model was still doing.
        timeout: 15 * 60 * 1000,
        proxyTimeout: 15 * 60 * 1000,
      },
    },
  },
})
