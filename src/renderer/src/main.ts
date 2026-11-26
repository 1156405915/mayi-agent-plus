import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import './styles.css'

/**
 * 挂载 Vue 3 双栏工作台前端应用。
 */
const app = createApp(App)
app.use(createPinia())
app.mount('#app')
