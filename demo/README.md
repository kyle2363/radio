# Radio public demo

A focused, standalone browser showcase for AI Radio. Fictional catalog data,
deterministic recommendation rules, and original browser-synthesized audio make
the demo independent of the backend, personal library, and Android app.
The demo does not run live AI or access any visitor music.

The interface follows the desktop app's compact player and queue layout, navy
theme tokens, teal controls, and Orbit visualization. It demonstrates a subset
of the desktop UI; the Android app is described separately below the player.

## Preview

From the repository root, serve only the demo directory:

```powershell
backend\venv\Scripts\python -B -m http.server 4173 --bind 127.0.0.1 --directory demo
```

Visit http://127.0.0.1:4173. Playback requires clicking Play. Do not open the
HTML directly with `file://`, because browser modules require HTTP.

## Verify

```powershell
node --test demo/tests/*.test.mjs
```

Manual checks: play/pause, next, seeking, volume, each mood, new mix, likes,
Vibe lock, keyboard navigation, narrow mobile width, and reduced motion.

## Publish on GitHub Pages

1. Review and commit the demo and `.github/workflows/demo-pages.yml`, then push
   the branch. Merge to `main` when ready.
2. In the GitHub repository, choose **Settings → Pages → Source → GitHub Actions**.
3. The **Deploy radio demo** workflow runs on changes to the demo on `main`.
   It can also be run manually from Actions after the workflow is on the default
   branch (select the desired branch).
4. The workflow reports the published URL, expected to be
   `https://kyle2363.github.io/radio/` with the current repository name.

GitHub Free requires a public repository for Pages. If the app repository must
remain private on a free account, publish these demo files from a separate public
repository instead. Do not change repository visibility just to preview the demo.

The deployment artifact contains only `index.html`, `styles.css`, `app.js`,
`model.mjs`, and `.nojekyll`. No backend, credentials, real music, database, tests,
or project documentation is included. All asset paths are relative for project
site compatibility. This workflow owns the repository's Pages site; check for an
existing Pages site before enabling it.
