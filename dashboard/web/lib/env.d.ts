// Build-time flags replaced by Vite (`import.meta.env` is undefined under the Node test runner).
interface ImportMetaEnv {
  readonly VITE_STATIC_DEMO?: string;
}
interface ImportMeta {
  readonly env?: ImportMetaEnv;
}
