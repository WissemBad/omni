"""omni command line: search / convert / report."""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .core.config import CONFIG


def _source(name: str):
    from .sources.registry import get_source
    try:
        return get_source(name)
    except KeyError as e:
        raise SystemExit(str(e))


def cmd_search(a):
    src = _source(a.source)
    n = 0
    needle = (a.match or "").lower()
    for info in src.list_models():
        if needle in info.name.lower() or needle == info.key.lower():
            print(info.key, info.size, info.name[:140])
            n += 1
            if n >= a.limit:
                break


def _quality(a) -> str:
    from .core import settings
    return a.tex_quality or settings.get("textures", "quality")


def cmd_convert(a):
    from .pipeline import make_options
    from .targets.source.build import build_model
    src = _source(a.source)
    opts = make_options(not a.no_physics, a.collision, a.lossless_normals, _quality(a))
    keys = list(a.hash or [])
    if a.match:
        needle = a.match.lower()
        for info in src.list_models():
            if needle in info.name.lower():
                keys.append(info.key)
                if len(keys) >= a.limit:
                    break
    for k in keys:
        r = build_model(src, k.upper(), CONFIG, opts)
        print(json.dumps(asdict(r), ensure_ascii=False))


def cmd_batch(a):
    import random
    from .pipeline import run_batch
    src = _source(a.source)
    pool = [i for i in src.list_models() if i.name and (a.match or "").lower() in i.name.lower()
            and (not a.max_kb or i.size <= a.max_kb * 1024)]
    random.Random(a.seed).shuffle(pool)
    keys = [i.key for i in pool[: a.limit]]
    print(f"{len(keys)} models selected", flush=True)
    done = []
    def show(r):
        done.append(r)
        print(f"[{len(done)}/{len(keys)}] {r['status']:7} {r['key']} {r.get('model','')} {r.get('seconds',{}).get('total','')}s {'; '.join(r.get('errors',[]))[:140]}", flush=True)
    print(run_batch(src.id, keys, a.workers, not a.no_physics, on_result=show,
                    collision=a.collision, lossless_normals=a.lossless_normals, tex_quality=_quality(a)))


def cmd_preview(a):
    from .preview.glb import export_glb
    src = _source(a.source)
    for k in a.hash:
        model, mats = src.load_model(int(k, 16))
        out = CONFIG.previews / f"{k.upper()}.glb"
        export_glb(src, model, mats, out)
        print(out)


def cmd_blend(a):
    from .targets.blender.export import export_blends
    src = _source(a.source)
    for p in export_blends(src, [k.upper() for k in a.hash]):
        print(p)


def cmd_index(a):
    from .core.catalog import Catalog
    src = _source(a.source)
    print(Catalog(src).build(force=True), "assets indexed")


def cmd_ui(a):
    """The server alone, for the browser (``omni app`` opens the same interface in its own window)."""
    from . import app as desktop
    from .core import settings
    settings.apply()
    from .ui.api import WEB_DIST
    if not WEB_DIST.exists():
        print("interface non construite : cd web && bun install && bun run build")
    url = desktop.running_url(a.port)
    if url:
        print(f"omni tourne deja : {url}")
        if a.open is not False:
            import webbrowser
            webbrowser.open(url)
        return
    raise SystemExit(desktop.run(a.port, browser=True) if a.open is not False and (a.open or settings.get("general", "open_browser"))
                     else _serve_only(a.port))


def _serve_only(port: int) -> int:

    from . import app as desktop
    srv = desktop.Server(desktop.free_port(port)).start()
    print(f"omni ui: {srv.url}  (Ctrl+C ou le bouton Arreter omni des reglages pour quitter)")
    try:
        while srv.thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        srv.stop()
    return 0


def cmd_pick_folder(a):
    from .core import folderdialog
    raise SystemExit(folderdialog.main([a.title]))


def cmd_app(a):
    from . import app as desktop
    raise SystemExit(desktop.run(a.port, browser=a.browser))


def cmd_pm(a):
    from .targets.source.pm_build import PMOptions, build_playermodel
    src = _source(a.source)
    o = PMOptions(name=a.name, title=a.title or a.name, template=a.template, lod=a.lod, max_tris=a.max_tris)
    from .pipeline import apply_quality
    apply_quality(o.mat, _quality(a))
    r = build_playermodel(src, a.parts, o)
    print(json.dumps(asdict(r), ensure_ascii=False, indent=1))


def _families(src, match: str | None):
    from collections import defaultdict
    from .sources.glacier.outfit import OutfitResolver
    fam = defaultdict(list)
    for f, v, h, _n in OutfitResolver(src).outfits():
        if not match or match.lower() in f.lower():
            fam[f].append((v, h))
    return fam


def cmd_outfits(a):
    from .sources.glacier.outfit import body_type
    fam = _families(_source(a.source), a.match)
    for f in sorted(fam)[: a.limit]:
        print("%-70s %3d variation(s)  %s" % (f, len(fam[f]), body_type(f) or "?"))
    print(f"{len(fam)} families")


def cmd_pm_outfit(a):
    from .pipeline import apply_quality
    from .targets.source.pm_build import PMOptions
    from .targets.source.pm_outfit import build_outfit_pm
    src = _source(a.source)
    fam = _families(src, a.family)
    exact = [f for f in fam if f.lower() == a.family.lower()]
    if exact:
        fam = {exact[0]: fam[exact[0]]}
    if len(fam) != 1:
        print(f"{len(fam)} families match '{a.family}':", *sorted(fam)[:30], sep="\n  ")
        return
    family, items = next(iter(fam.items()))
    keep = {int(x) for x in a.variants.split(",")} if a.variants else None
    keys = [h for v, h in sorted(items) if keep is None or v in keep]
    o = PMOptions(lod=a.lod, max_tris=a.max_tris)
    apply_quality(o.mat, _quality(a))
    r = build_outfit_pm(src, keys, o, name=a.name or family.replace("outfit_", ""), title=a.title or "")
    print(json.dumps(asdict(r), ensure_ascii=False, indent=1))


def cmd_sounds(a):
    from pathlib import Path
    from .targets.audio.export import export_sounds
    src = _source(a.source)
    out = Path(a.out) if a.out else CONFIG.sounds_dir(src.id)
    refs = src.list_sounds(progress=lambda m: print(m, flush=True))
    from .core import settings
    st = settings.load()["sounds"]
    s = export_sounds(refs, out, a.format, a.workers or st["workers"], a.match, a.limit, progress=lambda m: print(m, flush=True),
                      tags=st["tags"], skip_stubs=st["skip_stubs"], force=a.force, clean=a.clean)
    print(json.dumps(s, indent=1))
    print(f"-> {out}")


def cmd_deploy(a):
    from .targets.source import deploy
    src = _source(a.source)
    print(deploy.undeploy(src.id) if a.remove else deploy.deploy(src.id, src.title))


def cmd_native(a):
    from . import native
    if a.build:
        rc = native.build(release=True)
        print("build", "OK" if rc == 0 else f"FAILED ({rc})")
        if rc:
            raise SystemExit(rc)
        print("restart the command to load the new build")
        return
    print(json.dumps(native.status(), indent=1))


def cmd_home(a):
    from .core import migrate
    from .core.config import default_home, store_home
    if a.action == "set":
        if not a.path:
            raise SystemExit("omni home set <dossier>")
        rep = migrate.relocate(Path(a.path))
        print(f"Dossier Omni : {a.path} ({rep.summary()})")
    elif a.action == "reset":
        rep = migrate.relocate(default_home())
        store_home(None)
        print(f"Dossier Omni : {default_home()} ({rep.summary()})")
    else:
        print(f"Dossier Omni  : {CONFIG.home}")
        print(f"  workspace   : {CONFIG.workspace}")
        print(f"  exports     : {CONFIG.exports}")


def cmd_migrate(a):
    from .core import migrate
    old = Path(a.old)
    if not (old / "workspace").is_dir():
        raise SystemExit(f"{old} ne contient pas de dossier workspace")
    rep = migrate.apply(old, CONFIG.home, dry_run=a.dry_run)
    verb = "à déplacer" if a.dry_run else "déplacé"
    for m in rep.moved:
        print(f"{verb}: {m.src} -> {m.dst}")
    for m in rep.skipped + rep.errors:
        print(f"laissé: {m.src} ({m.note})")
    for line in rep.relinked:
        print(f"lien GMod: {line}")
    print(rep.summary())


def cmd_gma(a):
    from .targets.source import deploy
    src = _source(a.source)
    print(deploy.build_gma(src.id))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="omni")
    ap.add_argument("--source", default="007fl")
    ap.add_argument("--version", action="version", version=f"omni {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("search"); s.add_argument("match", nargs="?"); s.add_argument("--limit", type=int, default=20); s.set_defaults(fn=cmd_search)
    c = sub.add_parser("convert"); c.add_argument("--hash", nargs="*"); c.add_argument("--match"); c.add_argument("--limit", type=int, default=5)
    for p_ in (c,):
        p_.add_argument("--no-physics", action="store_true")
    c.set_defaults(fn=cmd_convert)
    b = sub.add_parser("batch"); b.add_argument("--match"); b.add_argument("--limit", type=int, default=50); b.add_argument("--seed", type=int, default=1)
    b.add_argument("--workers", type=int, default=4); b.add_argument("--max-kb", type=int, default=0); b.add_argument("--no-physics", action="store_true"); b.set_defaults(fn=cmd_batch)
    for p_ in (c, b):
        p_.add_argument("--collision", choices=["game", "parts", "hull", "coacd"], default="game",
                        help="game: the game's own collision shapes, else one hull per part (default); parts: computed "
                             "from the render mesh; hull: single hull; coacd: precise but slow")
        p_.add_argument("--tex-quality", choices=["max", "high", "balanced", "light"], default=None,
                        help="max: the game's own resolution, normal maps 2048 (default, see settings); high: 4096/2048; balanced: 2048/1024; light: 1024/512")
        p_.add_argument("--lossless-normals", action="store_true", help="uncompressed normal maps (4x size)")
    v = sub.add_parser("preview"); v.add_argument("hash", nargs="+"); v.set_defaults(fn=cmd_preview)
    bl = sub.add_parser("blend"); bl.add_argument("hash", nargs="+"); bl.set_defaults(fn=cmd_blend)
    ix = sub.add_parser("index"); ix.set_defaults(fn=cmd_index)
    ui = sub.add_parser("ui", help="the interface in the default browser")
    ui.add_argument("--port", type=int, default=8770)
    ui.add_argument("--open", dest="open", action="store_true", default=None, help="open the browser (default: settings)")
    ui.add_argument("--no-open", dest="open", action="store_false")
    ui.set_defaults(fn=cmd_ui)
    ap_ = sub.add_parser("app", help="the interface in omni's own window (default when launched without arguments)")
    ap_.add_argument("--port", type=int, default=8770)
    ap_.add_argument("--browser", action="store_true", help="use the default browser instead of the built-in window")
    ap_.set_defaults(fn=cmd_app)
    pmp = sub.add_parser("pm", help="build a GMod player model from character parts (hash or path fragment)")
    pmp.add_argument("name"); pmp.add_argument("parts", nargs="+"); pmp.add_argument("--title")
    pmp.add_argument("--template", choices=["male", "female"], default="male")
    pmp.add_argument("--lod", type=int, default=2); pmp.add_argument("--max-tris", type=int, default=60000)
    pmp.add_argument("--tex-quality", choices=["max", "high", "balanced", "light"], default=None)
    pmp.set_defaults(fn=cmd_pm)
    ol = sub.add_parser("outfits", help="list the game's outfit families (complete characters)")
    ol.add_argument("match", nargs="?"); ol.add_argument("--limit", type=int, default=40); ol.set_defaults(fn=cmd_outfits)
    po = sub.add_parser("pm-outfit", help="build a GMod player model from an outfit family (variations -> bodygroups + skins)")
    po.add_argument("family", help="family name, or a fragment matching exactly one family")
    po.add_argument("--variants", help="comma-separated variation numbers to keep (default: all)")
    po.add_argument("--name"); po.add_argument("--title")
    po.add_argument("--lod", type=int, default=2); po.add_argument("--max-tris", type=int, default=60000)
    po.add_argument("--tex-quality", choices=["max", "high", "balanced", "light"], default=None)
    po.set_defaults(fn=cmd_pm_outfit)
    d = sub.add_parser("deploy"); d.add_argument("--remove", action="store_true"); d.set_defaults(fn=cmd_deploy)
    g = sub.add_parser("gma"); g.set_defaults(fn=cmd_gma)
    hm = sub.add_parser("home", help="show the Omni folder; `set <dir>` / `reset` move it")
    hm.add_argument("action", nargs="?", choices=["show", "set", "reset"], default="show"); hm.add_argument("path", nargs="?")
    hm.set_defaults(fn=cmd_home)
    mg = sub.add_parser("migrate", help="bring the data of an earlier layout (a folder with workspace/, tools/) into the Omni folder")
    mg.add_argument("old"); mg.add_argument("--dry-run", action="store_true"); mg.set_defaults(fn=cmd_migrate)
    nv = sub.add_parser("native", help="status of the Rust core; --build compiles and installs it")
    nv.add_argument("--build", action="store_true")
    nv.set_defaults(fn=cmd_native)
    so = sub.add_parser("sounds", help="export every sound of the game to a named folder tree")
    so.add_argument("--out", help="output folder (default: <exports>/<source>/sounds)")
    so.add_argument("--format", default="auto", choices=["auto", "ogg", "flac", "wav", "mp3"],
                    help="auto = game Vorbis rewrapped as .ogg without re-encoding, other codecs as lossless .flac")
    so.add_argument("--match", default="", help="only sounds whose output path contains this text")
    so.add_argument("--limit", type=int, default=0, help="stop after N unique sounds (0 = all)")
    so.add_argument("--workers", type=int, default=0)
    so.add_argument("--force", action="store_true", help="rewrite files already exported (new names / tags)")
    so.add_argument("--clean", action="store_true", help="with --force: remove files of an earlier naming")
    so.set_defaults(fn=cmd_sounds)
    pf = sub.add_parser("pick-folder", help=argparse.SUPPRESS)       # used by the server: the native folder dialog
    pf.add_argument("title", nargs="?", default="Choisir un dossier")
    pf.set_defaults(fn=cmd_pick_folder)
    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):                    # no command: the desktop window
        a = ap.parse_args(["app"])
    problems: list[str] = []
    if a.fn in (cmd_app, cmd_ui):                     # data moves before any file of the workspace is opened
        from .core import migrate
        for step in (migrate.finish_pending, migrate.migrate_installed):
            try:
                step()
            except (OSError, RuntimeError) as e:
                problems.append(f"{step.__name__}: {e}")
    if a.fn is not cmd_pick_folder:                   # every command uses the configured paths (assets, GMod...)
        from .core import settings
        settings.apply()
    if a.fn in (cmd_app, cmd_ui):                     # the packaged app has no console: errors go to logs/omni.log
        from .core import log
        log.setup(console=a.fn is cmd_ui)
        for p in problems:
            log.logging.getLogger("omni").warning("data move skipped: %s", p)
    a.fn(a)


if __name__ == "__main__":
    main()
