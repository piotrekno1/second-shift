import { defineStore } from 'pinia'

export const useAgentConnectionStore = defineStore('agentConnection', {
  state: () => ({ visible: false }),
})
