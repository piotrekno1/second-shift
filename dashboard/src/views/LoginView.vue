<template>
  <main class="login-page">
    <section class="login-card">
      <img src="/secondshift-mark.svg" alt="SecondShift" />
      <span class="eyebrow">Your agents, safely on duty</span>
      <h1>The work continues<br />after you sign off.</h1>
      <p>
        Connect a repository, approve its skills and secrets, and let SecondShift
        handle the recurring work.
      </p>
      <a class="primary-action" href="/accounts/github/login/">
        <i class="mdi mdi-github" /> Continue with GitHub
      </a>
      <a class="admin-link" href="/-/admin/login/">Use local administrator</a>
      <small>GitHub identifies you. Repository installations and service connections remain separately revocable.</small>
    </section>
    <aside class="login-atmosphere" aria-hidden="true">
      <div class="orbit orbit-one" />
      <div class="orbit orbit-two" />
      <div class="shift-preview">
        <span>Next shift</span>
        <strong>Weekly infrastructure review</strong>
        <div><i class="mdi mdi-clock-outline" /> Monday, 09:00 · Europe/Warsaw</div>
        <div><i class="mdi mdi-shield-key-outline" /> 2 approved secrets</div>
      </div>
    </aside>
  </main>
</template>
