import os
import re
import time as time_module
from datetime import UTC, time, timedelta

from pkg.logger import LoggerHandler


class TestLogRotationRetention:

    def test_retention_cleans_old_files(self, tmp_path):
        """
        测试 Retention: 验证旧文件被删除
        注意：loguru 的 retention 在轮转时触发，不是写日志时
        """
        # 1. 准备路径和旧文件
        base_log_dir = tmp_path / "logs"
        base_log_dir.mkdir(parents=True, exist_ok=True)
        log_dir = base_log_dir
        print(f"log_dir: {log_dir}")

        old_file = log_dir / "app.2023-01-01_00-00-00_000000.log"
        old_file.write_text("Old logs...")

        # 修改为 40 天前
        days_ago_40 = time_module.time() - (40 * 24 * 3600)
        os.utime(old_file, (days_ago_40, days_ago_40))

        new_file = log_dir / "app.2023-12-01_00-00-00_000000.log"
        new_file.write_text("New logs...")

        unrelated_file = log_dir / "audit.log"
        unrelated_file.write_text("Audit logs...")
        os.utime(unrelated_file, (days_ago_40, days_ago_40))

        # 2. 实例化 Manager (直接注入配置)
        # 使用 1 byte 轮转触发 retention 检查
        manager = LoggerHandler(
            base_log_dir=base_log_dir,
            timezone="UTC",
            rotation=1,  # <--- 1 byte 轮转，达到后立即轮转
            retention=timedelta(days=30),  # <--- 30天保留
            enqueue=False  # 测试环境通常不需要多进程队列
        )

        # 3. 启动
        logger = manager.setup(write_to_console=False)

        # 4. 触发多次日志写入以触发轮转
        logger.info("Trigger action 1")
        logger.info("Trigger action 2")  # 超过 1 byte，触发轮转
        time_module.sleep(0.2)  # 等待 retention 执行

        # 5. 验证
        # 注意：retention 可能在后台线程执行，需要等待
        for _ in range(10):
            if not old_file.exists():
                break
            time_module.sleep(0.1)

        assert not old_file.exists(), f"旧文件未被删除: {old_file}"
        assert new_file.exists(), f"新文件不应被删除: {new_file}"
        assert unrelated_file.exists(), "普通日志 retention 不应清理其他日志流"

    def test_rotation_creates_new_files(self, tmp_path):
        """
        测试 Rotation: 验证文件切割
        """
        # 确保基础目录存在
        base_log_dir = tmp_path / "logs"
        base_log_dir.mkdir(parents=True, exist_ok=True)

        # 1. 实例化 (注入 1秒 轮转策略)
        manager = LoggerHandler(
            base_log_dir=base_log_dir,
            timezone="UTC",
            rotation=timedelta(seconds=1),  # <--- 注入 1秒 轮转
            enqueue=False
        )

        logger = manager.setup(write_to_console=False)
        log_dir = base_log_dir
        print(f"log_dir: {log_dir}")

        # 2. 第一条日志
        logger.info("Log entry 1")
        files_step_1 = list(log_dir.glob("*.log*"))
        assert files_step_1 == [log_dir / "app.log"]

        # 3. 等待超时
        time_module.sleep(1.2)

        # 4. 第二条日志
        logger.info("Log entry 2")

        files_step_2 = list(log_dir.glob("*.log*"))
        assert len(files_step_2) > len(files_step_1)
        assert (log_dir / "app.log").exists()

        rotated_files = [path for path in files_step_2 if path.name != "app.log"]
        assert len(rotated_files) == 1
        assert re.fullmatch(
            r"app\.\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d{6}\.log",
            rotated_files[0].name,
        )

    def test_init_sets_correct_timezone(self):
        """
        测试构造函数的时区逻辑
        """
        from zoneinfo import ZoneInfo

        # 1. 默认情况: timezone=UTC, rotation 无时区 -> 自动使用 UTC
        mgr1 = LoggerHandler(timezone="UTC", rotation=time(0, 0, 0))
        assert mgr1.rotation.tzinfo.key == "UTC"

        # 2. 非UTC时区: timezone=Asia/Shanghai, rotation 无时区 -> 自动使用 Asia/Shanghai
        mgr2 = LoggerHandler(timezone="Asia/Shanghai", rotation=time(0, 0, 0))
        assert mgr2.rotation.tzinfo == ZoneInfo("Asia/Shanghai")

        # 3. 混合: timezone=UTC, rotation 已带 UTC 时区 -> 正常
        custom_utc = time(12, 0, 0, tzinfo=UTC)
        mgr3 = LoggerHandler(timezone="UTC", rotation=custom_utc)
        assert mgr3.rotation == custom_utc

        # 4. 验证 timezone 属性 (使用 timedelta rotation 避免时区检查)
        mgr4 = LoggerHandler(timezone="Asia/Shanghai", rotation=timedelta(days=1))
        assert mgr4.timezone == ZoneInfo("Asia/Shanghai")

        # 5. 验证字符串和 ZoneInfo 对象都可以
        mgr5 = LoggerHandler(timezone=ZoneInfo("America/New_York"), rotation=timedelta(days=1))
        assert mgr5.timezone == ZoneInfo("America/New_York")

    def test_rotation_timezone_must_match(self):
        """
        测试 rotation 时区必须与 timezone 一致
        """
        from zoneinfo import ZoneInfo

        import pytest

        # rotation 时区与 timezone 不一致应该抛出 ValueError
        with pytest.raises(ValueError, match="rotation timezone .* must match timezone"):
            LoggerHandler(timezone="UTC", rotation=time(0, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

        # timezone=Asia/Shanghai, rotation 带 UTC 时区 -> 抛出异常
        with pytest.raises(ValueError, match="rotation timezone .* must match timezone"):
            LoggerHandler(timezone="Asia/Shanghai", rotation=time(0, 0, 0, tzinfo=UTC))

        # rotation 无时区，自动使用 timezone -> 正常
        mgr = LoggerHandler(timezone="Asia/Shanghai", rotation=time(8, 0, 0))
        assert mgr.rotation.tzinfo == ZoneInfo("Asia/Shanghai")
        assert mgr.rotation.hour == 8

    def test_datetime_utc_support(self):
        """
        测试 datetime.UTC 作为 timezone 参数
        """
        from datetime import UTC
        from zoneinfo import ZoneInfo

        # datetime.UTC 应该被正确转换为 ZoneInfo("UTC")
        mgr = LoggerHandler(timezone=UTC, rotation=timedelta(days=1))
        assert mgr.timezone == ZoneInfo("UTC")

        # 与 rotation 搭配使用
        mgr2 = LoggerHandler(timezone=UTC, rotation=time(0, 0, 0))
        assert mgr2.rotation.tzinfo.key == "UTC"

    def test_default_logs_written_to_base_dir(self, tmp_path):
        """
        测试默认日志始终写入 base_log_dir。
        """
        base_log_dir = tmp_path / "logs"
        base_log_dir.mkdir(parents=True, exist_ok=True)

        manager = LoggerHandler(
            base_log_dir=base_log_dir,
            rotation=timedelta(days=1),
            enqueue=False,
        )
        logger = manager.setup(write_to_console=False)
        logger.info("test")

        assert not (base_log_dir / "default").exists()
        assert (base_log_dir / "app.log").exists()
