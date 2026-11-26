/// <reference types="vite/client" />

import type { MayiDesktopApi } from '@shared/protocol'

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

declare global {
  interface Window {
    mayiApi: MayiDesktopApi
  }
}
