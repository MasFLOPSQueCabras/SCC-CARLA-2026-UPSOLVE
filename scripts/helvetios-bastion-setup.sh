#!/usr/bin/env bash
# Execute on the bastion; installs tools only under this team's home directory.
set -euo pipefail
root="$HOME/.local/cabrita-tools"
mkdir -p "$root/rpms" "$HOME/.local/bin" "$HOME/cabrita_serve/artifacts"
base=https://download.rockylinux.org/pub/rocky/10
for item in \
    BaseOS/x86_64/os/Packages/m/mtools-4.0.43-7.el10.x86_64.rpm \
    BaseOS/x86_64/os/Packages/r/rsync-3.5.0-3.el10_2.x86_64.rpm \
    AppStream/x86_64/os/Packages/l/libburn-1.5.6-6.el10.x86_64.rpm \
    AppStream/x86_64/os/Packages/l/libisofs-1.5.6-6.el10.x86_64.rpm \
    AppStream/x86_64/os/Packages/l/libisoburn-1.5.6-6.el10.x86_64.rpm \
    AppStream/x86_64/os/Packages/x/xorriso-1.5.6-6.el10.x86_64.rpm; do
    rpm="$root/rpms/${item##*/}"
    if [[ ! -s "$rpm" ]]; then
        curl --fail --location --retry 3 "$base/$item" -o "$rpm"
    fi
    rpmkeys --checksig "$rpm"
    (cd "$root"; rpm2cpio "$rpm" | cpio -idmu --quiet)
done
for tool in xorriso rsync mcopy mdir mtype; do
    cat > "$HOME/.local/bin/$tool" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
export LD_LIBRARY_PATH="$HOME/.local/cabrita-tools/usr/lib64:${LD_LIBRARY_PATH:-}"
exec "$HOME/.local/cabrita-tools/usr/bin/$(basename -- "$0")" "$@"
WRAPPER
    chmod 0755 "$HOME/.local/bin/$tool"
done
"$HOME/.local/bin/xorriso" -version
"$HOME/.local/bin/rsync" --version
# SSH noninteractive Bash reads .bashrc. Preserve its contents and place PATH first.
if ! grep -q '^# Cabrita team-local media tools$' "$HOME/.bashrc"; then
    cp -p "$HOME/.bashrc" "$root/bashrc.before"
    { printf '%s\n' '# Cabrita team-local media tools' 'export PATH="$HOME/.local/bin:$PATH"'; cat "$root/bashrc.before"; } > "$root/bashrc.new"
    cat "$root/bashrc.new" > "$HOME/.bashrc"
fi
sha256sum "$root"/rpms/*.rpm > "$root/rpms.sha256"
