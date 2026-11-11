<script setup lang="ts">
import AddRepositoryFlow from '../components/AddRepositoryFlow.vue'
import AppIcon from '../components/AppIcon.vue'
import PageHeader from '../components/PageHeader.vue'
import { useAppStore } from '../stores/app'
import { Action } from '../utils/permissions'

const app = useAppStore()
</script>

<template>
  <div class="page">
    <PageHeader compact title="Add repository" description="Jobs come from the repository's .secondshift.yaml." :parent="{ label: 'Repositories', to: '/repositories' }" />
    <AddRepositoryFlow v-if="app.canAddLocalRepositories || app.can(Action.AddGitHubRepositories)" />
    <div v-else class="panel empty-state"><AppIcon name="lock-outline" /><strong>Workspace owners add repositories</strong></div>
  </div>
</template>
