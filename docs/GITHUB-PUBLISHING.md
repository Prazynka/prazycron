# Publishing PrazyCron on GitHub

The automated publisher targets:

```text
https://github.com/Prazynka/prazycron
```

## First public release

From the project directory run:

```bash
./scripts/publish.sh
```

The script performs these operations:

1. Checks or installs required Linux packages.
2. Opens GitHub CLI browser authentication when it is not already configured.
3. Runs tests and builds the DEB package, source archive, and checksums.
4. Initializes Git and creates the initial commit.
5. Creates the public `Prazynka/prazycron` repository when it does not exist.
6. Pushes `main`, creates the release tag, and publishes the release assets.
7. Configures the project description, topics, Issues, workflow permissions, and private vulnerability reporting where permitted.

The GitHub account authorization is the only account-level confirmation that
cannot be bypassed safely.

## Later releases

```bash
./scripts/publish.sh 2.1.1
```

The optional version argument updates the package version and creates a release
notes template when one does not already exist. Edit the generated release notes
before publishing a production release.

## Different account or repository

```bash
PRAZYCRON_GITHUB_OWNER=example \
PRAZYCRON_GITHUB_REPO=prazycron \
./scripts/publish.sh
```
