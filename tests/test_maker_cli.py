"""End-to-end checks for the maker JSON CLI (python -m maker.cli).

Every check shells out to the CLI exactly as an agent would, against
throwaway projects under .maker_tmp/ (never the real demo data). Also
renders two demo screenshots into screenshots/ for visual inspection.

Run with:  .venv/bin/python -m tests.test_maker_cli
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / ".maker_tmp" / "cli_test"
PY = sys.executable

ENV = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
           PYGAME_HIDE_SUPPORT_PROMPT="1")

failures: list = []


def check(label: str, cond: bool) -> bool:
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        failures.append(label)
    return cond


def cli(*args, project=None, stdin=None, expect_ok=True):
    """Run the CLI; return (exit_code, parsed_json_or_None)."""
    argv = [PY, "-m", "maker.cli"]
    if project is not None:
        argv += ["--project", str(project)]
    argv += [str(a) for a in args]
    proc = subprocess.run(argv, cwd=ROOT, env=ENV, input=stdin,
                          capture_output=True, text=True, timeout=300)
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        payload = None
        print("  stdout was not one JSON object:", proc.stdout[:300])
        print("  stderr:", proc.stderr[-800:])
    if expect_ok and (payload is None or not payload.get("ok")):
        print("  unexpected failure:", proc.stdout[:400], proc.stderr[-800:])
    return proc.returncode, payload


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def tree_digest(root: Path) -> dict:
    return {str(p.relative_to(root)): md5(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


def png_size(path: Path):
    import struct
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def write_png(path: Path, w: int, h: int, color=(200, 80, 40, 255)) -> None:
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    surf.fill(color)
    pygame.draw.rect(surf, (40, 40, 40, 255), (0, 0, w, h), 1)
    pygame.image.save(surf, str(path))


# ---------------------------------------------------------------------------


def test_starter(proj: Path) -> None:
    code, out = cli("new", proj, "--name", "clitest", "--title", "CLI Test")
    check("new starter project", code == 0 and out["ok"]
          and (proj / "project.json").exists())

    code, out = cli("info", project=proj)
    check("info works", code == 0 and out["ok"])

    code, out = cli("validate", "--min-severity", "error", project=proj)
    errs = [] if out is None else out["result"].get("issues", out["result"])
    check("starter validates with 0 errors",
          code == 0 and isinstance(errs, list) and len(errs) == 0)

    code, out = cli("cell", "new", "1,0", "--zone", "Start", project=proj)
    check("cell new 1,0", code == 0 and (proj / "world/cells/1_0.json").exists())

    rows = ["#" * 16] + ["#" + "." * 14 + "#"] * 8 + ["#" * 16]
    rows[8] = "#...====.......#"
    code, out = cli("cell", "ascii", "1,0", project=proj,
                    stdin="\n".join(rows) + "\n")
    check("cell ascii via stdin", code == 0 and out["ok"])
    code, out = cli("cell", "get", "1,0", "--ascii", project=proj)
    check("cell ascii round-trips",
          code == 0 and out["result"]["ascii"] == rows)

    code, out = cli("edge", "set", "0,0", "east", "open", "--carve",
                    project=proj)
    code2, out2 = cli("edge", "set", "1,0", "west", "open", "--carve",
                      project=proj)
    check("edge set open both sides", code == 0 and code2 == 0)
    code, out = cli("room", "get", "0,0", project=proj)
    check("room joins 0,0 + 1,0",
          code == 0 and sorted(map(tuple, out["result"]["members"]))
          == [(0, 0), (1, 0)])

    cli("cell", "new", "0,1", "--zone", "Start", project=proj)
    code, out = cli("door", "link", "0,0", "south", "--to", "0,1",
                    "--carve", project=proj)
    check("door link", code == 0 and out["ok"])
    data = json.loads((proj / "world/cells/0_0.json").read_text())
    check("door stored on edge", data["edges"].get("south", {}).get("type")
          == "door")

    code, out = cli("entity", "add", "1,0", "pickup", "--at", "6,7",
                    "--prop", "item=missile", project=proj)
    ents = json.loads((proj / "world/cells/1_0.json").read_text())["entities"]
    check("entity add", code == 0 and any(e["type"] == "pickup"
                                          for e in ents))

    code, out = cli("zone", "set", "Cave", "--json",
                    json.dumps({"rock": [[60, 50, 40]] * 8}), project=proj,
                    expect_ok=False)
    # zone set may require a full zone; fall back to copying Start
    if code != 0:
        start = cli("zone", "get", "Start", project=proj)[1]["result"]["data"]
        code, out = cli("zone", "set", "Cave", "--json", json.dumps(start),
                        project=proj)
    zones = json.loads((proj / "zones.json").read_text())
    check("zone set", code == 0 and "Cave" in zones)


def test_batch_and_dry_run(proj: Path) -> None:
    before = tree_digest(proj)
    batch = [
        {"op": "set_tiles", "coord": [1, 0], "points": [[2, 2]],
         "kind": "solid"},
        {"op": "set_tiles", "coord": [1, 0], "points": [[3, 3]],
         "kind": "no_such_kind"},
    ]
    code, out = cli("batch", project=proj, stdin=json.dumps(batch),
                    expect_ok=False)
    check("bad batch fails with failed_index=1",
          code == 1 and out and out.get("failed_index") == 1
          and out.get("op") == "set_tiles")
    check("failed batch leaves files untouched", tree_digest(proj) == before)

    code, out = cli("--dry-run", "tiles", "rect", "1,0", "--from", "2,2",
                    "--to", "5,5", "--kind", "solid", project=proj)
    check("dry-run succeeds", code == 0 and out["ok"])
    check("dry-run writes nothing", tree_digest(proj) == before)

    code, out = cli("batch", project=proj, stdin=json.dumps(batch[:1]))
    check("good batch applies", code == 0 and out["result"]["count"] == 1)
    tile = json.loads((proj / "world/cells/1_0.json").read_text())[
        "tiles"][2][2]
    check("batch tile written", tile.get("kind") == "solid")


def test_render(proj: Path) -> None:
    out_png = TMP / "room.png"
    code, out = cli("render", "0,0", "--room", "-o", out_png,
                    "--overlay", "grid,cells,edges", project=proj)
    check("render room ok", code == 0 and out_png.exists())
    if out_png.exists():
        # room = 0,0 + 1,0 (open edge); 0,1 is behind a door
        check("render room size = 2x1 cells", png_size(out_png)
              == (2 * 768, 480))
    map_png = TMP / "map.png"
    code, out = cli("render", "map", "-o", map_png, project=proj)
    check("render map ok", code == 0 and map_png.exists())


def test_palettes_textures(proj: Path) -> None:
    code, out = cli("palette", "create", "mossy", "--from", "default",
                    project=proj)
    tex = proj / "textures" / "mossy"
    check("palette create + textures", code == 0 and (tex / "solid.png")
          .exists())
    if not (tex / "solid.png").exists():
        return
    h0 = md5(tex / "solid.png")
    code, out = cli("palette", "color", "mossy", "rock", "7", "10,200,30",
                    project=proj)
    check("palette color rebuilds solid.png",
          code == 0 and md5(tex / "solid.png") != h0)

    one = TMP / "oneway48.png"
    write_png(one, 48, 48)
    code, out = cli("texture", "import", "mossy", "oneway", one,
                    project=proj)
    check("import 48x48 oneway", code == 0
          and png_size(tex / "oneway.png") == (48, 48))
    sol = TMP / "solid16.png"
    write_png(sol, 16, 16, (90, 90, 120, 255))
    code, out = cli("texture", "import", "mossy", "solid", sol,
                    project=proj)
    check("import 16x16 solid -> autotile sheet", code == 0
          and png_size(tex / "solid.png") == (48, 768))
    pal = json.loads((proj / "palettes/mossy.json").read_text())
    check("imported kinds are locked",
          set(pal.get("build", {}).get("locked", [])) >= {"oneway", "solid"})


def test_mac_os9_palette(proj: Path) -> None:
    colors = {
        "rock": [[v, v, v] for v in (32, 64, 96, 128, 170, 204, 221, 238)],
        "cap": [[v, v, v] for v in (85, 119, 153, 187, 204, 221, 238, 255)],
        "accent": [51, 85, 153],
    }
    data = {
        "display_name": "Mac OS 9 Platinum",
        "tile_px": 48,
        "build": {"generator": "mac_os9", "colors": colors},
        "textures": {
            "solid":   {"file": "solid.png",   "layout": "autotile16",
                        "variants": 4, "overhang_px": 0},
            "oneway":  {"file": "oneway.png",  "layout": "variants",
                        "variants": 4},
            "spring":  {"file": "spring.png",  "layout": "frames",
                        "frames": 3, "frame_ticks": 6},
            "crumble": {"file": "crumble.png", "layout": "stages",
                        "stages": 4},
        },
    }
    code, out = cli("palette", "create", "macos9", project=proj)
    check("macos9 create", code == 0 and out["ok"])
    code, out = cli("palette", "set", "macos9", "--json", json.dumps(data),
                    project=proj)
    check("macos9 set --json parser + mac_os9 build",
          code == 0 and out["ok"])
    pal = json.loads((proj / "palettes/macos9.json").read_text())
    check("macos9 palette data",
          pal.get("build", {}).get("generator") == "mac_os9"
          and pal.get("display_name") == "Mac OS 9 Platinum")
    tex = proj / "textures" / "macos9"
    check("macos9 sheet dimensions",
          png_size(tex / "solid.png") == (192, 768)
          and png_size(tex / "oneway.png") == (192, 48)
          and png_size(tex / "spring.png") == (144, 48)
          and png_size(tex / "crumble.png") == (192, 48))

    import pygame
    pygame.init()
    solid = pygame.image.load(str(tex / "solid.png"))
    rock0 = tuple(colors["rock"][0])
    cap1 = tuple(colors["cap"][1])
    cap2 = tuple(colors["cap"][2])
    cap5 = tuple(colors["cap"][5])
    cap6 = tuple(colors["cap"][6])
    cap7 = tuple(colors["cap"][7])

    def at(mask, col, row):
        return tuple(solid.get_at(
            (col * 3 + 1, mask * 48 + row * 3 + 1)))[:3]

    body = at(15, 0, 8)
    check("interior solid is light client fill",
          body in (cap5, cap6) and body != rock0)
    check("W-exposed left border",
          at(7, 0, 8) == rock0 and at(7, 1, 8) == cap7 and at(7, 15, 8) != rock0)
    check("E-exposed right border",
          at(13, 15, 8) == rock0 and at(13, 14, 8) == cap1)
    check("titlebar chrome + close/zoom boxes",
          at(14, 8, 0) == rock0 and at(14, 8, 1) == cap7 and at(14, 8, 3) == cap2
          and at(6, 2, 2) == cap7 and at(6, 2, 3) == rock0
          and at(12, 13, 2) == cap7 and at(12, 12, 4) == rock0)

    one = pygame.image.load(str(tex / "oneway.png"))
    check("oneway shelf opaque, transparent below",
          one.get_at((24, 4))[3] == 255 and one.get_at((24, 40))[3] == 0)

    h0 = md5(tex / "solid.png")
    code, out = cli("palette", "rebuild", "macos9", project=proj)
    check("macos9 rebuild deterministic",
          code == 0 and md5(tex / "solid.png") == h0)


def test_mac_os9_locked(proj: Path) -> None:
    import pygame
    tex = proj / "textures" / "macos9"
    one48 = TMP / "macos9_oneway48.png"
    write_png(one48, 48, 48, (90, 90, 120, 255))
    code, out = cli("texture", "import", "macos9", "oneway", one48,
                    project=proj)
    check("import locks macos9 oneway",
          code == 0 and png_size(tex / "oneway.png") == (48, 48))
    h_lock = md5(tex / "oneway.png")
    pal = json.loads((proj / "palettes/macos9.json").read_text())
    code, out = cli("palette", "rebuild", "macos9", project=proj)
    check("locked oneway preserved on rebuild",
          "oneway" in pal.get("build", {}).get("locked", [])
          and md5(tex / "oneway.png") == h_lock
          and png_size(tex / "solid.png") == (192, 768))

    def frame_md5(sheet, i):
        return hashlib.md5(
            pygame.image.tostring(sheet.subsurface((i * 48, 0, 48, 48)),
                                  "RGBA")).hexdigest()

    spring = pygame.image.load(str(tex / "spring.png"))
    check("spring frames distinct",
          len({frame_md5(spring, i) for i in range(3)}) == 3)
    crum = pygame.image.load(str(tex / "crumble.png"))
    check("crumble stages distinct",
          len({frame_md5(crum, i) for i in range(4)}) == 4)
    check("crumble stage3 shows gaps",
          any(crum.get_at((3 * 48 + x, y))[3] == 0
              for x in range(48) for y in range(48)))

    from src.gfx.texture_registry import TextureRegistry
    reg = TextureRegistry(proj / "palettes", proj / "textures")
    surf, y_off = reg.get("macos9", "solid", mask=15)
    check("TextureRegistry serves macos9 sheets",
          surf is not None and y_off == 0)

    batch = [
        {"op": "fill_rect", "coord": [1, 0], "x0": 2, "y0": 1, "x1": 8,
         "y1": 2, "kind": "solid", "palette": "macos9"},
        {"op": "set_tiles", "coord": [1, 0], "points": [[3, 4], [4, 4]],
         "kind": "oneway", "palette": "macos9"},
        {"op": "set_tiles", "coord": [1, 0], "points": [[6, 4]],
         "kind": "spring", "palette": "macos9"},
        {"op": "set_tiles", "coord": [1, 0], "points": [[7, 4]],
         "kind": "crumble", "palette": "macos9"},
    ]
    code, out = cli("batch", project=proj, stdin=json.dumps(batch))
    check("macos9 paint batch", code == 0 and out["ok"])
    room_png = TMP / "macos9_room.png"
    code, out = cli("render", "0,0", "--room", "-o", room_png, project=proj)
    check("macos9 room render in test project",
          code == 0 and room_png.exists() and png_size(room_png) == (1536, 480))


def test_zone_palette(proj: Path) -> None:
    code, out = cli("zone", "palette", "Start", "mossy", project=proj)
    check("zone palette sets the default",
          code == 0 and json.loads((proj / "zones.json").read_text())
          ["Start"].get("default_palette") == "mossy")

    code, out = cli("cell", "new", "2,0", "--zone", "Start", project=proj)
    cell = json.loads((proj / "world/cells/2_0.json").read_text())
    solid = [t for row in cell["tiles"] for t in row
             if t.get("kind") != "empty"]
    check("new cell uses zone default palette",
          code == 0 and solid and all(t.get("palette") == "mossy"
                                      for t in solid))

    code, out = cli("zone", "apply-palette", "Start", "--only-unset",
                    "--palette", "default", project=proj)
    check("apply-palette --only-unset ok", code == 0 and out["ok"])

    code, out = cli("zone", "apply-palette", "Start", project=proj)
    check("apply-palette uses the zone default",
          code == 0 and out["result"]["cells"] >= 1
          and out["result"]["tiles"] >= 1)
    cell = json.loads((proj / "world/cells/0_0.json").read_text())
    solid = [t for row in cell["tiles"] for t in row
             if t.get("kind") != "empty"]
    check("apply-palette re-skinned the zone's tiles",
          solid and all(t.get("palette") == "mossy" for t in solid))

    code, out = cli("zone", "palette", "Start", "none", project=proj)
    check("zone palette none clears",
          code == 0 and "default_palette" not in json.loads(
              (proj / "zones.json").read_text())["Start"])
    code, out = cli("zone", "apply-palette", "Start", project=proj,
                    expect_ok=False)
    check("apply-palette without a default raises",
          code == 1 and "no default_palette" in out.get("error", ""))
    code, out = cli("zone", "apply-palette", "Start", "w", project=proj,
                    expect_ok=False)
    check("apply-palette accepts no positional arg (parse error)",
          code == 2)


def test_export(proj: Path) -> None:
    out_dir = TMP / "exported"
    code, out = cli("export", out_dir, project=proj)
    check("export ok", code == 0 and (out_dir / "play.sh").exists())
    code, out = cli("export", out_dir, project=proj)
    check("re-export over a marked dir ok", code == 0)

    if (out_dir / "src" / "main.py").exists():
        proc = subprocess.Popen(
            [PY, "-m", "src.main", "--start-cell", "0,0",
             "--start-tile", "3,8"], cwd=out_dir, env=ENV,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(3)
        alive = proc.poll() is None
        proc.kill()
        _, err = proc.communicate()
        check("exported game runs 3 s without traceback",
              alive and "Traceback" not in err)

    plain = TMP / "not_an_export"
    plain.mkdir(exist_ok=True)
    (plain / "keep.txt").write_text("user data")
    code, out = cli("export", plain, project=proj, expect_ok=False)
    check("export refuses unmarked non-empty dir",
          code == 1 and (plain / "keep.txt").exists())

    # remove the only spawn point -> validation error -> export refused
    cell = json.loads((proj / "world/cells/0_0.json").read_text())
    idx = [i for i, e in enumerate(cell["entities"])
           if e["type"] == "spawn_point"][0]
    cli("entity", "remove", "0,0", idx, project=proj)
    bad = TMP / "export_bad"
    code, out = cli("export", bad, project=proj, expect_ok=False)
    check("export refused on validation errors",
          code == 1 and "error" in out.get("error", ""))
    code, out = cli("export", bad, "--force", project=proj)
    check("export --force overrides", code == 0)


def test_demo_screens() -> None:
    demo = TMP / "demo_copy"
    code, out = cli("new", demo, "--name", "democopy", "--template", "copy",
                    "--from", ROOT / "tests/fixtures/demo_project")
    check("demo copy project", code == 0)
    shots = ROOT / "screenshots"
    shots.mkdir(exist_ok=True)
    room_png = shots / "maker_render_demo.png"
    code, out = cli("render", "0,0", "--room", "--overlay",
                    "grid,cells,edges,entities", "-o", room_png,
                    project=demo)
    check("demo room render", code == 0 and room_png.exists())
    map_png = shots / "maker_map_demo.png"
    code, out = cli("render", "map", "-o", map_png, project=demo)
    check("demo map render", code == 0 and map_png.exists())
    code, out = cli("validate", "--min-severity", "error", project=demo)
    check("demo copy has no errors", code == 0
          and len(out["result"].get("issues", out["result"])) == 0)


def main() -> int:
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True)
    proj = TMP / "proj"
    test_starter(proj)
    test_batch_and_dry_run(proj)
    test_render(proj)
    test_palettes_textures(proj)
    test_mac_os9_palette(proj)
    test_mac_os9_locked(proj)
    test_zone_palette(proj)
    test_export(proj)
    test_demo_screens()
    print()
    print(f"{len(failures)} failure(s)")
    for f in failures:
        print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
