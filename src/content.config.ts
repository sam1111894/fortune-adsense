import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

const syndicationTarget = z.object({
  platform: z.enum(['x', 'threads', 'linkedin', 'dev.to']),
  status: z.enum(['pending', 'skipped', 'published']),
  reason: z.string().optional(),
  url: z.string().url().optional(),
  draft: z.string().optional(),
  // Bare integers ONLY. The renderer appends the unit, so a value that
  // already carries one ("475자") renders as "475자자"; and a BYTE count
  // labelled as a character count misreports the draft size (Korean text
  // is 3 UTF-8 bytes per character). Measured by
  // `tools/sns_publish.py measure --drafts-dir <dir>`.
  draftChars: z.string().regex(/^[0-9]+$/).optional(),
  draftBytes: z.string().regex(/^[0-9]+$/).optional(),
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
