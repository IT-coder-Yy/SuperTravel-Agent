from pathlib import Path

from config_loader import ConfigLoader


def test_unsplash_mcp_is_available_when_local_config_does_not_declare_it(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n  api_key: test\nserver:\n  reload: false\nmcp:\n  servers: {}\n",
        encoding="utf-8",
    )

    config = ConfigLoader(str(config_path)).load_config()

    assert config.mcp is not None
    server = config.mcp.servers["unsplash-mcp"]
    assert server.command
    assert server.args and Path(server.args[0]).as_posix().endswith("mcp_servers/unsplash-mcp/main.py")
    assert server.description == "搜索并提供带版权署名的旅行图片。"
