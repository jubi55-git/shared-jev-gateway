"""構造の見張り: Coreはプロジェクト非依存、親モデルへの経路が無い、接続は1か所（18〜21）。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import jev_decision_gateway

SRC = Path(jev_decision_gateway.__file__).parent
FILES = sorted(SRC.glob("*.py"))

PROJECT_WORDS = ("yle", "dream", "dearm", "yume", "blog", "translation", "editorial",
                 "study", "quiz", "ocr", "photo", "夢", "翻訳", "編集部")
MODEL_VENDORS = ("openai", "anthropic", "google", "genai", "vertexai", "mistralai", "cohere")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def test_core_imports_only_stdlib_and_httpx():
    for path in FILES:
        extra = _imports(path) - set(sys.stdlib_module_names) - {"httpx", "__future__"}
        assert not extra, (path.name, extra)


def test_no_parent_model_sdk():
    for path in FILES:
        assert not (_imports(path) & set(MODEL_VENDORS)), path.name


def test_network_only_in_typesafe():
    users = [p.name for p in FILES if "httpx" in _imports(p)]
    assert users == ["typesafe.py"]


def test_no_project_concepts_in_core():
    for path in FILES:
        text = path.read_text(encoding="utf-8").lower()
        hits = [w for w in PROJECT_WORDS if w in text]
        assert not hits, (path.name, hits)


def test_typesafe_is_single_source():
    # jev_guard相当の処理はtypesafe.pyにだけある（gatewayは再実装しない）
    gateway = (SRC / "gateway.py").read_text(encoding="utf-8")
    assert "typesafe.validate_noul_response" in gateway
    assert "typesafe.post_system_one" in gateway
    assert "httpx" not in gateway and "api.typesafe.ai" not in gateway
