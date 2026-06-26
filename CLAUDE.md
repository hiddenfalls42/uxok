# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**IMPORTANT**: Always think and respond critically. Your job is to tell the truth, ESPECIALLY when the user is incorrect. There is no need to sugarcoat. The user DOES NOT CARE about being correct, and actively dislikes being praised. NEVER say "you're absolutely right" 
## Project Overview

**uxok** is an experimental, hot-loading plugin microkernel for Python, built on a kernel architecture in the spirit of the MIT exokernel (mechanism not policy; secure bindings; downloaded policy — but no protection boundary between plugins, which share a process). The core provides only essential primitives (event bus, hooks, plugin registry, capability system); everything else is a plugin.

## Constitutional API System

**⚠️ IMPORTANT**: uxok operates under a constitutional API system where `docs/manifests/API.md` is the **single source of truth** for all API changes.

- **API.md is constitutional**: It defines the complete public API that must be implemented
- **Versioning policy**: Pre-1.0, breaking changes are allowed — each must update
  CHANGELOG.md and API.md in the same commit. At 1.0 the constitution locks and all
  changes must be backward compatible
- **Discussion required**: API changes must be discussed and added to the constitutional document
- **Implementation guide**: The current codebase is being refactored to match the constitutional API
- **Reference implementation**: Use API.md as the guide for all development work

## Essential Commands

### Testing
```bash
# Run all tests (fast, no coverage)
pytest

# Coverage run with gate (91.5% branch coverage floor — raise as it improves, never lower).
# COVERAGE_RUN=1 disables Hypothesis deadlines (instrumentation slows the tick system
# past the 200ms default and sends failing examples into endless shrink loops);
# COVERAGE_CORE=sysmon uses the fast Python 3.12 monitoring backend.
COVERAGE_RUN=1 COVERAGE_CORE=sysmon pytest tests/ --cov=src/uxok --cov-branch --cov-fail-under=91.5

# Run specific test categories
pytest tests/test_core_lifecycle.py -v          # Unit tests for core lifecycle
pytest tests/integration/ -v                    # Integration tests (incl. hot reload)
pytest tests/properties/ -v                     # Property-based tests (Hypothesis)
pytest tests/test_hooks_through_core.py -v      # Hooks through a running core

# Run single test
pytest tests/test_core_lifecycle.py -v -k test_name

# Performance tests (deselected by default)
pytest -m performance
```

### Linting and Formatting
```bash
# Ruff: ZERO violations enforced over the whole enforced scope.
# The rule set is curated (see pyproject.toml): kernel + plugins/ held to the
# full set, tests relax annotation/style pedantry. Never add per-change noqa
# without a reason comment.
ruff check src tests plugins

# Run Ruff formatter
ruff format src tests plugins

# Type checking
mypy src
```

### Development Setup
```bash
# Install in development mode with all dependencies
pip install -e .[dev]
```

## Kernel Architecture

### Kernel Boundary

**The kernel is `src/uxok/` and nothing else.** The reference plugins under
`plugins/` (e.g. `supervisor/`) are built *with* the kernel, not part of it. The
dependency arrow points one way: plugins import the kernel, never the reverse.
This is enforced by `tests/test_imports.py::TestKernelBoundary`.

### Core Primitives (The Kernel)
The uxok core provides **ONLY** these essential primitives:

1. **Event Bus** (`src/uxok/events/_bus.py`) - Inter-plugin communication via publish-subscribe
2. **Hook System** (`src/uxok/hooks/_system.py`) - Extension points with priority-based execution
3. **Plugin Registry** (`src/uxok/registry/impl.py`) - Plugin registration, lookup, dependency management
4. **Capability System** (`src/uxok/core/_capability_system.py`) - Kernel-style dependency declarations
5. **Plugin** (`src/uxok/plugin/_base.py`) - Developer experience abstraction layer

**Everything else is a plugin.** 

See `docs/explanation/architecture-overview.md` for the full architecture overview.

## Plugin Abstraction

**Key Features:**
- Auto-name detection from class names (override with `name=` parameter)
- Convenience methods: `emit()`, `config()`, `hook()` (v2.0 API)
- Declarative metadata: `provides`, `requires`, `hooks_consumed`, `events_published`
- Built-in event emission: `emit()`
- Capability access: via `self.core.get_capability()`
- Lifecycle hooks: `on_start()`, `on_stop()`

## Framework Philosophy

**uxok is a framework, not a product.** We provide clean building blocks, not opinionated solutions.

### Core Principles

1. **Framework Over Product** - Provide tools and patterns for users to build upon
2. **Convention Over Configuration** - Simple patterns over complex APIs (e.g., `emit(at_tick=N)` instead of a `DeferredEvent` class)
3. **Simplicity Over Features** - Complexity creep is the enemy; remove complexity before adding features
4. **Protocol-First Design** - Immutable protocols that never change, simple implementations
5. **Predictable Behavior** - Same input → same output; no hidden magic
6. **Kernel Architecture** - Only essential primitives in core; everything else is a plugin

### Critical Philosophy Points

- **Unified Configuration**: All settings in `CoreConfig`, never scattered across multiple config classes
- **Protocol-Based Coupling**: Always depend on protocols, never on implementations (e.g., `EventBus` not `EventBusImpl`)
- **Configuration Evolution**: Add new fields with defaults; validate in `CoreConfig.__post_init__()`
- **User Choice**: Framework provides tools; users make decisions about strategies and approaches

### What We Avoid

- ❌ Complex class hierarchies and opinionated solutions
- ❌ Breaking changes to core interfaces
- ❌ Scattered configuration across multiple config classes
- ❌ Tight coupling to implementations
- ❌ Forced behaviors on users

### What We Embrace

- ✅ Simple conventions and configuration
- ✅ Predictable, documented behavior
- ✅ User choice and flexibility
- ✅ Protocol-first design
- ✅ Opt-in features via configuration

**Remember: uxok's strength is its simplicity. Protect it fiercely.**

See `docs/explanation/framework-philosophy.md` for the full philosophy and examples.

## Code Structure

```
src/uxok/          THE KERNEL (nothing else is)
├── core/                Core orchestrator, capability system, state manager
├── events/              Event bus + subscription manager
├── hooks/               Hook system + cache
├── plugin/              Plugin base class, decorators, config fields
├── protocols/           Protocol definitions (the contracts)
├── registry/            Plugin registry, proxy/collection introspection
├── timing/              Tick clock, gate, scheduler
└── utils/               Helpers, task manager, async primitives

plugins/                 Committed reference plugins (e.g. supervisor/)
```

## Important Implementation Notes

### State Management
- Core uses `CoreState` with constitutional graph: INITIALIZED → RUNNING → STOPPING → STOPPED; STOPPING → FAILED (teardown itself failed); STOPPED/FAILED → INITIALIZED (restart with a fresh plugin graph).
- STOPPING is the drain phase: `core.stop()` is a full teardown that unregisters every plugin, leaving an empty reusable core. Plugin instances are one-shot; state continuity is explicit via `get_state()`/`restore_state()`.
- Plugin-level failures are signals (`core.plugin_error`, `core.hook_error`), not core states; supervision policy lives in plugins (see `plugins/supervisor/`).
- State transitions are validated; `core.state.changed` hook fires on every transition.
- Name mangling (`__state`) for true privacy in state management

### Configuration (CRITICAL)
- **Single source of truth**: `CoreConfig` in `protocols.py` - NEVER create separate config classes
- All configuration in one unified dataclass, never scattered across multiple configs
- Protocol-based coupling: Always depend on `EventBus`, `CoreConfig` protocols, NEVER on `EventBusImpl` or implementation-specific configs
- Validation centralized in `CoreConfig.__post_init__()` - one place for all validation
- New config fields must have sensible defaults to maintain backward compatibility

**Before adding configuration:**
- Does this integrate with `CoreConfig`? (Must be YES)
- Does this use protocols, not implementations? (Must be YES)
- Is this backward compatible with defaults? (Must be YES)
- Is validation centralized in `CoreConfig.__post_init__()`? (Must be YES)

### Plugin Lifecycle
1. Plugin instantiation (user code)
2. `core.register_plugin(plugin)` - registers and validates capabilities
3. Plugin initialization via `start()` - processes decorators, calls `on_start()`
4. Plugin runs (event handlers, hooks execute)
5. `core.unregister_plugin(plugin_id)` - calls `on_stop()`, cleanup

### Async Patterns
- All core operations are async
- **Lock-free invariant**: registry and capability-system state mutations are
  synchronous critical sections — never add an `await` inside one. Under
  cooperative asyncio that makes them atomic without locks (decision #12);
  an await inside a mutation requires reintroducing a lock around it
- **Lifecycle lock**: the multi-step lifecycle operations (register/load/unregister/swap)
  span awaits and are serialized by a single reentrant `_ReentrantLock` in `Core`.
  Per-plugin `_active_operations` guards are retained for id-level protection.
- Use `Plugin.create_background_task()` for background tasks — tracked in the
  plugin's `_task_manager` and cancelled automatically on stop/reload
- **Event dispatch is concurrent fire-and-forget**: `publish()` dispatches each subscriber
  as an independent tracked task and returns immediately. Ordering is causal (a handler's
  nested emit/hook completes before it continues), not global. The serial `TickGate` is
  deleted — there is no dispatch queue, no dispatch timeout, and no serialization across
  independent publishers.

### Testing Requirements
- 91.5% branch coverage floor, enforced by the coverage command above
  (measured baseline 2026-06-12: 91.80–91.93% across runs); raise the floor as coverage improves,
  never lower it
- Use `@pytest.mark.asyncio` for async tests
- **Integration over unit** - Test how components work together; focus on user workflows
- **Property-based testing** 
- Simple test cases focused on essential functionality
- Test error conditions and edge cases
- Mock external dependencies, not internal components


## Code Quality and Inspection

### Using the Code Auditor Agent

When inspecting code for adherence to specifications, patterns, or architectural principles, **always use the `code-auditor` agent** instead of direct inspection.

**When to Use:**
- After implementing a feature or component
- Before merging code changes
- When verifying adherence to a skill or specification
- When auditing code against architectural principles
- During code reviews to check compliance

**How to Use:**

```python
# Always use the Task tool with code-auditor subagent
Task(
    subagent_type="code-auditor",
    description="Brief description of audit",
    prompt="Detailed audit instructions..."
)
```

**CRITICAL: Always Pass Relevant Skills**

Before running the auditor, **check if a skill exists** for that type of code. Available skills include:

- `developer-documentation-blueprint-SKILL` - For auditing developer docs
- `public-document-blueprint-SKILL` - For auditing public-facing docs
- Other domain-specific skills as they are added

**Pattern:**

1. Identify what type of code you're auditing (capabilities, docs, flows, etc.)
2. Check if a relevant skill exists in `.claude/skills/`
3. Pass the skill path to the auditor in the prompt
4. Auditor checks code against that skill's specification
5. Review the PASS/FAIL report and fix issues

**Example:**

```python
Task(
    subagent_type="code-auditor",
    description="Audit a kernel component",
    prompt="""Audit src/uxok/registry/ against the constitutional API in docs/manifests/API.md.

Check for:
- Architectural violations
- Missing/extra public interfaces vs API.md
- Naming convention violations
- Specification compliance
"""
)
```

**What You Get:**
- PASS/FAIL status for each criterion
- Line numbers for violations
- Prioritized recommendations (CRITICAL → LOW)
- Impact analysis
- Actionable fix recommendations

**Best Practices:**
1. Run auditor **after** implementing, not before
2. Always audit against the **relevant skill** if one exists
3. Fix CRITICAL and HIGH issues before proceeding
4. Re-audit after fixes to verify compliance

## Decision Framework for Changes

Before making changes, ask:

1. **Is this framework or product?** (Framework = building blocks ✅, Product = complete solution ❌)
2. **Does this add complexity?** (Simple ✅, Complexity creep ❌)
3. **Is this opt-in?** (User choice ✅, Forces behavior ❌)
4. **Does this break existing code?** (Backward compatible ✅, Breaking change ❌)
5. **Is there a simpler way?** (Convention ✅, Complex API ❌)
6. **Does this belong in core or plugin?** (Can it be built with primitives? → Plugin)
7. **Has the code-auditor verified this?** (Run auditor with relevant skill ✅, Skip audit ❌)


## Documentation Workflow

uxok uses an iterative audit-writer loop for documentation development:

**Process:**
1. **One Thing at a Time**: Technical writer documents ONE file or section
2. **Auditor Verification**: Documentation auditor verifies against blueprint requirements
3. **Fix Issues**: If verification fails, writer fixes specific issues
4. **Re-verify**: Auditor re-verifies until approved
5. **Next Item**: Move to next file/section only after current one is approved

**Key Constraints:**
- Writer must work on ONE file/documentation item at a time - never batch multiple files
- Each file requires atomic notes: overview, reference, explanation (plus how-to as needed)
- Auditor checks: structural conformance, accuracy against source code, wikilink validation, writing style
- Verification reports stored in: `docs/agent_data/compliance-auditor/verifications/`
  (documentation-audit handoffs live separately under `docs/agent_data/doc-auditor/handoffs/`)
- Loop continues until all documentation passes verification

**Tools:**
- `technical-writer` agent: Creates/updates documentation one item at a time
- `documentation-auditor` agent: Verifies documentation against blueprint skill

## Key Documentation

- `docs/manifests/API.md` - **CONSTITUTIONAL API REFERENCE** (single source of truth - 35+ public APIs)
- `docs/explanation/architecture-overview.md` - Kernel architecture guide
- `docs/explanation/framework-philosophy.md` - Design philosophy and principles
- `docs/` - Public documentation (tutorials, how-to, explanation, reference)



