"""Sequential, non-destructive folder intake with stable PDF snapshots."""
import math
import os
import stat
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable, Iterator

from .config import ConfigurationError, LLMSettings
from .invoice import InvoiceFormatError
from .llm_client import APIError
from .pdf_text import TextExtractionError
from .pipeline import ProcessResult, process_pdf
from .storage import DocumentBusyError, DocumentStore, StorageError, content_sha256

MAX_INTAKE_BYTES = 20 * 1024 * 1024


class SourceChanged(Exception):
    """The producer changed, removed, or locked the file; try a later poll."""


@dataclass
class Observation:
    signature: tuple[int, ...]
    since: float
    handled: bool = False
    notice: str | None = None


@dataclass(frozen=True)
class IntakeEvent:
    source: Path
    status: str
    result: ProcessResult | None = None
    message: str = ""


def signature(info: os.stat_result) -> tuple[int, ...]:
    # On Windows/Python 3.12, stat and fstat can expose different ctime
    # semantics for the same unchanged file. Compare identity, size and mtime.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def wait_for_quiet_period(seconds: float) -> None:
    """Wait on the same elapsed clock as observations, including coarse clocks."""
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        time.sleep(remaining)


def read_stable_snapshot(source: Path, expected: tuple[int, ...]) -> bytes:
    """Bound memory and compare the open file and directory entry before/after."""
    try:
        with source.open("rb") as stream:
            if signature(os.fstat(stream.fileno())) != expected:
                raise SourceChanged
            content = stream.read(MAX_INTAKE_BYTES + 1)
            after = signature(os.fstat(stream.fileno()))
        current = signature(source.stat(follow_symlinks=False))
    except (FileNotFoundError, PermissionError) as exc:
        raise SourceChanged from exc
    if after != expected or current != expected:
        raise SourceChanged
    if len(content) > MAX_INTAKE_BYTES:
        raise ValueError("PDF exceeds the 20 MB intake limit.")
    if len(content) != expected[2]:
        raise SourceChanged
    return content


def archive_snapshot(archive: Path, source: Path, content: bytes, digest: str) -> Path:
    """Publish a complete managed copy; retain matching existing snapshots."""
    destination = archive / digest / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if content_sha256(destination.read_bytes()) != digest:
            raise OSError(f"Archived PDF was modified: {destination}")
        return destination
    temporary = None
    try:
        with NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        # A competing watcher can only publish the same content at this path.
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


class FolderIntake:
    def __init__(
        self, incoming: Path, store: DocumentStore, *,
        archive: Path = Path("data/intake"), exports: Path = Path("output/intake"),
        stable_seconds: float = 2, retry_failed: bool = False,
        settings_loader: Callable[[], LLMSettings] | None = None,
        today: Callable[[], date] = date.today,
        clock: Callable[[], float] = time.monotonic,
        processor: Callable[[Path, Path], ProcessResult] | None = None,
    ) -> None:
        if not math.isfinite(stable_seconds) or stable_seconds <= 0:
            raise ValueError("--stable-seconds must be a positive finite number.")
        self.incoming = Path(incoming).resolve()
        self.archive = Path(archive).resolve()
        self.exports = Path(exports).resolve()
        for destination in (self.archive, self.exports):
            if (destination == self.incoming or destination.is_relative_to(self.incoming)
                    or self.incoming.is_relative_to(destination)):
                raise ValueError("The incoming folder must not overlap archive or output folders.")
        if processor is None and store.input_kind != "llm":
            raise StorageError("Live intake requires a separate LLM result database.")
        self.incoming.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.stable_seconds = stable_seconds
        self.retry_failed = retry_failed
        self.settings_loader = settings_loader
        self.today = today
        self.clock = clock
        self.processor = processor
        self.observations: dict[Path, Observation] = {}
        self.pending = 0

    def candidates(self) -> tuple[Path, ...]:
        """Only direct PDF children; no recursive traversal or symlink intake."""
        return tuple(sorted(
            (path for path in self.incoming.iterdir() if path.suffix.lower() == ".pdf"),
            key=lambda path: path.name.casefold(),
        ))

    def poll(self, paths: tuple[Path, ...] | None = None) -> Iterator[IntakeEvent]:
        paths = self.candidates() if paths is None else paths
        present = set(paths)
        self.observations = {p: item for p, item in self.observations.items() if p in present}
        self.pending = 0
        for source in paths:
            try:
                info = source.stat(follow_symlinks=False)
            except FileNotFoundError:
                self.observations.pop(source, None)
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            current, now = signature(info), self.clock()
            observation = self.observations.get(source)
            if observation is None or observation.signature != current:
                observation = self.observations[source] = Observation(current, now)
            if observation.handled:
                continue
            if now - observation.since < self.stable_seconds:
                self.pending += 1
                continue
            try:
                event = self._process(source, observation)
            except (SourceChanged, DocumentBusyError):
                # Reset the quiet period after a copy race or active reservation.
                observation.since = self.clock()
                self.pending += 1
                if observation.notice != "deferred":
                    observation.notice = "deferred"
                    yield IntakeEvent(source, "deferred", message="File changed, is locked, or is being processed; waiting.")
                continue
            observation.handled = True
            yield event

    def _process(self, source: Path, observation: Observation) -> IntakeEvent:
        digest = None
        try:
            content = read_stable_snapshot(source, observation.signature)
            digest = content_sha256(content)
            receipt = self.store.intake_receipt(source, digest)
            if receipt is not None:
                if receipt["state"] == "completed":
                    return IntakeEvent(source, "skipped", message=f"Already imported as document #{receipt['document_id']}.")
                if not self.retry_failed:
                    return IntakeEvent(source, "skipped_failed", message=f"Previous {receipt['error_type']}; use --retry-failed after fixing the input.")
            snapshot = archive_snapshot(self.archive, source, content, digest)
            output = self.exports / digest
            if self.processor is not None:
                result = self.processor(snapshot, output)
            else:
                result = process_pdf(
                    snapshot, None, output, store=self.store, overwrite=True,
                    settings_loader=self.settings_loader, reference_date=self.today(),
                )
            self.store.record_intake(source, digest, document_id=result.document_id)
            return IntakeEvent(source, "duplicate" if result.duplicate else "processed", result)
        except (APIError, ConfigurationError, StorageError, SourceChanged):
            # Stop on service/config/storage failure, or defer a changing input.
            # No failure receipt: restarting is an explicit retry, not a loop.
            raise
        except (TextExtractionError, InvoiceFormatError, OSError, ValueError, UnicodeError) as exc:
            if digest is not None:
                self.store.record_intake(source, digest, error_type=type(exc).__name__)
            return IntakeEvent(source, "failed", message=f"{type(exc).__name__}: {exc}")
