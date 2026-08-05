import { colors } from "@posthog/brand/colors";
import { assets } from "@posthog/brand/hoggies/metadata";

export interface Hoggie {
  /** PNG file stem — what goes into the payload and onto the CDN URL. */
  slug: string;
  name: string;
  tags: string[];
  /** Bundled asset URL — thumbnails render without any network. */
  src: string;
}

// The shipped PNG files are the catalog: each exists at the same path in the
// pinned CDN copy the app loads from. The metadata manifest can't be the list
// itself — variants there share one slug (five "wizard" entries for files
// wizard-1..5.png) — so it only decorates files with names and search tags.
const pngFiles = import.meta.glob(
  "../../../node_modules/@posthog/brand/dist/generated/hoggies/png/*.png",
  { eager: true, query: "?url", import: "default" },
) as Record<string, string>;

const metaByFileStem = new Map(
  assets.map((asset) => {
    const variant = Object.values(asset.variant ?? {})[0];
    return [variant ? `${asset.slug}-${variant}` : asset.slug, asset] as const;
  }),
);

function titleCase(stem: string): string {
  return stem.replace(/-/g, " ").replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

export const hoggieCatalog: Hoggie[] = Object.entries(pngFiles)
  .map(([path, src]) => {
    const stem = path
      .slice(path.lastIndexOf("/") + 1)
      .replace(/\.png$/, "")
      .toLowerCase();
    // Second lookup catches stem/slug drift like file 9-9-6.png ↔ slug "996".
    const meta =
      metaByFileStem.get(stem) ?? metaByFileStem.get(stem.replace(/-/g, ""));
    const variant = Object.values(meta?.variant ?? {})[0];
    const name = meta
      ? variant
        ? `${meta.name} ${variant}`
        : meta.name
      : titleCase(stem);
    return { slug: stem, name, tags: meta?.tags ?? [], src };
  })
  .sort((a, b) => a.name.localeCompare(b.name));

export const hoggieSrcBySlug = new Map(
  hoggieCatalog.map((hoggie) => [hoggie.slug, hoggie.src]),
);

/** Hero band background presets, straight from the brand color tokens. */
export const BAND_COLORS: { name: string; hex: string }[] = Object.values(
  colors,
).map((color) => ({ name: color.name, hex: color.core.toLowerCase() }));
