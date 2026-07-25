"""設定監査(正誤判定)エンジン。

ゴールデンコンフィグ(あるべき値)と機器の現在値を突合し、項目ごとに判定を返す。

判定:
  ok      … 期待値と一致
  warn    … 一致しないが運用停止には至らない(severity: warning のルール)
  fail    … 一致しない、かつ重要(severity: error のルール)
  unknown … Router API で現在値を取得できず判定できない
  manual  … Router API に該当設定が無いため、Web GUI での目視確認が必要
"""

from __future__ import annotations

from typing import Any

from .catalog import catalog_index


class RuleError(ValueError):
    """プロファイル定義の記述ミス。"""


# --------------------------------------------------------------------------- 比較


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _norm(value: Any) -> Any:
    """比較用に値を正規化する(真偽値・文字列の表記ゆれを吸収)。"""
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "on"):
            return True
        if low in ("false", "no", "off"):
            return False
        return value.strip()
    if isinstance(value, list):
        return sorted(_norm(v) for v in value if v is not None)
    return value


def _compare(expect: dict, actual: Any) -> tuple[bool, str]:
    """1つの expect 条件を評価し、(合否, 期待値の説明) を返す。"""
    if not isinstance(expect, dict) or len(expect) != 1:
        raise RuleError(f"expect は演算子1つの辞書で記述してください: {expect!r}")
    op, want = next(iter(expect.items()))
    a = _norm(actual)

    if op == "equals":
        return a == _norm(want), f"= {want}"
    if op == "not_equals":
        return a != _norm(want), f"≠ {want}"
    if op == "in":
        return a in [_norm(v) for v in _as_list(want)], "いずれか: " + " / ".join(map(str, _as_list(want)))
    if op == "not_in":
        return a not in [_norm(v) for v in _as_list(want)], "以下を除く: " + " / ".join(map(str, _as_list(want)))
    if op == "is_set":
        empty = a in (None, "", [], {})
        return (not empty) if want else empty, "設定済みであること" if want else "未設定であること"
    if op == "min":
        try:
            return float(a) >= float(want), f"≧ {want}"
        except (TypeError, ValueError):
            return False, f"≧ {want}"
    if op == "max":
        try:
            return float(a) <= float(want), f"≦ {want}"
        except (TypeError, ValueError):
            return False, f"≦ {want}"
    if op == "between":
        lo, hi = _as_list(want)[0], _as_list(want)[1]
        try:
            return float(lo) <= float(a) <= float(hi), f"{lo} 〜 {hi}"
        except (TypeError, ValueError):
            return False, f"{lo} 〜 {hi}"
    if op == "contains":
        return _norm(want) in _as_list(a), f"{want} を含む"
    if op == "not_contains":
        return _norm(want) not in _as_list(a), f"{want} を含まない"
    raise RuleError(f"未知の演算子です: {op}")


# --------------------------------------------------------------------------- 展開


def _expand_keys(pattern: str, available: list[str]) -> list[str]:
    """`wan.*.healthcheck.method` のようなワイルドカードを実在キーに展開する。"""
    if "*" not in pattern:
        return [pattern]
    parts = pattern.split(".")
    matched = []
    for key in available:
        kparts = key.split(".")
        if len(kparts) != len(parts):
            continue
        if all(p == "*" or p == k for p, k in zip(parts, kparts)):
            matched.append(key)
    return matched


def _applies_to(rule: dict, snapshot: dict) -> bool:
    """when 条件(WAN種別など)を評価する。"""
    when = rule.get("when")
    if not when:
        return True
    wan_type = when.get("wan_type")
    if wan_type:
        # 対象キーのWAN IDがその種別かどうかは展開後に個別判定するため、ここでは通す
        return True
    return True


def _wan_of(key: str, snapshot: dict) -> dict | None:
    if not key.startswith("wan."):
        return None
    try:
        wan_id = int(key.split(".")[1])
    except (IndexError, ValueError):
        return None
    for wan in snapshot.get("wans", []):
        if wan["id"] == wan_id:
            return wan
    return None


def _wan_type_of(key: str, snapshot: dict) -> str | None:
    wan = _wan_of(key, snapshot)
    return wan.get("type") if wan else None


# --------------------------------------------------------------------------- 本体


def run_audit(snapshot: dict, values: dict[str, Any], profile: dict) -> dict:
    """監査を実行し、結果サマリと明細を返す。"""
    index = catalog_index(snapshot)
    available = [k for k in values.keys()]
    results: list[dict] = []
    # 無効化されたWANは既定で監査対象外にする(未使用回線の設定不備を誤検知しないため)。
    # プロファイル側で skip_disabled_wan: false、またはルート個別に include_disabled: true で対象化できる。
    skip_disabled = profile.get("skip_disabled_wan", True)

    for rule in profile.get("rules") or []:
        pattern = rule.get("key")
        if not pattern:
            raise RuleError(f"key が無いルールがあります: {rule!r}")
        severity = rule.get("severity", "error")
        expect = rule.get("expect")
        when = rule.get("when") or {}

        keys = _expand_keys(pattern, available)
        if not keys and pattern in index:
            keys = [pattern]
        if not keys:
            # 対象キーがこの機器に存在しない(WAN構成が違うなど)
            results.append(
                {
                    "key": pattern,
                    "label": index[pattern].label if pattern in index else pattern,
                    "category": index[pattern].category if pattern in index else "—",
                    "verdict": "unknown",
                    "expect_text": _describe(expect),
                    "actual": None,
                    "message": "この機器には該当する設定項目がありません",
                    "severity": severity,
                    "ref": rule.get("ref"),
                    "reason": rule.get("reason"),
                }
            )
            continue

        for key in keys:
            item = index.get(key)
            want_type = when.get("wan_type")
            if want_type and _wan_type_of(key, snapshot) not in _as_list(want_type):
                continue
            wan = _wan_of(key, snapshot)
            if (
                wan is not None
                and wan.get("enable") is False
                and skip_disabled
                and not rule.get("include_disabled")
            ):
                continue

            row = {
                "key": key,
                "label": item.label if item else key,
                "category": item.category if item else "—",
                "expect_text": _describe(expect),
                "actual": values.get(key),
                "severity": severity,
                "ref": rule.get("ref") or (item.ref if item else None),
                "reason": rule.get("reason"),
                "writable": bool(item and item.writable),
                "fix_value": rule.get("fix_value"),
            }

            if item is not None and item.manual:
                row.update(
                    verdict="manual",
                    message=f"Web GUIでの目視確認が必要: {item.manual}",
                )
            elif item is None or not item.readable:
                row.update(verdict="unknown", message="Router API で現在値を取得できません")
            elif key not in values:
                row.update(verdict="unknown", message="現在値が取得できませんでした")
            else:
                try:
                    passed, _text = _compare(expect, values.get(key))
                except RuleError as exc:
                    row.update(verdict="unknown", message=str(exc))
                else:
                    if passed:
                        row.update(verdict="ok", message="期待値と一致")
                    else:
                        row.update(
                            verdict="warn" if severity == "warning" else "fail",
                            message="期待値と一致しません",
                        )
            results.append(row)

    # プロファイルに書かれた「手動確認項目」も明細に載せる
    for entry in profile.get("manual_checks") or []:
        key = entry.get("key", "")
        item = index.get(key)
        results.append(
            {
                "key": key,
                "label": entry.get("label") or (item.label if item else key),
                "category": item.category if item else entry.get("category", "手動確認"),
                "verdict": "manual",
                "expect_text": entry.get("expect_text") or "—",
                "actual": None,
                "message": entry.get("reason")
                or (item.manual if item else "Router API 非対応のため目視確認"),
                "severity": "info",
                "ref": entry.get("ref") or (item.ref if item else None),
                "reason": entry.get("reason"),
                "writable": False,
            }
        )

    counts = {"ok": 0, "warn": 0, "fail": 0, "unknown": 0, "manual": 0}
    for row in results:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    judged = counts["ok"] + counts["warn"] + counts["fail"]
    compliance = round(counts["ok"] / judged * 100) if judged else None

    return {
        "profile": {
            "name": profile.get("profile"),
            "title": profile.get("title") or profile.get("profile"),
            "description": profile.get("description"),
        },
        "counts": counts,
        "judged": judged,
        "compliance": compliance,
        "results": results,
    }


def _describe(expect: Any) -> str:
    if not isinstance(expect, dict) or not expect:
        return "—"
    try:
        _passed, text = _compare(expect, object())
        return text
    except RuleError:
        return str(expect)
