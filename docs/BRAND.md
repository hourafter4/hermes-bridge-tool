# Hermes Bridge brand

**Your agent. Within reach.**

Bring your remote agent into your workflow. Hermes Bridge is an independent companion that connects Codex and Claude Code to a remote Hermes agent. It is not an official Nous Research, OpenAI, or Anthropic product.

## Identity

The mark combines an **H** with an arched bridge: two endpoints, one connection. Use the plain mark in compact UI, the wordmark in documentation, and the square app icon for macOS. Keep language direct and useful. Describe the connection and the task it enables; avoid claims of official affiliation.

| Color | Hex | Use |
| --- | --- | --- |
| Teal | `#16756B` | Mark, primary accents |
| Ink | `#183532` | Headings and body text |
| Paper | `#F4F1E9` | Warm neutral backgrounds |
| Muted | `#52645D` | Supporting text |
| White | `#FFFFFF` | Surfaces and reverse marks |

Use the native system sans serif for interfaces. The SVG wordmark and banners use Helvetica Neue, Helvetica, and Arial fallbacks. All assets are editable SVG with no external resources or embedded fonts. The PNG social card provides consistent typography for upload.

## Files

All files are in [`assets/brand`](../assets/brand/).

| Asset | Use |
| --- | --- |
| `mark.svg` | Teal mark; 96 × 96 view box |
| `mark-mono.svg` | Black mark for single color and template rendering |
| `wordmark.svg` | Transparent horizontal logo |
| `app-icon.svg`, `app-icon.png` | 1024 × 1024 macOS icon source; transparent outer margin |
| `menu-bar-template.png`, `menu-bar-template@2x.png` | 18 pt menu bar icon at 1× and 2×; render as an AppKit template image |
| `github-banner.svg` | 1280 × 640 README hero |
| `social-card.svg`, `social-card.png` | 1200 × 630 GitHub social preview and project sharing |

Keep the mark at least 16 pixels wide. Leave at least one pillar's width between the visible mark and surrounding content. Do not stretch, rotate, or add details inside the arch. Use the single color mark where gradients or several colors would reduce clarity. The app icon's subtle tonal background is reserved for the app icon.

## Regenerating PNGs

The checked-in SVGs are the source of truth. ImageMagick can export the assets locally; it is only needed when editing the artwork.

```sh
magick -background none assets/brand/app-icon.svg assets/brand/app-icon.png
magick -background none assets/brand/social-card.svg assets/brand/social-card.png
magick -background none assets/brand/mark-mono.svg -resize 18x18 assets/brand/menu-bar-template.png
magick -background none assets/brand/mark-mono.svg -resize 36x36 assets/brand/menu-bar-template@2x.png
```

Text rendering depends on installed fonts. Review the exported social card after changing a headline or typeface. For a GitHub repository, upload `social-card.png` under the repository's social preview setting once the repo has been published.
