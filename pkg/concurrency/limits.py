import multiprocessing

CPU = max(1, multiprocessing.cpu_count())
GLOBAL_MAX_DEFAULT = min(max(32, 4 * CPU), 256)
THREAD_MAX_DEFAULT = min(max(16, (2 * GLOBAL_MAX_DEFAULT) // 3), 128)
PROCESS_MAX_DEFAULT = max(1, min(CPU, 8))

DEFAULT_TIMEOUT = 180
ANYIO_TASK_MANAGER_MAX_QUEUE = 10_000
