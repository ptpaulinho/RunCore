from enum import Enum


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class OptimizationResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class BlockType(str, Enum):
    SYSTEM = "system"
    USER = "user"
    TOOL_RESULT = "tool_result"
    ASSISTANT = "assistant"
    MEMORY = "memory"
    DYNAMIC = "dynamic"


class ToolCategory(str, Enum):
    SEARCH = "search"
    READ = "read"
    WRITE = "write"
    COMPUTE = "compute"
    EXTERNAL = "external"


class LoopPolicy(str, Enum):
    ABORT = "abort"
    WARN = "warn"
    SKIP_DUPLICATE = "skip_duplicate"
    CONTINUE = "continue"
