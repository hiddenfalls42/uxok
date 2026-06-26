#!/usr/bin/env python3
"""
API Inventory - Clear, simple inventory of all APIs in a codebase

Shows all classes, functions, methods, and attributes with clear visibility distinction.
This is a factual inventory - no interpretation of what "should" be public.

Usage:
    pip install griffe
    python api_inventory.py <path_to_package>

Example:
    python api_inventory.py ./uxok
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Set

try:
    from griffe import load, Module, Class, Function, Attribute, Kind
except ImportError:
    print("Error: griffe is not installed.", file=sys.stderr)
    print("Install it with: pip install griffe", file=sys.stderr)
    sys.exit(1)


@dataclass
class APIInventory:
    """Simple inventory of all APIs"""
    external_classes: List[str] = field(default_factory=list)
    internal_classes: List[str] = field(default_factory=list)
    external_functions: List[str] = field(default_factory=list)
    internal_functions: List[str] = field(default_factory=list)
    external_methods: List[str] = field(default_factory=list)
    internal_methods: List[str] = field(default_factory=list)
    external_attributes: List[str] = field(default_factory=list)
    internal_attributes: List[str] = field(default_factory=list)

    # Public exports (from __all__)
    public_exports: List[str] = field(default_factory=list)
    export_map: Dict[str, str] = field(default_factory=dict)  # name -> full_path

    # Special categories
    exceptions: List[str] = field(default_factory=list)
    protocols: List[str] = field(default_factory=list)
    enums: List[str] = field(default_factory=list)
    dataclasses: List[str] = field(default_factory=list)
    type_aliases: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        """Get summary counts"""
        return {
            'total_classes': len(self.external_classes) + len(self.internal_classes),
            'external_classes': len(self.external_classes),
            'internal_classes': len(self.internal_classes),
            'total_functions': len(self.external_functions) + len(self.internal_functions),
            'external_functions': len(self.external_functions),
            'internal_functions': len(self.internal_functions),
            'total_methods': len(self.external_methods) + len(self.internal_methods),
            'external_methods': len(self.external_methods),
            'internal_methods': len(self.internal_methods),
            'total_attributes': len(self.external_attributes) + len(self.internal_attributes),
            'external_attributes': len(self.external_attributes),
            'internal_attributes': len(self.internal_attributes),
            'public_exports': len(self.public_exports),
            'total_items': (
                len(self.external_classes) + len(self.internal_classes) +
                len(self.external_functions) + len(self.internal_functions) +
                len(self.external_methods) + len(self.internal_methods) +
                len(self.external_attributes) + len(self.internal_attributes)
            )
        }


class APICollector:
    """Collects API information from Python code"""

    def __init__(self):
        self.inventory = APIInventory()

    def is_public(self, name: str) -> bool:
        """Check if name is public (doesn't start with _)"""
        return not name.startswith('_')

    def is_dunder(self, name: str) -> bool:
        """Check if name is dunder (__init__, __str__, etc)"""
        return name.startswith('__') and name.endswith('__')

    def analyze_class(self, cls: Class, module_path: str = ""):
        """Analyze a class and its members"""
        full_name = f"{module_path}.{cls.name}" if module_path else cls.name
        is_public_class = self.is_public(cls.name)

        # Class categorization
        if is_public_class:
            self.inventory.external_classes.append(full_name)
        else:
            self.inventory.internal_classes.append(full_name)

        # Special category detection
        if cls.bases:
            base_names = [str(b) for b in cls.bases]
            if any('Exception' in b or 'Error' in b for b in base_names):
                self.inventory.exceptions.append(full_name)
            elif any('Protocol' in b for b in base_names):
                self.inventory.protocols.append(full_name)
            elif any('Enum' in b for b in base_names):
                self.inventory.enums.append(full_name)

        if cls.decorators:
            if any('dataclass' in str(d) for d in cls.decorators):
                self.inventory.dataclasses.append(full_name)

        # Analyze class members
        for member_name, member in cls.members.items():
            if member.inherited:
                continue

            member_is_public = self.is_public(member_name) or self.is_dunder(member_name)

            if isinstance(member, Function):
                full_method_name = f"{full_name}.{member_name}()"
                # Method visibility based on both method and class visibility
                if member_is_public and is_public_class:
                    self.inventory.external_methods.append(full_method_name)
                else:
                    self.inventory.internal_methods.append(full_method_name)

            elif isinstance(member, Attribute):
                full_attr_name = f"{full_name}.{member_name}"
                if member_is_public and is_public_class:
                    self.inventory.external_attributes.append(full_attr_name)
                else:
                    self.inventory.internal_attributes.append(full_attr_name)

            elif isinstance(member, Class):
                # Nested class
                self.analyze_class(member, full_name)

    def analyze_module(self, module: Module, parent_path: str = ""):
        """Recursively analyze a module"""
        module_path = f"{parent_path}.{module.name}" if parent_path else module.name

        # Check for __all__ exports by trying to read the source file
        try:
            if hasattr(module, 'filepath') and module.filepath:
                with open(module.filepath, 'r') as f:
                    source = f.read()
                    self.extract_all_exports(source, module_path)
        except:
            pass

        for member_name, member in module.members.items():
            if isinstance(member, Class):
                self.analyze_class(member, module_path)

            elif isinstance(member, Function):
                full_name = f"{module_path}.{member_name}()"
                if self.is_public(member_name):
                    self.inventory.external_functions.append(full_name)
                else:
                    self.inventory.internal_functions.append(full_name)

            elif isinstance(member, Attribute):
                # Type aliases - simple heuristic
                if (self.is_public(member_name) and member.annotation and
                    any(kw in str(member.annotation) for kw in ['TypeVar', 'Type[', 'Union[', 'Optional['])):
                    full_name = f"{module_path}.{member_name}"
                    self.inventory.type_aliases.append(full_name)

            elif isinstance(member, Module):
                self.analyze_module(member, module_path)

    def extract_all_exports(self, source: str, module_path: str):
        """Extract __all__ exports from source code"""
        import ast
        import re

        try:
            # Parse the AST
            tree = ast.parse(source)

            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == '__all__':
                            # Found __all__ assignment
                            if isinstance(node.value, (ast.List, ast.Tuple)):
                                for elt in node.value.elts:
                                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                        export_name = elt.value
                                        self.inventory.public_exports.append(export_name)
                                        self.inventory.export_map[export_name] = module_path
                            elif isinstance(node.value, ast.Name):
                                # __all__ = SOME_VAR (try to find the var)
                                var_name = node.value.id
                                # Simple regex to find the variable assignment
                                pattern = rf'{var_name}\s*=\s*\[(.*?)\]'
                                match = re.search(pattern, source, re.DOTALL)
                                if match:
                                    items = match.group(1).split(',')
                                    for item in items:
                                        item = item.strip().strip('"\'')
                                        if item:
                                            self.inventory.public_exports.append(item)
                                            self.inventory.export_map[item] = module_path
        except:
            # Fallback: simple regex approach
            try:
                all_match = re.search(r'__all__\s*=\s*\[(.*?)\]', source, re.DOTALL)
                if all_match:
                    content = all_match.group(1)
                    # Extract string literals
                    strings = re.findall(r'["\']([^"\']+)["\']', content)
                    for export_name in strings:
                        self.inventory.public_exports.append(export_name)
                        self.inventory.export_map[export_name] = module_path
            except:
                pass

    def collect(self, package_path: Path) -> APIInventory:
        """Collect APIs from a Python package"""
        try:
            if package_path.is_file():
                package = load(package_path)
            else:
                package = load(package_path.name, search_paths=[str(package_path.parent)])

            self.analyze_module(package)
            return self.inventory

        except Exception as e:
            print(f"Error loading package: {e}", file=sys.stderr)
            sys.exit(1)


def format_inventory(inventory: APIInventory) -> str:
    """Format inventory as clean, readable text"""
    summary = inventory.summary()

    lines = [
        "=" * 80,
        "API INVENTORY",
        "=" * 80,
        "",
        "SUMMARY",
        "-" * 40,
        f"Total Items: {summary['total_items']}",
        f"Public Exports (__all__): {summary['public_exports']}",
        "",
        "Classes: {total} ({external} external, {internal} internal)".format(
            total=summary['total_classes'],
            external=summary['external_classes'],
            internal=summary['internal_classes']
        ),
        "Functions: {total} ({external} external, {internal} internal)".format(
            total=summary['total_functions'],
            external=summary['external_functions'],
            internal=summary['internal_functions']
        ),
        "Methods: {total} ({external} external, {internal} internal)".format(
            total=summary['total_methods'],
            external=summary['external_methods'],
            internal=summary['internal_methods']
        ),
        "Attributes: {total} ({external} external, {internal} internal)".format(
            total=summary['total_attributes'],
            external=summary['external_attributes'],
            internal=summary['internal_attributes']
        ),
        ""
    ]

    # Public Exports (__all__)
    if inventory.public_exports:
        # Remove duplicates and filter out internal items
        unique_exports = {}
        for export in inventory.public_exports:
            # Skip internal items that shouldn't be exported
            if export.startswith('_') and not export in ['__all__', '__version__']:
                continue
            # Keep the first occurrence (most likely the main __init__.py)
            if export not in unique_exports:
                unique_exports[export] = inventory.export_map.get(export, "unknown")

        lines.extend([
            "=" * 80,
            "PUBLIC EXPORTS (__all__)",
            "=" * 80,
            f"Total Unique Public Exports: {len(unique_exports)}",
            ""
        ])

        # Group exports by category
        classes = []
        functions = []
        decorators = []
        protocols = []
        exceptions = []
        others = []

        for export in sorted(unique_exports.keys()):
            module_path = unique_exports[export]

            # Categorization based on naming patterns
            if export.startswith(('I',)) and not export.startswith(('ID')):
                protocols.append((export, module_path))
            elif export.endswith(('Error', 'Exception')):
                exceptions.append((export, module_path))
            elif export in ['hook', 'event', 'handle_errors', 'hook_wrapper']:
                decorators.append((export, module_path))
            elif export.startswith(('load_', 'get_', 'set_', 'create_')):
                functions.append((export, module_path))
            elif export[0].isupper():
                classes.append((export, module_path))
            else:
                others.append((export, module_path))

        def format_section(title, items):
            if items:
                lines.append(f"{title} ({len(items)}):")
                for item, module_path in items:
                    lines.append(f"  {item} (from {module_path})")
                lines.append("")

        format_section("Main Classes", classes)
        format_section("Protocols", protocols)
        format_section("Decorators", decorators)
        format_section("Functions", functions)
        format_section("Exceptions", exceptions)
        format_section("Other", others)

    # External (Public) APIs
    lines.extend([
        "=" * 80,
        "EXTERNAL (Public) - Items not starting with '_'",
        "=" * 80,
        ""
    ])

    if inventory.external_classes:
        lines.append(f"CLASSES ({len(inventory.external_classes)}):")
        for cls in sorted(inventory.external_classes):
            lines.append(f"  {cls}")
        lines.append("")

    if inventory.external_functions:
        lines.append(f"FUNCTIONS ({len(inventory.external_functions)}):")
        for func in sorted(inventory.external_functions):
            lines.append(f"  {func}")
        lines.append("")

    if inventory.external_methods:
        lines.append(f"METHODS ({len(inventory.external_methods)}):")
        for method in sorted(inventory.external_methods):
            lines.append(f"  {method}")
        lines.append("")

    if inventory.external_attributes:
        lines.append(f"ATTRIBUTES ({len(inventory.external_attributes)}):")
        for attr in sorted(inventory.external_attributes):
            lines.append(f"  {attr}")
        lines.append("")

    # Internal (Private) APIs
    lines.extend([
        "=" * 80,
        "INTERNAL (Private) - Items starting with '_'",
        "=" * 80,
        ""
    ])

    if inventory.internal_classes:
        lines.append(f"CLASSES ({len(inventory.internal_classes)}):")
        for cls in sorted(inventory.internal_classes):
            lines.append(f"  {cls}")
        lines.append("")

    if inventory.internal_functions:
        lines.append(f"FUNCTIONS ({len(inventory.internal_functions)}):")
        for func in sorted(inventory.internal_functions):
            lines.append(f"  {func}")
        lines.append("")

    if inventory.internal_methods:
        lines.append(f"METHODS ({len(inventory.internal_methods)}):")
        for method in sorted(inventory.internal_methods):
            lines.append(f"  {method}")
        lines.append("")

    if inventory.internal_attributes:
        lines.append(f"ATTRIBUTES ({len(inventory.internal_attributes)}):")
        for attr in sorted(inventory.internal_attributes):
            lines.append(f"  {attr}")
        lines.append("")

    # Special categories
    if any([inventory.exceptions, inventory.protocols, inventory.enums,
            inventory.dataclasses, inventory.type_aliases]):
        lines.extend([
            "=" * 80,
            "SPECIAL CATEGORIES",
            "=" * 80,
            ""
        ])

        for category, items, label in [
            (inventory.exceptions, "exceptions", "Exceptions"),
            (inventory.protocols, "protocols", "Protocols"),
            (inventory.enums, "enums", "Enums"),
            (inventory.dataclasses, "dataclasses", "Dataclasses"),
            (inventory.type_aliases, "type_aliases", "Type Aliases"),
        ]:
            if items:
                lines.append(f"{label} ({len(items)}):")
                for item in sorted(items):
                    lines.append(f"  {item}")
                lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Error: Path '{path}' does not exist", file=sys.stderr)
        sys.exit(1)

    collector = APICollector()
    inventory = collector.collect(path)
    print(format_inventory(inventory))


if __name__ == '__main__':
    main()