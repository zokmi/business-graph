"""舊知識庫遷移與命令列的行為測試。"""
import os
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from business_graph_mcp.errors import GraphError, GraphMigrationError
from business_graph_mcp.migrate import migrate_workspace
from business_graph_mcp.node import NODE_RULE, parse_legacy_meta, parse_node


@pytest.fixture
def 舊知識庫(tmp_path):
    pages = tmp_path / ".wiki" / "pages"
    pages.mkdir(parents=True)
    (pages / "Discount Calculation.md").write_text(
        "---\ntitle: Discount Calculation\naliases: [折扣計算]\n"
        "status: confirmed\nupdated: 2026-08-14\n---\n\n"
        "見 [[Shipping Rules]]。\n\n第二段保留。\n",
        encoding="utf-8",
    )
    (tmp_path / ".wiki" / "wiki.toml").write_text("stale_days = 120\n", encoding="utf-8")
    return tmp_path


def test_複製節點並保留欄位內文和關係(舊知識庫):
    source = 舊知識庫 / ".wiki" / "pages" / "Discount Calculation.md"
    original = source.read_bytes()
    report = migrate_workspace(舊知識庫)
    assert report.moved == ("Discount Calculation",)
    assert report.skipped == ()
    parsed = parse_node(
        (舊知識庫 / ".bgraph" / "nodes" / source.name).read_text(encoding="utf-8")
    )
    assert parsed.meta is not None
    assert parsed.meta.title == "Discount Calculation"
    assert parsed.meta.node_type == NODE_RULE
    assert parsed.meta.aliases == ("折扣計算",)
    assert parsed.meta.code == ()
    assert parsed.meta.status == "confirmed"
    assert parsed.meta.updated == date(2026, 8, 14)
    assert parsed.body == "見 [[Shipping Rules]]。\n\n第二段保留。"
    assert [(edge.edge_type, edge.target) for edge in parsed.edges] == [
        ("relates_to", "Shipping Rules")
    ]
    assert source.read_bytes() == original


def test_設定檔複製並更名但保留原件(舊知識庫):
    source = 舊知識庫 / ".wiki" / "wiki.toml"
    original = source.read_bytes()
    report = migrate_workspace(舊知識庫)
    assert report.config_moved is True
    assert (舊知識庫 / ".bgraph" / "bgraph.toml").read_bytes() == original
    assert source.read_bytes() == original


def test_解析不了的頁被跳過而非中斷整批(舊知識庫):
    source = 舊知識庫 / ".wiki" / "pages" / "Broken.md"
    source.write_text("沒有 front matter", encoding="utf-8")
    report = migrate_workspace(舊知識庫)
    assert report.skipped == ("Broken",)
    assert report.moved == ("Discount Calculation",)
    assert not (舊知識庫 / ".bgraph" / "nodes" / "Broken.md").exists()
    assert source.read_text(encoding="utf-8") == "沒有 front matter"


def test_重跑不覆寫節點與設定(舊知識庫):
    migrate_workspace(舊知識庫)
    target = 舊知識庫 / ".bgraph" / "nodes" / "Discount Calculation.md"
    config = 舊知識庫 / ".bgraph" / "bgraph.toml"
    target.write_text("既有內容", encoding="utf-8")
    config.write_text("stale_days = 90\n", encoding="utf-8")
    report = migrate_workspace(舊知識庫)
    assert report.moved == ()
    assert report.skipped == ("Discount Calculation",)
    assert report.config_moved is False
    assert target.read_text(encoding="utf-8") == "既有內容"
    assert config.read_text(encoding="utf-8") == "stale_days = 90\n"


def test_沒有舊知識庫時回傳空報告(tmp_path):
    report = migrate_workspace(tmp_path)
    assert report.moved == ()
    assert report.skipped == ()
    assert report.config_moved is False
    assert not (tmp_path / ".bgraph").exists()


def test_無設定檔也可遷移且清單按檔名排序(舊知識庫):
    (舊知識庫 / ".wiki" / "wiki.toml").unlink()
    pages = 舊知識庫 / ".wiki" / "pages"
    (pages / "A.md").write_text(
        "---\ntitle: A\nstatus: inferred\nupdated: '2026-09-01'\n---\n內文",
        encoding="utf-8",
    )
    report = migrate_workspace(舊知識庫)
    assert report.moved == ("A", "Discount Calculation")
    assert report.config_moved is False
    meta = parse_node((舊知識庫 / ".bgraph" / "nodes" / "A.md").read_text(encoding="utf-8")).meta
    assert meta is not None
    assert meta.status == "inferred"
    assert meta.updated == date(2026, 9, 1)
    assert meta.aliases == ()


@pytest.mark.parametrize("raw", [
    "沒有 front matter",
    "---\n[not, mapping]\n---\n",
    "---\ntitle: [broken\n---\n",
    "---\ntitle: ''\nstatus: confirmed\nupdated: 2026-08-14\n---\n",
    "---\ntitle: 12\nstatus: confirmed\nupdated: 2026-08-14\n---\n",
    "---\ntitle: A\nstatus: unknown\nupdated: 2026-08-14\n---\n",
    "---\ntitle: A\nstatus: [confirmed]\nupdated: 2026-08-14\n---\n",
    "---\ntitle: A\nstatus: confirmed\nupdated: invalid\n---\n",
    "---\ntitle: A\nstatus: confirmed\n---\n",
    "---\ntitle: A\nstatus: confirmed\nupdated: 2026-08-14\naliases: wrong\n---\n",
])
def test_舊格式解析失敗回傳空值(raw):
    assert parse_legacy_meta(raw) is None


def test_舊格式解析補上_rule但正式解析仍要求_type():
    text = (
        "---\ntitle: '  A  '\naliases: [' 一 ', '', 12]\n"
        "status: inferred\nupdated: '2026-08-14'\n---\n正文"
    )
    meta = parse_legacy_meta(text)
    assert meta is not None
    assert meta.title == "A"
    assert meta.node_type == NODE_RULE
    assert meta.code == ()
    assert meta.aliases == ("一", "12")
    assert meta.updated == date(2026, 8, 14)
    assert parse_node(text).meta is None


@pytest.mark.parametrize("aliases", ["null", "false", "0", "''", "{}"])
def test_有提供但不是陣列的空值別名必須跳過(舊知識庫, aliases):
    source = 舊知識庫 / ".wiki" / "pages" / "BadAliases.md"
    text = (
        "---\ntitle: BadAliases\nstatus: confirmed\nupdated: 2026-08-14\n"
        f"aliases: {aliases}\n---\n內文\n"
    )
    source.write_text(text, encoding="utf-8")
    assert parse_legacy_meta(text) is None
    report = migrate_workspace(舊知識庫)
    assert report.skipped == ("BadAliases",)
    assert report.moved == ("Discount Calculation",)
    assert not (舊知識庫 / ".bgraph" / "nodes" / "BadAliases.md").exists()
    assert source.read_text(encoding="utf-8") == text


def test_命令列遷移只向_stderr_列出報告(舊知識庫, monkeypatch, capsys):
    from business_graph_mcp.__main__ import main

    (舊知識庫 / ".wiki" / "pages" / "Broken.md").write_text("壞掉", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["business_graph_mcp", "migrate", str(舊知識庫)])
    main()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "已遷移 1" in captured.err
    assert "Discount Calculation" in captured.err
    assert "跳過 1" in captured.err
    assert "Broken" in captured.err
    assert "bgraph.toml" in captured.err
    assert "rule" in captured.err and "逐一複查" in captured.err
    assert (舊知識庫 / ".bgraph" / "nodes" / "Discount Calculation.md").is_file()


@pytest.mark.parametrize("args", [[], ["repo", "extra"]])
def test_命令列參數數量錯誤回傳_2(args, monkeypatch, capsys):
    from business_graph_mcp.__main__ import main

    monkeypatch.setattr("sys.argv", ["business_graph_mcp", "migrate", *args])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "用法" in captured.err


def test_模組命令列可直接呼叫(tmp_path):
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "business_graph_mcp", "migrate", str(tmp_path)],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert result.stderr
    assert not (Path(tmp_path) / ".bgraph").exists()


def test_遷移保留需引號的中繼資料與原始內文位元組(舊知識庫):
    source = 舊知識庫 / ".wiki" / "pages" / "Quoted.md"
    body = "\r\n\r\n    程式碼縮排\r\n\r\n[[Shipping Rules]]  \r\n\r\n".encode()
    source.write_bytes(
        (
            "---\r\ntitle: '折扣: 規則'\r\naliases: ['一, 二', 'true', '折扣 #1']\r\n"
            "status: confirmed\r\nupdated: 2026-08-14\r\n---\r\n"
        ).encode() + body
    )
    original = source.read_bytes()
    report = migrate_workspace(舊知識庫)
    assert "Quoted" in report.moved
    target = 舊知識庫 / ".bgraph" / "nodes" / "Quoted.md"
    result = target.read_bytes()
    meta = parse_node(result.decode()).meta
    assert meta is not None
    assert meta.title == "折扣: 規則"
    assert meta.aliases == ("一, 二", "true", "折扣 #1")
    assert result.split(b"---", 2)[2] == b"\r\n" + body
    assert source.read_bytes() == original


def test_非_utf8_頁面跳過並繼續遷移(舊知識庫):
    source = 舊知識庫 / ".wiki" / "pages" / "Broken.md"
    source.write_bytes(b"\xff\xfe\x00")
    report = migrate_workspace(舊知識庫)
    assert report.skipped == ("Broken",)
    assert report.moved == ("Discount Calculation",)
    assert source.read_bytes() == b"\xff\xfe\x00"


@pytest.mark.parametrize("target_name", ["Discount Calculation.md", "bgraph.toml"])
def test_檢查後才出現的目標也不覆寫(舊知識庫, monkeypatch, target_name):
    original_link = os.link
    raced = False

    def create_before_link(source, target, *args, **kwargs):
        nonlocal raced
        if target.name == target_name and not raced:
            raced = True
            target.write_bytes(b"concurrent content")
        return original_link(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "link", create_before_link)
    report = migrate_workspace(舊知識庫)
    relative = "nodes/Discount Calculation.md" if target_name.endswith(".md") else "bgraph.toml"
    assert (舊知識庫 / ".bgraph" / relative).read_bytes() == b"concurrent content"
    if target_name.endswith(".md"):
        assert report.skipped == ("Discount Calculation",)
        assert report.moved == ()
    else:
        assert report.config_moved is False


@pytest.mark.parametrize("code_line, expected_code", [("", None), ("code: [Discount.apply]\n", ["Discount.apply"])])
def test_遷移保留所有既有_front_matter_欄位(舊知識庫, code_line, expected_code):
    source = 舊知識庫 / ".wiki" / "pages" / "Extended.md"
    text = (
        "---\ntitle: '  Extended  '\nstatus: inferred\nupdated: '2026-08-14'\n"
        "aliases: [' 折扣 ', 12]\n"
        "owner: 業務部\nreview: {required: true, count: 2, reviewers: [甲, 乙]}\n"
        "optional: null\nlabels: [A, 'B: C']\n"
        f"{code_line}---\n\n正文。\n"
    )
    source.write_text(text, encoding="utf-8")
    report = migrate_workspace(舊知識庫)
    assert "Extended" in report.moved
    result = (舊知識庫 / ".bgraph" / "nodes" / "Extended.md").read_text(encoding="utf-8")
    data = yaml.safe_load(result.split("---", 2)[1])
    expected = {
        "title": "  Extended  ", "type": "rule", "status": "inferred",
        "updated": "2026-08-14", "aliases": [" 折扣 ", 12],
        "owner": "業務部", "review": {"required": True, "count": 2, "reviewers": ["甲", "乙"]},
        "optional": None, "labels": ["A", "B: C"],
    }
    if expected_code is not None:
        expected["code"] = expected_code
    else:
        assert "code" not in data
    assert data == expected
    assert source.read_text(encoding="utf-8") == text


def test_md_目錄跳過後仍遷移後續頁面(舊知識庫):
    malformed = 舊知識庫 / ".wiki" / "pages" / "A-directory.md"
    malformed.mkdir()
    report = migrate_workspace(舊知識庫)
    assert report.skipped == ("A-directory",)
    assert report.moved == ("Discount Calculation",)
    assert malformed.is_dir()
    assert not (舊知識庫 / ".bgraph" / "nodes" / malformed.name).exists()


@pytest.mark.parametrize(
    ("error", "stage"),
    [(PermissionError, "source"), (FileNotFoundError, "source"),
     (FileNotFoundError, "target")],
)
def test_單頁路徑錯誤跳過後仍遷移後續頁面(舊知識庫, monkeypatch, error, stage):
    source = 舊知識庫 / ".wiki" / "pages" / "A-error.md"
    content = b"---\ntitle: A\nstatus: inferred\nupdated: 2026-08-14\n---\nbody\n"
    source.write_bytes(content)
    target = 舊知識庫 / ".bgraph" / "nodes" / source.name
    original_open = Path.open
    original_link = os.link

    def failing_open(path, *args, **kwargs):
        if path == source and stage == "source":
            raise error("單頁路徑錯誤")
        return original_open(path, *args, **kwargs)

    def failing_link(staged, destination, *args, **kwargs):
        if destination == target and stage == "target":
            raise error("單頁路徑錯誤")
        return original_link(staged, destination, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "open", failing_open)
        scoped.setattr(os, "link", failing_link)
        report = migrate_workspace(舊知識庫)

    assert report.skipped == ("A-error",)
    assert report.moved == ("Discount Calculation",)
    assert not target.exists()
    assert source.read_bytes() == content


@pytest.mark.parametrize("stage", ["write", "close"])
@pytest.mark.parametrize("concurrent_target", [False, True])
def test_寫入或關檔失敗不留下半成品且保留競爭目標(舊知識庫, monkeypatch, stage, concurrent_target):
    source = 舊知識庫 / ".wiki" / "pages" / "Discount Calculation.md"
    original = source.read_bytes()
    target = 舊知識庫 / ".bgraph" / "nodes" / source.name
    original_open = Path.open
    injected = False

    def fail():
        if concurrent_target:
            with original_open(target, "wb") as stream:
                stream.write(b"user content")
        raise OSError("寫入或關檔失敗")

    class FailingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def write(self, content):
            if stage == "write":
                self.stream.write(content[:5])
                self.stream.flush()
                fail()
            return self.stream.write(content)

        def __exit__(self, *args):
            self.stream.__exit__(*args)
            if stage == "close":
                fail()

    def failing_open(path, mode="r", *args, **kwargs):
        nonlocal injected
        stream = original_open(path, mode, *args, **kwargs)
        if mode == "xb" and ".bgraph" in path.parts and not injected:
            injected = True
            return FailingWriter(stream)
        return stream

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "open", failing_open)
        report = migrate_workspace(舊知識庫)
    assert report.moved == ()
    assert report.skipped == ("Discount Calculation",)
    assert source.read_bytes() == original
    if concurrent_target:
        assert target.read_bytes() == b"user content"
        assert list(target.parent.iterdir()) == [target]
    else:
        assert list(target.parent.iterdir()) == []
        rerun = migrate_workspace(舊知識庫)
        assert rerun.moved == ("Discount Calculation",)
        assert rerun.skipped == ()
        assert parse_node(target.read_text(encoding="utf-8")).meta is not None


@pytest.mark.parametrize("failing_name", ["Discount Calculation.md", "bgraph.toml"])
def test_不支援硬連結時清楚回報遷移錯誤且可重跑(舊知識庫, monkeypatch, failing_name):
    original_link = os.link

    def unsupported_link(source, target, *args, **kwargs):
        if target.name == failing_name:
            raise OSError("檔案系統不支援硬連結")
        return original_link(source, target, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "link", unsupported_link)
        with pytest.raises(GraphError, match="硬連結") as error:
            migrate_workspace(舊知識庫)
    assert "檔案系統" in str(error.value)
    assert "重跑" in str(error.value)
    nodes = 舊知識庫 / ".bgraph" / "nodes"
    if failing_name.endswith(".md"):
        assert list(nodes.iterdir()) == []
    else:
        assert [path.name for path in nodes.iterdir()] == ["Discount Calculation.md"]
    assert not (舊知識庫 / ".bgraph" / "bgraph.toml").exists()
    rerun = migrate_workspace(舊知識庫)
    assert rerun.moved == (("Discount Calculation",) if failing_name.endswith(".md") else ())
    assert rerun.config_moved is True


def test_硬連結錯誤的_cli_只向_stderr_顯示原因並回傳非零(舊知識庫, monkeypatch, capsys):
    from business_graph_mcp.__main__ import main

    def unsupported_link(*args, **kwargs):
        raise OSError("檔案系統不支援硬連結")

    monkeypatch.setattr(os, "link", unsupported_link)
    monkeypatch.setattr("sys.argv", ["business_graph_mcp", "migrate", str(舊知識庫)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "遷移失敗" in captured.err
    assert "硬連結" in captured.err
    assert "重跑" in captured.err
    assert "已遷移" not in captured.err
    assert "Traceback" not in captured.err


def test_硬連結權限遭拒明確回報遷移錯誤(舊知識庫, monkeypatch):
    def denied_link(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr(os, "link", denied_link)
    with pytest.raises(GraphMigrationError, match="權限") as error:
        migrate_workspace(舊知識庫)
    assert "重跑" in str(error.value)


def test_發布成功後暫存清理失敗不改變遷移結果(舊知識庫, monkeypatch):
    original_cleanup = TemporaryDirectory.cleanup

    def failing_cleanup(directory):
        original_cleanup(directory)
        raise OSError("暫存清理失敗")

    monkeypatch.setattr(TemporaryDirectory, "cleanup", failing_cleanup)
    report = migrate_workspace(舊知識庫)
    assert report.moved == ("Discount Calculation",)
    assert report.skipped == ()
    assert report.config_moved is True
    node = 舊知識庫 / ".bgraph" / "nodes" / "Discount Calculation.md"
    assert parse_node(node.read_text(encoding="utf-8")).meta is not None
    assert (舊知識庫 / ".bgraph" / "bgraph.toml").read_bytes() == (
        舊知識庫 / ".wiki" / "wiki.toml"
    ).read_bytes()
