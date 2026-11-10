import tailwindcss from '@tailwindcss/postcss';
import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';
import { rmSync } from 'node:fs';
import { fileURLToPath, URL } from 'node:url';

// `vite build --mode static` builds the public read-only demo (synthetic patients, no backend) into
// ../../demo-static, ready to deploy as a static Vercel site; the default build is the local workbench.
const STATIC_OUT = fileURLToPath(new URL('../../demo-static/', import.meta.url));
const vercelConfig: Plugin = {
  name: 'vercel-config',
  // Replace only the build output; keep the Vercel CLI link (.vercel/, .gitignore) of the deploy folder.
  buildStart() {
    for (const name of ['assets', 'static-demo', 'index.html', 'favicon.svg', 'vercel.json'])
      rmSync(STATIC_OUT + name, { recursive: true, force: true });
  },
  generateBundle() {
    this.emitFile({
      type: 'asset',
      fileName: 'vercel.json',
      source:
        JSON.stringify(
          {
            $schema: 'https://openapi.vercel.sh/vercel.json',
            rewrites: [{ source: '/(.*)', destination: '/index.html' }],
            headers: [
              {
                source: '/static-demo/(.*)',
                headers: [{ key: 'Cache-Control', value: 'public, max-age=0, must-revalidate' }],
              },
              {
                source: '/(.*)',
                headers: [
                  { key: 'X-Content-Type-Options', value: 'nosniff' },
                  { key: 'Referrer-Policy', value: 'no-referrer' },
                ],
              },
            ],
          },
          null,
          2,
        ) + '\n',
    });
  },
};

export default defineConfig(({ mode }) => {
  const staticDemo = mode === 'static';
  return {
    plugins: [react(), ...(staticDemo ? [vercelConfig] : [])],
    css: { postcss: { plugins: [tailwindcss()] } },
    resolve: { alias: { '@': fileURLToPath(new URL('.', import.meta.url)) } },
    define: staticDemo ? { 'import.meta.env.VITE_STATIC_DEMO': JSON.stringify('1') } : {},
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy: { '/api': 'http://127.0.0.1:8765' },
    },
    build: staticDemo ? { outDir: STATIC_OUT, emptyOutDir: false } : { outDir: 'dist', emptyOutDir: true },
  };
});
