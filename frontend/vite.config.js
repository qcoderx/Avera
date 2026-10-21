import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiTarget = env.AVERA_API_PROXY_TARGET || 'http://127.0.0.1:8766';

  return {
    server: {
      proxy: {
        // Same-origin browser calls avoid broad backend CORS. This also carries
        // EventSource responses through to FastAPI's ordered SSE stream.
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api(?=\/|$)/, '') || '/',
        },
      },
    },
  };
});
