## Kernel Architecture

**Date:** October 30, 2025  
**Status:** Implemented

---

## Overview

uxok Framework has been transformed from a feature-rich framework into a **pure kernel architecture** in the spirit of the MIT exokernel. The core provides only essential primitives, and all other features are implemented as capability-providing plugins.

uxok takes its name and its instincts from the MIT exokernel: a minimal core that provides **mechanism, not policy**, with management pushed out into replaceable plugins, and resources acquired through **secure bindings** that authorize once and are cheap to use. It deliberately does **not** implement hardware-grade protection between mutually distrustful principals — plugins share a process and a trust domain. The borrowed ideas are the binding discipline, visible revocation, and downloaded policy; the omission is the protection boundary.

This document describes the kernel architecture, capability system, and guidelines for what belongs in core vs plugins.

---

## Core Primitives (The Kernel)

The uxok kernel provides **only** these essential primitives:

### 1. Event Bus
**Purpose:** Inter-plugin communication via publish-subscribe pattern.

**Why in core:** Fundamental communication primitive needed by all plugins.

```python
# Any plugin can emit events with auto-prefix
await self.emit("data_processed", {"count": 42})

# Any plugin can subscribe to events using core
await self.core.events.subscribe("system.*", self.handle_system_events)
```

### 2. Hook System
**Purpose:** Extension points for framework lifecycle and plugin behavior.

**Why in core:** Fundamental extension primitive for plugin coordination.

```python
# Register hook during initialization
@hook("data.process", priority=10)  # Global hook name
async def process_data(self, data: dict) -> dict:
    return {"processed": True, **data}
```

### 3. Plugin Registry
**Purpose:** Plugin registration, lookup, and dependency management.

**Why in core:** Fundamental plugin management primitive.

```python
# Core manages plugin lifecycle
await core.register_plugin(my_plugin)
plugin = await core.registry.get(plugin_id)
await core.unregister_plugin(plugin_id)
```

### 4. Capability System
**Purpose:** Kernel-style capability declarations and lookups for plugin dependencies.

**Why in core:** Fundamental dependency primitive for plugin composition.

```python
# Plugins declare capabilities
class StreamingPlugin(Plugin):
    def __init__(self):
        super().__init__(provides={"streaming"})

# Other plugins require capabilities
class LLMPlugin(Plugin):
    def __init__(self):
        super().__init__(requires={"streaming"})

    async def generate(self):
        streaming = await self.core.get_capability("streaming")
        stream = await streaming.create_stream("output")
```

### 5. Plugin
**Purpose:** Base class providing convenient access to core primitives.

**Why in core:** Developer experience layer over core primitives.

---

## Capability System Details

The capability system is the key to uxok's kernel architecture. It works like Linux kernel modules:

### How It Works

1. **Capability Providers** declare what they provide:
```python
class StreamingPlugin(Plugin):
    def __init__(self):
        super().__init__(
            provides={"streaming"}  # Declares capability
        )
```

2. **Capability Consumers** declare what they require:
```python
class LLMPlugin(Plugin):
    def __init__(self):
        super().__init__(
            requires={"streaming"}  # Declares dependency
        )
```

3. **Core validates** capabilities during plugin registration:
```python
await core.register_plugin(streaming_plugin)  # Register provider first
await core.register_plugin(llm_plugin)         # Validates streaming exists
```

4. **Plugins access** capabilities dynamically:
```python
streaming = await self.get_capability("streaming")
stream = await streaming.create_stream("output")
```

### Capability Naming Conventions

- Use lowercase, underscore-separated names
- Use specific names: `streaming`, `database`, `vector_search`
- Avoid generic names: `utility`, `helper`, `manager`

### Multiple Providers

Capability collision behavior is configured via `CoreConfig.capability_collision`:

- `"error_on_conflict"`: Multiple providers cause registration failure (default for strict environments)
- `"first_wins"`: First provider registered is kept, subsequent providers are ignored
- `"last_wins_with_warning"`: Last provider registered wins, with warning logged (default - allows hot-swapping)

When multiple providers are allowed, selection is controlled by `CoreConfig.capability_selection`:

- `"first_registered"`: Returns the first provider registered
- `"last_registered"`: Returns the most recently registered provider (default — consistent with last_wins_with_warning)

```python
# Configure for hot-swapping with last-wins selection
core = Core(
    capability_collision="last_wins_with_warning",
    capability_selection="last_registered"
)

# First provider
await core.register_plugin(sqlite_plugin)  # provides "database"

# Second provider (wins due to last_registered selection)
await core.register_plugin(postgres_plugin)  # provides "database"
# Warning logged, postgres becomes the selected provider

db = await core.get_capability("database")  # Returns postgres_plugin
```

---

## What Belongs in Core vs Plugins

### ✅ Core (Minimal Kernel)

Features that belong in core:
- Event Bus (fundamental IPC)
- Hook System (fundamental extension points)
- Plugin Registry (fundamental plugin management)
- Capability System (fundamental dependencies)
- Plugin (developer experience layer)
- Core lifecycle (start/stop/state)

**Test:** Would removing this break the plugin system itself?

### ❌ Plugins (Everything Else)

Features that belong as plugins:
- **Streaming** - Real-time data flow
- **Metrics** - Performance tracking
- **Tracing** - Distributed tracing
- **Database** - Data persistence
- **Vector Search** - AI embeddings
- **LLM** - Language model integration
- **Web Server** - HTTP endpoints
- **Scheduler** - Task scheduling
- **Cache** - Data caching

**Test:** Can this be implemented using only core primitives?

### Decision Framework

Ask these questions:
1. **Is it a primitive?** (communication, extension, management) → Core
2. **Can it be built with primitives?** → Plugin
3. **Is it optional for some users?** → Plugin
4. **Does it add external dependencies?** → Plugin
5. **Can we imagine someone NOT using it?** → Plugin

---

## Creating Capability-Providing Plugins

### Example: SupervisorPlugin (committed reference: `plugins/supervisor/`)

```python
"""SupervisorPlugin - restart-on-failure policy on kernel failure signals."""

from uxok import Plugin, event

class SupervisorPlugin(Plugin):
    def __init__(self):
        super().__init__(
            name="supervisor",
            provides={"supervision"},  # Declare capability
        )
        self._watches = {}

    # Consumes the kernel's standardized failure signals
    @event("core.plugin_error")
    async def _on_plugin_error(self, ev):
        ...

    # Public API for capability consumers
    def watch(self, plugin_name, factory, *, max_failures=3, window_s=60.0):
        ...
```

### Best Practices

1. **Single Capability per Plugin** (usually)
   - Each plugin should provide one well-defined capability
   - Exception: Related capabilities can be bundled (e.g., "database" might include "migrations")

2. **Clear Public API**
   - Document all public methods capability consumers will use
   - Keep API stable for backward compatibility

3. **Resource Cleanup**
   - Implement `on_stop()` to cleanup resources
   - Track background tasks in `self._background_tasks`

4. **Emit Lifecycle Events**
   - Emit "ready" event when initialized
   - Emit "shutdown" event when stopping
   - Emit error events for failures

---

## Hook Naming Conventions

With the unified hook API, hooks are registered in a global namespace. To prevent naming conflicts and ensure clarity, follow these naming conventions:

### Recommended Pattern: `domain.action`

```python
# Global hooks for common operations
@hook("data.validate")      # Data validation
@hook("data.process")       # Data processing
@hook("data.transform")     # Data transformation
@hook("auth.login")         # Authentication
@hook("auth.logout")        # Logout
@hook("config.load")        # Configuration loading
@hook("config.save")        # Configuration saving
@hook("cache.get")          # Cache retrieval
@hook("cache.set")          # Cache storage
```

### Plugin-Specific Hooks: `pluginname.action`

When a hook is specific to your plugin, prefix with the plugin name:

```python
# Plugin-specific hooks (when needed)
@hook("streaming.create_stream")    # Streaming plugin specific
@hook("database.query")             # Database plugin specific
@hook("scheduler.add_job")          # Scheduler plugin specific
```

### Hook Execution Pattern

```python
class MyPlugin(Plugin):
    async def process_data(self, data):
        # Use bound method for efficient hook execution
        validation_result = await self.hook("data.validate", data)
        processed_data = await self.hook("data.process", data)

        # Pass to plugin-specific hook
        await self.hook("myplugin.custom_processing", processed_data)
```

### Benefits of Global Namespace

1. **No hidden prefixing** - Hook names are explicit and clear
2. **Cross-plugin communication** - Any plugin can call any global hook
3. **Discoverability** - Hooks are easily found and documented
4. **Consistency** - Same naming pattern across all plugins

### Migration from Auto-Prefixed Hooks

```python
# BEFORE (auto-prefixed)
@hook("validate")  # Became "myplugin.validate"

# AFTER (explicit global naming)
@hook("data.validate")  # Clear global name
```

## Using Capabilities in Plugins

### Example: Plugin Requiring Streaming

```python
class LLMPlugin(Plugin):
    def __init__(self):
        super().__init__(
            requires={"streaming"}  # Declare requirement
        )
    
    async def generate_response(self, prompt: str):
        # Get capability
        streaming = await self.get_capability("streaming")
        
        # Use capability API
        stream = await streaming.create_stream("response")
        
        for token in self._generate_tokens(prompt):
            await stream.send(token)
        
        await stream.send("", is_final=True)
```

### Best Practices

1. **Check Capability Availability**
   ```python
   try:
       streaming = await self.core.get_capability("streaming")
       # Use streaming capability
   except CapabilityError:
       # Fallback or error
   ```

   Or configure `capability_missing="return_none"` in CoreConfig:
   ```python
   streaming = await self.core.get_capability("streaming")  # Returns None if unavailable
   if streaming:
       # Use streaming capability
   else:
       # Fallback or error
   ```

2. **Handle Missing Capabilities Gracefully**
   ```python
   try:
       streaming = await self.get_capability("streaming")
   except KeyError:
       logger.warning("Streaming not available, falling back to batch mode")
       # Fallback logic
   ```

3. **Document Capability Requirements**
   - In plugin docstring, list required capabilities
   - In README, explain what capabilities are needed

---

## Plugin Distribution Models

### Layer 1: File-Based (Current)

Plugins as Python files in `plugins/` directory:

```bash
plugins/
├── __init__.py
├── streaming/
│   ├── __init__.py
│   └── streaming_plugin.py
└── database/
    ├── __init__.py
    └── database_plugin.py
```

**Usage:**
```python
from plugins.streaming import StreamingPlugin

streaming = StreamingPlugin()
await core.register_plugin(streaming)
```

**Benefits:**
- Simple for development
- Easy for agents to generate code
- No packaging required

### Layer 2: Pip Packages (Future)

Plugins as installable packages:

```bash
pip install uxok-plugin-streaming
pip install uxok-plugin-database
```

**Usage:**
```python
from orion_plugin_streaming import StreamingPlugin

streaming = StreamingPlugin()
await core.register_plugin(streaming)
```

**Benefits:**
- Version management
- Dependency resolution
- Distribution via PyPI

### Layer 3: Registry System (Future)

Core auto-discovers plugins:

```python
# In config
plugins:
  - streaming
  - database
  - vector_search

# Core auto-loads from registry
core = Core(config)
await core.start()  # Loads plugins automatically
```

**Benefits:**
- Zero-code plugin loading
- Declarative configuration
- Plugin marketplace potential

---

## Migration Guide: Moving Features to Plugins

If you have code in core that should be a plugin:

### Step 1: Create Plugin Structure

```bash
mkdir -p plugins/feature_name
touch plugins/feature_name/__init__.py
touch plugins/feature_name/feature_plugin.py
```

### Step 2: Implement Plugin

```python
class FeaturePlugin(Plugin):
    def __init__(self):
        super().__init__(
            provides={"feature_name"}
        )
        # Move core implementation here
```

### Step 3: Remove from Core

- Remove imports from `core.py`
- Remove initialization in `Core.__init__()`
- Remove getter methods like `get_feature_manager()`

### Step 4: Update Plugin (if needed)

- Remove convenience methods that wrap the feature
- Add capability access examples in docstrings

### Step 5: Update Tests

- Create `tests/test_feature_plugin.py`
- Update core tests to not expect feature

### Step 6: Update Documentation

- Add plugin to `plugins/` directory README
- Update examples to show capability usage
- Add migration notes for existing users

---

## Examples

### Complete Examples

1. **SupervisorPlugin** - `plugins/supervisor/supervisor_plugin.py`
   - Provides "supervision" capability
   - Consumes kernel failure signals; restarts plugins with state carried
     via the get_state()/restore_state() contract

### Minimal Example

```python
# Provider
class CachePlugin(Plugin):
    def __init__(self):
        super().__init__(provides={"cache"})
        self._cache = {}
    
    def get(self, key: str): return self._cache.get(key)
    def set(self, key: str, val): self._cache[key] = val

# Consumer
class DataPlugin(Plugin):
    def __init__(self):
        super().__init__(requires={"cache"})
    
    async def process(self, key: str):
        cache = await self.get_capability("cache")
        if cached := cache.get(key):
            return cached
        result = await self._compute(key)
        cache.set(key, result)
        return result

# Usage
await core.register_plugin(CachePlugin())
await core.register_plugin(DataPlugin())
```

---

## Benefits of Kernel Architecture

### 1. Modularity
- Core stays minimal and stable
- Features can evolve independently
- Easy to swap implementations

### 2. Flexibility
- Users install only what they need
- Agents can generate capability providers
- Plugin ecosystem can emerge organically

### 3. Testability
- Core tests are focused and fast
- Plugin tests are isolated
- Mock capabilities for testing

### 4. Self-Coding
- Agents can write new capability providers
- Agents can hot-load new capabilities
- Framework evolves through plugin code generation

### 5. Clarity
- Clear boundary between core and features
- Explicit dependencies via capabilities
- No hidden coupling

---

## Comparison to Linux Kernel

| Linux Kernel | uxok Framework |
|--------------|-----------------|
| Process scheduler | Event Bus + Hook System |
| System calls | Core API (register_plugin, get_capability) |
| Module loading | Plugin registration |
| Module dependencies | Capability system |
| `/proc`, `/sys` | Metrics + Config (as plugins) |
| Device drivers | Capability providers |
| File systems | Storage plugins |

---

## Future Enhancements

### 1. Capability Versioning
```python
super().__init__(
    core,
    provides={"streaming": "2.0"},  # Version
    requires={"database": ">=1.5"}  # Version constraint
)
```

### 2. Capability Metadata
```python
core.get_capability_info("streaming")
# Returns: {
#   "provider": "streaming_plugin",
#   "version": "2.0",
#   "methods": ["create_stream", "get_stream"],
#   "doc": "Provides real-time streaming..."
# }
```

### 3. Capability Discovery
```python
available = core.list_capabilities()
# Returns: ["streaming", "database", "cache"]

providers = core.find_providers("database")
# Returns: [sqlite_plugin, postgres_plugin]
```

### 4. Dynamic Loading
```python
# Load from any source - file, network, database, generated code
code = Path("plugins/feature.py").read_text()
await core.load_plugin(code)

# From a URL
code = requests.get("https://plugins.uxok.ai/streaming.py").text
await core.load_plugin(code)

# Zero-downtime hot reload - just call load_plugin() again
code = Path("plugins/feature.py").read_text()  # Updated code
await core.load_plugin(code)  # Automatically swaps if already exists
```

---

## Summary

uxok Framework's kernel architecture:

✅ **Minimal core** - Only essential primitives  
✅ **Capability system** - Kernel-style dependencies  
✅ **Plugin ecosystem** - All features as plugins  
✅ **Self-coding ready** - Agents can generate capability providers  
✅ **Linux-inspired** - Proven architecture pattern  

This architecture enables maximum flexibility while maintaining a stable, testable core.

