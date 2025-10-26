from fastmcp import FastMCP

app = FastMCP(
    name="Demo MCP Server",
    streamable_http_path="/mcp"
)

@app.tool()
async def echo_tool(input: str) -> str:
    """一个简单的回声工具"""
    return f"Echo: {input}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, transport="streamable-http"
    )
