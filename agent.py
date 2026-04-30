"""
Streaming agent with Claude tool-use loop.
Yields Server-Sent Event strings that the Flask route forwards to the browser.
"""

from __future__ import annotations

import json
from typing import Generator

from dotenv import load_dotenv
load_dotenv()

import anthropic

from tools import TOOL_SCHEMAS, execute_tool

_client = anthropic.Anthropic()

SYSTEM_PROMPT = """You are a professional wealth advisor and real-time trading analyst. You have live market data tools and can:

1. **Stock analysis** — technical indicators (50/200-day MA, RSI, MACD, momentum), trend, buy/sell/hold signal
2. **Portfolio review** — analyze the user's full portfolio, detect investing style, recommend actions
3. **Day trading setups** — fetch intraday candles, identify VWAP, support/resistance, candlestick patterns, and give specific entry zone, exit target, and stop loss
4. **Real-time prices** — get current price and daily change for any ticker
5. **Stock search** — find ticker symbols by company name

Guidelines:
- ALWAYS call the relevant tool to get live data before answering — never guess prices or levels
- For day trading questions, use `analyze_day_trading` and explain the setup clearly: direction, entry zone, exit target, stop loss, risk/reward ratio
- For candlestick patterns explain what they mean in plain English
- Be specific and data-driven; cite the actual numbers from tool results
- Keep responses concise but complete — use bullet points for levels and signals
- Always add a brief risk disclaimer for day trading recommendations
- If the user mentions "my portfolio", call `get_portfolio` first to see their holdings"""


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def stream_response(
    user_message: str,
    history: list,
    user_id: int,
) -> Generator[str, None, None]:
    """
    Run the agentic tool-use loop and yield SSE strings.

    SSE event types sent to the browser:
      {"type": "text",      "content": "..."}        — text chunk to append
      {"type": "tool_call", "name": "...", "message": "..."} — tool progress
      {"type": "chart",     "data": {...}}            — candlestick chart data
      {"type": "done"}                                — stream finished
      {"type": "error",     "message": "..."}         — error
    """
    messages = list(history) + [{"role": "user", "content": user_message}]

    try:
        while True:
            full_text   = ""
            tool_uses   = []
            stop_reason = None

            # ── Stream Claude response ────────────────────────────────────────
            with _client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
            ) as stream:

                for event in stream:
                    etype = getattr(event, "type", None)

                    if etype == "content_block_start":
                        block = event.content_block
                        if getattr(block, "type", None) == "tool_use":
                            tool_uses.append({
                                "id":    block.id,
                                "name":  block.name,
                                "input": "",
                            })

                    elif etype == "content_block_delta":
                        delta = event.delta
                        dtype = getattr(delta, "type", None)
                        if dtype == "text_delta":
                            full_text += delta.text
                            yield _sse({"type": "text", "content": delta.text})
                        elif dtype == "input_json_delta" and tool_uses:
                            tool_uses[-1]["input"] += delta.partial_json

                final_msg   = stream.get_final_message()
                stop_reason = final_msg.stop_reason

            # ── No tools needed — done ────────────────────────────────────────
            if stop_reason != "tool_use":
                messages.append({"role": "assistant", "content": full_text})
                yield _sse({"type": "done"})
                return

            # ── Execute tool calls ────────────────────────────────────────────
            messages.append({"role": "assistant", "content": final_msg.content})
            tool_results = []

            for tu in tool_uses:
                name = tu["name"]
                try:
                    inputs = json.loads(tu["input"]) if tu["input"] else {}
                except json.JSONDecodeError:
                    inputs = {}

                friendly = {
                    "get_stock_analysis":  "Fetching technical analysis...",
                    "get_current_price":   "Getting live price...",
                    "analyze_day_trading": "Analyzing intraday candles...",
                    "get_portfolio":       "Loading your portfolio...",
                    "analyze_portfolio":   "Running full portfolio analysis (~60s)...",
                    "search_stock":        "Searching for stock...",
                }.get(name, f"Running {name}...")

                yield _sse({"type": "tool_call", "name": name, "message": friendly})

                result = execute_tool(name, inputs, user_id)

                # Send rich stock card before Claude writes its commentary
                if name == "get_stock_analysis" and not result.get("error"):
                    yield _sse({"type": "stock_card", "data": result})

                # Send chart data as a separate event; strip candles from the prompt
                if "candles" in result and result["candles"]:
                    yield _sse({"type": "chart", "data": result})
                    result_for_claude = {k: v for k, v in result.items() if k != "candles"}
                else:
                    result_for_claude = result

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tu["id"],
                    "content":     json.dumps(result_for_claude),
                })

            messages.append({"role": "user", "content": tool_results})
            # Loop back — Claude will now respond using the tool results

    except Exception as exc:
        yield _sse({"type": "error", "message": str(exc)})
        yield _sse({"type": "done"})
