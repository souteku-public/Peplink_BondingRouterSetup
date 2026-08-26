"""対話ウィザード(コマンドライン操作に不慣れな人向け)。

引数なしで run.py を起動すると(またはダブルクリック用の「計測スタート」から)
このウィザードが立ち上がり、番号を選ぶだけで測定できる。

設計方針:
  * すべて日本語・番号選択式。Enterだけで無難な既定値が選ばれる
  * 入力ミスは弾いて言い直しを促す(落ちない)
  * 実行前に「これから何をするか」と通信量の目安を必ず表示する
"""

from __future__ import annotations

import webbrowser
from pathlib import Path


# ---------------------------------------------------------------------------
# 入力ヘルパー
# ---------------------------------------------------------------------------


def ask(prompt: str, default: str | None = None) -> str:
    """1行入力。空Enterで既定値。Ctrl+Cは呼び出し元で処理する。"""
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        try:
            value = input(f"{prompt}{suffix} > ").strip()
        except EOFError:
            value = ""
        if value:
            return value
        if default is not None:
            return default
        print("  入力してください。")


def ask_choice(title: str, options: list[str], *, default: int = 1, zero_label: str | None = None) -> int:
    """番号選択。戻り値は1始まりの番号(zero_label選択時は0)。"""
    print()
    print(title)
    for i, label in enumerate(options, 1):
        mark = "★" if i == default else " "
        print(f"  {i}) {label} {'(そのままEnterでこれ)' if i == default else ''}".rstrip())
    if zero_label:
        print(f"  0) {zero_label}")
    while True:
        raw = ask("番号を入力", str(default))
        if raw.isdigit():
            n = int(raw)
            if (zero_label and n == 0) or 1 <= n <= len(options):
                return n
        print(f"  0〜{len(options)} の番号で答えてください。")


def ask_yesno(prompt: str, *, default: bool = True) -> bool:
    while True:
        raw = ask(f"{prompt} (y=はい / n=いいえ)", "y" if default else "n").lower()
        if raw in ("y", "yes", "はい"):
            return True
        if raw in ("n", "no", "いいえ"):
            return False
        print("  y か n で答えてください。")


def ask_duration_text(parse_duration) -> tuple[str, float]:
    print("  例: 30m(30分) / 2h(2時間) / 1h30m(1時間30分) / 45s(45秒)")
    while True:
        raw = ask("計測時間", "30m")
        try:
            return raw, parse_duration(raw)
        except ValueError:
            print("  読み取れませんでした。例のように入力してください(30m、2h など)。")


def ask_limit_mb() -> float | None:
    print("  従量SIMの使いすぎ防止です。上限に達すると計測を自動終了します。")
    while True:
        raw = ask("データ量の上限(MB)。制限しない場合はそのままEnter", "なし")
        if raw in ("なし", "", "none", "no"):
            return None
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
        print("  数字(MB)か、Enterだけ(制限なし)で答えてください。")


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------

BANNER = """
==============================================================
  ボンディング回線 スループット計測 — かんたんメニュー
==============================================================
 番号を選んでEnterを押すだけで計測できます。
 迷ったら、そのままEnter(★の選択肢)でOKです。
 途中でやめたいときは Ctrl+C を押してください。
"""


def run(ctx) -> int:
    """ウィザード本体。ctx には run.py 側の関数・モジュールが入っている。

    ctx に必要なもの:
      base_dir, build_targets, load_config_soft, run_phase, run_per_wan,
      monitor(モジュール), report(モジュール), testserver(モジュール)
    """
    print(BANNER)
    while True:
        try:
            return_code = _menu_once(ctx)
        except KeyboardInterrupt:
            print("\n\n中断しました。メニューに戻ります(終了する場合は 0 を選択)。")
            continue
        if return_code is not None:
            return return_code


def _menu_once(ctx) -> int | None:
    choice = ask_choice(
        "何をしますか?",
        [
            "かんたん測定 — 今のボンディング回線の速度とRTTを測る(約30秒)",
            "連続計測 — 時間を決めて1秒ごとにCSVへ記録し続ける",
            "回線ごとの測定 — 回線を1本ずつ切り替えて各回線の実力を測る",
            "結果を見る — ビューワー(グラフ画面)をブラウザで開く",
            "動作確認 — ネットワークを使わずに試してみる(デモ)",
            "測定先サーバーとして起動 — 本社・拠点側のPCで使う",
        ],
        default=1,
        zero_label="終了",
    )
    if choice == 0:
        print("終了します。")
        return 0
    if choice == 1:
        _quick_test(ctx)
    elif choice == 2:
        _monitor(ctx)
    elif choice == 3:
        _per_wan(ctx)
    elif choice == 4:
        _open_viewer(ctx)
    elif choice == 5:
        _demo(ctx)
    elif choice == 6:
        _reflector(ctx)

    print()
    if not ask_yesno("メニューに戻りますか?", default=True):
        print("終了します。")
        return 0
    return None


# ---------------------------------------------------------------- 各メニュー


def _quick_test(ctx) -> None:
    config = ctx.load_config_soft()
    targets = ctx.build_targets(config)
    print()
    print("── かんたん測定 ──────────────────────────")
    print(f"  測定先: {targets['download_url'].split('?')[0]}")
    print("  内容  : RTT → 下り12秒 → 上り12秒(通信量の目安: 100Mbps回線で約300MB)")
    limit_mb = ask_limit_mb()
    limit = int(limit_mb * 1e6) if limit_mb else None
    if not ask_yesno("測定を開始しますか?", default=True):
        return
    phases = [ctx.run_phase("ボンディング(現構成)", targets, limit)]
    ctx.report.print_summary_table(phases)
    written = ctx.report.save_results(phases, Path(config["output_dir"]))
    print("\n保存しました:")
    for p in written:
        print(f"  {p}")


def _monitor(ctx) -> None:
    config = ctx.load_config_soft()
    targets = ctx.build_targets(config)
    print()
    print("── 連続計測(1秒ごとにCSV記録) ──────────────")
    text, seconds = ask_duration_text(ctx.monitor.parse_duration)

    direction = ask_choice(
        "何を計測しますか?",
        [
            "下りと上りの両方(通信量: 大)",
            "下りのみ(通信量: 中)",
            "上りのみ(通信量: 中)",
            "RTT(応答時間)のみ — 通信量ほぼゼロ。長時間の安定性監視におすすめ",
        ],
        default=1,
    )
    do_down = direction in (1, 2)
    do_up = direction in (1, 3)

    limit = None
    if do_down or do_up:
        print(f"  目安: 100Mbpsの回線を飽和させると1時間あたり約45GB(片方向)を消費します。")
        limit_mb = ask_limit_mb()
        limit = int(limit_mb * 1e6) if limit_mb else None

    print()
    print("── 実行内容の確認 ──")
    print(f"  計測時間 : {ctx.monitor.format_duration(seconds)}")
    print(f"  計測対象 : " + ("下り+上り" if do_down and do_up else "下りのみ" if do_down else "上りのみ" if do_up else "RTTのみ"))
    print(f"  データ上限: {f'{limit/1e6:.0f} MB(片方向)' if limit else 'なし'}")
    print(f"  記録先   : results/monitor_日時.csv(1時間あたり約0.2MB)")
    print("  途中でやめたいときは Ctrl+C(そこまでのデータは残ります)")
    if not ask_yesno("計測を開始しますか?", default=True):
        return

    summary = ctx.monitor.run_monitor(
        targets, seconds, Path(config["output_dir"]),
        limit_bytes=limit, do_download=do_down, do_upload=do_up,
    )
    ctx.report.save_results([summary], Path(config["output_dir"]))
    if ask_yesno("結果をビューワー(グラフ画面)で開きますか?", default=True):
        _open_viewer(ctx, hint_csv=summary.get("monitor_csv"))


def _per_wan(ctx) -> None:
    config = ctx.load_config_soft()
    print()
    print("── 回線ごとの測定 ──────────────────────────")
    print("  ルーターのWAN優先度を一時的に切り替えながら、回線を1本ずつ測定します。")
    print("  測定終了後は自動で元に戻します。※本番運用中には実行しないでください。")

    router = config.get("router") or {}
    if not router.get("host"):
        print("\n  ルーターへの接続情報が設定されていません。ここで入力できます。")
        if not ask_yesno("入力を続けますか?", default=True):
            return
        router = {
            "host": ask("ルーターのIPアドレス", "192.168.50.1"),
            "scheme": "https",
            "username": ask("管理ユーザー名", "admin"),
            "password": ask("管理パスワード"),
        }
        config["router"] = router
        _offer_save_router(ctx, router)

    targets = ctx.build_targets(config)
    limit_mb = ask_limit_mb()
    limit = int(limit_mb * 1e6) if limit_mb else None

    phases = [ctx.run_phase("ボンディング(現構成)", targets, limit)]
    try:
        phases += ctx.run_per_wan(config, targets, limit, assume_yes=False)
    except SystemExit:
        return
    ctx.report.print_summary_table(phases)
    written = ctx.report.save_results(phases, Path(config["output_dir"]))
    print("\n保存しました:")
    for p in written:
        print(f"  {p}")


def _offer_save_router(ctx, router: dict) -> None:
    """入力されたルーター情報を config.yaml に保存するか確認する。"""
    if not ask_yesno("この接続情報を保存して次回から入力を省略しますか?(パスワードがファイルに残ります)",
                     default=False):
        return
    import yaml

    path = ctx.base_dir / "config.yaml"
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if "router:" in text:
                print(f"  {path.name} に既に router 設定があるため、上書きは行いません。手動で編集してください。")
                return
            with path.open("a", encoding="utf-8") as f:
                f.write("\n# ウィザードから追加されたルーター接続情報\n")
                f.write(yaml.safe_dump({"router": router}, allow_unicode=True, sort_keys=False))
        else:
            path.write_text(
                "# ウィザードが作成した設定ファイル(詳細は config.example.yaml を参照)\n"
                + yaml.safe_dump({"router": router}, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        print(f"  保存しました: {path}")
    except OSError as exc:
        print(f"  保存できませんでした: {exc}")


def _open_viewer(ctx, hint_csv: str | None = None) -> None:
    viewer = ctx.base_dir / "viewer.html"
    results = ctx.base_dir / "results"
    print()
    print(f"ビューワーをブラウザで開きます: {viewer}")
    if hint_csv:
        print(f"開いたら、次のファイルを読み込んでください: {hint_csv}")
    elif results.exists():
        csvs = sorted(results.glob("monitor_*.csv"))
        if csvs:
            print(f"開いたら、{results} の中のCSVを読み込んでください(最新: {csvs[-1].name})")
    else:
        print("まだ計測結果がありません。先に計測を実行してください。")
    try:
        opened = webbrowser.open(viewer.as_uri())
        if not opened:
            raise RuntimeError
    except Exception:  # noqa: BLE001
        print("自動で開けませんでした。エクスプローラー/Finderで次のファイルをダブルクリックしてください:")
        print(f"  {viewer}")


def _demo(ctx) -> None:
    print()
    print("── 動作確認(デモ) ── 実際の回線は使いません。")
    server = ctx.testserver.start_background(18123)
    try:
        targets = {
            "download_url": "http://127.0.0.1:18123/__down?bytes={bytes}",
            "upload_url": "http://127.0.0.1:18123/__up",
            "rtt_host": "127.0.0.1", "rtt_port": 18123,
            "duration": 4, "streams": 4,
        }
        phases = [ctx.run_phase("デモ測定", targets, None)]
        ctx.report.print_summary_table(phases)
        print("\nデモが正常に完了しました。この環境で計測できます。")
    finally:
        server.shutdown()


def _reflector(ctx) -> None:
    print()
    print("── 測定先サーバー(リフレクター) ──")
    print("  本社・拠点側のPCでこれを起動しておくと、現場からの測定先にできます。")
    port = ask("待ち受けポート番号", "8123")
    try:
        ctx.testserver.serve("0.0.0.0", int(port))
    except ValueError:
        print("  ポート番号は数字で入力してください。")
    except OSError as exc:
        print(f"  起動できませんでした: {exc}(ポートが使用中の可能性)")
