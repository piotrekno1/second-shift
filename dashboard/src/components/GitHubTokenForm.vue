<script setup lang="ts">
import { ref } from 'vue'
import { storeToRefs } from 'pinia'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import AppIcon from './AppIcon.vue'
import DocsLink from './DocsLink.vue'
import { DocsPage } from '../utils/docs'
import { GITHUB_TOKENS_URL, newTokenUrl } from '../services/githubRepositories'
import { useGitHubRepositoriesStore } from '../stores/githubRepositories'

/** Paste a fine-grained token. With connectionId, it replaces that connection's token. */
const props = withDefaults(defineProps<{ connectionId?: number | null }>(), { connectionId: null })
const emit = defineEmits<{ connected: [] }>()

const github = useGitHubRepositoriesStore()
const { connecting, tokenError } = storeToRefs(github)
const token = ref('')
github.clearTokenError()

async function submit(): Promise<void> {
  if (!token.value.trim()) return
  if (!await github.connectToken(token.value, props.connectionId)) return
  token.value = ''
  emit('connected')
}
</script>

<template>
  <form class="github-token-form" :aria-busy="connecting" @submit.prevent="submit">
    <div class="github-token-row">
      <InputText v-model="token" type="password" aria-label="Fine-grained personal access token" placeholder="github_pat_…" autocomplete="new-password" maxlength="2000" autofocus />
      <Button class="main-action" type="submit" :label="connectionId === null ? 'Connect' : 'Replace token'" :loading="connecting" :disabled="!token.trim()">
        <template #icon><AppIcon name="check" /></template>
      </Button>
    </div>
    <div v-if="connectionId === null" class="github-token-guide">
      <ol>
        <li><a :href="newTokenUrl()" target="_blank" rel="noopener noreferrer">Create a fine-grained token on GitHub <AppIcon name="open-in-new" /></a>. Read permissions are filled in.</li>
        <li>Choose the resource owner (your account or organization), set an expiration, and select only the repositories you want to connect.</li>
        <li>Generate the token, copy it, then paste it above and select Connect. If your organization requires approval, an owner must approve it before its repositories appear.</li>
      </ol>
      <p>Need draft pull requests or issues? <a :href="newTokenUrl(true)" target="_blank" rel="noopener noreferrer">Create a token with write access <AppIcon name="open-in-new" /></a> instead. <DocsLink :page="DocsPage.GitHubAccess" section="use-a-personal-access-token">Full permissions guide</DocsLink></p>
    </div>
    <small v-else>
      On GitHub, <a :href="GITHUB_TOKENS_URL" target="_blank" rel="noopener noreferrer">regenerate the token <AppIcon name="open-in-new" /></a> to keep its repositories and permissions. The new token must belong to the same GitHub account.
    </small>
    <p v-if="tokenError" class="form-error" role="alert">{{ tokenError }}</p>
  </form>
</template>
