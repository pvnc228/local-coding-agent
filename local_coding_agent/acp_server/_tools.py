"""ACP tool catalog schemas."""

from typing import Any

ACP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "spill_read",
        "description": "Read or paginate a spilled tool output artifact (R24).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "locator": {"type": "string", "description": "Spill locator token (e.g. locator:spill:... or path)"},
                "offset": {"type": "integer", "description": "0-based line offset", "default": 0},
                "limit": {"type": "integer", "description": "Maximum number of lines to read", "default": 1000},
            },
            "required": ["locator"],
        },
    },
    {
        "name": "grep",
        "description": "Fast ripgrep / regex code search across workspace (R24).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query or regex pattern"},
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Optional glob filters or paths"},
                "regex": {"type": "boolean", "description": "Treat query as regular expression", "default": False},
                "case_sensitive": {"type": "boolean", "description": "Perform case-sensitive matching", "default": False},
                "max_results": {"type": "integer", "description": "Maximum match results", "default": 100},
                "workspace": {"type": "string", "description": "Workspace root directory override"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "lsp",
        "description": "Run LSP code intelligence query (definition, references, hover, symbols) (R25).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["definition", "references", "hover", "symbols"],
                    "description": "LSP operation",
                },
                "file": {"type": "string", "description": "Target source file path"},
                "line": {"type": "integer", "description": "0-based line number", "default": 0},
                "char": {"type": "integer", "description": "0-based character/column offset", "default": 0},
                "workspace": {"type": "string", "description": "Workspace root directory override"},
            },
            "required": ["operation", "file"],
        },
    },
    {
        "name": "skeletonize",
        "description": "Parse and skeletonize code symbols using AST compactor (R17).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Target source file path"},
                "symbols": {"type": "array", "items": {"type": "string"}, "description": "Target symbols to retain in full"},
            },
            "required": ["file"],
        },
    },
    {
        "name": "lint_patch",
        "description": "Fast pre-test semantic linter checking patch in memory (R18).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "patch": {"type": "string", "description": "Unified diff or SEARCH/REPLACE patch to check"},
                "workspace": {"type": "string", "description": "Workspace root directory override"},
            },
            "required": ["patch"],
        },
    },
    {
        "name": "read_file",
        "description": "Read one UTF-8 file from workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace"},
                "workspace": {"type": "string", "description": "Workspace root directory override"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": "List files below workspace directory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path relative to workspace", "default": "."},
                "workspace": {"type": "string", "description": "Workspace root directory override"},
            },
        },
    },
    {
        "name": "run_tests",
        "description": "Run test or shell check command in workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "workspace": {"type": "string", "description": "Workspace root directory override"},
                "timeout": {"type": "number", "description": "Timeout in seconds", "default": 60},
            },
            "required": ["command"],
        },
    },
    {
        "name": "propose_patch",
        "description": "Validate whether a patch applies cleanly to the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "patch": {"type": "string", "description": "Patch content to validate"},
                "workspace": {"type": "string", "description": "Workspace root directory override"},
            },
            "required": ["patch"],
        },
    },
]
