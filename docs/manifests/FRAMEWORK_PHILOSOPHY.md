# uxok Framework Philosophy: Clean & Simple

## 🎯 Core Principle

**uxok is a framework, not a product.** We provide clean building blocks, not opinionated solutions.

## 🧹 Design Philosophy

### 1. Framework Over Product
- **Framework**: Provides tools and patterns for users to build upon
- **Product**: Provides complete, opinionated solutions
- **uxok**: Framework-first, always

### 2. Convention Over Configuration
- Use simple conventions instead of complex APIs
- Example: `emit(at_tick=N)` instead of a `DeferredEvent` class
- Defaults should work for 80% of use cases

### 3. Simplicity Over Features
- **Complexity creep** is the enemy
- Each feature should solve a real problem, not a hypothetical one
- Remove complexity before adding features

### 4. Clean Architecture
- **Immutable protocols** that never change
- **Simple implementations** that are easy to understand
- **Clear separation** between concerns

## 🚫 What We Avoid

### Complexity Explosion
```python
# ❌ DON'T: Complex event subclasses for scheduling
@dataclass(frozen=True)
class DeferredEvent(Event):
    delay_ms: int = 0
    target_tick: int | None = None
    drop_policy: DropPolicy = DropPolicy.LOG_SAMPLE

# ✅ DO: Simple convention — tick-based deferral, no subclass
await self.emit("user.request", {...}, at_tick=self.core.tick + 500)
```

### Opinionated Solutions
```python
# ❌ DON'T: Force specific chaos handling
class ChaosAwareEventBus:
    # 498 lines of complex logic
    # Circuit breakers, dead letter queues, load levels...
    # Assumes we know how users should handle chaos

# ✅ DO: Provide configurable strategies
class EventBusImpl:
    # Simple strategy selection
    # Users choose their approach: timeout, drop, queue
```

### Breaking Changes
```python
# ❌ DON'T: Change core interfaces
async def publish(self, event: Event | PriorityEvent) -> bool:
    # Forces all users to update their code

# ✅ DO: Extend without breaking
async def publish(self, event: Event) -> bool:
    # Existing code continues to work
    # New features are opt-in via config
```

## ✅ What We Embrace

### Simple Configuration
```python
# All settings live in CoreConfig and arrive as Core(**kwargs):
core = Core(
    tick_rate=1000,
    # Overload policy is a simple strategy selection
    tick_queue_max_size=10_000,
    tick_queue_overflow="block",   # "block", "drop", "error"
    tick_catchup="skip",           # "skip", "burst"
)
```

### Predictable Behavior
- **Same input → same output**
- **Clear error messages**
- **Documented trade-offs**
- **No hidden magic**

### User Choice
```python
# Users choose their approach to overload:
core = Core(tick_queue_overflow="block")   # wait for the tick to drain
core = Core(tick_queue_overflow="drop")    # shed load, count drops
core = Core(tick_queue_overflow="error")   # fail fast

# Framework provides the tools, user makes the decisions
```

## 🎨 Code Patterns

### 1. Protocol-First Design
```python
class EventBus(Protocol):
    async def publish(self, event: Event) -> None: ...
    async def subscribe(self, event_name: EventName, callback: Callable[[Event], None]) -> None: ...
```

### 2. Simple Implementations
```python
class EventBusImpl:
    def __init__(self, config: EventPoolConfig | None = None) -> None:
        self._config = config or EventPoolConfig()
        # Simple, focused implementation
```

### 3. Configuration-Driven Features
```python
# Features are enabled by configuration, not by default
if self._config.backpressure_strategy == "drop":
    return await self._publish_with_drop_strategy(event, subscribers, priority)
```

### 4. Convention-Based Extensions
```python
# Scheduling is a convention, not a subclass — deferral is tick-based
await self.emit("work.retry", payload, at_tick=self.core.tick + 500)
await self.emit("work.scheduled", payload, at_tick=target_tick)
```

## 🧪 Testing Philosophy

### 1. Test the Core
- Focus on essential functionality
- Test error conditions and edge cases
- Maintain high coverage for core features

### 2. Simple Test Cases
```python
async def test_publish_subscribe_basic(self, event_bus):
    """Test basic publish/subscribe functionality."""
    callback = AsyncMock()
    event = Event(name="test.event", data="test_data", timestamp=time.time())
    
    await event_bus.subscribe("test.event", callback)
    await event_bus.publish(event)
    
    callback.assert_called_once_with(event)
```

### 3. Integration Over Unit
- Test how components work together
- Focus on user workflows
- Mock external dependencies

## 📚 Decision Framework

When adding a new feature, ask:

1. **Is this framework or product?**
   - Framework: Provides building blocks ✅
   - Product: Provides complete solution ❌

2. **Does this add complexity?**
   - Simple addition ✅
   - Complexity creep ❌

3. **Is this opt-in?**
   - Users can choose to use it ✅
   - Forces behavior on all users ❌

4. **Does this break existing code?**
   - Backward compatible ✅
   - Breaking change ❌

5. **Is there a simpler way?**
   - Convention over configuration ✅
   - Complex API ❌

## 🎯 Success Metrics

### Code Quality
- [ ] Low cyclomatic complexity
- [ ] Few dependencies
- [ ] Clear documentation
- [ ] Consistent patterns

### User Experience
- [ ] Easy to get started
- [ ] Predictable behavior
- [ ] Good error messages
- [ ] Flexible configuration

### Framework Health
- [ ] All tests passing
- [ ] High coverage on core features
- [ ] No breaking changes
- [ ] Simple architecture

## 🔗 Integration Principles

### Configuration Integration
1. **Unified Configuration** - All settings in `CoreConfig`, not scattered
2. **Protocol-Based Coupling** - Depend on protocols, not implementations
3. **Backward Compatibility** - New config fields don't break existing code
4. **Validation Centralization** - Validate in one place (`CoreConfig.__post_init__`)

### Clean Architecture Patterns
```python
# ✅ DO: Use protocols for dependencies
def __init__(self, event_bus: EventBus, config: CoreConfig | None = None):
    # Clean protocol-based coupling

# ❌ DON'T: Tight coupling to implementations  
def __init__(self, event_bus: EventBusImpl, config: EventPoolConfig | None = None):
    # Creates tight coupling and scattered config
```

### Configuration Evolution
- **Start simple** - Core config with basic fields
- **Extend carefully** - Add new fields with validation
- **Maintain defaults** - Existing code continues working
- **Document trade-offs** - Be explicit about new features

## 🚀 Future Guidelines

### Adding Features
1. **Start with protocol** - define interface
2. **Simple implementation** - basic functionality first
3. **Configuration-driven** - make features opt-in
4. **Convention-based** - use simple patterns
5. **Test thoroughly** - cover core use cases

### Handling Complexity
1. **Identify problem** - what are we really solving?
2. **Question assumptions** - is this complexity necessary?
3. **Simplify** - what's the minimal solution?
4. **Document trade-offs** - be explicit about choices

### Maintaining Simplicity
1. **Regular cleanup** - remove unused/complex code
2. **Code reviews** - check for complexity creep
3. **User feedback** - listen to real use cases
4. **Framework first** - always ask "is this framework-like?"

### Integration Checklist
Before adding new components:
- [ ] Does this integrate with `CoreConfig`?
- [ ] Does this use protocols, not implementations?
- [ ] Is this backward compatible?
- [ ] Is configuration validation centralized?
- [ ] Does this follow convention over configuration?

### Configuration Integration Rules

#### 🚫 NEVER: Scattered Configuration
```python
# ❌ DON'T: Separate config classes
@dataclass
class EventPoolConfig:
    max_concurrent_tasks: int = 500
    backpressure_strategy: str = "timeout"

@dataclass 
class StreamConfig:
    buffer_size: int = 1000
    timeout: float = 1.0

# Creates configuration complexity and integration headaches
```

#### ✅ ALWAYS: Unified CoreConfig
```python
# ✅ DO: All configuration in one place
@dataclass
class CoreConfig:
    # Event system
    max_concurrent_tasks: int = 500
    backpressure_strategy: str = "timeout"
    
    # Streaming system
    stream_buffer_size: int = 1000
    stream_timeout: float = 1.0
    
    # Plugin system
    write_buffer_size: int = 10_000
    
    def __post_init__(self) -> None:
        # Centralized validation for all config
        if self.max_concurrent_tasks <= 0:
            raise ValueError("max_concurrent_tasks must be positive")
        # ... validate all fields here
```

#### 🚫 NEVER: Implementation Coupling
```python
# ❌ DON'T: Tight coupling to implementations
class ConfigProviderImpl:
    def __init__(self, event_bus: EventBusImpl, config: EventPoolConfig):
        # Creates tight coupling and hard to test
        # Forces specific implementation on users
```

#### ✅ ALWAYS: Protocol-Based Coupling
```python
# ✅ DO: Depend on abstractions
class ConfigProviderImpl:
    def __init__(self, event_bus: EventBus, config: CoreConfig):
        # Clean protocol coupling
        # Easy to test, easy to extend
        # Users can provide any EventBus implementation
```

### Configuration Evolution Pattern

#### Adding New Configuration Fields
1. **Add to CoreConfig**: New field with sensible default
2. **Update Validation**: Add check in `__post_init__`
3. **Update Components**: Use `config.new_field` throughout
4. **Maintain Backward Compatibility**: Defaults ensure existing code works
5. **Document Trade-offs**: Explain why new field exists

#### Example: Adding Event Queue Size
```python
# Step 1: Add to CoreConfig
@dataclass
class CoreConfig:
    # ... existing fields ...
    event_queue_max_size: int = 1000  # NEW: with default
    
    def __post_init__(self) -> None:
        # ... existing validation ...
        # Step 2: Add validation
        if self.event_queue_max_size <= 0:
            raise ValueError("event_queue_max_size must be positive")

# Step 3: Use in components
class EventBusImpl:
    def __init__(self, config: CoreConfig | None = None) -> None:
        self._config = config or CoreConfig()
        self._queue = asyncio.Queue(maxsize=self._config.event_queue_max_size)
        # Existing code continues working with default value
```

### Future-Proofing Integration

#### Configuration Migration Strategy
When configuration needs to change:
1. **Add new fields** with defaults (backward compatible)
2. **Mark old fields** as deprecated if needed
3. **Provide migration** helpers or documentation
4. **Remove old fields** only in major version bumps

#### Testing Integration Changes
```python
# Test both old and new configuration patterns
def test_backward_compatibility():
    # Old way should still work
    old_config = CoreConfig()  # Uses all defaults
    bus = EventBusImpl(old_config)
    
    # New way should work too
    new_config = CoreConfig(event_queue_max_size=2000)
    bus2 = EventBusImpl(new_config)
    
    # Both should be valid
    assert bus._config.max_concurrent_tasks == 500
    assert bus2._config.event_queue_max_size == 2000
```

---

## 🔧 Kernel Architecture

**uxok is a kernel, not a monolith.** Inspired by the Linux kernel, uxok provides only essential primitives in core, with all other features implemented as capability-providing plugins.

### Core as Kernel

The uxok core provides **only** these primitives:
- **Event Bus** - Fundamental IPC
- **Hook System** - Fundamental extension points
- **Plugin Registry** - Fundamental plugin management
- **Capability System** - Fundamental dependency management
- **Plugin** - Developer experience layer

Everything else is a plugin.

### Capability System

Plugins declare what they provide and require:

```python
# Provider
class StreamingPlugin(Plugin):
    def __init__(self):
        super().__init__(provides={"streaming"})

# Consumer
class LLMPlugin(Plugin):
    def __init__(self):
        super().__init__(requires={"streaming"})
    
    async def generate(self):
        streaming = await self.get_capability("streaming")
        stream = await streaming.create_stream("output")
```

### Benefits

1. **Minimal Core** - Core stays stable and focused
2. **Maximum Flexibility** - Users install only what they need
3. **Plugin Ecosystem** - Features evolve independently
4. **Self-Coding Ready** - Agents can generate capability providers
5. **Clear Boundaries** - No hidden coupling between features

### What Lives Outside Core

- **Supervision** - restart policy (see the reference `plugins/supervisor/`); core ships only failure signals
- **Storage** - implemented as a capability-providing plugin
- Future: Metrics, Tracing, Database, etc.

For complete details, see **[KERNEL_ARCHITECTURE.md](KERNEL_ARCHITECTURE.md)**.

---

## 🤖 Meta-Framework Vision

**uxok enables agents to build their own tools.** While this document focuses on framework philosophy for human developers, uxok's hot loading and Plugin capabilities also enable self-coding AI systems.

For the complete vision of how uxok supports self-coding agents that dynamically generate, optimize, and evolve their own capabilities, see **[AGENT_VISION.md](AGENT_VISION.md)**.

---

**Remember: uxok's strength is its simplicity. Protect it fiercely.**
