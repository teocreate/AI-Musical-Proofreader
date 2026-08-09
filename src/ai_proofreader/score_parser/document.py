"""The source document: the live XML tree plus the handle table that ties the IR to it.

This is the mechanism behind architecture decision §2.1 — corrections are patches on the file
MuseScore wrote, not a re-serialization of our model. The parser registers every element it turns
into an IR object and stores the returned integer handle on that object; the applier resolves the
handle back to the element and edits it in place. Layout, credits, engraving hints and everything
else we do not model survive untouched.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from tempfile import TemporaryDirectory

from lxml import etree

from .errors import MuseScoreNotAvailableError, ScoreLoadError, UnsupportedFormatError

__all__ = ["MUSICXML_SUFFIXES", "SourceDocument", "find_musescore", "localname"]

MUSICXML_SUFFIXES = frozenset({".musicxml", ".xml"})
COMPRESSED_SUFFIXES = frozenset({".mxl"})
MUSESCORE_SUFFIXES = frozenset({".mscz", ".mscx"})

_CONTAINER_PATH = "META-INF/container.xml"
_MUSESCORE_CANDIDATES = (
    "mscore",
    "musescore",
    "MuseScore",
    "MuseScore4",
    "mscore4portable",
    "/Applications/MuseScore 4.app/Contents/MacOS/mscore",
    "/Applications/MuseScore 3.app/Contents/MacOS/mscore",
    r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
)


def localname(tag: object) -> str:
    """Element tag without its namespace.

    MusicXML is normally namespace-free, but files that have been through XSLT or certain
    editors arrive namespaced. Stripping at the accessor keeps the parser free of the usual
    ``{ns}note`` noise.
    """
    if not isinstance(tag, str):
        return ""
    return tag.rpartition("}")[2]


def find_musescore(explicit: str | None = None) -> str | None:
    """Locate a MuseScore executable, or return ``None``.

    Order: explicit argument, ``MUSESCORE_PATH``, then the usual names and install locations.
    """
    import os

    candidates = [explicit, os.environ.get("MUSESCORE_PATH"), *_MUSESCORE_CANDIDATES]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


@dataclass
class SourceDocument:
    """A loaded MusicXML document and its element handle table."""

    path: Path
    tree: etree._ElementTree
    source_format: str = "musicxml"
    #: For ``.mxl`` inputs: the other zip entries, so a save can rebuild an equivalent container.
    container_entries: dict[str, bytes] = field(default_factory=dict)
    container_rootfile: str = "score.xml"
    _elements: list[etree._Element] = field(default_factory=list, repr=False)

    # -- loading -------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | PathLike[str], musescore_binary: str | None = None) -> SourceDocument:
        """Read a score from disk.

        Accepts ``.musicxml`` / ``.xml`` (plain), ``.mxl`` (zip container) and ``.mscz`` /
        ``.mscx`` (converted through MuseScore).
        """
        source = Path(path)
        if not source.is_file():
            raise ScoreLoadError(f"No such file: {source}")
        suffix = source.suffix.lower()

        if suffix in MUSICXML_SUFFIXES:
            return cls._from_xml_bytes(source.read_bytes(), source, "musicxml")
        if suffix in COMPRESSED_SUFFIXES:
            return cls._from_mxl(source)
        if suffix in MUSESCORE_SUFFIXES:
            return cls._from_musescore(source, musescore_binary)
        raise UnsupportedFormatError(
            f"{source.name}: expected .musicxml, .xml, .mxl or .mscz, got {suffix or 'no suffix'}"
        )

    @classmethod
    def _parser(cls) -> etree.XMLParser:
        # huge_tree: orchestral scores blow past libxml2's default limits.
        # resolve_entities=False: MusicXML files are untrusted input; no external entity loading.
        return etree.XMLParser(
            remove_blank_text=False,
            resolve_entities=False,
            huge_tree=True,
            recover=True,
        )

    @classmethod
    def _from_xml_bytes(cls, data: bytes, path: Path, source_format: str) -> SourceDocument:
        try:
            root = etree.fromstring(data, parser=cls._parser())
        except etree.XMLSyntaxError as exc:  # pragma: no cover - defensive
            raise ScoreLoadError(f"{path.name} is not valid XML: {exc}") from exc
        if root is None:
            raise ScoreLoadError(f"{path.name} contains no XML document")
        tag = localname(root.tag)
        if tag not in {"score-partwise", "score-timewise"}:
            raise ScoreLoadError(
                f"{path.name}: root element is <{tag}>, expected <score-partwise>. "
                "Timewise MusicXML must be converted before analysis."
            )
        if tag == "score-timewise":
            raise UnsupportedFormatError(
                "score-timewise MusicXML is not supported; export partwise from MuseScore."
            )
        return cls(path=path, tree=etree.ElementTree(root), source_format=source_format)

    @classmethod
    def _from_mxl(cls, path: Path) -> SourceDocument:
        """Unwrap a compressed MusicXML container, keeping the other entries for re-saving."""
        try:
            with zipfile.ZipFile(path) as archive:
                entries = {name: archive.read(name) for name in archive.namelist()}
        except zipfile.BadZipFile as exc:
            raise ScoreLoadError(f"{path.name} is not a readable .mxl archive: {exc}") from exc

        rootfile = cls._rootfile_from_container(entries)
        if rootfile not in entries:
            raise ScoreLoadError(f"{path.name}: container names {rootfile!r}, which is not present")

        document = cls._from_xml_bytes(entries[rootfile], path, "mxl")
        document.container_entries = {k: v for k, v in entries.items() if k != rootfile}
        document.container_rootfile = rootfile
        return document

    @staticmethod
    def _rootfile_from_container(entries: dict[str, bytes]) -> str:
        container = entries.get(_CONTAINER_PATH)
        if container:
            try:
                root = etree.fromstring(container)
                for element in root.iter():
                    if localname(element.tag) == "rootfile":
                        full_path = element.get("full-path")
                        if full_path:
                            return full_path
            except etree.XMLSyntaxError:
                pass  # fall through to the heuristic below
        for name in entries:
            if name.lower().endswith((".xml", ".musicxml")) and not name.startswith("META-INF"):
                return name
        raise ScoreLoadError("compressed score contains no MusicXML part")

    @classmethod
    def _from_musescore(cls, path: Path, binary: str | None) -> SourceDocument:
        """Convert a MuseScore file by shelling out to MuseScore itself."""
        executable = find_musescore(binary)
        if not executable:
            raise MuseScoreNotAvailableError(
                f"{path.name} is a MuseScore file. Set MUSESCORE_PATH to your MuseScore "
                "executable, or export MusicXML from MuseScore (File → Export → MusicXML) "
                "and analyse that instead."
            )
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "converted.musicxml"
            try:
                result = subprocess.run(
                    [executable, "-o", str(target), str(path)],
                    capture_output=True,
                    timeout=180,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ScoreLoadError(f"MuseScore conversion failed for {path.name}: {exc}") from exc
            if not target.is_file():
                detail = result.stderr.decode("utf-8", "replace").strip()[:400]
                raise ScoreLoadError(
                    f"MuseScore produced no output for {path.name}"
                    + (f": {detail}" if detail else "")
                )
            document = cls._from_xml_bytes(target.read_bytes(), path, "mscz")
        return document

    # -- element handles -----------------------------------------------------------

    def register(self, element: etree._Element) -> int:
        """Record an element and return its handle."""
        self._elements.append(element)
        return len(self._elements) - 1

    def element(self, ref: int) -> etree._Element:
        """Resolve a handle. Raises ``IndexError`` for unknown handles, which is a programming
        error rather than a user error and should never be swallowed."""
        if ref < 0 or ref >= len(self._elements):
            raise IndexError(f"element handle {ref} is not registered in this document")
        return self._elements[ref]

    def rebind(self, ref: int, element: etree._Element) -> None:
        """Point an existing handle at a different element.

        Needed by the edit applier's undo: restoring a removed element puts a *new* object in the
        tree, and the handle must follow it or later edits would target a detached node.
        """
        if ref < 0 or ref >= len(self._elements):
            raise IndexError(f"element handle {ref} is not registered in this document")
        self._elements[ref] = element

    def try_element(self, ref: int) -> etree._Element | None:
        try:
            return self.element(ref)
        except IndexError:
            return None

    @property
    def element_count(self) -> int:
        return len(self._elements)

    def locator(self, ref: int) -> str:
        """XPath-ish location of a handle, for logs and error messages."""
        element = self.try_element(ref)
        if element is None:
            return f"<unregistered ref {ref}>"
        return self.tree.getelementpath(element)

    def reset_handles(self) -> None:
        """Drop the handle table. Called before a re-parse of the same tree."""
        self._elements.clear()

    # -- saving --------------------------------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialize the (possibly patched) tree as standalone MusicXML."""
        return etree.tostring(
            self.tree,
            xml_declaration=True,
            encoding="UTF-8",
            doctype=self._doctype(),
            pretty_print=False,
        )

    def _doctype(self) -> str | None:
        docinfo = self.tree.docinfo
        if docinfo is not None and docinfo.doctype:
            return str(docinfo.doctype)
        return (
            '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
            '"http://www.musicxml.org/dtds/partwise.dtd">'
        )

    def save(self, path: str | PathLike[str], musescore_binary: str | None = None) -> Path:
        """Write the document out, choosing the container from ``path``'s extension.

        ``.mscz`` output requires MuseScore, for the same reason reading it does.
        """
        target = Path(path)
        suffix = target.suffix.lower()
        target.parent.mkdir(parents=True, exist_ok=True)

        if suffix in MUSICXML_SUFFIXES:
            target.write_bytes(self.to_bytes())
            return target
        if suffix in COMPRESSED_SUFFIXES:
            self._save_mxl(target)
            return target
        if suffix in MUSESCORE_SUFFIXES:
            self._save_via_musescore(target, musescore_binary)
            return target
        raise UnsupportedFormatError(f"cannot write {suffix or 'extension-less'} files")

    def _save_mxl(self, target: Path) -> None:
        rootfile = self.container_rootfile or "score.xml"
        entries = dict(self.container_entries)
        if _CONTAINER_PATH not in entries:
            entries[_CONTAINER_PATH] = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<container><rootfiles><rootfile full-path="'
                f'{rootfile}" media-type="application/vnd.recordare.musicxml+xml"/>'
                "</rootfiles></container>"
            ).encode()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            # The spec requires the container to be the first entry.
            archive.writestr(_CONTAINER_PATH, entries.pop(_CONTAINER_PATH))
            archive.writestr(rootfile, self.to_bytes())
            for name, data in entries.items():
                archive.writestr(name, data)

    def _save_via_musescore(self, target: Path, binary: str | None) -> None:
        executable = find_musescore(binary)
        if not executable:
            raise MuseScoreNotAvailableError(
                "Writing .mscz needs MuseScore. Set MUSESCORE_PATH, or save as .musicxml and "
                "open it in MuseScore yourself."
            )
        with TemporaryDirectory() as tmp:
            intermediate = Path(tmp) / "corrected.musicxml"
            intermediate.write_bytes(self.to_bytes())
            try:
                subprocess.run(
                    [executable, "-o", str(target), str(intermediate)],
                    capture_output=True,
                    timeout=180,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ScoreLoadError(f"MuseScore export failed: {exc}") from exc
        if not target.is_file():
            raise ScoreLoadError(f"MuseScore did not produce {target.name}")
