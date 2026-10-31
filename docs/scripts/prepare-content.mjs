import { readFile, readdir, mkdir, writeFile, copyFile } from 'node:fs/promises';
import { join } from 'node:path';

const root = new URL('../', import.meta.url);
const output = new URL('../src/content/docs/docs/', import.meta.url);
const publicScreenshots = new URL('../public/screenshots/', import.meta.url);
const publicMark = new URL('../public/secondshift-mark.svg', import.meta.url);
const publicFavicon = new URL('../public/favicon.svg', import.meta.url);
const publicShareImage = new URL('../public/og.png', import.meta.url);
await mkdir(output, { recursive: true });
await mkdir(publicScreenshots, { recursive: true });
await copyFile(new URL('../../dashboard/public/secondshift-mark.svg', import.meta.url), publicMark);
await copyFile(new URL('../../dashboard/public/favicon.svg', import.meta.url), publicFavicon);
await copyFile(new URL('../src/assets/og.png', import.meta.url), publicShareImage);
await copyFile(new URL('../src/assets/apple-touch-icon.png', import.meta.url), new URL('../public/apple-touch-icon.png', import.meta.url));
const files = (await readdir(root)).filter((name) => name.endsWith('.md'));
for (const name of files) {
  let body = await readFile(new URL(name, root), 'utf8');
  const heading = body.match(/^# (.+)\n/);
  const title = heading?.[1] ?? name.replace(/\.md$/, '').replaceAll('-', ' ');
  if (heading) body = body.slice(heading[0].length).trimStart();
  body = body.replace(/^\[README\][^\n]*\n\n/, '');
  body = body.replace(/\]\((?:\.\/)?([\w-]+)\.md(#[^)]*)?\)/g, (_, slug, hash = '') => `](/docs/${slug}/${hash})`);
  body = body.replace(/\]\(\.\.\/README\.md(#[^)]*)?\)/g, (_, hash = '') => `](https://github.com/piotrekno1/second-shift/blob/main/README.md${hash})`);
  body = body.replace(/\]\(\.\.\/([^)]*)\)/g, (_, path) => `](https://github.com/piotrekno1/second-shift/blob/main/${path})`);
  body = body.replace(/\]\(screenshots\//g, '](/screenshots/');
  await writeFile(new URL(name, output), `---\ntitle: ${JSON.stringify(title)}\n---\n\n${body}`);
}
for (const name of await readdir(new URL('../screenshots/', import.meta.url))) {
  await copyFile(new URL(`../screenshots/${name}`, import.meta.url), new URL(name, publicScreenshots));
}
