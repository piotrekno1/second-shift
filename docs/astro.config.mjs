import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://second-shift.app',
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
      { label: 'Start', items: [{ label: 'Overview', slug: 'docs/overview', attrs: { 'data-icon': 'compass' } }, { label: 'Try locally', slug: 'docs/quickstart', attrs: { 'data-icon': 'terminal' } }, { label: 'Explore skills', slug: 'docs/explore', attrs: { 'data-icon': 'search' } }, { label: 'One-off tasks', slug: 'docs/tasks', attrs: { 'data-icon': 'play' } }, { label: 'Repository jobs', slug: 'docs/engineering-tasks', attrs: { 'data-icon': 'clock' } }, { label: 'Skill catalog', slug: 'docs/skill-catalog', attrs: { 'data-icon': 'book' } }] },
      { label: 'Set up', items: [{ label: 'Repository config', slug: 'docs/repository-configuration', attrs: { 'data-icon': 'file-code' } }, { label: 'GitHub access', slug: 'docs/github-app', attrs: { 'data-icon': 'branch' } }, { label: 'Connect your coding agent', slug: 'docs/mcp', attrs: { 'data-icon': 'bot' } }, { label: 'Connect vendor tools', slug: 'docs/mcp-recipes', attrs: { 'data-icon': 'plug' } }, { label: 'Agent subscriptions', slug: 'docs/agent-subscriptions', attrs: { 'data-icon': 'key' } }, { label: 'Deploy', slug: 'docs/production-deployment', attrs: { 'data-icon': 'server' } }, { label: 'Self-hosting', slug: 'docs/self-hosting', attrs: { 'data-icon': 'drive' } }, { label: 'Environment variables', slug: 'docs/environment-variables', attrs: { 'data-icon': 'sliders' } }] },
      { label: 'How it works', items: [{ label: 'Architecture', slug: 'docs/architecture', attrs: { 'data-icon': 'layers' } }, { label: 'Security model', slug: 'docs/security-model', attrs: { 'data-icon': 'shield' } }, { label: 'Alternatives', slug: 'docs/comparisons', attrs: { 'data-icon': 'scale' } }] },
      { label: 'Contributing', collapsed: true, items: [{ label: 'Backend ownership', slug: 'docs/backend-architecture', attrs: { 'data-icon': 'code' } }] },
    ],
  })],
});
