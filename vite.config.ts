import { defineConfig } from 'vite'
import path from 'path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'


function figmaAssetResolver() {
  return {
    name: 'figma-asset-resolver',
    resolveId(id) {
      if (id.startsWith('figma:asset/')) {
        const filename = id.replace('figma:asset/', '')
        return path.resolve(__dirname, 'src/assets', filename)
      }
    },
  }
}

export default defineConfig({
  plugins: [
    figmaAssetResolver(),
    // The React and Tailwind plugins are both required for Make, even if
    // Tailwind is not being actively used – do not remove them
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      // Alias @ to the src directory
      '@': path.resolve(__dirname, './src'),
    },
  },

  // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
  assetsInclude: ['**/*.svg', '**/*.csv'],

  build: {
    rollupOptions: {
      output: {
        // Vite already gives every React.lazy()/dynamic import() target its
        // own chunk automatically — this just groups the big third-party
        // libraries sensibly on top of that:
        //   - react/react-dom: needed on every screen including login, so
        //     splitting them out doesn't shrink the login path, but it's a
        //     stable chunk browsers can cache independently of app code that
        //     changes on every deploy.
        //   - leaflet/react-leaflet: only used by FacilityNetworkMap
        //     (Requests screen) and FacilityLocationPicker (CompleteProfile
        //     screen) — both lazy-loaded (see App.tsx) — so this chunk is
        //     shared between those two dynamic imports instead of being
        //     duplicated into each one, and never touches the login path.
        //   - recharts: only used by the Dashboard screen's forecast chart,
        //     also lazy-loaded.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('node_modules/react-dom') || id.includes('node_modules/react/') || id.includes('node_modules/scheduler')) {
            return 'vendor-react'
          }
          if (id.includes('node_modules/leaflet') || id.includes('node_modules/react-leaflet')) {
            return 'vendor-leaflet'
          }
          if (id.includes('node_modules/recharts') || id.includes('node_modules/d3-') || id.includes('node_modules/victory-vendor')) {
            return 'vendor-charts'
          }
          return undefined
        },
      },
    },
  },
})
