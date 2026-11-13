<script setup lang="ts">
/** Whether pull requests, issues and commits through a GitHub source name SecondShift. */
const props = defineProps<{ attribution: boolean, saving: boolean }>()
const emit = defineEmits<{ change: [attribution: boolean] }>()

/** The box shows the saved value. It changes once the server agrees. */
function toggle(event: Event): void {
  const box = event.target as HTMLInputElement
  emit('change', box.checked)
  box.checked = props.attribution
}
</script>

<template>
  <div class="attribution-setting">
    <label>
      <input type="checkbox" :checked="attribution" :disabled="saving" @change="toggle">
      <span>Say SecondShift opened pull requests and issues</span>
    </label>
    <small v-if="attribution">Branches are named <code>secondshift/run-&lt;id&gt;</code>. Descriptions and commits name the run.</small>
    <small v-else>Pull requests and issues read as this account's own. Branches are named after the job.</small>
  </div>
</template>
