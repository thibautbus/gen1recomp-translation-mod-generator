# Translation: upstream engine gaps

Both mods use only the public `gen1recomp` content and hook APIs. Each game's section (RBY, then Gold) tracks these kinds of entries:

- **Required upstream capabilities**: strings that render in English with no
  override reaching them at all -- no hook, no catalog entry can fix these
  from the translation mod; they must not be implemented by reaching into
  private UI classes.
- **Translated via a compromise (`engine-contract-gap`)**: strings that
  already render translated today, but through a compromise (an
  AI-generated stand-in, a reordered-argument adaptation, or one override
  serving several incompatible original contexts) instead of the real
  official localized phrasing, because the engine's callsite doesn't map
  cleanly onto it. These are not broken, but the goal is to retire this
  category over time as gen1recomp's contract at each callsite is fixed.
- **Fixed upstream**: a former "Required upstream capabilities" entry,
  closed either by wiring this project's own config to an already-public
  hook (Gold's "Fixed: rows already reachable..." section), or by a
  gen1recomp engine change (both games' "Fixed upstream" sections).
- **In progress**: fixes implemented and verified against a real build --
  whether still on a local branch, opened as a PR, or already merged
  upstream -- kept here until this project's pinned `gen1recomp_revision`
  catches up to a revision that contains them (RBY only for now).
- **Verified working, not a gap**: something that looked like a gap on
  inspection but turned out to already render correctly once checked
  against a real build -- kept here so the same question does not get
  re-investigated later.

Two standalone kinds of report close the file, kept separate because they
are not translation gaps at all: gen1recomp rendering bugs surfaced by TTF
mode (both fixed), and `tools/modkit.py` Windows-encoding bugs hit while
running or validating a mod (one fixed, one still open with a
project-side workaround only).

## RBY

### Verified working, not a gap

The SummaryMenu's own stat labels (`NAME`, `ATTACK`, `DEFENSE`, `SPEED`,
`SPECIAL`) are **not** blocked, despite each needing a `forced_dynamic_keys`
entry in `config/shared/engine_manifest.json` to be reachable at all: the
callsite (`ui/SummaryMenu.lua:150-157`) reads them from a runtime table
(`Strings(s[1])`), which a static literal scanner cannot see, so they must be
force-added to the catalog by key -- but `Strings()` itself is an ordinary
runtime lookup by string value, indifferent to whether the call site used a
literal or a variable. `config/rby/semantic_anchors.json` already carries a
working anchor for all five (`rb.stat_names.VitaminStats.2-5`,
`rb.start_sub_menus.TrainerInfo_NameMoneyTimeText`), confirmed translated
(`fr`: `FOR`/`DEF`/`VIT`/`SPE`/`NOM`) in a real build's generated
`strings.lua`. (A prior version of this README's "Remaining limitations"
listed these as English-only; that was stale.)

If a future gen1recomp version rewrote this callsite to pass a literal
(`Strings("ATTACK")` per case instead of `Strings(s[1])`), the scanner would
discover it on its own and `forced_dynamic_keys` would no longer be needed
to make the key reachable -- and the anchors themselves would likely become
redundant too, not just the manual catalog entry. Two things confirm this
rather than just the scanner gap: `corpus_to_engine("ATTACK@")` already
strips the trailing `@` down to plain `"ATTACK"`, the same normalization
ordinary aligned records get before exact/normalized auto-matching runs;
and although `ATTACK@` appears at two RedBlue qids
(`rb.stat_names.VitaminStats.2` and `rb.stat_mod_names.StatModTextStrings.0`),
both carry the identical translation in every shipped language (`fr`: `FOR`
for both), so the duplicate would not make an automatic match unsafe. The
anchors would stay harmless to keep either way -- explicit anchors still
win over auto-matching in the resolution order -- but the manual upkeep
they represent could be dropped.

Yellow's Melanie's House dialogue (`MelanieText1-5`, `MelanieBulbasaurText`,
`MelanieOddishText`, `MelanieSandshrewText`) looked like the same "extractor
skips labels without a leading underscore" gap as several RBY sites
investigated below, since those labels don't have one either. It isn't a
gap: `tools/make_yellow_manifest.py`'s `YELLOW_EXTRA_TEXT_LABELS` already
force-includes all eight into the Yellow manifest, and a real built
`dialogue_yellow.lua` already carries correct French translations for
them. (A prior version of this doc listed this as still open; that was
stale.)

**Reading `game.data.text` directly, not through `Strings()`, is not a
gap.** A prior version of this doc claimed the opposite for a set of
flavor NPCs, covering 6 `literal_handlers.json` handlers and a "no public
API to give a Pokémon" blocker on Mt. Moon's Magikarp salesman. That did
not hold: `src/core/Strings.lua`'s own header explains extracted dialogue
(`Data.text`) has its own override path, `mod.content.text:override(id,
value)`, independent of `Strings()`/`mod.content.strings` -- so a script
reading `t[label]` is translatable regardless of whether it goes through
`Strings()` first. Confirmed directly against a real build's
`lang/dialogue.lua`: the Bike Shop's three NPCs and Route 24's
Nugget-Bridge recruiter were already reading `game.data.text` correctly,
with translations already present, via gen1recomp's own vanilla scripts
(`data/scripts/story2.lua`, `story4.lua`,
`data/scripts/flavor/bike_shop.lua`) -- which are also *more* complete
than this project's former reimplementations (real bag-capacity checks
via a `require` unrestricted for core engine code). Those 4 redundant
handlers were removed from `config/rby/literal_handlers.json`; letting
vanilla run is simpler and more correct. The same real-build check was
repeated for every other flavor NPC reading `game.data.text` this way --
Viridian City's `GAMBLER1`/`GIRL`, Pewter City's `SUPER_NERD1`/
`SUPER_NERD2`, the 8-NPC `gift()` family in `data/scripts/story5.lua`,
and Mt. Moon's Magikarp salesman (which also already calls
`give_pokemon` natively, so "no public API to give a Pokémon" was never
actually a blocker here) -- all confirmed already correctly translated,
no config needed. The Pokédex "kind" classification
(`src/ui/DexEntryMenu.lua:93`) looked like the same deep gap as the
status-ailment abbreviations above, but isn't: `pipeline/mod.py` already
has a dedicated `species_kinds` catalog for it. Viridian City's second
Youngster was the one genuine exception -- two of its three lines had no
reachable label at all until `fix/text-extractor-underscore-requirement`
(see "Fixed upstream" below), so it briefly needed its own
`map_scripts:register` reimplementation. That handler has since been
removed too: `config/rby/literal_handlers.json` now has 0 handlers, and
this project's pin includes the fix as of v0.2.19, so a normal pinned
build shows this NPC's real French text without any workaround.

### Fixed upstream (engine changes, not just this project's config)

- **Stat-rise messages:** the X-item/vitamin `"rose!"` messages
  substituted the raised stat's name as a raw Lua string, bypassing
  `Strings()`. Fixed on gen1recomp `fix/stat-rise-message-translation`
  (merged, PR #1439); the same bug was also fixed in `battle/TrainerAI.lua`
  and, Gold-side, in two `battle/gen2/Battle.lua` messages (see Gold
  below).
- **Museum 1F's ticket clerk:** three of `museumClerk`'s five lines were
  bare English literals, never routed through `game.data.text`. Fixed on
  gen1recomp `fix/museum-1f-ticket-clerk-strings` (merged, PR #1492).
  `config/rby/literal_handlers.json`'s `museum-1f-ticket-clerk` workaround
  has been removed -- vanilla handles all five lines correctly on its own.
- **Status condition abbreviations:** `PSN`/`PAR`/`BRN`/`FRZ`/`SLP` always rendered in English on the summary and party screens (`ui/SummaryMenu.lua:148`, `ui/PartyMenu.lua:824`), never passed through anything translatable. Fixed on gen1recomp `fix/status-abbreviation-translation` (merged, PR #1527). Not through `Strings()`, though: a status abbreviation is translated through the separate `statuses` content registry (`mod.content.statuses:patch(id, { label = value })`), the same one `BattleState.lua`'s in-combat status HUD already reads. This project's existing `status_labels` catalog was already patching it correctly before the merge -- it was just aimed at a callsite that ignored it, so nothing needed to change on this project's side once the fix landed. The same PR also fixed a second, independent bug: `Status.RECORDS`' five vanilla entries duplicated `hudLabel = label` for no reason, and the lookup reads `hudLabel` before `label` -- so even after the callsite fix, this project's `{ label = value }`-only patch was silently shadowed by the untouched vanilla `hudLabel` on every status. Confirmed live before the fix: SLP/BRN/FRZ stayed English, PSN/PAR only looked fine because French keeps the same three letters there. Both fixes shipped together; nothing to patch differently on this project's side.

### Fixed upstream: more battle/overworld/menu messages now routed through the real ROM text

gen1recomp `fix/route-more-messages-through-romtext` (merged upstream as PR #1559, included in gen1recomp release v0.2.12; this project's pin now includes it as of v0.2.19). 21 real ROM labels that only ever had a `Strings()` call with an adapted/approximate source now call `romText()`/`self:romText()` with their real extracted ROM label instead, so they show the corpus's official localized phrasing rather than the AI-adapted compromise. `tests/engine/` gained 7 new regression tests, one per message family, each driving the real interactive flow (or faking `Game`/`TextBox` via `debug.setupvalue` where there's no state to drive directly) and asserting the routed text. Manually verified in-game (French mod, Windows build) for 9 of 11 distinct scenarios, comparing `dev` against the branch side by side -- the slot machine's 3-symbol match and a link battle were left uncovered live (both awkward to trigger on demand), relying instead on the automated/static checks. This live pass caught one real bug the automated tests missed (see `_ItemUseBallText00` below), now fixed on the same branch. The rows below name what each fixed call site retires from the compromise table below -- **not yet re-audited against the bumped pin**: several are explicitly partial (a row shared with another, untouched call site only loses one of its collapsed contexts), so the compromise table itself is left untouched here rather than edited without the same live-build verification the rest of this doc relies on:

- `_PlayerBlackedOutText2` (`BattleState:enter()`): merges the two-line
  black-out message into one `\f`-paged call (previously two separate
  `Strings()` calls joined with `.. "\f" ..`).
- `_TrainerSentOutText`/`_AIBattleWithdrawText` (4 call sites in
  `BattleState.lua`): trainer send-out/withdraw lines.
- `_ItemUseBallText06`/`_ItemUseBallText07`/`_ItemUseBallText08`
  (`BattleState:storeCaughtMon`): the PokéDex-added message, and the
  box-transfer message (previously one `Strings()` source manually
  switched between "BILL's PC"/"someone's PC", now two real labels picked
  on `EVENT_MET_BILL`); retires the `%s was\ntransferred to\n%s!` row.
- `_ItemUseBallText00` (`BattleState:throwBall`): retires the
  `This POKéMON\ncan't be caught!` row. Resolves the dodge +
  can't-be-caught two-liner as one `\f`-paged `romText()` call, then
  splits the result into two `sayNext()` pages itself -- confirmed live
  that the battle queue's own `startMessage()` (unlike `TextBox.lua`)
  does not page on `\f`, so a single `sayNext()` call with the raw
  `\f` still inside it overflowed the second sentence off the box.
- `_PlayerMonFaintedText`/`_EnemyMonFaintedText` (`BattleState:onFaint`) and
  `_PokemonFaintedText` (`OverworldController:applyFieldPoison`): retires
  the `%s\nfainted!` row's three collapsed contexts -- `_EnemyMonFaintedText`
  already carries its own "Enemy " wording, so this passes the raw name
  instead of `displayName`'s separate `Strings("Enemy %s", ...)`.
- `_ItemUseNoEffectText`/`_PotionText`
  (`OverworldController:useSoftboiledFieldMove`): retires this call site's
  share of the `It won't have\nany effect.` row (still shared with
  `ItemEffects.lua`/`PartyMenu.lua`'s own call sites, untouched here); the
  healing message now also passes the real recovered-HP amount
  `_PotionText` expects, which the `Strings()` fallback never showed.
- `_FoundItemText`/`_FoundHiddenItemText` (4 call sites in
  `OverworldController.lua`): retires the `%s found\n%s!` row's two
  collapsed contexts.
- `_OnceReleasedText` (`BoxMenu.lua`'s `release()`): retires the
  `Once released,\n%s is\ngone forever. OK?` row. `_MonWasReleasedText`,
  the "X was released outside. Bye X!" follow-up shown right after
  confirming, was missed in an earlier pass and only caught testing the
  flow live -- same `t._X or Strings(...)` pattern, fixed alongside it.
- `_LinedUpText` (`SlotMachine:resolveWin`): retires the
  `%s lined up!\nScored %d coins!` row.
- `_PokemartTellBuyPriceText`/`_PokemartTellSellPriceText`
  (`ShopMenu.lua`'s `buy()`/`sell()`): buy/sell price confirmations,
  previously untracked in the compromise table.
- `_TrainerWantsToFightText` (`LinkBattle.new`): link-battle opening line,
  previously untracked in the compromise table.

Two things this same sweep confirmed should **not** change:

- **`_TrainerAboutToUseText`** (the SHIFT-switch prompt,
  `BattleState:enemyMonFainted`): tried merging its `say()` + `sayChoice()`
  pair into one `\f`-paged `romText()` + `sayChoice()` call, the same way
  as `_ItemUseBallText00` above. `tests/engine/trainer_shift_prompt_bug565.lua`
  caught it immediately (wrong line count, no `\f` paging) -- the battle
  queue's own text renderer behind `sayChoice()` doesn't page `\f` the way
  `TextBox.lua` does for `sayNext()`/`say()`. Left as the original two
  separate `Strings()` calls, with a code comment recording why. Not a
  translation gap either way: both fragments already render correctly
  today via the compromise mechanism, confirmed in a real fr build
  (`"%s is\nabout to use\n%s!"` -> `"%s\nva appeler...\n%s!"`,
  `"Will %s\nchange POKéMON?"` -> `"%s va-t-il\nchanger de POKéMON?"`) --
  this is purely about which mechanism serves it, not whether it's
  translated.
- **`MoveEffects.lua`'s stat-rise/fall messages** (`changeStage`'s four
  rose/greatly-rose/fell/greatly-fell variants): already covered by the
  reordered-arguments compromise table below (`%s's %s\nrose!` row) -- not
  a new gap, just confirmed the existing entry already accounts for it.
  Confirmed there is no upgrade available here even in principle: the
  real ROM labels (`_MonsStatsRoseText`/`_MonsStatsFellText`) extract as
  `"{USER}'s\n{RAM:wStringBuffer}"`/`"{TARGET}'s\n{RAM:wStringBuffer}"` --
  the second line is just a pointer to a RAM buffer pokered fills at
  runtime with the stat name and verb ("ATTACK rose!", "SPCL.DEF greatly
  fell!", ...) via ASM logic the static text extractor never captures.
  Routing through `romText()` here would show the exact same generic
  template the compromise already shows, so there is nothing to retire.
- **Argument-reordering/fragmentary-source compromise rows** (`%sBOX %2d`,
  `PLAYER %s\nBADGES %d\nPOKéDEX %3d\nTIME %6d:%02d`, `HT`/`WT`/`BADGES`,
  and the stat-rise row above): explicitly out of scope for this sweep --
  these need the engine's callsite itself to change its `printf` argument
  order or stop fragmenting the sentence, not just a `romText()` label
  swap, so they stay as compromise entries until a template-level upstream
  fix is worth doing.

### Fixed upstream: pokered dialogue labels were missing from data/generated/text.lua

gen1recomp `fix/text-extractor-underscore-requirement` (merged upstream as PR #1598, included in gen1recomp release v0.2.12; this project's pin now includes it as of v0.2.19 -- see the "Required upstream capabilities" bullet below for what still isn't covered by this fix alone). Started from the same "extractor requires a leading underscore" theory as a prior version of this doc, but tracing it against a real `pret/pokered` checkout turned up a more precise picture -- there are two independent, differently-behaved label scanners in gen1recomp:

- `tools/extract/text.py`'s `parse_text_file()` does require `_`, and that's a real bug in isolation -- but nothing in the codebase calls this function (no importer, no `__main__` entry point). It looks like dead code from an earlier version of the pipeline.
- The function that actually produces the shipped label list is `text_metadata()` in `tools/make_rom_manifest.py`, feeding `manifest["text"]["labels"]`, which `build_rom_data.py`'s `extract_text()` decodes straight from a ROM. `text_metadata()` already uses the permissive regex (no `_` requirement) since gen1recomp commit `0f581e2f`.

So the real blocker is that the *committed* `tools/rom_manifest.json` is stale relative to `text_metadata()`'s current code, not a source bug. Verified live: running `text_metadata()` today against a real pokered checkout returns 2595 labels including everything below; the committed manifest only has 2585. Cross-checked against a real built French `dialogue.lua`/`dialogue_yellow.lua` to see exactly what's actually missing from a shipped build today:

- **Confirmed missing** (10 labels): both of Viridian City's second Youngster's lines, `TMNotebookText`, the SS Anne kitchen cook's three dish lines, the Viridian fisher's pre-gift line, and three never-previously-documented lines at Silph Co. 9F's nurse (`SilphCo9FNurseDontGiveUpText`/`ThankYouText`/`YouLookTiredText`).
- **Confirmed already fine** (9 labels): `SilphCo2FSilphWorkerFPleaseTakeThisText` (likely hand-fixed for issue #393 without a full manifest regeneration) and all eight of Yellow's Melanie's House labels (see "Verified working, not a gap" above -- `YELLOW_EXTRA_TEXT_LABELS` already covers that one, it was never actually broken).

`tools/extract/text.py`'s regex was relaxed anyway, for consistency with `text_metadata()` -- harmless since nothing calls it, but no reason to leave a dead copy of the same scanner out of sync. Four of gen1recomp's own hand-ported scripts were carrying the confirmed-missing labels' English text as inline literals (no `game.data.text` lookup at all) and got fixed to read the real label first, same `t[label] or fallback` pattern used everywhere else -- ready to pick up the real text as soon as someone with ROM access regenerates the manifest, which this contribution can't do itself:

- `data/scripts/celadon_eevee.lua` (`TMNotebookText`, the TM pamphlet).
- `data/scripts/flavor/ss_anne_kitchen.lua` (the cook's three dish lines).
- `data/scripts/flavor/viridian_city.lua` (both of the second Youngster's caterpillar-description lines).
- `data/scripts/story5.lua` (the SilphCo2F worker's and Viridian fisher's pre-gift lines -- these already read `t[label]` via the generic `gift()` helper, so only their stale comments needed correcting, plus a real English fallback added where the worker's was missing).

Verified end-to-end ahead of the merge: built the branch's manifest for real against a ROM (see the branch's own commit message for the full byte-for-byte verification), then built this project's mod against that branch (bypassing the `config/pipeline.toml`/`engine_scope.py` pins programmatically, no committed config touched for that check) and confirmed a real French Windows build reads `ViridianCityYoungster2OkThenText`/`CaterpieAndWeedleDescriptionText` from `lang/dialogue.lua` instead of the `literal_handlers.json` override -- side by side against the still-pinned v0.1.91 build, only the fixed branch showed French, the pinned build still showed the hardcoded English literal, exactly as expected. `config/rby/literal_handlers.json`'s `viridian-city-youngster2` handler was removed on that strength, ahead of both the gen1recomp PR being opened and this project's pin catching up -- a deliberate call, not an oversight (see "Verified working, not a gap" above). The PR merged upstream in v0.2.12, and this project's pin was bumped to v0.2.19 (which contains it): a normal pinned build now shows this Youngster's real French text too, with no regression window left open. Silph Co. 9F's nurse needs more than the manifest regeneration alone -- see "Required upstream capabilities" below.

### Translated via a compromise, not blocked (`engine-contract-gap`)

Everything in "Required upstream capabilities" below renders in **English**
with no override reaching it at all. This section is different: every entry
here already shows **translated** text in-game today, through
`overrides/<language>/rby/engine.json`. They are tracked here anyway because
the override is a compromise, not the real localized phrasing -- the
pipeline tags each one `"reason": "engine-contract-gap"` and records why in
its `provenance` field. The goal is to retire this whole section over time:
each row names the concrete engine change that would let the corpus's real,
official localized text replace the compromise. Until then the compromise
stays live and correct-enough, not broken.

Two distinct shapes of compromise, both summarized below (fr shown; every
shipped language carries its own compromise the same way, see each
`overrides/<language>/rby/engine.json`):

**Engine-authored settings/launcher labels** (31 entries,
`config/shared/engine_manifest.json`'s `engine_dynamic_values`, each with a
real callsite, `eligibility: "ineligible"`): these are modern
launcher/options UI, not ROM text, so there is no PokeCorpus qid to match
against at all -- the override is either an AI-generated translation
(flagged `requires in-game visual validation` in its provenance) or the
acronym/label kept as-is where translating it would lose meaning (`SGB`,
`GBC`, `SGB INV`).

| Source | fr override | Callsite |
|---|---|---|
| `OG RED` | `OG ROUGE` | `ui/OptionsMenu.lua:276`, `import/LauncherSettings.lua:118` (`PaletteFX.modeLabel`) |
| `OG BLUE` | `OG BLEU` | same as above |
| `OG YELLOW` | `OG JAUNE` | same as above |
| `SGB` | `SGB` (kept) | same as above |
| `ADVANCED` | `AVANCE` | same as above |
| `OG INV` | `ORIG. INV` | same as above |
| `SGB INV` | `SGB INV` (kept) | same as above |
| `CLASSIC` | `CLASSIQUE` | same as above |
| `GBC` | `GBC` (kept) | same as above |
| `WINDOWED` | `FENETRE` | `ui/OptionsMenu.lua:346`, `import/LauncherSettings.lua:169` (`VideoMode.modeLabel`) |
| `BORDERLESS` | `SANS BORD` | same as above |
| `TREES` | `ARBRES` | `ui/OptionsMenu.lua:330`, `import/LauncherSettings.lua:154` (`TileRenderer.voidFillLabel`) |
| `WATER` | `EAU` | same as above |
| `OFF` | (per-context) | `ui/OptionsMenu.lua:251`, `import/LauncherSettings.lua:99` (`FILTERS`/`FaithfulRes`) |
| `1X` / `2X` / `3X` | (per-context) | `ui/OptionsMenu.lua:251`, `import/LauncherSettings.lua:60` (`FILTERS`/`FaithfulRes`) |
| `NORMAL` | (per-context) | `ui/OptionsMenu.lua:398`, `import/LauncherSettings.lua:218` (`GameSpeed.levelLabel`) |
| `AUTO` / `LANDSCAPE` / `PORTRAIT` / `REVERSE LANDSCAPE` | (per-context) | `ui/OptionsMenu.lua:357` (`Orientation.modeLabel`) |
| `FAST` / `MEDIUM` / `SLOW` | (per-context) | `ui/OptionsMenu.lua:126` (`SPEEDS` table) |
| `HEAVY` / `LIGHT` | (per-context) | `ui/OptionsMenu.lua:447`, `import/LauncherSettings.lua:250` (`TouchControls.hapticLabel`) |
| `auto` / `balanced` / `high` / `low` | (per-context) | `ui/OptionsMenu.lua:264` (`Performance.label`) |

**Corpus translations adapted to the engine's argument order or a
fragmentary source** (7 entries): the real localized ROM phrasing exists in
the corpus, but the engine's `Strings()` callsite either reorders the
`printf` arguments differently than the ROM script did, or only passes a
fragment of the original sentence (a technical field like `HT`/`WT`, a
standalone label like `BADGES`), so a literal per-fragment translation
loses grammatical context a single upstream template change could restore.

| Source | fr override | What's missing |
|---|---|---|
| `%s's %s\nrose!` | `%s voit son %s\naugmenter !` | Reordered stat arguments at shared callsites |
| `%sBOX %2d` | `%sBOITE %2d` | Reordered box-name and numeric arguments |
| `BADGES` | `BADGES` (kept) | Fragmentary standalone source |
| `HT %d′%02d″` | `HT %d′%02d″` (kept) | Missing unit context around numeric arguments |
| `WT %.1flb` | `WT %.1flb` (kept) | Missing unit-context argument |
| `Once released,\n%s is\ngone forever. OK?` | `Une fois libéré,\n%s sera\nperdu à jamais. OK ?` | Reordered release-confirmation context around one argument |
| `Use on which one?` | `Utiliser sur lequel ?` | Missing target-selection argument |

**Corpus translations shared across multiple, incompatible original
contexts** (2 entries): the engine collapses several different ROM strings
into one `Strings()` source key, so one override has to serve every context
even though the original ROM script used different phrasing for each --
only a callsite split upstream (one key per context) would let each one
carry its real, distinct localized text.

| Source | fr override | Original contexts collapsed together |
|---|---|---|
| `%s\nfainted!` | `%s\nest K.O. !` | `rb.text_2.EnemyMonFaintedText`, `rb.text_2.PlayerMonFaintedText`, `rb.text_4.PokemonFaintedText` |
| `POKéDEX` | `POKéDEX` (kept) | `StartMenu.lua`, `PokedexMenu.lua`, `TitleState.lua`, `HallOfFame.lua` labels, no single upstream qid |

**Retired by the v0.2.19 pin bump** (8 entries removed from every language's
`overrides/<language>/rby/engine.json`): `%s lined up!\nScored %d coins!`,
`%s was\ntransferred to\n%s!`, `This POKéMON\ncan't be caught!`,
`PLAYER %s\nBADGES %d\nPOKéDEX %3d\nTIME %6d:%02d`, `evolving!`,
`%s found\n%s!`, `%s's HP\nwas restored!`, and `It won't have\nany effect.`.
The first five are named explicitly in "Fixed upstream" above
(`fix/route-more-messages-through-romtext`, PR #1559); the last three are
not -- discovered instead by running
`pipeline.engine_scope.complete_engine_keys` (the same check
`pipeline/mod.py`'s real build uses to reject a stale override key) against
a real v0.2.19 checkout and diffing it against every override file's key
set. All eight came back with zero matching callsites anywhere in the
engine, meaning they would have made the next real build fail outright
with `engine overrides contain N unknown key(s)` had they been left in
place. That check is only about whether the exact source string still
exists as *some* callsite anywhere in the whole engine -- RBY or not,
`romText()`-routed or not -- which is also why `%s\nfainted!` and
`Once released,...` survive above despite RBY no longer needing either
override: the former still has a genuine `Strings()` call in Gen2's own
`world/gen2/World.lua` (unrelated to this project's RBY mod, but enough
to keep the key valid), and the latter is still a live `Strings()` call
inside `ui/BoxMenu.lua`'s `t._OnceReleasedText or Strings(...)` fallback
idiom -- written by hand rather than through the `romText()` helper, so
the scanner still counts it as a translatable `Strings()` source even
though `t._OnceReleasedText` already wins whenever it resolves. Whichever exact commit
fixed the last three wasn't tracked down -- v0.1.91..v0.2.19 spans close to
200 merged upstream PRs and this project's local checkout of gen1recomp is
shallow, so bisecting each one individually wasn't attempted.

### Not yet investigated: Surfing Pikachu/Hall of Fame HUD text rewritten upstream

Bumping this project's pin from v0.1.91 to v0.2.19 (`gen1recomp_revision`
in `config/pipeline.toml`/`config/shared/engine_manifest.json`) pulled in
gen1recomp PR #1581 (`feat/pikachu-surf-and-gold-gamecorner`), which
**rewrote** the existing Surfing Pikachu minigame's HUD in
`src/ui/SurfingMinigame.lua` -- not a new file: this project already had
`overrides/<language>/rby/yellow_engine.json` entries for its old HUD text
(`A: done`, `HI    %d`, `New record!`, `SCORE %d`, tagged
`yellow-only-engine-text`, "no PokeCorpus source"). The rewrite replaced
those four with new text -- `Hi-Score!!`, `Pts`, `Radness`, `Total`,
`HP Left` -- and also touched `src/ui/LeaguePC.lua`'s Hall of Fame screen,
adding `HALL OF FAME No`. The four old, now-orphaned overrides were removed
(see below); no replacement overrides have been added yet for the new
strings. Running `pipeline.engine_scope.classify_callsites` against the
real v0.2.19 source confirms all six fall into the same `review`
eligibility bucket as the pre-existing `Nothing here.`/`STATS` entries this
project already leaves untranslated pending a manual scope decision -- but
unlike every other row in this document, they have not yet had the
live-build/corpus-alignment treatment (checking for a real PokeCorpus qid,
since the old HUD text notably had none; confirming what a real French
build actually shows; deciding `rby_ui_modules` vs. `key_scope_overrides`
classification). Recorded here so that investigation isn't lost, not
because it's been done.

**Real build failure caught by this rewrite, now fixed:** a real Yellow mod
build against the bumped pin failed with `Error: Yellow engine override
contains unknown key: 'A: done'` (`pipeline/builder.py`'s Yellow layer
validates `overrides/<language>/rby/yellow_engine.json` against a real
`strings.lua` worksheet dumped from the built game, the same kind of check
`pipeline/mod.py`'s RBY layer does with `complete_engine_keys`). All four
old HUD strings were removed from all five languages' `yellow_engine.json`
files once confirmed dead by the same `complete_engine_keys` check used for
the RBY overrides cleanup above.

**Same audit, applied to semantic anchors too:** the same PR's
`_ItemUseBallText00` merge (see "Fixed upstream" above) also orphaned
`config/rby/semantic_anchors.json`/`semantic_anchor_decisions.json`'s
`"It dodged the\nthrown BALL!"` entry, caught by
`tests/test_multilingual.py`'s `test_rby_anchor_callsites_are_unique_and_contextually_eligible`
once `.cache/dependencies/gen1recomp` refreshed to the new pin (that test's
now-stale expectation was removed). Semantic anchors have no equivalent of
`pipeline/mod.py`'s `stale_overrides` build-time check, so an orphaned one
doesn't crash a build -- it just silently stops matching anything. Running
the same "is this key still a real callsite" audit across the *entire*
anchor/decision config (not just this one test's narrow probe list) found
**15 anchor keys** (10 of them also in `semantic_anchor_decisions.json`) in
the same orphaned state, most plausibly other multi-line messages some
other PR in the same huge version range also routed through `romText()`.
Left uninvestigated here rather than bulk-edited: unlike the engine.json
overrides, there's no hard validation check confirming a correct edit, and
several of these anchors extract per-language target spans from specific
qids, which is exactly the kind of decision this project's own convention
insists on re-verifying against a real build before touching, not assuming
from a key-existence check alone.

### Required upstream capabilities

Still genuinely out of reach: no hook, no catalog entry can fix these
from the translation mod without gen1recomp itself changing.

- **ROM labels missing from the manifest (Silph Co. 9F's nurse):** was
  "not fixable from this project without gen1recomp vendoring the real,
  unmodified `pokered` ASM source" -- the actual cause turned out to be a
  stale committed manifest, not a source bug (see the "In progress"
  section above), and gen1recomp's own regenerating it against a ROM
  would close every other site this sweep found. Silph Co. 9F's nurse
  (`SilphCo9FNurseDontGiveUpText`/`ThankYouText`/`YouLookTiredText`,
  found by the same sweep, never previously documented) needs more than
  that regeneration alone: `data/scripts/flavor/silph_co_9f.lua`'s
  `TEXT_SILPHCO9F_NURSE` is a static command table
  (`face_player`/`check_flag`/`heal_party`/`fade`/`wait`/`show_text`
  rows), not a function, with its three lines as bare literals -- no
  `game.data.text` lookup at all, so even a fresh manifest doesn't help
  until that script itself is rewritten. Two ways to close it, neither
  done yet: an upstream gen1recomp rewrite of that script (needs live
  testing this project can't do), or a `config/rby/literal_handlers.json`
  entry the same way as the now-obsolete Youngster2 handler used to (see
  "Verified working, not a gap" above) -- blocked on extending
  `pipeline/literals.py`'s flow DSL with `heal_party`/`fade`/`wait`
  operations, which it doesn't support yet
  (only `say`/`choice`/`if`/`set_flag`/`inventory`/`money`/
  `script_move`/`done`/`engage_trainer`). Those three primitives already
  exist as ordinary script commands in gen1recomp
  (`src/script/Commands.lua`), so the DSL extension would call/mirror
  existing engine behavior rather than invent new mechanics -- but
  `map_scripts:register` is a single-winner override per TEXT constant
  (`src/script/MapScripts.lua:3-8`), so a handler that only translated
  the text and dropped `heal_party`/`fade` would be a real gameplay
  regression (the nurse would stop healing the party), not just an
  incomplete translation.

## Gold

### Fixed: rows already reachable through an existing public hook

`PcMenu`'s five storage-menu rows (`Withdraw Pokémon`, `Deposit Pokémon`,
`Change box`, `Move Pokémon w/o mail`, `See ya!`) and the battle party
submenu's `Switch`/`Stats` rows are *not* private-class reads: both already
run through public list hooks (`ui.pc.items`, `ui.party.submenu`) that this
mod already wraps for other rows (`Cancel`, item-PC actions, decoration,
mail box). They were simply missing from `config/gold/literal_handlers.json`.
Likewise the START menu's two-line highlighted-entry description
(`Pokémon database`, `Party Pokémon status`, `Contains items`, and five
more) runs through `ui.start_menu.items`, whose `item.label` field this mod
already localized -- `item.desc` just wasn't wired up yet. All now fixed;
`Party <PK><MN>\nstatus`'s French translation is slightly longer than the
original two-glyph line and should get an in-game width check.

### Fixed upstream (engine changes, not just this project's config)

- **Clock UI (weekdays, `o'clock`, `MORN`/`DAY`/`NITE`):** `DAYS`
  (SUNDAY..SATURDAY), the `PrintHour` daytime word, and the `"%s
  o'clock"`/`"%d min."` suffixes on InitClock's screens, the main menu
  clock box and the Pokegear's own clock card were plain Lua literals with
  no `Strings()` lookup at all -- reported from a real Spanish Gold build.
  Fixed on gen1recomp `fix/translate-clock-and-day-of-week` (merged,
  PR #1450): both now live in `Strings`-backed lookups in
  `src/core/gen2/Clock.lua`. `SUNDAY`..`SATURDAY` and `MORN`/`DAY`/`NITE`
  translate for free -- `pipeline/engine.py`'s corpus alignment matches
  them byte-for-byte against poke-corpus's own literal ROM text rows.
  `"%s o'clock"`/`"%d min."` don't align automatically the same way (see
  the compromise table below), but the real corpus text is still directly
  available at `corpus/GoldSilver/{lang}_msg.txt` lines 904-905 (parallel
  to `qid_msg.txt`'s `gs.timeset.String_oclock`/`String_min`) -- no longer
  a `Strings()` gap either way.

### Translated via a compromise, not blocked (`engine-contract-gap`)

Same pattern as RBY above -- these 22 entries (`overrides/<language>/gold/engine.json`,
common across fr/de/es/it/ja-Hrkt/ko) already show translated text in-game,
adapted to the engine's split or reordered contract rather than the exact
official phrasing:

| Source | fr override | What's missing |
|---|---|---|
| `%d #MON seen\n%d #MON owned\n\nPROF.OAK's\nRating:` | `%d POKéMON vus\n%d POKéMON pris\fÉvaluation du\nPROF. CHEN :` | Adapted to the engine's two-`printf` contract |
| `%s got %s%d for winning!` | `%s remporte %s%d !` | Currency/printf order follows the engine contract |
| `%s got %s%d for winning! Sent some to MOM!` | `%s remporte %s%d ! Une partie est\nenvoyée à MAMAN !` | Currency/printf order follows the engine contract |
| `%s o'clock` | `%sh` | Bare-suffix corpus source (`gs.timeset.String_oclock`), no placeholder to align against the engine's `%s` template; French drops the space to match `"20h"` |
| `%d min.` | `%d min.` (identical to source) | Bare-suffix corpus source (`gs.timeset.String_min`); coincidentally the same word in English and French |
| `A▶PRINT` / `B▶CANCEL` / `L▶BEFORE` / `R▶NEXT` | `A▶IMPRIMER` / `B▶ANNULER` / `L▶RETOUR` / `R▶SUITE` | Physical button glyph retained |
| `Fly to %s?` | `Voler vers %s ?` | Adapted to the engine's printf contract |
| `LEFT SIDE` / `RIGHT SIDE` | `À GAUCHE` / `À DROITE` | Decoration-menu labels, engine contract |
| `START>CANCEL` | `START>ANNULER` | Physical button retained |
| `Registered the` + `that item.` | `Objet enregistré :` + `d'enregistrer.` | One ROM sentence (`CantRegisterItemText`/`RegisteredItemText`) split across two engine fragments |
| `You can't register` | `Impossible` | Split-contract half of `CantRegisterItemText` |
| `You have no more\nPOKéMON that can\x0bfight!` | `Plus de POKéMON\napte au combat !` | Adapted from `gs.common_2.NoUsableMonText` |
| `{PLAYER} used the` | `{PLAYER} utilise :` | Item-on-next-fragment order |
| `OT/` | `DO/` | Compact engine label, slash retained |
| `HP` | `PV` | `ui/gen2/PhotoStudio.lua:164`, fragmentary standalone source |
| `№.` | `N°` | `ui/gen2/PhotoStudio.lua:157`, fragmentary standalone source (`HallOfFame.lua`/`SummaryMenu.lua` print the same glyph directly, outside `Strings()`) |
| `{STRBUF}.` | `{STRBUF}.` (kept) | `ui/gen2/TradeAnim.lua:187`, runtime name fragment, punctuation-only and language-invariant |

Two more are per-language only, each documented individually in the relevant
`overrides/<language>/gold/engine.json`: German and Korean also override
`"%s\nis about to use...\nWill %s\nchange POKéMON?"` (the German ROM omits
the player-name printf the engine expects), and Japanese and Korean also
override `"Congratulations!"` (the Japanese diploma folds that idea into the
preceding line instead of a standalone sentence).

### Required upstream capabilities

Still genuinely out of reach: these have no public hook at all, only a
hardcoded local table or a `self:say(...)`/`:drawBottomLines(...)` call, so
they must not be implemented by reaching into private UI classes.

Underlying most of the bullets below: RBY's `romText()`/`data.text[label]`
pairing -- the mechanism the "In progress" section above used to close 21
RBY gaps by pointing an existing `Strings()` compromise at its real ROM
label instead -- has **no Gold equivalent at all**. Confirmed directly:
nothing under `src/*/gen2/` (~110 files, 80k lines) calls
`romText()`/`require("src.core.RomText")` or reads `data.text[...]`, and
Gold's own ROM-text extractor (`import/RomExtractorGen2.lua`) keys
dialogue by bank:address (`Opcodes.key(bank, address)`), not by a named
pokered-style label the way RBY's extractor does -- so there is no
label-keyed table to route a Gold `Strings()` fallback through even in
principle. This is why Gold's remaining gaps below are not a small mirror
of the RBY fixes: introducing the pattern for Gold is new engine work, not
a matter of wiring a few missed callsites.

- **PC and storage dialogue:** `CenterPcMenu:buildEntries()` -- the
  "which PC" list (`BILL's PC`, `PROF.OAK's PC`, the player name's own
  `<name>'s PC`, `HALL OF FAME`, and this menu's own `TURN OFF` row) -- is
  built and stored to `self.entries` directly with no `Runtime.call` at all,
  unlike `PcMenu`/`ItemPcMenu`'s row lists (so `ItemPcMenu`'s own
  `TURN OFF`/`LOG OFF` rows, reached through `ui.pc.items`, *are* already
  translated; only `CenterPcMenu`'s copy of `TURN OFF` is not). The same
  file's free-form prompts (`What?`, `Access whose PC?`,
  `<name>'s PC accessed.`, `Want to get your Pokédex rated?`, the
  link-closed message, and its `YES`/`NO` confirmation box) are drawn with
  direct `self:say(...)`/`Chrome.print(...)` calls, also with no hook.
  `BoxMenu`'s `Choose a Pokémon`/`Cancel`/`Party Pokémon`/`Which box?` rows
  are the same: drawn directly, no hook. Box names (`BOX1`, `BOX2`, …) are a
  different case again -- not a menu string at all, but save data written
  once by `SetDefaultBoxNames` when a new save is created
  (`core/gen2/Boxes.lua`'s `save.boxNames`), so they would need a save-init
  hook, not a menu-list one.
- **Battle messages and action menu:** the `Fight`/`Pack`/`Run` action menu
  is a hardcoded local table in `BattleState.lua` with no hook at all, and
  still needs one.

  **Corrected from a prior version of this doc:** the rest of this bullet
  used to list `Wild Pokémon appeared!`, `Pokémon's defense rose`,
  `Pokémon learned …`, `Got away safely`, `Pokémon's attack missed`, `…
  wants to battle`, `… sent out …`, `A critical hit`, and `You have no
  more Pokémon` as needing a brand-new public string/event registry. That
  doesn't hold: `src/battle/gen2/Battle.lua` already `require`s and uses
  `Strings()` (e.g. `Strings("%s\nused %s!", ...)`) -- the hook exists.
  The real gap is that the large majority of this file's combat messages
  (100+ lines) build their text by raw string concatenation instead,
  bypassing that existing hook one message at a time, not because a hook
  is missing. `Pokémon's defense rose` (`EFFECT_REFLECT`) and its
  undocumented `EFFECT_LIGHT_SCREEN` sibling (`SPCL.DEF rose!`) are fixed
  upstream (gen1recomp branch `fix/stat-rise-message-translation`, merged,
  PR #1439); the rest of `gen2/Battle.lua`'s
  messages, including the ones still named above, need the same
  treatment -- wrapping each one in `Strings(...)`, message by message. A
  real chunk of work by volume, but not a design gap.
- **Status condition abbreviations (Gold):** unlike RBY (pending fix
  above), all three Gold screens that draw a status abbreviation
  (`ui/gen2/PartyMenu.lua:672-678`, `ui/gen2/SummaryMenu.lua:208`,
  `ui/gen2/BattleState.lua:3401-3402`) each derive it their own way --
  `ItemEffects.STATUS_CLASS` lookups or a hardcoded local table -- none
  reads `hudLabel`/`label` from the merged `statuses` registry the way
  RBY's fix will. Not a small mirror of the RBY fix: it needs all
  three call sites rewritten, not one lookup swapped in. This project has
  no `status_labels`-equivalent catalog for Gold yet either, so there is
  nothing to wire up on this project's side until both exist.
- **Gen2 Pokédex screen:** expose the Gen2 Pokédex text and its `START` /
  `SELECT` / `OPTION` / `SEARCH` labels through a public registry. The mod can
  generate species and Pokédex catalogs, but the current screen reads a
  separate internal `data.gen2Pokedex` table, which is why the in-game entry
  can be blank.
- **Pokegear "Press any button to exit":** still needs a public hook -- this
  one line is not covered by the fix below.
- **Received-item/system rewards:** confirmed with a real in-game boot (fr):
  both cases still show the empty name, and both are genuine engine-side
  name-resolution gaps, not a missing translation -- the surrounding "reçoit"
  text is correctly translated, only the substituted name is empty.
  - The Pokégear ("reçoit .", `gs.std_text.ReceivedItemText`, period
    terminator): this message is built by the `verbosegiveitem` opcode
    (`script/gen2/Vm.lua`), which sources the name from `self.getItemNameFn`.
    POKéGEAR does not appear anywhere in the corpus's 256-entry item table
    (`gs.names.ItemNames.*`) -- it isn't a normal bag item -- and
    `getItemName()` (`world/gen2/World.lua`) is written to fall back to an
    `"ITEM<n>"` placeholder for an unknown item, not an empty string, so
    either a second `getItemName` registration exists elsewhere with a
    different (buggier) fallback, or this specific grant resolves to a
    genuinely empty name upstream.
  - Cyndaquil/Héricendre ("reçoit !", `gs.ElmsLab.ReceivedStarterText`, a
    different row using `wStringBuffer3`, not `wStringBuffer4`): the
    `givepoke` opcode (`script/gen2/Vm.lua`) adds the Pokémon to the party
    but does not itself write any name buffer; whatever separate ROM script
    instruction is supposed to populate `wStringBuffer3` beforehand
    (elsewhere in the engine this is a `nameMon`-style special, see
    `script/gen2/Specials.lua`) is either missing or broken for this
    specific script.
  Neither is fixable from this translation mod without reaching into private
  engine internals; both are candidates for an upstream gen1recomp bug
  report.
- **Item descriptions and summary/stat labels:** expose the bag item
  descriptions and the remaining Pokémon summary labels (`Level up`, `EXP
  Points`, `Type`, `Item`, `Move`, `OT`, `Attack`, `Defense`, and related
  screens) through public data or hooks.

The entries in `config/gold/literal_handlers.json` record known stable corpus
matches for these screens. They can be activated when the corresponding public
upstream hooks exist; they are deliberately not a private-class monkey patch.
This keeps the release manifest permission-free and makes the remaining work
visible to the engine project.

## Engine bugs surfaced by TTF mode (not translation gaps) -- fixed upstream

Two unrelated gen1recomp bugs, both only visible once a mod activates TTF
text mode (`mod.content.font:register("ttf", ...)`), translated or not --
neither fixable from a mod, since neither had a hook into the broken code.

- **ManagerState white-on-white:** reported by a user (fr, Fusion Pixel
  profile): the in-game Mod Manager screen (`src/mods/ManagerState.lua`)
  showed no text at all, everything white -- invisible on the vanilla
  tile font, whose glyphs are always black-on-transparent regardless of
  the current draw color. Root cause: `ManagerState:draw()`/
  `drawOverlay()` set white before their `Font.drawBox` call and never
  reset to black afterward, unlike every other screen that calls
  `Font.drawBox` (`TitleState.lua`, `StartMenu.lua`, `HallOfFame.lua`,
  `BoxMenu.lua`, `PartyMenu.lua`). Fixed on gen1recomp
  `fix/manager-state-draw-color` (merged, PR #1426): both call sites now
  reset to black right after `Font.drawBox`, matching every other screen.
- **Fragmented glyphs on Android:** a mod's custom TTF font rendered
  correctly on Windows but came out fragmented (strokes dropped, doubled,
  or interrupted) on Android, while ROM tile glyphs stayed sharp. Root
  cause: the in-game renderer draws into a pixel-exact `dpiscale = 1`
  canvas, but TTF fonts were created with no explicit DPI scale, so LÖVE
  rasterized them at the Android window's (higher) density and the result
  got resampled back down, distorting one-pixel strokes. Fixed on
  gen1recomp `fix/android-ttf-dpi` (merged, PR #1042): TTF fonts are now
  created with an explicit `dpiscale = 1`, matching the game canvas; the
  existing ROM-tile fallback on load failure is unchanged. Verified with a
  translation mod on an Android emulator: glyphs render cleanly after the
  fix.

## Build tooling bugs in `tools/modkit.py` on Windows (not translation gaps)

Two unrelated Windows-only encoding bugs in gen1recomp's own
`tools/modkit.py` (vendored, not this project's code), both hit while
running or validating a translation mod.

### Fixed upstream: dumped text crash when it isn't representable in the system codepage

`dump_dataset()` (and two similar call sites, `run_loader()`'s loader
driver and `check_data_dump()`'s dump check) called `subprocess.run(...,
capture_output=True, text=True)` with no explicit `encoding=`, so Python
fell back to the OS locale's default codepage -- on Windows, always a
legacy single-byte codepage, never UTF-8. A LuaJIT dump byte with no
mapping in that codepage (e.g. `”` U+201D, whose UTF-8 trailing byte
`0x9D` is undefined in cp1252) crashed `subprocess.communicate()`'s
internal reader thread silently, leaving `.stdout` as `None` and the
caller crashing one line later with `AttributeError: 'NoneType' object
has no attribute 'splitlines'`. Verified reproducible today: the real
Yellow-imported dataset's `_ColosseumHeightText` row contains exactly
this byte, so scaffolding/refreshing a Yellow-aware translation on
Windows hit this reliably; the Red/Blue-only dump has zero occurrences.
Fixed on gen1recomp `fix/modkit-dump-dataset-utf8-decode` (merged,
PR #996): all three call sites now pass `encoding="utf-8"` explicitly,
matching the UTF-8 the dumps are actually produced in.

### Still open: `modkit pack` fails on a non-ASCII Windows path

Two independent Windows users reported the GUI's build failing with only
"Command failed with exit code 1" and a `modkit.py ... pack ...` command
line -- no other detail (the GUI not showing the command's own captured
output was a separate, real gap, since fixed). One report's path was
`D:\Jeux\Fan Made Pokémon\Gen1 Recomp\...`; the other reproduced it
directly by picking `Downloads\ééé` as the output directory. Both point at
the same thing: an accented character anywhere in the working tree
`modkit.py pack` runs from.

**Root cause, traced through `tools/modkit.py` (gen1recomp, vendored --
not this project's own code):** `cmd_pack` calls `run_loader(repo, mod_dir,
...)` to validate the mod by actually booting it under LuaJIT headlessly.
`run_loader` builds a map of every mod file to its absolute filesystem path
(`files[f"{mount}/{rel}"] = os.path.join(mod_dir, rel)`), embeds that map as
Lua string literals into a generated driver script
(`entries = "".join("  [%s] = %s,\n" % (lua_quote(k), lua_quote(v)) ...)`),
writes it as a UTF-8 text file (`tempfile.NamedTemporaryFile("w", ...,
encoding="utf-8")`), and runs it with
`subprocess.run([LUAJIT, driver_path], cwd=repo, ...)`. The driver then
`io.open`s those paths to actually load the mod's files.

LuaJIT (like standard Lua) has no concept of source-file text encoding for
string literals: the bytes between the quotes in the driver script --
UTF-8 bytes, since Python wrote the file as UTF-8 -- become the runtime
string's bytes verbatim. On Windows, `io.open` reaches the C runtime's
narrow `fopen()`, which interprets those bytes against the **active ANSI
codepage**, not UTF-8. UTF-8's two-byte encoding of "é" (`0xC3 0xA9`)
decoded as a Windows-1252-family codepage does not round-trip back to "é"
-- the resulting filename does not exist on disk, `io.open` fails, the Lua
driver errors out, LuaJIT exits non-zero, and `run_loader` reports it as
`Finding("MK100", "error", "loader driver crashed: ...")`, which `cmd_pack`
treats as fatal (exit code 1) -- matching both reports exactly.

Not fixable from this project: the failure is inside gen1recomp's own
`tools/modkit.py` driving LuaJIT, not in anything this mod's pipeline
generates or controls. Two realistic upstream fixes, neither requiring a
LuaJIT rebuild:

- Convert `mod_dir`'s absolute path to its Windows short (8.3) name (always
  pure ASCII) via `ctypes.windll.kernel32.GetShortPathNameW` before
  embedding it in the driver script. Requires 8.3 name generation to be
  enabled for the volume, which is the Windows default but can be turned
  off.
- Copy the mod tree into an ASCII-safe temporary directory before invoking
  LuaJIT for this check, sidestepping the encoding mismatch entirely
  regardless of where the real mod directory lives.

This project's own workaround is narrower and does not depend on an
upstream fix: the GUI no longer roots its working directory (where
gen1recomp is cloned and the mod is actually built and packed) inside the
user's chosen *output* directory, since that is the more commonly
non-ASCII one (a deep, descriptively-named project folder picked via a
file browser, as in both reports) -- it now stays anchored near the
executable, matching what the CLI already did by default. That does not
fully close the gap (the executable's own launch location, or a
non-ASCII Windows username, can still trigger this), which is why it is
recorded here rather than closed.
