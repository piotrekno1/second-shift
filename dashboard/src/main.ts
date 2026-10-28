import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import Aura from '@primeuix/themes/aura'
import { definePreset } from '@primeuix/themes'
import './styles.css'

import App from './App.vue'
import router from './router'

// No brand accent: PrimeVue's own focus rings, checks and selections use neutral greys too.
const Neutral = definePreset(Aura, {
  semantic: {
    primary: {
      50: '{neutral.50}', 100: '{neutral.100}', 200: '{neutral.200}', 300: '{neutral.300}', 400: '{neutral.400}',
      500: '{neutral.500}', 600: '{neutral.600}', 700: '{neutral.700}', 800: '{neutral.800}', 900: '{neutral.900}', 950: '{neutral.950}',
    },
    colorScheme: {
      dark: {
        primary: { color: '{neutral.200}', contrastColor: '{neutral.950}', hoverColor: '{neutral.100}', activeColor: '{neutral.50}' },
        surface: {
          0: '#ffffff', 50: '{neutral.50}', 100: '{neutral.100}', 200: '{neutral.200}', 300: '{neutral.300}', 400: '{neutral.400}',
          500: '{neutral.500}', 600: '{neutral.600}', 700: '{neutral.700}', 800: '{neutral.800}', 900: '#131313', 950: '#0d0d0d',
        },
      },
    },
  },
})

createApp(App)
  .use(createPinia())
  .use(router)
  .use(PrimeVue, {
    theme: {
      preset: Neutral,
      options: { darkModeSelector: '.secondshift-dark' },
    },
  })
  .mount('#app')
