# Packaging

Distribution packages for Cozempic across ecosystems. Each directory is self-contained.

On every version bump, refresh the `version`, URL, and `sha256` fields with:

```bash
VERSION=X.Y.Z
curl -sL "https://files.pythonhosted.org/packages/source/c/cozempic/cozempic-${VERSION}.tar.gz" -o /tmp/cozempic.tgz
echo "size: $(wc -c < /tmp/cozempic.tgz)"
echo "sha256: $(shasum -a 256 /tmp/cozempic.tgz | awk '{print $1}')"
echo "rmd160: $(openssl dgst -rmd160 /tmp/cozempic.tgz | awk '{print $NF}')"
python3 -c "import base64; print('nix-sri: sha256-' + base64.b64encode(bytes.fromhex('<sha256>')).decode())"
```

## Homebrew (`homebrew/cozempic.rb`)

Published via the self-hosted tap at `Ruya-AI/homebrew-cozempic`.

To release a new version:

```bash
cd /tmp && git clone https://github.com/Ruya-AI/homebrew-cozempic.git
cp <this-repo>/packaging/homebrew/cozempic.rb homebrew-cozempic/Formula/cozempic.rb
cd homebrew-cozempic && git add Formula/cozempic.rb
git commit -m "cozempic X.Y.Z" && git push
```

## AUR (`aur/PKGBUILD`, `aur/.SRCINFO`)

Requires an AUR account with an SSH key registered at https://aur.archlinux.org/account/.

First-time submission:

```bash
git clone ssh://aur@aur.archlinux.org/cozempic.git /tmp/aur-cozempic
cp packaging/aur/PKGBUILD packaging/aur/.SRCINFO /tmp/aur-cozempic/
cd /tmp/aur-cozempic && git add PKGBUILD .SRCINFO
git commit -m "Initial import, cozempic 1.7.1" && git push
```

Subsequent bumps:

```bash
# On an Arch box (or docker run -it archlinux):
cd /tmp/aur-cozempic
# update pkgver in PKGBUILD, then:
makepkg --printsrcinfo > .SRCINFO
git commit -am "cozempic X.Y.Z" && git push
```

## Nixpkgs (`nix/default.nix`, `nix/flake.nix`)

### Local use (flake)

```bash
nix run github:Ruya-AI/cozempic?dir=packaging/nix -- --help
# or
nix profile install github:Ruya-AI/cozempic?dir=packaging/nix
```

### Upstream PR to nixpkgs

Copy `default.nix` into `nixpkgs` and register it:

```bash
git clone https://github.com/NixOS/nixpkgs ~/nixpkgs
mkdir -p ~/nixpkgs/pkgs/by-name/co/cozempic
cp packaging/nix/default.nix ~/nixpkgs/pkgs/by-name/co/cozempic/package.nix
cd ~/nixpkgs && git checkout -b cozempic-init
# by-name auto-registers; no all-packages.nix edit needed.
nix-build -A cozempic   # sanity check
git add pkgs/by-name/co/cozempic && git commit -m "cozempic: init at 1.7.1"
git push origin cozempic-init
gh pr create --repo NixOS/nixpkgs --title "cozempic: init at 1.7.1"
```

## MacPorts (`macports/Portfile`)

Fork https://github.com/macports/macports-ports, then:

```bash
mkdir -p python/py-cozempic
cp packaging/macports/Portfile python/py-cozempic/Portfile
# From a Mac with MacPorts installed:
cd python/py-cozempic && port lint
sudo port -v install subport=py312-cozempic  # test build
git checkout -b py-cozempic-init && git add python/py-cozempic
git commit -m "py-cozempic: new port, version 1.7.1" && git push
gh pr create --repo macports/macports-ports --title "py-cozempic: new port, version 1.7.1"
```
