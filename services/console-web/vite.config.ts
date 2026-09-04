import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// The console is served as static assets behind a reverse proxy that also fronts the API
// (spec §5.2: "console-web | SPA | static"). In development Vite proxies /v1 to graph-svc
// so the app makes same-origin calls in both places and no CORS policy has to exist.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: process.env.ASTRA_API ?? 'http://localhost:8080', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['src/tests/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    // jsdom plus user-event's real keystroke delays make these tests slow in wall-clock
    // terms while doing very little work. The default five seconds is tight enough that
    // the suite fails intermittently on a loaded machine, and an intermittently failing
    // suite is one people learn to re-run instead of read.
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
});
