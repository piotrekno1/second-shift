<template>
  <main class="login-page">
    <section class="login-card">
      <img src="/secondshift-mark.svg" alt="SecondShift" />
      <span class="eyebrow">Your agents, safely on duty</span>
      <h1>The work continues<br />after you sign off.</h1>
      <p>
        Connect a repository and approve what its skills may access. SecondShift
        runs scheduled reviews in a sandbox and sends you the report.
      </p>
      <a class="primary-action" href="/accounts/github/login/">
        <i class="mdi mdi-github" /> Continue with GitHub
      </a>
      <a class="admin-link" href="/-/admin/login/">Use local administrator</a>
      <small>GitHub identifies you. Repository installations and service connections remain separately revocable.</small>
    </section>
    <aside class="login-atmosphere" aria-label="Example scheduled reviews">
      <div class="orbit orbit-one" />
      <div class="orbit orbit-two" />
      <article class="shift-preview shift-release">
        <span>Monday · 09:00</span>
        <strong>Weekly release recap</strong>
        <p>Merged features, fixes, and what comes next.</p>
      </article>
      <article class="shift-preview shift-ci">
        <span>Every morning · 08:00</span>
        <strong>CI failure review</strong>
        <p>Recurring failures and suggested fixes.</p>
      </article>
      <article class="shift-preview shift-health">
        <span>Monday · 09:15</span>
        <strong>Repository health review</strong>
        <p>Defects, test gaps, and docs that drifted.</p>
      </article>
      <article class="shift-preview shift-contributors">
        <span>Friday · 16:00</span>
        <strong>Contributor recap</strong>
        <p>The people behind the work and reviews.</p>
      </article>
      <article class="shift-preview shift-alerts">
        <span>Monday · 09:30</span>
        <strong>Alert coverage review</strong>
        <p>Removed alerts, paused rules, and changed thresholds.</p>
      </article>
    </aside>
  </main>
</template>
