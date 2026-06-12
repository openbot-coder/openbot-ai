# GrepTool Ripgrep Migration — Design

**Issue**: #10
**Branch**: `feature/grep-ripgrep`
**Status**: Phase 1 (Requirements) — Approved via issue

## Goal

将 `openbot/agent/tools/search.py` 中的 `GrepTool` 底层替换为 [ripgrep](https://github.com/BurntSushi/ripgrep)（`rg`），解决：

1. 性能差（纯 Python `re` + `os.walk` 在大仓库里慢）
2. 编码支持差（GBK/UTF-16/Shift-JIS 误判为二进制）
3. 二进制检测粗糙（NUL 字节 + 32 字节统计）
4. 文件大小被截断（> 2MB 跳过）
5. type 映射表有限（17 种 vs ripgrep 50+）
6. glob 引擎简陋（`fnmatch` 不支持 `!` 取反）
7. 无多行匹配

## Non-Goal

- 不改变 `GrepTool` 的对外接口（参数和返回格式完全不变）
- 不改变 `FindFilesTool`
- 不引入新的依赖（ripgrep 作为外部命令，缺失时 fallback）

## Architecture

```
GrepTool.execute()
    │
    ├─→ shutil.which("rg") ?
    │     │
    │     ├─ Yes → _RipgrepBackend.search()  → subprocess rg
    │     │           │
    │     │           └─→ parse JSON / line output → _Match records
    │     │
    │     └─ No  → _PythonGrepBackend.search() → current logic
    │
    └─→ format_matches(_Match records, output_mode) → str
```

## Backend Interface

```python
@dataclass
class _Match:
    path: str              # workspace-relative
    line_no: int           # 1-based; 0 for files_with_matches/count
    text: str              # raw line text (decoded); "" for non-content
    count: int             # for output_mode="count" only
    mtime: float           # file mtime (for sort by modified)

class _GrepBackend(Protocol):
    name: str
    def search(
        self,
        target: Path,
        pattern: str,
        *,
        case_insensitive: bool,
        fixed_strings: bool,
        output_mode: str,        # "content" | "files_with_matches" | "count"
        glob: str | None,
        type_: str | None,
        context_before: int,
        context_after: int,
        head_limit: int | None,
        offset: int,
    ) -> list[_Match]: ...
```

## Backend Implementations

### `_RipgrepBackend`

- 调 `rg --json` 获取结构化输出（最稳）
- 命令行参数映射：
  - `case_insensitive` → `-i`
  - `fixed_strings` → `-F`
  - `glob` → `-g <pattern>`
  - `type_` → `-t <type>`
  - `output_mode=files_with_matches` → `-l` （仍走 `--json`）
  - `output_mode=count` → `--count`
  - `context_before/after` → `-B/-A`
  - 隐藏文件跳过 → `--no-ignore --hidden=false`（保留 Python 行为）
  - 尊重原有 `_IGNORE_DIRS` → 通过 `-g '!dir/**'` 排除
- 超时：30s（防止 rg 卡死）
- 错误处理：exit 1 = no matches（正常）；exit 2 = error（返回 Error 字符串）

### `_PythonGrepBackend`

- 现有 `GrepTool.execute()` 内部逻辑抽到这
- 行为完全不变（用于 fallback 和测试）

## File Layout

```
openbot/agent/tools/search.py    # 改：拆分 GrepTool + 新增两个 backend
tests/tools/test_search_tools.py # 改：保留现有测试（验 Python backend）
tests/tools/test_ripgrep_backend.py # 新：验 ripgrep backend
```

## Compatibility

- `GrepTool.execute()` 参数签名：完全不变
- 返回文本格式：完全不变
- `_IGNORE_DIRS` 行为：保持（避免误扫描 `node_modules` 等）
- 性能：典型 monorepo 100k 文件从 30s+ 降到 < 2s

## Risk & Mitigation

| Risk | Mitigation |
|------|------------|
| Windows 上无 ripgrep | 自动 fallback 到 Python；CI matrix 加 `with-rg` job |
| rg 默认尊重 .gitignore | 显式 `--no-ignore`，再用 `-g '!node_modules/**'` 等保持现状 |
| rg 对超大单行报错 | 加 `-M 2000`（max columns） |
| subprocess 启动开销 | 用 `--json` 一次性消费，避免多次 invoke |
| 输出解析错误 | `json.JSONDecodeError` → fallback 到 Python |

## Phase Plan

1. **P1**: 抽出 `_GrepBackend` Protocol + `_Match` dataclass
2. **P2**: 把现有 `GrepTool` 逻辑搬到 `_PythonGrepBackend`（行为不变）
3. **P3**: 实现 `_RipgrepBackend`（subprocess + JSON 解析）
4. **P4**: `GrepTool.execute()` 改成分发：rg 可用 → ripgrep，否则 → python
5. **P5**: 写新测试（test_ripgrep_backend.py）+ 跑全套 search_tools 测试
6. **P6**: Dockerfile 加 `apt-get install -y ripgrep`（可选）
7. **P7**: 更新文档（描述、changelog）
