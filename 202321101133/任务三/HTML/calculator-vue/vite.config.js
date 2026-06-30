import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  base: './',                     // 相对路径，方便 HBuilder 打包 APK
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    // 确保小文件内联，减少 APK 加载时间
    cssMinify: true,
    minify: 'esbuild',
  },
  server: {
    host: '0.0.0.0',             // 局域网可访问，方便手机真机预览
    port: 5173,
  }
})
