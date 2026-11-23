# Gufo APT repository

Signed APT repository for packages built in `gufo-org`, served at
<https://apt.gufo.blog>. It currently provides `gufo` for Debian 13 (trixie),
amd64, AMD Strix Halo (gfx1151).

## Install

`gufo` depends on AMD's ROCm 10.1 packages: configure AMD's ROCm apt
repository first (see the `base` stage of
[`deb/Dockerfile`](https://github.com/gufo-org/toolboxes/blob/main/deb/Dockerfile)).
Then install the keyring package, which adds this repository's signing key and
APT source:

```sh
curl -fsSLo /tmp/gufo-archive-keyring.deb https://apt.gufo.blog/gufo-archive-keyring.deb
sudo apt install /tmp/gufo-archive-keyring.deb
sudo apt update
sudo apt install gufo
```

`apt upgrade` installs new Gufo releases and renewed signing keys. Install a
specific version with `sudo apt install gufo=0.36.0-1`.

To remove Gufo and the repository:

```sh
sudo apt purge gufo gufo-archive-keyring
```

The signing key's fingerprint is
`077A EBA3 DFCF 360A 72EA  2D49 FD8D F2D4 80A2 9245`
(`Gufo APT Repository <apt@gufo.blog>`). To configure the source by hand
instead, download <https://apt.gufo.blog/gufo-archive-keyring.asc> to
`/etc/apt/keyrings/`, check its fingerprint, and add:

```
Types: deb
URIs: https://apt.gufo.blog
Suites: trixie
Components: main
Architectures: amd64
Signed-By: /etc/apt/keyrings/gufo-archive-keyring.asc
```

## How it works

GitHub Pages serves the signed index (`dists/`), the public key and the
keyring package. Packages are not stored here: a Cloudflare redirect rule maps
`https://apt.gufo.blog/pool/<repo>/<tag>/<file>` to
`https://github.com/gufo-org/<repo>/releases/download/<tag>/<file>`, so every
package is the release asset its producer published, and the asset's download
count includes apt installs. `tools/serve` reproduces that mapping locally.

`packages/<name>.json` records every published package: its pool path, size,
SHA-256 and control data. Entries are append-only; a published version can't
change.

| Path | Purpose |
| :--- | :--- |
| `tools/add-package` | Download a release asset, verify its SHA-256, record it |
| `tools/build-index` | Build `dists/` from the manifests and sign it |
| `tools/build-keyring` | Build `gufo-archive-keyring` from `keys/` |
| `tools/serve` | Serve a built site with the pool redirect |
| `tests/smoke-test` | Install from a built site in a clean Debian container |

## Publishing a package

Publish the release first; draft release assets aren't downloadable. Then send
a repository dispatch with a token that has Contents write on this repository:

```sh
gh api repos/gufo-org/apt/dispatches -f event_type=add-package \
  -F 'client_payload[url]=https://github.com/gufo-org/gufo/releases/download/v0.36.0/gufo_0.36.0-1_amd64.deb' \
  -F 'client_payload[sha256]=<sha256>' -F 'client_payload[suite]=trixie'
```

Or run **Publish APT repository** manually with the same inputs. The workflow
records the package, rebuilds and signs the index, installs from the result in
a clean Debian container, and deploys Pages. Retrying an identical package is
a no-op; publishing different content for an existing version fails. Runs are
serialized.

Other repositories in `gufo-org` can publish here the same way. Add a suite
named after its Debian codename (for example `forky`) when a package targets
another Debian release.

## Signing key

The primary key (certify only, no expiry), its passphrase and its revocation
certificate are kept in the organisation's Bitwarden vault. CI holds only the
signing subkey `6A0F 2CED AFFE 4833 A9D3  7E84 4123 7F3C 1785 BA21`, in the
`APT_SIGNING_KEY` and `APT_SIGNING_PASSPHRASE` secrets. **It expires on
2028-11-22.**

apt verifies against the public key installed on each client, so a renewed key
must reach clients before the old expiry. Renew about three months early:

1. Import the primary key from Bitwarden into a temporary keyring:
   `export GNUPGHOME=$(mktemp -d -p /dev/shm)` and `gpg --import`.
2. Extend the subkey:
   `gpg --quick-set-expire 077AEBA3DFCF360A72EA2D49FD8DF2D480A29245 2y 6A0F2CEDAFFE4833A9D37E8441237F3C1785BA21`.
3. Replace the CI secret:
   `gpg --armor --export-secret-subkeys '6A0F2CEDAFFE4833A9D37E8441237F3C1785BA21!' | gh secret set APT_SIGNING_KEY -R gufo-org/apt`.
4. Export the public key to `keys/gufo-archive-keyring.asc`, run
   `tools/build-keyring YYYY.MM.DD` and
   `tools/add-package --keyring static/keyring/gufo-archive-keyring_YYYY.MM.DD_all.deb`,
   then open a pull request. Merging it deploys the new keyring package.
5. Remove the temporary keyring: `rm -rf "$GNUPGHOME"`.

Clients that upgrade before the old expiry continue without intervention.
Others must reinstall the keyring package as shown in [Install](#install).

## Development

```sh
python3 -m unittest discover -s tests -v
export GNUPGHOME=$(mktemp -d)
gpg --batch --passphrase '' --quick-gen-key 'Test <test@invalid>' ed25519 sign 1d
fpr=$(gpg --list-keys --with-colons | awk -F: '/^fpr/{print $10; exit}')
gpg --armor --export "$fpr" > /tmp/test-key.asc
tools/build-index /tmp/site --sign "$fpr"
tests/smoke-test /tmp/site /tmp/test-key.asc   # needs Docker
```
