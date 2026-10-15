"""Read-only discipline, path guarding, and progress reporting.

Design rule: source data is NEVER written to. Every operation that produces
output writes to an explicit output directory, which must be distinct from
any source root. This structurally enforces the "work on copies, never
originals" pattern rather than relying on convention.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence, TypeVar

T = TypeVar("T")


class GeolensError(Exception):
    """Base error. Message text is safe to surface to a model or user."""


class PathNotAllowed(GeolensError):
    """Raised when a path falls outside the configured roots."""


class ReadOnlyViolation(GeolensError):
    """Raised on an attempt to write into a source root."""


@dataclass
class Guard:
    """Validates every filesystem path crossing the tool boundary.

    ``roots`` is an allowlist of directories that may be *read*. An empty
    allowlist means "no restriction", which is the sensible default for a
    single-user desktop install but can be tightened for shared deployments
    via the GEOLENS_ROOTS environment variable.

    ``out_dir`` is the only location that may be *written*. It is created on
    demand and is never permitted to sit inside a read root.
    """

    roots: list[Path] = field(default_factory=list)
    out_dir: Path = field(default_factory=lambda: Path.cwd() / "geolens_out")

    @classmethod
    def from_env(cls) -> "Guard":
        raw_roots = os.environ.get("GEOLENS_ROOTS", "").strip()
        roots = [
            Path(p).expanduser().resolve()
            for p in raw_roots.split(os.pathsep)
            if p.strip()
        ]
        raw_out = os.environ.get("GEOLENS_OUT", "").strip()
        out = (
            Path(raw_out).expanduser().resolve()
            if raw_out
            else Path.cwd().resolve() / "geolens_out"
        )
        return cls(roots=roots, out_dir=out)

    # -- reading ---------------------------------------------------------

    def read_path(self, path: str | Path) -> Path:
        """Resolve ``path`` for reading, or raise ``PathNotAllowed``."""
        p = Path(path).expanduser()
        try:
            p = p.resolve(strict=False)
        except (OSError, RuntimeError) as exc:  # pragma: no cover - platform
            raise PathNotAllowed(f"Cannot resolve path: {path} ({exc})") from exc

        if not p.exists():
            raise PathNotAllowed(f"Path does not exist: {p}")

        if not self.roots:
            return p

        for root in self.roots:
            if p == root or root in p.parents:
                return p
        allowed = ", ".join(str(r) for r in self.roots)
        raise PathNotAllowed(
            f"Path is outside the allowed roots.\n  path:  {p}\n  roots: {allowed}"
        )

    # -- writing ---------------------------------------------------------

    def write_path(self, name: str) -> Path:
        """Return a writable path inside ``out_dir`` for the given filename."""
        safe = Path(name).name  # strip any directory component
        if not safe or safe in {".", ".."}:
            raise GeolensError(f"Invalid output filename: {name!r}")

        out = self.out_dir.resolve()
        for root in self.roots:
            if out == root or root in out.parents:
                raise ReadOnlyViolation(
                    "Output directory sits inside a read-only source root. "
                    "Set GEOLENS_OUT to a location outside your data folders.\n"
                    f"  out:  {out}\n  root: {root}"
                )
        out.mkdir(parents=True, exist_ok=True)
        return out / safe


# -- progress -----------------------------------------------------------------
#
# Progress is written to STDERR, never stdout. The MCP server speaks JSON-RPC
# over stdout, so anything printed there corrupts the protocol stream.


def _supports_progress() -> bool:
    if os.environ.get("GEOLENS_NO_PROGRESS"):
        return False
    return True


def progress(
    items: Sequence[T] | Iterable[T],
    label: str = "working",
    total: int | None = None,
    width: int = 28,
) -> Iterator[T]:
    """Yield from ``items`` behind a progress bar.

    Uses tqdm when installed — ``tqdm.auto`` picks the notebook widget inside
    Jupyter and the terminal bar elsewhere — and falls back to a plain ASCII bar
    otherwise, so the package has no hard dependency on it.

    Output goes to STDERR in both cases. The MCP server speaks JSON-RPC over
    stdout; anything written there corrupts the protocol stream.
    """
    if total is None:
        try:
            total = len(items)  # type: ignore[arg-type]
        except TypeError:
            total = None

    if not _supports_progress() or not total:
        yield from items
        return

    if os.environ.get("GEOLENS_PROGRESS") == "json":
        # Machine-readable, for the QGIS plugin: one line per update, at
        # most a few a second, parsed into the Processing progress bar.
        import json as _json
        last = 0.0
        n = 0
        for n, item in enumerate(items, 1):
            yield item
            now = time.monotonic()
            if now - last > 0.25 or n == total:
                last = now
                _safe_write(sys.stderr, "GEOLENS_PROGRESS " + _json.dumps(
                    {"label": label, "n": n, "total": total}) + "\n")
                sys.stderr.flush()
        return

    try:
        from tqdm.auto import tqdm

        yield from tqdm(
            items, total=total, desc=label, unit="it", leave=False,
            file=sys.stderr, dynamic_ncols=True,
            # tqdm draws with block characters too, so it has the same cp1252
            # problem as the fallback bar below. `ascii=True` is tqdm's own
            # switch for it; passing it conditionally keeps the nice bar
            # wherever the stream can actually take it.
            ascii=not _stream_can_encode(sys.stderr, "\u2588\u2591"),
            bar_format="  {desc:<24s} {bar} {percentage:3.0f}% {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
        )
        return
    except ImportError:
        pass

    stream = sys.stderr
    start = time.monotonic()
    last_draw = 0.0
    # Decided once, not per redraw. ASCII is not a downgrade worth apologising
    # for -- it is a bar that draws instead of a traceback.
    full, empty = (("\u2588", "\u2591")
                   if _stream_can_encode(stream, "\u2588\u2591") else ("#", "-"))

    def draw(n: int, force: bool = False) -> None:
        nonlocal last_draw
        now = time.monotonic()
        if not force and now - last_draw < 0.08:
            return
        last_draw = now
        frac = n / total if total else 1.0
        filled = int(width * frac)
        bar = full * filled + empty * (width - filled)
        elapsed = now - start
        pct = f"{frac * 100:5.1f}%"
        _safe_write(
            stream,
            f"\r  {label:<24s} [{bar}] {pct}  {n}/{total}  {elapsed:5.1f}s")
        stream.flush()

    draw(0, force=True)
    n = 0
    for n, item in enumerate(items, 1):
        yield item
        draw(n)
    draw(n, force=True)
    stream.write("\n")
    stream.flush()


def _stream_can_encode(stream, probe: str) -> bool:
    """Will this stream accept these characters?

    Windows is the reason. A Python console is UTF-8, but the moment output is
    redirected -- ``geolens landscape dem.tif > run.log``, which is what anyone
    does for a job that takes hours -- the stream falls back to the locale
    encoding, cp1252 for a US/Western install. cp1252 has the em dash and the
    superscript two this package uses freely, so those are fine; it does NOT
    have U+2588 or U+2591, the block characters the progress bar is drawn
    with. Writing one raises UnicodeEncodeError and takes the whole run with
    it -- a progress bar, the one thing on screen whose entire job is to be
    reassuring, killing an overnight job at the first redraw.
    """
    enc = getattr(stream, "encoding", None) or "utf-8"
    try:
        probe.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError, TypeError):
        return False


def _safe_write(stream, text: str) -> None:
    """Write, degrading unrepresentable characters rather than raising.

    Nothing this package prints is worth ending a run over.
    """
    try:
        stream.write(text)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "ascii"
        try:
            stream.write(text.encode(enc, "replace").decode(enc, "replace"))
        except Exception:
            stream.write(text.encode("ascii", "replace").decode("ascii"))


def note(message: str) -> None:
    """Emit a status line to stderr (safe alongside MCP stdio)."""
    if _supports_progress():
        tag = ("GEOLENS_NOTE " if os.environ.get("GEOLENS_PROGRESS") == "json"
               else "  ")
        _safe_write(sys.stderr, f"{tag}{str(message).strip()}\n"
                    if tag.startswith("GEOLENS") else f"  {message}\n")
        sys.stderr.flush()
