"""首次启动器必须只创建空实例配置，不能修改已有配置。"""

import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "shared_chat_bootstrap", Path(__file__).resolve().parents[1] / "deploy" / "start_shared_chat.py"
)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def test_initial_config_never_overwrites_existing(tmp_path):
    config = tmp_path / "vault" / "config.yaml"
    assert bootstrap.initialize(config) is True
    assert "enabled: false" in config.read_text()
    config.write_text("embedding:\n  enabled: true\ncustom: 保留原有配置\n")
    previous = config.read_bytes()
    assert bootstrap.initialize(config) is False
    assert config.read_bytes() == previous
