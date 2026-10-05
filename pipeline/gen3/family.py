"""The generation-3 game families a translation mod is built for.

gen1recomp runs FireRed/LeafGreen and Emerald on the same game3 runtime, but
each family has its own PokeCorpus collection, qid namespace, pret charmap and
text dialect (``TextIR.DIALECTS``), and its own reviewed configuration under
its release's ``config/`` and ``overrides/<language>/`` directories.  The joins in
pipeline.gen3 read all of that from the family the corpus was loaded for.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Gen3Family:
    # config/<id>/, overrides/<language>/<id>/ and the schema names' prefix
    id: str
    # the family's name in messages and reports
    game: str
    # PokeCorpus collection directory (corpus/<collection>/)
    collection: str
    # every qid of the collection starts with it
    qid_prefix: str
    # src/core/game3/scripting/text_ir.lua, TextIR.DIALECTS
    dialect: str
    # the editions one mod covers; the first one is the default
    editions: tuple[str, ...]
    # other game3 families whose reviewed engine strings this one falls back
    # on: the runtime and its Strings() keys are shared
    shared_engine_families: tuple[str, ...] = ()
    # the release whose config/ and overrides/<language>/ directory holds the
    # family's files, and the prefix they carry there: a release's base game
    # has none, its companion edition is named, as Crystal's files are
    # crystal_*.json in Gold/Silver's release and Yellow's yellow_*.json in
    # Red/Blue's
    directory: str = ""
    file_prefix: str = ""

    def schema(self, name: str) -> str:
        return f"gen1recomp-translation-mods/{self.id}-{name}"

    def config_path(self, root: str | Path, name: str) -> Path:
        """config/<release>/<prefix><name>"""
        return Path(root) / "config" / (self.directory or self.id) / f"{self.file_prefix}{name}"

    def overrides_path(self, root: str | Path, language: str, name: str) -> Path:
        """overrides/<language>/<release>/<prefix><name>"""
        return Path(root) / "overrides" / language / (self.directory or self.id) / f"{self.file_prefix}{name}"


FRLG = Gen3Family("frlg", "FireRed", "FireRedLeafGreen", "frlg.", "frlg", ("firered", "leafgreen"))
# Emerald is the companion edition of the "rse" release (config/rse/,
# overrides/<language>/rse/, its files named emerald_*.json).
EMERALD = Gen3Family("emerald", "Emerald", "Emerald", "e.", "rse", ("emerald",),
                     shared_engine_families=("frlg",), directory="rse", file_prefix="emerald_")
FAMILIES = {family.id: family for family in (FRLG, EMERALD)}
