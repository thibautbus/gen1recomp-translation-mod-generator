from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .align import align, apply_corpus_overrides
from .corpus import load_corpus, parse_redblue, canonical_language
from .generate import generate_lua
from .validate import release_gate, validate
from .roms import catalog_roms, import_rom, import_all
from .mod import font_profile_warning, generate_mod
from .disassembly_audit import run_audit
from .engine_backlog import MATRIX_LANGUAGES, run_backlog, run_backlog_matrix


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="fr-pipeline")
    sub = p.add_subparsers(dest="command", required=True)
    parse = sub.add_parser("parse"); parse.add_argument("corpus"); parse.add_argument("-o", "--output", required=True); parse.add_argument("--target-lang", default="fr")
    corpus_overrides_option = ("--corpus-overrides",)
    al = sub.add_parser("align"); al.add_argument("records"); al.add_argument("-o", "--output", required=True); al.add_argument("--target-lang", default="fr"); al.add_argument(*corpus_overrides_option, dest="corpus_overrides")
    gen = sub.add_parser("generate"); gen.add_argument("aligned"); gen.add_argument("-o", "--output", required=True); gen.add_argument("--mod-id", default=None); gen.add_argument("--target-name", default=None); gen.add_argument("--target-lang", default=None); gen.add_argument(*corpus_overrides_option, dest="corpus_overrides"); gen.add_argument("--modkit-worksheet"); gen.add_argument("--engine-catalog"); gen.add_argument("--engine-overrides", default=None); gen.add_argument("--engine-source"); gen.add_argument("--engine-scope"); gen.add_argument("--font-source"); gen.add_argument("--font-profile", choices=("fusion", "pokemon"), default="fusion"); gen.add_argument("--semantic-anchors"); gen.add_argument("--semantic-anchor-decisions"); gen.add_argument("--report")
    refresh = sub.add_parser("refresh"); refresh.add_argument("aligned"); refresh.add_argument("--mod", required=True); refresh.add_argument(*corpus_overrides_option, dest="corpus_overrides")
    val = sub.add_parser("validate"); val.add_argument("aligned"); val.add_argument("--release", action="store_true"); val.add_argument("--version", choices=("red", "blue")); val.add_argument("--report"); val.add_argument("--charmap", help="JSON glyph->byte map required for release"); val.add_argument("--coverage", help="modkit join coverage JSON required for release")
    cat = sub.add_parser("catalog"); cat.add_argument("--red", required=True); cat.add_argument("--blue", required=True); cat.add_argument("-o", "--output", required=True)
    imp = sub.add_parser("import"); imp.add_argument("version", choices=("red", "blue")); imp.add_argument("rom"); imp.add_argument("--gen1recomp", required=True); imp.add_argument("--out", required=True); imp.add_argument("--assets", required=True)
    all_imp = sub.add_parser("import-all"); all_imp.add_argument("--red", required=True); all_imp.add_argument("--blue", required=True); all_imp.add_argument("--gen1recomp", required=True); all_imp.add_argument("--cache-root", required=True)
    sub.add_parser("audit-disassemblies", help="developer-only private localized disassembly audit")
    backlog = sub.add_parser("engine-backlog", help="developer-only private unresolved engine-string backlog")
    backlog.add_argument("--language", "--target-lang", dest="language", default=None)
    backlog.add_argument("--checkout", help="private Gen1Recomp checkout (defaults to .cache/dependencies/gen1recomp)")
    backlog.add_argument("--corpus-root", help="private PokeCorpus checkout")
    backlog.add_argument("--coverage", dest="coverage_path", help="cached coverage JSON")
    backlog.add_argument("--engine-catalog", help="cached strings.lua scaffold")
    matrix = sub.add_parser("engine-backlog-matrix", help="developer-only private multilingual engine backlog matrix")
    matrix.add_argument("--languages", default="fr,de,es,it,ja-Hrkt", help="comma-separated canonical languages")
    matrix.add_argument("--checkout", help="private Gen1Recomp checkout (defaults to .cache/dependencies/gen1recomp)")
    matrix.add_argument("--corpus-root", help="private PokeCorpus checkout")
    matrix.add_argument("--coverage-dir", help="directory/template for per-language coverage.json snapshots")
    matrix.add_argument("--engine-catalog-dir", help="directory/template for per-language strings.lua scaffolds")
    matrix.add_argument("--coverage", action="append", metavar="LANG=PATH", help="explicit per-language coverage snapshot (repeatable)")
    matrix.add_argument("--engine-catalog", action="append", metavar="LANG=PATH", help="explicit per-language strings.lua scaffold (repeatable)")
    args = p.parse_args(argv)
    if args.command == "catalog":
        catalog_roms({"red": args.red, "blue": args.blue}, args.output); return 0
    if args.command == "import":
        import_rom(args.version, args.rom, args.gen1recomp, args.out, args.assets); return 0
    if args.command == "import-all":
        import_all({"red": args.red, "blue": args.blue}, args.gen1recomp, args.cache_root); return 0
    if args.command == "audit-disassemblies":
        run_audit()
        return 0
    if args.command == "engine-backlog":
        try:
            report = run_backlog(
                language=args.language,
                checkout=args.checkout,
                corpus_root=args.corpus_root,
                coverage_path=args.coverage_path,
                engine_catalog=args.engine_catalog,
            )
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"engine-backlog: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"language": report["language"], "stats": report["stats"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "engine-backlog-matrix":
        try:
            def matrix_paths(values, label):
                result = {}
                for value in values or []:
                    if "=" not in value:
                        raise ValueError("matrix paths must use LANG=PATH")
                    raw_language, path = value.split("=", 1)
                    language = canonical_language(raw_language, "")
                    if language not in MATRIX_LANGUAGES:
                        raise ValueError(f"unsupported {label} language {raw_language!r}")
                    if not path:
                        raise ValueError(f"missing {label} path for language {raw_language!r}")
                    if language in result:
                        raise ValueError(f"duplicate {label} language mapping for {language!r}")
                    result[language] = path
                return result or None
            coverage_paths = matrix_paths(args.coverage, "coverage")
            catalog_paths = matrix_paths(args.engine_catalog, "engine catalog")
            if coverage_paths is not None and args.coverage_dir:
                raise ValueError("cannot combine --coverage mappings with --coverage-dir")
            if catalog_paths is not None and args.engine_catalog_dir:
                raise ValueError("cannot combine --engine-catalog mappings with --engine-catalog-dir")
            report = run_backlog_matrix(
                languages=args.languages,
                checkout=args.checkout,
                corpus_root=args.corpus_root,
                coverage_paths=coverage_paths,
                engine_catalog_paths=catalog_paths,
                coverage_dir=args.coverage_dir,
                engine_catalog_dir=args.engine_catalog_dir,
            )
        except (FileNotFoundError, ValueError, OSError) as exc:
            print(f"engine-backlog-matrix: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"languages": report["languages"], "stats": report["stats"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "parse":
        records = parse_redblue(args.corpus, canonical_language(args.target_lang))
        Path(args.output).write_text(json.dumps([r.__dict__ for r in records], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    raw = json.loads(Path(args.records if hasattr(args, "records") else args.aligned).read_text(encoding="utf-8"))
    if args.command == "align":
        from .model import CorpusRecord
        def record(row):
            known = {k: row[k] for k in ("qid", "language", "text", "game", "source", "english", "override") if k in row}
            return CorpusRecord(**known)
        target_lang = canonical_language(args.target_lang)
        items = apply_corpus_overrides(align((record(r) for r in raw), target_lang=target_lang), args.corpus_overrides)
        Path(args.output).write_text(json.dumps([x.as_dict() for x in items], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    from .model import Alignment, CorpusRecord
    items = []
    inferred_lang = None
    for row in raw:
        en = CorpusRecord(row.get("qid"), "en", row.get("english", ""), row.get("game", "red"))
        lang = row.get("target_lang") or ("fr" if "french" in row else None) or "fr"
        if inferred_lang is None: inferred_lang = lang
        if lang != inferred_lang:
            raise ValueError(f"aligned JSON contains mixed target languages: {inferred_lang} and {lang}")
        value = row.get("translation", row.get("french"))
        target = CorpusRecord(row.get("qid"), lang, value, row.get("game", "red")) if value is not None else None
        items.append(Alignment(row.get("qid", ""), row.get("game", "red"), en, target, row.get("method", "qid"), row.get("override"), lang, row.get("provenance", {})))
    requested_lang = canonical_language(args.target_lang) if getattr(args, "target_lang", None) else None
    if requested_lang and inferred_lang and requested_lang != canonical_language(inferred_lang):
        raise ValueError(f"aligned JSON target language {inferred_lang!r} does not match requested {requested_lang!r}")
    if args.command in {"generate", "refresh"}:
        items = apply_corpus_overrides(items, args.corpus_overrides)
    if args.command == "generate":
        for option, value in (("--semantic-anchors", args.semantic_anchors), ("--semantic-anchor-decisions", args.semantic_anchor_decisions)):
            if value and not Path(value).is_file():
                raise ValueError(f"{option} file not found: {value}")
        output = Path(args.output)
        if output.suffix == ".lua":
            generate_lua(items, output, inferred_lang or "fr")
        else:
            target_lang = canonical_language(args.target_lang or inferred_lang or "fr")
            warning = font_profile_warning(args.font_profile)
            if warning:
                print(f"Warning: {warning}", file=sys.stderr)
            generate_mod(items, output, args.mod_id or f"translation-{target_lang.lower()}", language=target_lang, modkit_worksheet=args.modkit_worksheet, report_path=args.report,
                         engine_catalog=args.engine_catalog, engine_overrides=args.engine_overrides or f"overrides/{target_lang}/shared_engine_overrides.json",
                         semantic_anchors=args.semantic_anchors,
                         semantic_anchor_decisions=args.semantic_anchor_decisions,
                         engine_source=args.engine_source, engine_scope=args.engine_scope,
                         font_source=args.font_source,
                         font_profile=args.font_profile,
                         target_name=args.target_name,
                         strict_engine=bool(args.modkit_worksheet or args.engine_catalog))
        return 0
    if args.command == "refresh":
        # Refresh is deliberately non-destructive: merge qid overrides into
        # newly aligned rows, then rewrite only the generated catalogs.
        generate_mod(items, args.mod)
        return 0
    charmap = json.loads(Path(args.charmap).read_text(encoding="utf-8")) if args.charmap else None
    findings = validate(items, glyphs=charmap, expected_version=getattr(args, "version", None))
    if args.release:
        coverage = json.loads(Path(args.coverage).read_text(encoding="utf-8")) if args.coverage else None
        ok, summary = release_gate(items, findings, charmap, coverage); report = {"ok": ok, **summary}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.report: Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0 if ok else 1
    print(json.dumps(findings, ensure_ascii=False, indent=2))
    if args.report: Path(args.report).write_text(json.dumps({"ok": not findings, "findings": findings}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
