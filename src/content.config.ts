import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const syndicationTarget = z.object({
  platform: z.enum(['x', 'threads', 'linkedin', 'dev.to']),
  status: z.enum(['pending', 'skipped', 'published']),
  reason: z.string().optional(),
  url: z.string().url().optional(),
  draft: z.string().optional(),
  draftChars: z.string().optional(),
  draftBytes: z.string().optional(),
});

const syndication = z.object({
  attemptedAt: z.string(),
  worker: z.string(),
  targets: z.array(syndicationTarget),
}).optional();

const blog = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/blog' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    pubDate: z.coerce.date(),
    tags: z.array(z.string()),
    author: z.string().default('오늘의 운세 편집팀'),
    syndication,
  }),
});

export const collections = { blog };
