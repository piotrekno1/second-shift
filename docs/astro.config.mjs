import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://second-shift.app',
  // Pages that moved keep their old addresses.
  redirects: {
    '/docs/engineering-tasks/': '/docs/jobs/',
    '/docs/mcp-recipes/': '/docs/connections/',
    '/docs/self-hosting/': '/docs/server-configuration/',
    '/docs/guardrails/': '/docs/control/',
  },
  integrations: [starlight({
    title: 'SecondShift',
    customCss: ['./src/styles/docs.css'],
    head: [
      { tag: 'meta', attrs: { property: 'og:image', content: 'https://second-shift.app/og.png' } },
      { tag: 'meta', attrs: { property: 'og:image:width', content: '1200' } },
      { tag: 'meta', attrs: { property: 'og:image:height', content: '630' } },
      { tag: 'meta', attrs: { name: 'twitter:image', content: 'https://second-shift.app/og.png' } },
      { tag: 'meta', attrs: { property: 'og:image:type', content: 'image/png' } },
      { tag: 'link', attrs: { rel: 'apple-touch-icon', href: '/apple-touch-icon.png' } },
    ],
    social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/piotrekno1/second-shift' }],
    sidebar: [
      { label: 'Start', items: [
          { label: 'Overview', slug: 'docs/overview', attrs: { 'data-icon': 'compass' } },
          { label: 'Evaluate', slug: 'docs/evaluating', attrs: { 'data-icon': 'clipboard' } },
          { label: 'Quickstart', slug: 'docs/quickstart', attrs: { 'data-icon': 'terminal' } },
          { label: 'Explore', slug: 'docs/explore', attrs: { 'data-icon': 'search' } },
      ] },
      { label: 'Use', items: [
          { label: 'Tasks', slug: 'docs/tasks', attrs: { 'data-icon': 'play' } },
          { label: 'Jobs', slug: 'docs/jobs', attrs: { 'data-icon': 'clock' } },
          { label: 'Models', slug: 'docs/models', attrs: { 'data-icon': 'sparkle' } },
          { label: 'Connections', slug: 'docs/connections', attrs: { 'data-icon': 'plug' } },
          { label: 'Subscriptions', slug: 'docs/agent-subscriptions', attrs: { 'data-icon': 'key' } },
          { label: 'Coding agents', slug: 'docs/mcp', attrs: { 'data-icon': 'bot' } },
      ] },
      { label: 'Control', items: [
          { label: 'Overview', slug: 'docs/control', attrs: { 'data-icon': 'shield' } },
          { label: 'Access', slug: 'docs/access', attrs: { 'data-icon': 'users' } },
          { label: 'Services', slug: 'docs/services', attrs: { 'data-icon': 'plug' } },
          { label: 'Spend', slug: 'docs/spend', attrs: { 'data-icon': 'gauge' } },
          { label: 'Sandbox', slug: 'docs/sandbox', attrs: { 'data-icon': 'box' } },
      ] },
      { label: 'Operate', items: [
          { label: 'Deploy', slug: 'docs/production-deployment', attrs: { 'data-icon': 'server' } },
          { label: 'Configure', slug: 'docs/server-configuration', attrs: { 'data-icon': 'drive' } },
          { label: 'Runners', slug: 'docs/runners', attrs: { 'data-icon': 'cpu' } },
          { label: 'GitHub', slug: 'docs/github-app', attrs: { 'data-icon': 'branch' } },
          { label: 'Variables', slug: 'docs/environment-variables', attrs: { 'data-icon': 'sliders' } },
      ] },
      { label: 'Reference', items: [
          { label: 'Manifest', slug: 'docs/repository-configuration', attrs: { 'data-icon': 'file-code' } },
          { label: 'Catalog', slug: 'docs/skill-catalog', attrs: { 'data-icon': 'book' } },
          { label: 'Alternatives', slug: 'docs/comparisons', attrs: { 'data-icon': 'scale' } },
      ] },
      { label: 'How it works', items: [
          { label: 'Architecture', slug: 'docs/architecture', attrs: { 'data-icon': 'layers' } },
          { label: 'Security', slug: 'docs/security-model', attrs: { 'data-icon': 'shield' } },
      ] },
      { label: 'Contributing', collapsed: true, items: [{ label: 'Backend', slug: 'docs/backend-architecture', attrs: { 'data-icon': 'code' } }] },
      { label: 'Request a feature', link: 'https://github.com/piotrekno1/second-shift/issues/new', attrs: { 'data-icon': 'message', target: '_blank', rel: 'noopener' } },
    ],
  })],
});
