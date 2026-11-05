"""JSON schemas for the mission tools."""

OPEN = {
    "type": "object",
    "properties": {
        "port": {"type": "integer", "description": "Loopback port (default 7337).", "minimum": 1024, "maximum": 65535},
        "open_browser": {"type": "boolean", "description": "Open the deck in the default browser (default true)."},
    },
    "additionalProperties": False,
}

SNAPSHOT = {
    "type": "object",
    "properties": {
        "hours": {"type": "integer", "minimum": 1, "maximum": 168},
        "top": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    "additionalProperties": False,
}

SEARCH = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Words to look for in every stored message."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    },
    "required": ["query"],
    "additionalProperties": False,
}

WASTE = {"type": "object", "properties": {}, "additionalProperties": False}
