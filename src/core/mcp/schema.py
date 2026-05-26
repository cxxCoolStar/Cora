"""MCP tool schema and result data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPToolSchema:
    """MCP tool definition.
    
    Represents a tool provided by an MCP server, including its name,
    description, and input schema.
    """
    
    name: str
    """Tool name"""
    
    description: str
    """Tool description"""
    
    input_schema: dict[str, Any]
    """Input parameters JSON Schema"""
    
    server_name: str
    """Name of the MCP server providing this tool"""
    
    is_mutating: bool = False
    """Whether this tool performs mutating operations (for idempotency)"""
    
    idempotency_key_extractor: str | None = None
    """Expression to extract idempotency key from arguments (e.g., 'args.file_path')"""
    
    def to_tool_spec(self) -> dict[str, Any]:
        """Convert to LLM-compatible tool specification.
        
        Returns:
            Tool spec in OpenAI function calling format
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            }
        }


@dataclass
class MCPToolResult:
    """MCP tool execution result.
    
    Represents the result of calling an MCP tool, following the MCP protocol format.
    """
    
    content: list[dict[str, Any]] = field(default_factory=list)
    """Result content (MCP protocol format)"""
    
    is_error: bool = False
    """Whether this result represents an error"""
    
    error_code: str | None = None
    """Error code if is_error is True"""
    
    error_message: str | None = None
    """Error message if is_error is True"""
    
    metadata: dict[str, Any] | None = None
    """Additional metadata"""
    
    def to_text(self) -> str:
        """Convert result to plain text format for LLM consumption.
        
        Returns:
            Plain text representation of the result
        """
        if self.is_error:
            return f"Error: {self.error_message or 'Unknown error'}"
        
        # Extract text content from MCP protocol format
        texts = []
        for item in self.content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif item.get("type") == "resource":
                # Handle resource content
                resource_text = item.get("resource", {}).get("text", "")
                if resource_text:
                    texts.append(resource_text)
        
        return "\n".join(texts) if texts else "(empty result)"
