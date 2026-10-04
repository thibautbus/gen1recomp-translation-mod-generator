"""The generation-3 game families a translation mod is built for.

gen1recomp runs FireRed/LeafGreen and Emerald on the same game3 runtime, but
each family has its own PokeCorpus collection, qid namespace, pret charmap and
text dialect (``TextIR.DIALECTS``), and its own reviewed configuration under
``config/<id>/`` and ``overrides/<language>/<id>/``.  The joins in
pipeline.gen3 read all of that from the family the corpus was loaded for.
"""
from __future__ import annotations

from dataclasses import dataclass


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

    def schema(self, name: str) -> str:
        return f"gen1recomp-translation-mods/{self.id}-{name}"


FRLG = Gen3Family("frlg", "FireRed", "FireRedLeafGreen", "frlg.", "frlg", ("firered", "leafgreen"))
EMERALD = Gen3Family("rse", "Emerald", "Emerald", "e.", "rse", ("emerald",),
                     shared_engine_families=("frlg",))
FAMILIES = {family.id: family for family in (FRLG, EMERALD)}
