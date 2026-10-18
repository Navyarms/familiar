import { defineConfig, externalizeDepsPlugin } from 'electron-vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: { lib: { entry: resolve(__dirname, 'src/main/index.ts') } },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: { lib: { entry: resolve(__dirname, 'src/preload/index.ts') } },
  },
  renderer: {
    plugins: [react()],
    root: resolve(__dirname, 'src'),
    build: {
      rollupOptions: {
        input: {
          overlay: resolve(__dirname, 'src/overlay/index.html'),
          panel: resolve(__dirname, 'src/panel/index.html'),
          settings: resolve(__dirname, 'src/settings/index.html'),
        },
      },
    },
  },
});
