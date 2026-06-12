# GrepTool Ripgrep — Progress

## Phase 1: 需求分析 ✅

- [x] Design doc written
- [x] Issue #10 reviewed
- [x] Branch `feature/grep-ripgrep` created

## Phase 2: 任务拆解

| # | Task | Status |
|---|------|--------|
| T1 | 抽 `_GrepBackend` Protocol + `_Match` dataclass | ⏳ |
| T2 | 现有逻辑搬到 `_PythonGrepBackend` | ⏳ |
| T3 | 实现 `_RipgrepBackend` | ⏳ |
| T4 | `GrepTool.execute()` 改成分发 | ⏳ |
| T5 | 写 test_ripgrep_backend.py | ⏳ |
| T6 | 跑全套 search_tools 测试 | ⏳ |
| T7 | Dockerfile 更新（可选） | ⏳ |
| T8 | 提交 commit + push | ⏳ |

## Phase 3: 代码执行

- 状态：未开始
- 工作目录：openbot/agent/tools/search.py, tests/tools/

## Phase 4: 测试验证

- 状态：未开始
- 验证清单：
  - [ ] 现有 test_search_tools.py 全部通过
  - [ ] test_ripgrep_backend.py 覆盖正例/反例/边界值
  - [ ] 覆盖率 100%
  - [ ] ruff check 通过

## Phase 5: 归档

- 状态：未开始
