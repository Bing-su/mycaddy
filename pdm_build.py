from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.request import urlretrieve

import pbs_installer

try:
    import tomllib
except ImportError:
    import tomli as tomllib

if TYPE_CHECKING:
    from pdm.backend.hooks import Context

NAME = "mycaddy"

pwd = Path(__file__).parent
with pwd.joinpath("pyproject.toml").open("rb") as f:
    pyproject = tomllib.load(f)
VERSION: str = pyproject["project"]["version"]


def do_not_cross_compile() -> None:
    if "GOOS" in os.environ or "GOARCH" in os.environ:
        msg = "Cross-compilation is not supported. "
        raise RuntimeError(msg)


def is_cibuildwheel() -> bool:
    return os.environ.get("CIBUILDWHEEL", "0") == "1"


def is_windows() -> bool:
    return platform.system() == "Windows"


def is_macos() -> bool:
    return platform.system() == "Darwin"


def get_go() -> str:
    go = shutil.which("go")
    if go is None:
        msg = "golang is required and 'go' should be in $PATH"
        raise RuntimeError(msg)
    return go


def setup_xcaddy(path: Path) -> str:
    xcaddy = Path(path, "xcaddy")
    if is_windows():
        xcaddy = xcaddy.with_suffix(".exe")

    if xcaddy.exists():
        return str(xcaddy)

    go = get_go()
    env = os.environ.copy()
    env["GOBIN"] = str(path)

    args = [
        go,
        "install",
        "github.com/caddyserver/xcaddy/cmd/xcaddy@latest",
    ]

    subprocess.run(args, check=True, env=env)  # noqa: S603
    return str(xcaddy)


def setup_caddy_snake(path: Path) -> Path:
    url = "https://github.com/mliezun/caddy-snake/archive/refs/heads/main.zip"
    zip_path = path / "caddy-snake.zip"

    if not zip_path.exists():
        urlretrieve(url, zip_path)  # noqa: S310

    target = path / "caddy-snake-main"
    if target.exists():
        shutil.rmtree(target)

    shutil.unpack_archive(zip_path, target.parent)

    c_modify = target / "caddysnake.c"
    content = c_modify.read_text("utf-8")
    content = re.sub(r"(?<!_)environ", "environ_", content)
    c_modify.write_text(content, "utf-8")
    return target


def install_pbs(path: Path) -> Path:
    version = ".".join(map(str, sys.version_info[:2]))
    target = path / "python"
    if target.exists():
        shutil.rmtree(target)
    pbs_installer.install(version, target)
    return target


def windows_pkg_config(path: Path) -> Path:
    version = ".".join(map(str, sys.version_info[:2]))
    pc_file = path / "python" / "lib" / "pkgconfig" / "python3-embed.pc"
    content = f"""prefix=${{pcfiledir}}/../.."
exec_prefix=${{prefix}}
libdir=${{exec_prefix}}
includedir=${{prefix}}/include

Name: python3-embed
Description: Python library
Requires:
Version: {version}
Libs: -L${{libdir}} -lpython{sysconfig.get_config_var("VERSION")} -lpthread -lm
Cflags: -I${{includedir}}
"""
    pc_file.parent.mkdir(parents=True, exist_ok=True)
    pc_file.write_text(content, "utf-8")
    return pc_file


def build(output: str) -> None:
    cwd = Path().cwd()
    xcaddy = setup_xcaddy(cwd)

    caddy_snake = setup_caddy_snake(cwd)
    pbs = install_pbs(cwd)

    modules = [
        "github.com/mholt/caddy-webdav",
        f"github.com/mliezun/caddy-snake={caddy_snake}",
    ]

    args = [
        xcaddy,
        "build",
    ]
    for module in modules:
        args.extend(["--with", module])
    args.extend(["--output", output])

    if is_windows():
        windows_pkg_config(cwd)

    env = os.environ.copy()
    env["CGO_ENABLED"] = "1"
    env["PKG_CONFIG_PATH"] = str(pbs / "lib" / "pkgconfig")

    subprocess.run(args, check=False, env=env)  # noqa: S603

    if not Path(output).exists():
        msg = f"Build failed, output not found at {output}"
        raise RuntimeError(msg)
    Path(output).chmod(0o777)

    if is_windows():
        dlls = pbs.glob("python*.dll")
        for dll in dlls:
            shutil.copy(dll, Path(output).parent)

    if is_cibuildwheel() and is_macos():
        dylib = next(Path(pbs, "lib").glob("libpython*.dylib"))
        shutil.copy(dylib, Path("/install", "lib", dylib.name))


def pdm_build_hook_enabled(context: Context):
    return context.target != "sdist"


def pdm_build_initialize(context: Context) -> None:
    do_not_cross_compile()

    config = {"--python-tag": "py3", "--py-limited-api": "none"}
    context.builder.config_settings = {**config, **context.builder.config_settings}

    context.ensure_build_dir()
    output_path = Path(context.build_dir, "bin", NAME)
    if is_windows():
        output_path = output_path.with_suffix(".exe")
    build(str(output_path))


def pdm_build_finalize(context: Context, artifact: Path) -> None:
    if Path(context.build_dir).exists():
        shutil.rmtree(context.build_dir)
