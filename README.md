# FYB Recording Studio — standalone frontend

For GitHub Actions deployment to Hostinger, first-run setup, verification, and
rollback instructions, see [DEPLOYMENT.md](DEPLOYMENT.md).

This folder is the deployable website. It uses HTML, CSS, and vanilla JavaScript, with local images, fonts, and the original MP4. It does not load WordPress, PHP, Elementor, jQuery, a database, or remote font services. The WordPress installation and backups outside this folder were left unchanged.

## Run locally

From the original project directory:

```sh
python3 -m http.server 8080 --directory frontend
```

Open `http://localhost:8080/`. For production, upload the contents of `frontend` to any static host. No installation or build is required. Keep the asset folders and relative paths intact. A host with HTTP byte-range support is recommended for seeking in the video. Configure the host to use `404.html` for missing pages if desired.

## Recovered public content

| Path | Content |
| --- | --- |
| `/` | Complete studio homepage, expertise, testimonial video, five galleries, hours, rates, and footer |
| `/home/` | Homepage slug alias |
| `/hello-world/` | The original site's only published blog post |
| `/category/uncategorized/` | Archive for that published post |
| `/404.html` | Standalone missing-page screen |

The database has one published page (`Home`, ID 13), one default published blog post, and a draft Privacy Policy. Header and footer are Elementor templates, not separate pages. Other apparent pages in the template kit are imported demo templates, not published website pages. The homepage's demo podcast, newsletter, process, and blog blocks were explicitly hidden on desktop, tablet, and mobile; they remain absent from the public frontend. The unpublished Privacy Policy is not presented as a live policy.

## Source and visual decisions

- Recovered content, visibility settings, gallery image IDs/order, rates, contact details, and design tokens from the matching `u320571673_qHpHh.20261025060131.sql` backup.
- Reviewed the Hello Elementor theme, Elementor's generated page CSS and data, MetaSlider settings, media files, template kit, and `wp-content/uploads.zip`.
- Reviewed the supplied mobile screenshot and the 132.7-second reference video, sampling across its entire duration. The recording contains older content: a different hero image, earlier service labels and hours, and different gallery selections. The screenshot's awards-wall hero matches the current database, so the current database and mobile screenshot take precedence. The video informed desktop proportions, section layout, gallery captions, and footer styling.
- Preserved the current backup's Monday–Saturday “Open 24 Hours” schedule; Sunday was commented out in the source. The current day is highlighted in the studio's `America/New_York` time zone.
- Preserved the five original gallery groups and all 15 slide entries, including the repeated Room C photo. Original image files and video are copied without modification. All 32 migrated source assets match the uploads ZIP by SHA-256. `asset-manifest.json` maps each asset to its original file.
- Matched the reference's rendered sans-serif headings and hero text. The original local font files are included; no font requests go to the old hosting domain.

## Functionality

- Mobile navigation opens and closes with its button, backdrop, and Escape; keyboard focus stays inside the open menu and returns to its trigger.
- Every gallery has automatic rotation, previous/next controls, arrow-key navigation, swipe support, and a play/pause control. Rotation pauses offscreen, in background tabs, during hover/focus, and by default for reduced-motion users.
- The testimonial uses the original local video with native playback, seek, volume, and fullscreen controls. Browser-native controls remain available when JavaScript is disabled.
- “Get Quote” now reaches the actual rates and contact information. The original button linked only to `#`. Expertise links reach contact details; rate rows link to their corresponding galleries.
- Telephone and email links open the visitor's dialer/email app. The address links to Maps. Instagram uses the exact profile from the source.
- Facebook, Twitter, and YouTube had no profile URLs in the source. Their original visual icons are retained as non-interactive icons rather than inventing destinations.
- There was no active booking, payment, contact-submission, or newsletter form on the published studio page. The static site does not claim to send messages or accept bookings. WordPress administration, authentication, and blog comment submission are backend features and are not implemented here.

## Editing

- `index.html`: homepage content and local asset references.
- `styles.css`: responsive layouts, colors, and typography.
- `app.js`: navigation, galleries, video launch, and current-day highlight.
- `assets/`: self-contained original site media and fonts.
- `home/index.html`: static homepage alias; keep this in sync when changing homepage markup.

## Verification

`verification.json` records the completed browser checks. The reusable `scripts/verify.mjs` checks routes, internal links, all local assets, seven viewport widths, image loading, mobile navigation, every gallery, video playback, and rendering without JavaScript. It starts and stops its own local HTTP server. Playwright is needed only to rerun the checks, not to run the website.

With Playwright available:

```sh
node scripts/verify.mjs
```

For an existing Playwright installation or a system Chrome executable, set `PLAYWRIGHT_MODULE` and `CHROME_PATH`. Screenshots and detailed results default to `/tmp/fyb-frontend-qa`; override with `QA_OUTPUT` if needed. External Instagram/Maps availability and email/phone delivery are outside the local-site verification.
