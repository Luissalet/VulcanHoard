import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  root: 'client',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../vulcan/static',
    emptyOutDir: true,
  },
  experimental: {
    // URLs that chunks compute at runtime (CSS/JS preloads of lazy chunks such as the dense viewer) are relative to
    // the importing chunk, so they also resolve when the studio serves this app under /library/.
    renderBuiltUrl: (filename, { hostType }) => (hostType === 'js' ? { relative: true } : undefined),
  },
  server: {
    host: '127.0.0.1',
    port: Number(process.env.VITE_PORT || 5173),
    proxy: {
      '/api': `http://127.0.0.1:${process.env.VULCAN_PORT || process.env.PORT || 5186}`,
    },
  },
});
