# Hermes Bridge Tool brand

**Your agent. Within reach.**

Bring your remote agent into your workflow. Hermes Bridge Tool is an independent companion that connects Codex and Claude Code to a remote Hermes agent. It is not an official Nous Research, OpenAI, or Anthropic product.

## Identity

The mark combines an **H** with an arched bridge: two endpoints, one connection. Use the plain mark in compact UI, the wordmark in documentation, and the macOS icon for the app bundle. Keep language direct and useful. Describe the connection and the task it enables; avoid claims of official affiliation.

The pillars and arch form one continuous SVG path in every variant. Keep that silhouette unified: separate shapes that meet at an exact edge can show antialiasing seams when a browser scales the logo.

The app icon uses a soft white-to-sage surface (`#FFFFFF` to `#EAEFE7`), an inset sage outline (`#72B7A0` at 60% opacity), and the bridge in a restrained teal gradient (`#2A9180` to `#17675D`). A diffuse shadow gives the bridge slight depth. Keep the generous spacing and rounded corners; the silhouette should remain readable at small sizes. The macOS variant uses the same tile and bridge, inset by 100 pixels in a 1024-pixel transparent canvas with a subtle outer shadow. This gives the Dock and Finder icon breathing room; keep the full tile for website cards and the setup window. The menu bar uses a dedicated monochrome template with the tile outline and bridge silhouette, optically sized for 18 points, so macOS can adapt it to light and dark appearances.

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
| `mark.svg` | Teal gradient mark; 96 × 96 view box |
| `mark-mono.svg` | Black mark for single color and template rendering |
| `wordmark.svg` | Transparent horizontal logo |
| `app-icon.svg`, `app-icon.png` | 1024 × 1024 app icon; soft sage surface and transparent rounded corners |
| `macos-icon.svg`, `macos-icon.png` | 1024 × 1024 macOS source; inset tile and transparent outer spacing |
| `menu-bar-template.svg`, `menu-bar-template.png`, `menu-bar-template@2x.png` | 18 pt menu bar icon at 1× and 2×; render as an AppKit template image |
| `github-banner.svg` | 1280 × 640 README hero with the current app icon |
| `social-card.svg`, `social-card.png` | 1200 × 630 GitHub social preview and project sharing |

Keep the mark at least 16 pixels wide. Leave at least one pillar's width between the visible mark and surrounding content. Do not stretch, rotate, or add details inside the arch. Use the single color mark where gradients or several colors would reduce clarity. Keep the app tile intact when using it in banners and preview cards. The standalone mark and wordmark remain transparent.

## Regenerating PNGs

The checked-in SVGs are the source of truth. Export the app icon, macOS icon, and social card with resvg (the CLI or `@resvg/resvg-js` binding) to preserve their SVG drop shadows; ImageMagick's built-in SVG renderer may omit this filter. ImageMagick can export the monochrome template assets. The README banner and social-card SVGs embed the same app tile geometry and gradients, so update those inline definitions whenever the app icon changes. These tools are only needed when editing the artwork.

```sh
resvg assets/brand/app-icon.svg assets/brand/app-icon.png
resvg assets/brand/macos-icon.svg assets/brand/macos-icon.png
resvg assets/brand/social-card.svg assets/brand/social-card.png
magick -background none assets/brand/menu-bar-template.svg -resize 18x18 assets/brand/menu-bar-template.png
magick -background none assets/brand/menu-bar-template.svg -resize 36x36 assets/brand/menu-bar-template@2x.png
```

`scripts/build-macos.sh` bundles the committed app PNG for the setup window and generates `HermesBridgeTool.icns` from `macos-icon.png` for the macOS app, including 16–1024 pixel representations. Regenerate both PNGs whenever their SVGs change, then run `sh scripts/build-macos.sh` to verify both uses. Generated `.app` and `.icns` files remain in the ignored `dist/` folder.

Text rendering depends on installed fonts. Review the exported social card after changing a headline or typeface. The README uses `github-banner.svg` directly. GitHub's external link preview is a separate repository setting: upload `social-card.png` under **Settings → General → Social preview**. Committing the PNG does not update that setting; verify the repository's `usesCustomOpenGraphImage` value and the resulting preview after upload.
