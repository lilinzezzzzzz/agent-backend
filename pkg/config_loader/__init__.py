"""
配置文件加载工具包

支持的配置文件格式：
- JSON (.json)
- YAML (.yaml, .yml)
- TOML (.toml)
- INI (.ini, .cfg)
- ENV (.env)
"""

import configparser
import json
import tomllib  # Python 3.11+
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


class ConfigLoader:
    """配置文件加载器"""

    @staticmethod
    def load(file_path: str | Path, encoding: str = "utf-8") -> dict[str, Any]:
        """
        自动根据文件扩展名加载配置文件

        Args:
            file_path: 配置文件路径
            encoding: 文件编码，默认 utf-8

        Returns:
            配置字典

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 不支持的文件格式
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        suffix = path.suffix.lower()

        if suffix == ".json":
            return ConfigLoader.load_json(path, encoding)
        elif suffix in {".yaml", ".yml"}:
            return ConfigLoader.load_yaml(path, encoding)
        elif suffix == ".toml":
            return ConfigLoader.load_toml(path, encoding)
        elif suffix in {".ini", ".cfg"}:
            return ConfigLoader.load_ini(path, encoding)
        elif suffix == ".env":
            return ConfigLoader.load_env(path, encoding)
        else:
            raise ValueError(f"Unsupported configuration file format: {suffix}")

    @staticmethod
    def load_json(file_path: str | Path, encoding: str = "utf-8") -> dict[str, Any]:
        """加载 JSON 配置文件"""
        path = Path(file_path)
        content = path.read_text(encoding=encoding)
        return json.loads(content)

    @staticmethod
    def load_yaml(file_path: str | Path, encoding: str = "utf-8") -> dict[str, Any]:
        """加载 YAML 配置文件"""
        if yaml is None:
            raise ImportError("PyYAML not found. Install it with: uv add pyyaml")

        path = Path(file_path)
        content = path.read_text(encoding=encoding)
        data = yaml.safe_load(content)
        return data if isinstance(data, dict) else {}

    @staticmethod
    def load_toml(file_path: str | Path, encoding: str = "utf-8") -> dict[str, Any]:
        """加载 TOML 配置文件"""
        path = Path(file_path)
        content = path.read_bytes()
        return tomllib.loads(content.decode(encoding))

    @staticmethod
    def load_ini(file_path: str | Path, encoding: str = "utf-8") -> dict[str, Any]:
        """
        加载 INI 配置文件

        返回格式: {"section": {"key": "value"}}
        """
        path = Path(file_path)
        content = path.read_text(encoding=encoding)

        parser = configparser.ConfigParser()
        parser.read_string(content)

        result: dict[str, Any] = {}
        for section in parser.sections():
            result[section] = dict(parser.items(section))

        return result

    @staticmethod
    def load_env(file_path: str | Path, encoding: str = "utf-8") -> dict[str, Any]:
        """
        加载 .env 配置文件

        格式: KEY=VALUE
        支持注释 (#) 和空行
        """
        path = Path(file_path)
        content = path.read_text(encoding=encoding)

        result: dict[str, Any] = {}

        for line in content.splitlines():
            line = line.strip()

            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue

            # 解析 KEY=VALUE
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()

                # 移除引号
                if value and value[0] in {'"', "'"} and value[-1] == value[0]:
                    value = value[1:-1]

                result[key] = value

        return result


class ConfigMerger:
    """配置合并工具"""

    @staticmethod
    def merge(*configs: dict[str, Any], deep: bool = True) -> dict[str, Any]:
        """
        合并多个配置字典

        Args:
            *configs: 要合并的配置字典（后面的会覆盖前面的）
            deep: 是否深度合并嵌套字典

        Returns:
            合并后的配置字典
        """
        if not configs:
            return {}

        result = configs[0].copy()

        for config in configs[1:]:
            if deep:
                result = ConfigMerger._deep_merge(result, config)
            else:
                result.update(config)

        return result

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """深度合并两个字典"""
        result = base.copy()

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigMerger._deep_merge(result[key], value)
            else:
                result[key] = value

        return result


# =========================================================
# 便捷函数
# =========================================================


def load_config(file_path: str | Path, encoding: str = "utf-8") -> dict[str, Any]:
    """
    加载配置文件（便捷函数）

    Args:
        file_path: 配置文件路径
        encoding: 文件编码

    Returns:
        配置字典
    """
    return ConfigLoader.load(file_path, encoding)


def merge_configs(*configs: dict[str, Any], deep: bool = True) -> dict[str, Any]:
    """
    合并多个配置字典（便捷函数）

    Args:
        *configs: 要合并的配置字典
        deep: 是否深度合并

    Returns:
        合并后的配置字典
    """
    return ConfigMerger.merge(*configs, deep=deep)
