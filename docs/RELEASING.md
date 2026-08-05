# Releasing

## Automated release

```bash
./scripts/publish.sh 2.1.1
```

The command updates the version, runs the release checks, builds artifacts,
commits the release, pushes the tag, waits for GitHub Actions, and falls back to
creating the GitHub release from local assets if necessary.

## Build without publishing

```bash
./scripts/check-release.sh
./scripts/make-release.sh
```

Generated files are stored in `dist/`.
