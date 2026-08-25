"""ACP protocol constants and error codes."""

from .. import __version__

PROTOCOL_VERSION: str = "2026-08-20"
SERVER_NAME: str = "local-coding-agent-acp"
SERVER_VERSION: str = __version__

JSONRPC_PARSE_ERROR: int = -32700
JSONRPC_INVALID_REQUEST: int = -32600
JSONRPC_METHOD_NOT_FOUND: int = -32601
JSONRPC_INVALID_PARAMS: int = -32602
JSONRPC_INTERNAL_ERROR: int = -32603
