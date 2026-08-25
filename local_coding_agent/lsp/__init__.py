"""Generic LSP Stdio Code Intelligence Seam & Language Server Navigation (R25).

Adapted from DeepSeek Harness @deepseek-ai/dsh-lsp and @deepseek-ai/dsh-tool-lsp.
Provides standardized JSON-RPC stdio language server communication, process lifecycle,
protocol translation, mock servers, and AST/regex fallback intelligence.
"""

from __future__ import annotations

from ._types import (
    DEFAULT_SERVER_CANDIDATES,
    EXTENSION_TO_LANGUAGE,
    HEADER_SEPARATOR,
    LspConnectionError,
    LspError,
    LspHoverResult,
    LspLocation,
    LspPosition,
    LspRange,
    LspResponseError,
    LspSymbol,
    LspTimeoutError,
    MAX_HEADER_BYTES,
    MAX_MESSAGE_BYTES,
    MessageDecoder,
    SYMBOL_KIND_NAMES,
    encode_message,
    path_to_uri,
    uri_to_path,
)
from ._client import LspClient
from ._fallback import FallbackLspEngine
from ._manager import LspManager
from ._mock import MockLspServer

__all__ = [
    "DEFAULT_SERVER_CANDIDATES",
    "EXTENSION_TO_LANGUAGE",
    "FallbackLspEngine",
    "HEADER_SEPARATOR",
    "LspClient",
    "LspConnectionError",
    "LspError",
    "LspHoverResult",
    "LspLocation",
    "LspManager",
    "LspPosition",
    "LspRange",
    "LspResponseError",
    "LspSymbol",
    "LspTimeoutError",
    "MAX_HEADER_BYTES",
    "MAX_MESSAGE_BYTES",
    "MessageDecoder",
    "MockLspServer",
    "SYMBOL_KIND_NAMES",
    "encode_message",
    "path_to_uri",
    "uri_to_path",
]
