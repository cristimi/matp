import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://dashboard-api:8003',
      '/ws':  { target: 'ws://dashboard-api:8003', ws: true },
    },
  },
});
