# Translation provenance and configuration

This document lists where every shipped translation comes from and which
reviewed configuration file records each decision, so a maintainer can
trace any string back to its source. The [README](../README.md) gives the
resolution order; the generated coverage reports are the authoritative
inventory of unmatched and ambiguous strings. Every manual override must
explain its source and accepted limitations in its `provenance` field;
otherwise the English fallback is preferred. The `reason` of each override
is authoritative: the table below names the values shipped today.

## Origins

| Origin | Meaning | Recorded in |
| --- | --- | --- |
| Automatic match | Exact, normalized, or structural match proved by the generator. | Generation report |
| Deterministic anchor | Reliable PokeCorpus qid, composition, or extraction rule. | `config/rby/semantic_anchors.json`, `config/gsc/semantic_anchors.json`, `config/gsc/crystal_semantic_anchors.json` |
| Human-reviewed RBY anchor | Contextual or language-specific extraction reviewed by a maintainer; text still comes from PokeCorpus. | `config/rby/semantic_anchor_decisions.json` |
| Human-reviewed Gold pointer | Ambiguous ROM pointer resolved to a reviewed PokeCorpus qid. | `config/gsc/pointer_decisions.json` |
| Human-reviewed Crystal pointer | Ambiguous Crystal ROM pointer resolved to a reviewed PokeCorpus qid. | `config/gsc/crystal_pointer_decisions.json` |
| Exact generation-3 dialogue join | ROM address -> the edition's pret symbol -> PokeCorpus qid label, English verified against the ROM text (FireRed/LeafGreen, Ruby/Sapphire, Emerald). | Generation report |
| Reviewed FireRed dialogue decision | A standard-script line gen1recomp reworded itself, joined to the cart's row carrying the same message; LeafGreen's own row where its table points elsewhere. | `config/frlg/dialogue_decisions.json` |
| Reviewed Ruby/Sapphire dialogue decision | A text pokeruby rewords between revisions, or that Sapphire words differently. | `config/rse/dialogue_decisions.json` |
| Reviewed generation-3 engine anchor | The cart's own row for an original menu string. | `config/frlg/engine_scope.json`, `config/rse/engine_scope.json`, `config/rse/emerald_engine_scope.json` |
| Reviewed Crystal engine selector | Crystal corpus row whose list boundaries or placeholder count don't fit the shared anchor grammar, resolved to a specific qid/segment. | `config/gsc/crystal_string_selectors.json` |
| Reviewed placeholder exception | Official localized wording legitimately adds or omits a runtime value such as the player name or an item quantity. This records no translated text and does not disable the audit; each exception is scoped to a language, ROM pointer, corpus QID, and exact audit message. | `config/gsc/placeholder_decisions.json` |
| Manual corpus correction | A maintainer corrects one selected-language corpus translation without changing the upstream corpus. Entries are indexed by qid. | `overrides/<language>/rby/corpus.json` |
| Manual translation — engine string from the corpus | The corpus row for an engine string the automatic join cannot reach by itself; the provenance names the qid it comes from. | `overrides/<language>/{rby,gsc,frlg}/engine.json`, `reason: "engine-corpus"` |
| Manual translation — engine contract gap | PokeCorpus has the text, but Gen1Recomp merges contexts or hides required parameters. | `overrides/<language>/{rby,gsc,frlg}/engine.json`, `overrides/<language>/frlg/dialogue.json`, `overrides/<language>/gsc/crystal_dialogue.json`, `overrides/<language>/rse/{dialogue,emerald_dialogue}.json`, `reason: "engine-contract-gap"` |
| Manual translation — corpus wording restored by reordering | The cart words a message in another order than the engine passes its values, which a numbered directive (`%2$s`) now expresses. The text is the cart's own, apart from an addition the provenance discloses (an adverb a language's cart drops, for instance); only the order is the translation's. | `overrides/<language>/{rby,gsc}/engine.json`, `reason: "engine-corpus-reordered"` |
| Manual translation — corpus wording in the engine's own order | The same restoration for a language whose cart already orders the values the way the engine passes them, so no directive is numbered. | `overrides/<language>/gsc/engine.json`, `reason: "engine-corpus-cart-order"` |
| Manual translation — engine original | Engine-specific text with no compatible ROM source. | `overrides/<language>/{rby,gsc,frlg}/engine.json`, `overrides/<language>/gsc/{dialogue,crystal_dialogue,crystal_registries}.json`, `overrides/<language>/rse/emerald_engine.json`, `reason: "engine-original"` |
| Manual translation — Yellow-only engine text | Engine-authored, Yellow-exclusive text (Surfing Pikachu minigame HUD) with no PokeCorpus source; applied only when `GameVersion.isYellow()`. | `overrides/<language>/rby/yellow_engine.json`, `reason: "yellow-only-engine-text"` |
| Manual translation — corpus gap | The corpus row is empty for this language while the cart has the line; the cart's own text (pret's multi-language decompilation) or a reviewed wording. | `overrides/<language>/rse/emerald_dialogue.json`, `reason: "corpus-gap"` |
| Manual translation — corpus defect | The corpus row carries a byte its extractor could not decode; the reviewed fix restores the cart's text. | `overrides/<language>/rby/corpus.json`, `overrides/<language>/gsc/dialogue.json`, `reason: "corpus-defect"` |
| Manual translation — row the US cart leaves empty | A language's cart prints a row the US cart leaves empty (the Japanese honorifics after the player's name), taken verbatim from that cart. | `overrides/<language>/frlg/dialogue.json`, `overrides/<language>/rse/{dialogue,emerald_dialogue}.json`, `reason: "us-cart-empty"` |
| Known limitation | Active anchor/override knowingly imperfect in a context or language; a status, not an origin. | Anchor metadata or override provenance |
| English fallback | No sufficiently reliable translation; runtime keeps English. | Generation report |

## Configuration files

Game-specific configuration lives under `config/rby/`, `config/gsc/`,
`config/frlg/` and `config/rse/`; language overrides follow the same split
under `overrides/<language>/`. Gold and Silver identify their production
strings directly from Gen 2 source subtrees. Missing or ambiguous evidence
always falls back to English. Private review candidates never become
executable configuration automatically. `strict_engine` requires the
engine catalog and scaffold to be present, not fully translated.

| Configuration | Purpose |
| --- | --- |
| `config/shared/engine_manifest.json` | Pinned engine revision and complete string universe shared by the releases. |
| `config/rby/engine_scope.json` | RBY coverage classification for engine strings. |
| `config/rby/terminology_anchors.json` | Evidence for corpus terminology used by RBY. |
| `config/rby/literal_handlers.json` | Documented RBY extraction gaps. |
| `config/rby/semantic_anchor_decisions.json` | Human-reviewed corrections to RBY semantic-anchor picks. |
| `config/rby/yellow_coverage_exceptions.json` | Reviewed exceptions to the Yellow ROM aggregate's markup-only exclusions. |
| `config/gsc/pointer_decisions.json` | Human-reviewed picks for ambiguous Gold dialogue pointers. |
| `config/gsc/placeholder_decisions.json` | Reviewed placeholder exceptions for Gold dialogue pointers. |
| `config/gsc/silver_pointer_aliases.json` | The 8 Gold-pointer-to-Silver-pointer aliases needed because a handful of field-move prompts shift address between editions. |
| `config/gsc/semantic_anchors.json` | Evidence for Gold/Silver engine-string corpus matches. |
| `config/gsc/engine_fallbacks.json` | Audited ledger of Gold/Silver engine keys deliberately left in English. |
| `config/gsc/engine_scope_exclusions.json` | Crystal-exclusive engine keys excluded from the Gold/Silver engine-string coverage metric (translated separately; see [coverage.md](coverage.md#gold-silver-and-crystal)). |
| `config/gsc/literal_handlers.json` | Reviewed corpus picks for the Gold menu screens exposed through public list hooks (`ui.pc.items` and similar). |
| `config/gsc/engine_launch_batch.json` | The frozen batch of keys the original Gold/Silver engine-string work added (549 since v0.3.51, which no longer reaches two of them), kept for exhaustive coverage auditing as the catalog keeps growing. |
| `config/gsc/status_anchors.json` | Evidence for the Gold/Silver status-label registry (`mod.content.statuses`). |
| `config/gsc/type_search_indices.json` | Gen 2 type ids mapped to the Pokédex type-search corpus row. |
| `config/gsc/crystal_pointer_decisions.json` | Human-reviewed picks for ambiguous Crystal dialogue pointers. |
| `config/gsc/crystal_rom_text_anchors.json` | Crystal-only RomText labels mapped to their PokeCorpus rows -- a labeled fallback path alongside Crystal's own pointer-based dialogue join. |
| `config/gsc/crystal_semantic_anchors.json` | Evidence for Crystal engine-string corpus matches. |
| `config/gsc/crystal_string_selectors.json` | Reviewed qid/segment picks for Crystal corpus rows whose list boundaries or placeholder count don't fit the shared semantic-anchor grammar. |
| `config/frlg/dialogue_decisions.json` | Reviewed corpus rows for FireRed standard-script lines gen1recomp reworded, with LeafGreen's own pick where its tables differ. |
| `config/frlg/edition_name_choices.json` | The naming screen's default-name labels each edition lists (pokefirered `src/oak_speech.c`), so either cart keys the other's tables. |
| `config/frlg/engine_scope.json` | FireRed-reachable `Strings()` keys, their callsites and reviewed cart rows. |
| `config/rse/engine_scope.json` | Ruby/Sapphire-reachable `Strings()` keys, their callsites and Ruby/Sapphire cart rows. |
| `config/rse/dialogue_decisions.json` | Reviewed rows for the Ruby and Sapphire texts pokeruby rewords between revisions, with Sapphire's own Pokédex row. |
| `config/rse/emerald_engine_scope.json` | Emerald-reachable `Strings()` keys, their callsites and Emerald cart rows. |
| `config/rse/emerald_european_trainer_text.json` | Emerald's trainer names and classes as the European carts print them. |

The three generation-3 engine scopes are generated from the pinned engine;
[gen3-pipeline.md](gen3-pipeline.md#regenerating-the-engine-scopes) gives
the commands.
