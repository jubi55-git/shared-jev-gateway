# shared-jev-gateway

**Jev Decision Gateway Core。** 複数のプロジェクトが**同じ実装・同じ版**を依存として使う判定ゲート。
各リポジトリへコピーしない。

```
Raw input → (Caller) 事実の計算・最小state
          → Core: 事実の条件表 → state検査 → hash/再送防止 → Jev 1リクエスト → コード側集計
          → PASS / REJECT / ESCALATE / HOLD
                               └ ESCALATE のときだけ Caller が親モデルを呼ぶ（Coreは呼ばない）
```

## 原則

- Jevには**小さな設問のNoul確率（p(YES)）だけ**を聞く。routeはコードが決める。自由文は使わない
- 設問1つにfailure mode 1つ。**包括的な一問・同義の言い換えはpolicy登録時に弾く**
- 各policyに**反証（risk）の設問**と**不確実性の設問1つ**が必須。必須設問は3つ以上
- 固定モデルだけ（`jev-latest` 拒否）・自動再送なし・同一input hashを再送しない・どの異常もHOLD
- コードで分かること（CI・schema・文字数・hash）は**事実（真偽値）**として渡し、Jevに聞かない
- stateは宣言したフィールドだけ。秘密情報らしき文字列・メールアドレスがあればHOLD
- Coreにプロジェクト固有の概念を入れない（`tests/test_boundaries.py` が見張る）

## ルート判定（上から順）

| 条件 | route |
|---|---|
| policy不正・事実の欠け・stateの異常・モデル不正 | HOLD |
| 事実の条件表で決まる | その route（Jevを呼ばない） |
| API・schema・モデル・台帳の異常 | HOLD |
| `counter_of` の対が強く逆を主張 | HOLD |
| critical 設問で p_ok ≤ 0.15 | REJECT |
| 必須設問すべてが p_ok ≥ 0.90（policyで上げられる。下げられない） | PASS |
| それ以外（UNKNOWN帯 0.15〜0.85 を含む） | ESCALATE |

`p_ok` は risk 設問なら `1 - p(YES)`、assurance 設問なら `p(YES)`。

## 入口

入口が違っても判断ロジックは同じ（入口は読んで渡すだけ）。

- **A. CLI（現行）** — バッチ・バックエンド用
- B. HTTP — 画面操作中の即時判定が必要になったときだけ足す（未実装）

```bash
jev-gateway evaluate --policy p.json --state s.json --facts f.json \
  --ledger ledger.jsonl --telemetry telemetry.jsonl \
  --escalation-target opus-5.5-medium --expect-version 0.1.1
jev-gateway lint-policy p.json
jev-gateway record-parent-call --telemetry telemetry.jsonl --input-hash <hash> --target opus-5.5-medium
jev-gateway telemetry --telemetry telemetry.jsonl
```

**呼び出し側の約束:** 終了コードが0以外、または標準出力のJSONを読めないときは**HOLDとして扱う。**

Python からは `from jev_decision_gateway import evaluate, redact`。**stateを作るときは `redact()` で秘密情報らしき部分を伏せる**（検査と同じパターン。伏せずに渡すとHOLD）。

TypeScript からの例（学習アプリ）:

```ts
const { stdout } = await execFileAsync("jev-gateway", ["evaluate", "--policy", p, "--state", s,
  "--facts", f, "--ledger", l, "--telemetry", t, "--expect-version", "0.1.1"]);
const result = JSON.parse(stdout); // 失敗・解析不能は HOLD 扱い
```

見本policy: `examples/learning_question_quality.json`

## 環境変数

- `TYPESAFE_API_KEY`（必須。stateへ入れない）
- `TYPESAFE_JEV_MODEL`（任意。既定 `jev-1.13.0`。`jev-` で始まる固定版だけ）

## 版の管理

- タグ `vX.Y.Z` で配る。**`__version__` を上げて main へ入れると、`.github/workflows/tag.yml` が自動でタグを打つ**（手で打たない）。各プロジェクトはタグか commit SHA で固定する
  - Python: `jev-decision-gateway @ git+https://github.com/jubi55-git/shared-jev-gateway@v0.1.0`
  - CLIだけ使う: `uv tool install git+https://github.com/jubi55-git/shared-jev-gateway@v0.1.0`
- 判定結果とinput hashに `core_version` / `policy_version` / `policy_hash` が入る。版が変わると別判定になる
- **閾値を緩めるのはCoreの版を上げるときだけ**

## 計測

`jev-gateway telemetry` が出すもの: 総件数・コードだけで終了・Jev到達・設問数・各route件数・
親モデル呼出し/回避件数・API失敗・確信度不足・`parent_call_reduction_rate`・`escalation_avoidance_rate`。
削減率の基準は「Gatewayが無ければ全件を親モデルへ回していた」とみなす推定値。

## 由来

`typesafe.py` は dearm-app の `scripts/jev_guard.py` を本体そのまま移したもの。
dearm-app 側はこのパッケージを読み直すだけの層にする（重複させない）。
入力hashの計算は dearm-app の `ai_call_guard.digest` と同じ出力（`tests/test_policy.py` で固定）。

## テスト

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```
