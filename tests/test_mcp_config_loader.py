"""Unit tests for MCP configuration loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.mcp.config import MCPServerConfig
from core.mcp.config_loader import load_mcp_configs


@pytest.fixture
def temp_config_file(tmp_path: Path) -> Path:
    """Create a temporary MCP config file."""
    config_data = {
        "servers": [
            {
                "name": "test-server",
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "tests.mcp.stub_server"],
                "env": {"TEST_VAR": "test_value"},
                "enabled": True,
                "timeout": 15.0,
                "retry_on_failure": False,
                "max_retries": 2
            },
            {
                "name": "disabled-server",
                "transport": "stdio",
                "command": "echo",
                "args": [],
                "enabled": False
            }
        ]
    }
    
    config_file = tmp_path / "mcp_servers.json"
    with open(config_file, "w") as f:
        json.dump(config_data, f)
    
    return config_file


def test_load_mcp_configs_from_file(temp_config_file: Path) -> None:
    """Test loading MCP configs from file."""
    configs = load_mcp_configs(temp_config_file)
    
    assert len(configs) == 2
    
    # Check first server
    assert configs[0].name == "test-server"
    assert configs[0].transport == "stdio"
    assert configs[0].command == "python"
    assert configs[0].args == ["-m", "tests.mcp.stub_server"]
    assert configs[0].env == {"TEST_VAR": "test_value"}
    assert configs[0].enabled is True
    assert configs[0].timeout == 15.0
    assert configs[0].retry_on_failure is False
    assert configs[0].max_retries == 2
    
    # Check second server
    assert configs[1].name == "disabled-server"
    assert configs[1].enabled is False


def test_load_mcp_configs_returns_empty_list_if_file_not_found() -> None:
    """Test that missing config file returns empty list."""
    configs = load_mcp_configs("nonexistent.json")
    assert configs == []


def test_load_mcp_configs_raises_on_invalid_json(tmp_path: Path) -> None:
    """Test that invalid JSON raises ValueError."""
    config_file = tmp_path / "invalid.json"
    config_file.write_text("{ invalid json }")
    
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_mcp_configs(config_file)


def test_load_mcp_configs_raises_on_missing_servers_key(tmp_path: Path) -> None:
    """Test that missing 'servers' key raises ValueError."""
    config_file = tmp_path / "no_servers.json"
    config_file.write_text('{"other_key": []}')
    
    with pytest.raises(ValueError, match="must contain 'servers' key"):
        load_mcp_configs(config_file)


def test_load_mcp_configs_raises_on_missing_required_fields(tmp_path: Path) -> None:
    """Test that missing required fields raises ValueError."""
    config_data = {
        "servers": [
            {
                "transport": "stdio",
                # Missing 'name' field
            }
        ]
    }
    
    config_file = tmp_path / "missing_name.json"
    with open(config_file, "w") as f:
        json.dump(config_data, f)
    
    with pytest.raises(ValueError, match="Missing required field"):
        load_mcp_configs(config_file)


def test_load_mcp_configs_uses_defaults_for_optional_fields(tmp_path: Path) -> None:
    """Test that optional fields use default values."""
    config_data = {
        "servers": [
            {
                "name": "minimal-server",
                "transport": "stdio",
                "command": "echo"
                # All other fields are optional
            }
        ]
    }
    
    config_file = tmp_path / "minimal.json"
    with open(config_file, "w") as f:
        json.dump(config_data, f)
    
    configs = load_mcp_configs(config_file)
    
    assert len(configs) == 1
    config = configs[0]
    
    # Check defaults
    assert config.enabled is True
    assert config.args == []
    assert config.env == {}
    assert config.timeout == 30.0
    assert config.retry_on_failure is True
    assert config.max_retries == 3
