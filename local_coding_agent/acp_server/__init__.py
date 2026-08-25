"""Universal Agent Client Protocol (ACP) Server & Interop Gateway (R29).

Adapted from DeepSeek Harness @deepseek-ai/dsh-acp.
Implements the standard JSON-RPC 2.0 Agent Client Protocol over stdio with
both Content-Length header and newline-delimited JSONL framing support.
Exposes session lifecycle, prompting, cancellation, and repo tools to
external AI-native editors (Zed, Cursor, VS Code, JetBrains, OpenCode).
"""

from ._codec import AcpCodec
from ._protocol import (
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
)
from ._server import AcpServer
from ._session import AcpSession
from ._tools import ACP_TOOLS

__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "JSONRPC_PARSE_ERROR",
    "JSONRPC_INVALID_REQUEST",
    "JSONRPC_METHOD_NOT_FOUND",
    "JSONRPC_INVALID_PARAMS",
    "JSONRPC_INTERNAL_ERROR",
    "ACP_TOOLS",
    "AcpCodec",
    "AcpSession",
    "AcpServer",
]
