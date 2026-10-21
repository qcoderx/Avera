import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  // Defaults to the deployed API; point at a local FastAPI with AVERA_API_PROXY_TARGET.
  const apiTarget = env.AVERA_API_PROXY_TARGET || 'https://api.avera.clippit.click';

  // Same-origin browser calls avoid broad backend CORS. This also carries
  // EventSource responses through to FastAPI's ordered SSE stream.
  const proxy = {
    '/api': {
      target: apiTarget,
      changeOrigin: true,
      secure: true,
      rewrite: (path) => path.replace(/^\/api(?=\/|$)/, '') || '/',
    },
  };

  return {
    server: { proxy },
    preview: { proxy },
  };
});
