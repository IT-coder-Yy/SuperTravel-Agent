from pathlib import Path

from config_loader import ConfigLoader


def test_server_reload_defaults_to_disabled_for_persistent_provider_runtime(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("model:\n  api_key: test\n", encoding="utf-8")
    monkeypatch.delenv("SAGE_RELOAD", raising=False)

    config = ConfigLoader(str(config_path)).load_config()

    assert config.server.reload is False


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


def test_tavily_mcp_is_auto_enabled_only_when_key_is_configured(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n  api_key: test\nserver:\n  reload: false\nmcp:\n  servers: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")

    config = ConfigLoader(str(config_path)).load_config()

    assert config.mcp is not None
    server = config.mcp.servers["tavily-mcp"]
    assert server.command == "npx"
    assert server.args == ["-y", "tavily-mcp@0.2.21"]
    assert server.env["TAVILY_API_KEY"] == "${TAVILY_API_KEY}"


def test_amap_mcp_is_auto_enabled_only_when_key_is_configured(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n  api_key: test\nserver:\n  reload: false\nmcp:\n  servers: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AMAP_MAPS_API_KEY", "amap-test")

    config = ConfigLoader(str(config_path)).load_config()

    assert config.mcp is not None
    server = config.mcp.servers["amap-maps"]
    assert server.command == "npx"
    assert server.args == ["-y", "@amap/amap-maps-mcp-server@0.0.8"]
    assert server.env == {"AMAP_MAPS_API_KEY": "${AMAP_MAPS_API_KEY}"}


def test_server_reload_can_be_disabled_from_environment(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server:\n  reload: true\n", encoding="utf-8")
    monkeypatch.setenv("SAGE_DOTENV_OVERRIDE", "0")
    monkeypatch.setenv("SAGE_RELOAD", "false")

    config = ConfigLoader(str(config_path)).load_config()

    assert config.server.reload is False
