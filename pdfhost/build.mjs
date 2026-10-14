// Builds the Vyum PDF host (Electron main) and bridge (browser) from GenOffice's PDF module, without editing it.
//   node pdfhost/build.mjs [genoffice root]
import { build } from '../../genoffice/node_modules/esbuild/lib/main.js'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { readFile } from 'node:fs/promises'

const here = dirname(fileURLToPath(import.meta.url))
const go = resolve(process.argv[2] || join(here, '../../genoffice'))
const pdfMain = join(go, 'apps/pdf/src/main/pdf-main.ts')

// pdf-main keeps registerPdfIpc and grantAndTrack private; the host needs them, so they are exported at build time.
const exposeInternals = {
  name: 'vyum-expose-pdf-internals',
  setup(b) {
    b.onResolve({ filter: /^GENOFFICE_PDF_MAIN$/ }, () => ({ path: pdfMain }))
    b.onLoad({ filter: /pdf-main\.ts$/ }, async (args) => ({
      contents: (await readFile(args.path, 'utf8')) + '\nexport { registerPdfIpc, grantAndTrack }\n',
      loader: 'ts',
      resolveDir: dirname(args.path),
    }))
  },
}

await build({
  entryPoints: [join(here, 'host.ts')],
  outfile: join(here, 'dist/host.cjs'),
  bundle: true, platform: 'node', format: 'cjs', target: 'node22',
  external: ['electron'],
  nodePaths: [join(go, 'node_modules')],
  plugins: [exposeInternals],
  // GenOffice finds its wasm (pdfium for text edits, harfbuzz for font subsets) next to its own sources, as in its
  // dev layout: import.meta.url points into apps/pdf/src/main of the GenOffice checkout, not at this bundle.
  define: { 'import.meta.url': 'import_meta_url' },
  banner: { js: `const import_meta_url = require('url').pathToFileURL(require('path').join(process.env.VYUM_GENOFFICE_SRC || ${JSON.stringify(go)}, 'apps/pdf/src/main/pdf-main.ts')).href;` },
  logLevel: 'warning',
})

// The browser bridge: GenOffice's preload with 'electron' pointing at bridge.ts.
await build({
  entryPoints: [join(go, 'apps/pdf/src/preload/index.ts')],
  outfile: join(here, 'dist/vyum-bridge.js'),
  bundle: true, platform: 'browser', format: 'iife', target: 'es2022',
  alias: { electron: join(here, 'bridge.ts') },
  nodePaths: [join(go, 'node_modules')],
  logLevel: 'warning',
})
console.log('built pdfhost/dist/host.cjs and pdfhost/dist/vyum-bridge.js')
