import { createRouter, createWebHistory } from 'vue-router'

import ConnectionsView from './views/ConnectionsView.vue'
import JobsView from './views/JobsView.vue'
import JobDetailView from './views/JobDetailView.vue'
import NotFoundView from './views/NotFoundView.vue'
import OverviewView from './views/OverviewView.vue'
import RepositoriesView from './views/RepositoriesView.vue'
import RepositoryDetailView from './views/RepositoryDetailView.vue'
import RunDetailView from './views/RunDetailView.vue'
import RunFileView from './views/RunFileView.vue'
import RunsView from './views/RunsView.vue'
import SettingsView from './views/SettingsView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    // Signing in happens on the server's pages. Old links still land home.
    { path: '/login', redirect: '/' },
    { path: '/', component: OverviewView },
    { path: '/repositories', component: RepositoriesView },
    { path: '/repositories/:id(\\d+)', component: RepositoryDetailView },
    { path: '/jobs', component: JobsView },
    { path: '/jobs/:id(\\d+)', component: JobDetailView },
    { path: '/runs', component: RunsView },
    { path: '/runs/:id(\\d+)', component: RunDetailView },
    { path: '/runs/:id(\\d+)/files/:name(input|output)', component: RunFileView },
    { path: '/connections', component: ConnectionsView },
    { path: '/settings', component: SettingsView },
    // Ids are numbers, so /runs/abc lands here instead of asking the server.
    { path: '/:unknown(.*)*', component: NotFoundView },
  ],
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition
    if (to.hash) return { el: to.hash, top: 16, behavior: 'smooth' }
    // Filter changes replace the query on the same page; keep the reader's place.
    if (to.path === from.path) return false
    // Connections scrolls to the row a link names. Going to the top would undo that.
    if (to.path === '/connections' && (to.query.connection || to.query.binding)) return false
    return { top: 0 }
  },
})
