<script setup lang="ts">
/** Whether each job's request for a connection waits for an owner. */
const requireApproval = defineModel<boolean>({ required: true })
</script>

<template>
  <div class="need-setup-wide approval-setting">
    <label class="need-setup-check">
      <input v-model="requireApproval" type="checkbox">
      <span>Require approval for each job</span>
    </label>
    <small v-if="!requireApproval" class="approval-warning">
      When off, anyone who can merge to a connected repository can use this connection's allowed tools.
    </small>
    <small v-else>Jobs that ask for this connection wait until an owner approves them.</small>
  </div>
</template>
