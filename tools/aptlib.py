"""Small, dependency-free helpers for reading and writing Debian packages and
APT repository metadata. Only the subset needed by this repository is
implemented: ar archives, control.tar.{gz,xz,zst}, and deb822 paragraphs."""

import gzip
import hashlib
import io
import lzma
import re
import shutil
import subprocess
import tarfile

AR_MAGIC = b"!<arch>\n"
SUITE = re.compile(r"^[a-z][a-z0-9-]*$")
PACKAGE = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")

# Packages are only ever fetched from release assets of this organisation.
# The Cloudflare redirect rule mirrors this mapping exactly:
#   https://apt.gufo.blog/pool/<repo>/<tag>/<file>
#   -> https://github.com/gufo-org/<repo>/releases/download/<tag>/<file>
RELEASE_URL = re.compile(
    r"^https://github\.com/gufo-org/([A-Za-z0-9][A-Za-z0-9._-]*)/releases/download/"
    r"([A-Za-z0-9][A-Za-z0-9._+-]*)/([A-Za-z0-9][A-Za-z0-9._+~-]*\.deb)$"
)


def pool_path(url):
    match = RELEASE_URL.match(url)
    if not match:
        raise ValueError(f"not a gufo-org release asset URL ending in .deb: {url}")
    repo, tag, name = match.groups()
    return f"pool/{repo}/{tag}/{name}"


def release_url(filename):
    parts = filename.split("/")
    if len(parts) != 4 or parts[0] != "pool":
        raise ValueError(f"not a pool path: {filename}")
    _, repo, tag, name = parts
    url = f"https://github.com/gufo-org/{repo}/releases/download/{tag}/{name}"
    if not RELEASE_URL.match(url):
        raise ValueError(f"not a pool path: {filename}")
    return url


def ar_members(data):
    if not data.startswith(AR_MAGIC):
        raise ValueError("not an ar archive")
    offset = len(AR_MAGIC)
    while offset < len(data):
        header = data[offset : offset + 60]
        if len(header) < 60 or header[58:60] != b"`\n":
            raise ValueError("corrupt ar header")
        name = header[:16].decode().strip().rstrip("/")
        size = int(header[48:58].decode().strip())
        start = offset + 60
        yield name, data[start : start + size]
        offset = start + size + (size % 2)


def _decompress(name, blob):
    if name.endswith(".gz"):
        return gzip.decompress(blob)
    if name.endswith(".xz"):
        return lzma.decompress(blob)
    if name.endswith(".zst"):
        if not shutil.which("zstd"):
            raise RuntimeError("zstd is required to read this package")
        return subprocess.run(
            ["zstd", "-dc"], input=blob, capture_output=True, check=True
        ).stdout
    if name.endswith(".tar"):
        return blob
    raise ValueError(f"unsupported control member {name}")


def deb_control(data):
    """Return the raw control paragraph of a .deb, without a trailing newline."""
    members = dict(ar_members(data))
    if members.get("debian-binary", b"").strip() != b"2.0":
        raise ValueError("not a Debian binary package")
    name = next((m for m in members if m.startswith("control.tar")), None)
    if name is None:
        raise ValueError("package has no control archive")
    with tarfile.open(fileobj=io.BytesIO(_decompress(name, members[name]))) as tar:
        for member in tar.getmembers():
            if member.name in ("./control", "control"):
                text = tar.extractfile(member).read().decode()
                return text.strip("\n")
    raise ValueError("control archive has no control file")


def parse_paragraph(text):
    """Parse one deb822 paragraph into an ordered dict of field -> value."""
    fields = {}
    current = None
    for line in text.split("\n"):
        if not line:
            continue
        if line[0] in " \t":
            if current is None:
                raise ValueError("continuation line before first field")
            fields[current] += "\n" + line
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"malformed control line: {line!r}")
        current = key.strip()
        fields[current] = value.strip()
    return fields


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _ar_entry(name, data, mtime):
    header = (
        f"{name + '/':<16}{mtime:<12}{0:<6}{0:<6}{0o100644:<8o}{len(data):<10}`\n"
    ).encode()
    assert len(header) == 60
    return header + data + (b"\n" if len(data) % 2 else b"")


def _tar_gz(files, mtime):
    """files: list of (path, mode, bytes or None for a directory)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for path, mode, content in files:
            info = tarfile.TarInfo(path)
            info.mtime = mtime
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mode = mode
            if content is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def build_deb(control, files, conffiles=(), mtime=0):
    """Build a reproducible .deb. files: list of (path, mode, bytes or None)."""
    control_files = [("./", 0o755, None), ("./control", 0o644, control.encode())]
    if conffiles:
        control_files.append(
            ("./conffiles", 0o644, "".join(f"{c}\n" for c in conffiles).encode())
        )
    data_files = [("./", 0o755, None)] + [(f"./{p.lstrip('/')}", m, c) for p, m, c in files]
    return (
        AR_MAGIC
        + _ar_entry("debian-binary", b"2.0\n", mtime)
        + _ar_entry("control.tar.gz", _tar_gz(control_files, mtime), mtime)
        + _ar_entry("data.tar.gz", _tar_gz(data_files, mtime), mtime)
    )
