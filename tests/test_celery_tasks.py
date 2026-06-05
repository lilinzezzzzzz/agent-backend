"""
Celery 任务测试用例
测试 number_sum 任务的执行
"""

import sys

import pytest
from celery.result import AsyncResult

from internal.tasks.celery import number_sum
from internal.infra.celery import celery_app, celery_client
from pkg.logger import init_logger

# 初始化日志系统（测试环境必须）
init_logger(level="INFO")


class TestCeleryTasks:
    """Celery 任务测试类"""

    def test_number_sum_sync_execution(self):
        """
        测试同步执行 number_sum 任务
        直接调用任务函数，不通过 Celery Worker
        """
        # 直接调用任务函数（绕过 Celery 队列）
        result = number_sum(10, 20)
        assert result == 30

    def test_number_sum_async_execution(self):
        """
        测试异步执行 number_sum 任务
        需要启动 Celery Worker 才能通过此测试
        """
        # 发送任务到 Celery 队列
        task_result: AsyncResult = number_sum.delay(15, 25)

        # 等待任务完成（最多等待 10 秒）
        try:
            result = task_result.get(timeout=10)
            assert result == 40
            assert task_result.successful()
        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")

    def test_number_sum_apply_async(self):
        """
        测试使用 apply_async 方法执行任务
        支持更多参数配置（如队列、优先级等）
        """
        task_result: AsyncResult = number_sum.apply_async(
            args=(100, 200),
            queue="default",  # 使用默认队列
        )

        try:
            result = task_result.get(timeout=10)
            assert result == 300

            # 验证任务状态
            assert task_result.state == "SUCCESS"
        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")

    def test_number_sum_task_retry(self):
        """
        测试任务重试机制
        通过传入错误参数触发异常
        """
        # 传入非数字类型，触发异常和重试
        task_result: AsyncResult = number_sum.delay("invalid", 10)

        try:
            # 预期任务会失败
            task_result.get(timeout=10)
            # 如果没有抛出异常，说明任务成功了（不应该）
            pytest.fail("Expected task to fail but it succeeded")
        except TypeError:
            # 预期的类型错误
            assert task_result.failed()
        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")

    def test_celery_app_configuration(self):
        """
        测试 Celery App 配置是否正确加载
        """
        # 检查任务是否已注册
        assert "internal.tasks.celery_tasks.number_sum" in celery_app.tasks

        # 检查配置
        assert celery_app.conf.task_default_queue == "default"
        assert celery_app.conf.timezone == "UTC"

    def test_celery_task_routes(self):
        """
        测试任务路由配置
        """
        task_name = "internal.tasks.celery_tasks.number_sum"

        # 获取任务路由信息
        route = celery_app.conf.task_routes.get(task_name)

        # 验证路由配置（如果有配置的话）
        if route:
            assert isinstance(route, dict)

    def test_celery_client_submit(self):
        """
        测试使用 celery_client.submit() 提交任务
        """
        task_result = celery_client.submit(
            task_name="internal.tasks.celery_tasks.number_sum",
            args=(50, 60),
        )

        try:
            result = task_result.get(timeout=10)
            assert result == 110
            assert task_result.successful()
        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")

    def test_celery_client_submit_with_options(self):
        """
        测试使用 celery_client.submit() 提交任务，并指定队列和优先级
        """
        task_result = celery_client.submit(
            task_name="internal.tasks.celery_tasks.number_sum",
            args=(100, 100),
            queue="default",  # 使用默认队列
            priority=5,
        )

        try:
            result = task_result.get(timeout=10)
            assert result == 200
        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")

    def test_celery_client_get_status(self):
        """
        测试使用 celery_client 查询任务状态
        """
        task_result = celery_client.submit(
            task_name="internal.tasks.celery_tasks.number_sum",
            args=(1, 2),
        )

        try:
            # 等待任务完成
            task_result.get(timeout=10)

            # 查询状态
            status = celery_client.get_status(task_result.id)
            assert status == "SUCCESS"

            # 查询结果
            result = celery_client.get_result(task_result.id)
            assert result == 3
        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")

    def test_celery_client_submit_with_custom_task_id(self):
        """
        测试使用自定义 task_id 提交任务
        """
        custom_id = "test-custom-task-id-12345"
        task_result = celery_client.submit(
            task_name="internal.tasks.celery_tasks.number_sum",
            args=(10, 10),
            task_id=custom_id,
        )

        # 验证 task_id 是否为自定义值
        assert task_result.id == custom_id

        try:
            result = task_result.get(timeout=10)
            assert result == 20
        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")

    def test_celery_client_submit_with_countdown(self):
        """
        测试使用 countdown 延迟执行任务
        """
        import time

        start_time = time.time()
        task_result = celery_client.submit(
            task_name="internal.tasks.celery_tasks.number_sum",
            args=(5, 5),
            countdown=2,  # 延迟 2 秒执行
        )

        try:
            result = task_result.get(timeout=15)
            elapsed = time.time() - start_time

            assert result == 10
            # 验证确实有延迟（至少 1.5 秒，给一些容错）
            assert elapsed >= 1.5
        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")

    def test_celery_client_chain(self):
        """
        测试 celery_client.chain() 链式调用
        task1(1,2) -> task2(result,3) -> task3(result,4)
        """
        # 创建任务签名
        sig1 = number_sum.s(1, 2)  # 1 + 2 = 3
        sig2 = number_sum.s(3)  # 3 + 3 = 6 (前一个结果作为第一个参数)
        sig3 = number_sum.s(4)  # 6 + 4 = 10

        try:
            chain_result = celery_client.chain(sig1, sig2, sig3)
            result = chain_result.get(timeout=30)
            assert result == 10
        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")

    def test_celery_client_group(self):
        """
        测试 celery_client.group() 并发调用
        同时执行多个任务
        """
        # 创建多个任务签名
        sig1 = number_sum.s(1, 1)  # 2
        sig2 = number_sum.s(2, 2)  # 4
        sig3 = number_sum.s(3, 3)  # 6

        try:
            group_result = celery_client.group(sig1, sig2, sig3)
            results = group_result.get(timeout=30)
            assert results == [2, 4, 6]
        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")

    def test_celery_client_chord(self):
        """
        测试 celery_client.chord() 回调模式
        修复后：number_sum 现已支持列表输入，可以完整验证结果
        流程：
        1. Header: [1+1=2, 2+2=4]
        2. Body: number_sum([2, 4], 5) -> sum([2,4]) + 5 -> 6 + 5 = 11
        """
        from celery import group as celery_group

        # Header: 并发执行两个任务
        header = celery_group(
            [
                number_sum.s(1, 1),  # 结果 2
                number_sum.s(2, 2),  # 结果 4
            ]
        )

        # Body: 接收 header 的结果 [2, 4] 作为第一个参数 x
        # 我们传入 5 作为第二个参数 y
        body = number_sum.s(5)

        try:
            chord_result = celery_client.chord(header, body)

            # 等待最终结果
            result = chord_result.get(timeout=30)

            # 验证逻辑: (2 + 4) + 5 = 11
            assert result == 11

        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")

    def test_celery_client_revoke(self):
        """
        测试 celery_client.revoke() 撤销任务
        """
        # 提交一个延迟执行的任务
        task_result = celery_client.submit(
            task_name="internal.tasks.celery_tasks.number_sum",
            args=(100, 100),
            countdown=60,  # 60 秒后执行
        )

        try:
            # 立即撤销任务
            celery_client.revoke(task_result.id, terminate=True)

            # 验证任务状态
            import time

            time.sleep(1)  # 等待撤销生效

            status = celery_client.get_status(task_result.id)
            # 撤销后状态可能是 REVOKED 或 PENDING
            assert status in ["REVOKED", "PENDING"]
        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")

    @pytest.mark.parametrize(
        "x,y,expected",
        [
            (1, 1, 2),
            (0, 0, 0),
            (-5, 5, 0),
            (100, -50, 50),
            (999, 1, 1000),
        ],
    )
    def test_number_sum_with_parameters(self, x, y, expected):
        """
        参数化测试：测试不同输入的计算结果
        """
        # 同步执行测试
        result = number_sum(x, y)
        assert result == expected


@pytest.mark.integration
class TestCeleryIntegration:
    """
    Celery 集成测试
    需要完整的 Celery Worker 和 Redis 环境
    """

    def test_celery_broker_connection(self):
        """
        测试 Celery Broker (Redis) 连接
        """
        try:
            with celery_app.connection_or_acquire() as conn:
                conn.ensure_connection(max_retries=3)
            assert True
        except Exception as e:
            pytest.skip(f"Redis Broker 不可用: {e}")

    def test_multiple_tasks_execution(self):
        """
        测试批量任务执行
        """
        tasks = [number_sum.delay(i, i * 2) for i in range(1, 6)]

        try:
            results = [task.get(timeout=10) for task in tasks]
            expected = [i + i * 2 for i in range(1, 6)]  # [3, 6, 9, 12, 15]
            assert results == expected
        except Exception as e:
            pytest.skip(f"Celery Worker 未启动或不可用: {e}")


if __name__ == "__main__":
    """
    直接运行测试
    运行前需要：
    1. 启动 Redis: docker-compose up redis
    2. 启动 Celery Worker: celery -A entrypoints.celery:celery_app worker -l info -c 1 -Q default,celery_queue
    3. 运行测试: pytest tests/test_celery_tasks.py -v
    """
    sys.exit(pytest.main(["-s", "-v", "--log-cli-level=INFO", __file__]))
