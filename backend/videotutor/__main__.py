"""Entry point: `python -m videotutor` from source, VideoTutor.exe when installed.

    --no-tray      run in the foreground without a tray icon (development)
    --no-browser   do not open the home page (used by the autostart shortcut)
    --smoke-test   start headless, self-check, exit 0/1 (used by tools/build.py)

Only standard-library modules are imported at the top: a broken install must
still be able to report that it is broken.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import traceback
from pathlib import Path

from videotutor.core.product import ProductFileError, load_product
from videotutor.infra import paths
from videotutor.infra.logging_setup import (
    ErrorDialog,
    StdStreams,
    install_crash_hook,
    message_box,
    setup_logging,
)
from videotutor.infra.single_instance import InstanceMutex, RuntimeFile


def _report_fatal(stage: str, exc: BaseException, log_dir: Path, show: ErrorDialog | None) -> int:
    """A failure before (or instead of) the tray icon. In a windowed exe it is
    otherwise written nowhere and the process just vanishes: to a non-technical
    user the app "does nothing"."""
    detail = f"{stage}\n\n{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
    target = log_dir / "startup_error.txt"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(detail, encoding="utf-8")
    except OSError:
        pass
    text = f"{stage}\n\n{type(exc).__name__}: {exc}\n\n详细信息：\n{target}"
    if show is not None:
        show(text)
    else:
        message_box(text, "启动失败", error=True)
    return 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="VideoTutor")
    p.add_argument("--no-tray", action="store_true")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--smoke-test", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    streams = StdStreams()
    try:
        product = load_product(paths.product_file())
    except ProductFileError as exc:
        # No product name yet, so no home dir: the temp dir is the only safe place.
        streams.ensure(Path(tempfile.gettempdir()))
        return _report_fatal(
            "安装文件损坏 / product.toml is broken",
            exc,
            Path(tempfile.gettempdir()) / "videotutor-startup",
            None,
        )

    loc = paths.resolve(product.name)
    streams.ensure(loc.logs_dir)
    dialog = ErrorDialog(streams, f"{product.name} — 出错了")
    args = parse_args(argv)

    try:
        paths.ensure_dirs(loc)
        log_file = loc.logs_dir / "app.log"
        setup_logging(log_file, console=streams.has_console())
        install_crash_hook(dialog, log_file)

        import logging

        log = logging.getLogger("main")
        log.info(
            "%s %s starting (frozen=%s, home=%s)",
            product.name,
            product.version,
            paths.is_frozen(),
            loc.home,
        )

        if args.smoke_test:
            from videotutor.bootstrap import smoke_test

            return smoke_test(product, loc)

        # Decide single-instance before importing the web stack: a duplicate
        # launch should exit in a fraction of a second.
        mutex = InstanceMutex(product.single_instance_mutex)
        if not mutex.claim():
            if not RuntimeFile(loc.runtime_file).notify_existing():
                message_box(
                    f"{product.name} 已经在运行，请在任务栏右下角的托盘图标中打开。", product.name
                )
            log.info("already running; handed over to the existing instance")
            return 0

        try:
            from videotutor.bootstrap import Application, Options

            app = Application(
                product, loc, Options(tray=not args.no_tray, open_browser=not args.no_browser)
            )
            return app.run()
        finally:
            mutex.release()
    except Exception as exc:  # noqa: BLE001 -- last line of defence: report, never vanish
        return _report_fatal("启动失败 / startup failed", exc, loc.logs_dir, dialog)


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
