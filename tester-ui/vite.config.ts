import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// /tester/ base so nginx can proxy_pass with prefix stripping
export default defineConfig({
  base: '/tester/',
  plugins: [react()],
  server: {
    port: 3001,
    proxy: {
      '/api/tester': {
        target: 'http://localhost:8006',
        rewrite: (path) => path.replace(/^\/api\/tester/, ''),
      },
    },
  },
});
